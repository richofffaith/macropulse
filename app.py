"""Fintech-style dashboard for multi-state WARN notices and FRED stress signals."""

from __future__ import annotations

import csv
import html
import re
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from collect_macro_signals import (
    AUTO_DELINQUENCY_FLAG_THRESHOLD,
    CREDIT_CARD_DELINQUENCY_FLAG_THRESHOLD,
    GEO_LABOR_SERIES,
    HISTORY_CSV,
    UNEMPLOYMENT_FLAG_THRESHOLD,
    _clip_score,
    collect_macro_signals,
    latest_snapshot,
)
from us_warn import US_WARN_CSV, collect_us_warn, warn_status

SUBSCRIBERS_CSV = Path(__file__).resolve().parent / "subscribers.csv"
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

REQUIRED_HISTORY_COLS = [
    "MSACSR",
    "PERMIT",
    "PERMIT_YOY",
    "TOTALSA",
    "PSAVERT",
    "housing_flag",
    "UNRATE",
    "CAUR",
    "TXUR",
    "NYUR",
    "FLUR",
    "CAICLAIMS",
    "TXICLAIMS",
    "NYICLAIMS",
    "FLICLAIMS",
    "MORTGAGE30US",
    "CUSR0000SEHA",
    "RENT_YOY",
    "DRALACBS",
    "CSUSHPINSA",
    "REVOLSL",
    "DRCCLACBS",
]
PAGE_SIZE = 20
DATA_MAX_AGE_SECONDS = 6 * 3600
REFRESH_RETRY_SECONDS = 60 * 60

st.set_page_config(
    page_title="MacroPulse // Economic Stress Radar",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@500;600;700&display=swap');
.stApp {
    background-color: #07090E !important;
    background-image:
        radial-gradient(at 0% 0%, rgba(30, 58, 138, 0.25) 0px, transparent 50%),
        radial-gradient(at 100% 100%, rgba(15, 118, 110, 0.2) 0px, transparent 50%),
        linear-gradient(rgba(255, 255, 255, 0.02) 1px, transparent 1px),
        linear-gradient(90deg, rgba(255, 255, 255, 0.02) 1px, transparent 1px) !important;
    background-size: 100% 100%, 100% 100%, 40px 40px, 40px 40px !important;
    color: #F8FAFC !important;
    font-family: Inter, -apple-system, sans-serif;
}
.stApp::before,
.stApp::after {
    content: "";
    position: fixed;
    left: 50%;
    top: 50%;
    width: min(78vmin, 820px);
    height: min(78vmin, 820px);
    pointer-events: none;
    z-index: 0;
}
.stApp::before {
    transform: translate(-50%, -50%);
    background:
        linear-gradient(rgba(255,255,255,0.05), rgba(255,255,255,0.05)) center / 100% 1px no-repeat,
        linear-gradient(rgba(255,255,255,0.05), rgba(255,255,255,0.05)) center / 1px 100% no-repeat,
        radial-gradient(circle, transparent 11.6%, rgba(148,163,184,0.18) 12%, transparent 12.7%),
        radial-gradient(circle, transparent 24.6%, rgba(148,163,184,0.14) 25%, transparent 25.7%),
        radial-gradient(circle, transparent 37.6%, rgba(148,163,184,0.12) 38%, transparent 38.7%),
        radial-gradient(circle, transparent 49.4%, rgba(148,163,184,0.10) 50%, transparent 50.7%);
    opacity: 0.7;
}
.stApp::after {
    background: conic-gradient(from 0deg, transparent 0deg 292deg, rgba(56,189,248,0.16) 328deg, transparent 360deg);
    -webkit-mask-image: radial-gradient(circle, #000 0 49.5%, transparent 50%);
    mask-image: radial-gradient(circle, #000 0 49.5%, transparent 50%);
    animation: mpSweep 22s linear infinite;
    will-change: transform;
}
@keyframes mpSweep {
    from { transform: translate(-50%, -50%) rotate(0deg); }
    to { transform: translate(-50%, -50%) rotate(360deg); }
}
@media (prefers-reduced-motion: reduce) {
    .stApp::after {
        animation: none !important;
        transform: translate(-50%, -50%);
    }
}
section[data-testid="stSidebar"] {
    z-index: 100 !important;
}
[data-testid="stAppViewContainer"],
[data-testid="stHeader"],
[data-testid="stMain"],
[data-testid="stMainBlockContainer"],
[data-testid="stAppViewBlockContainer"],
.stMain,
.stAppViewContainer,
.main,
.main .block-container,
.stApp > header {
    background: transparent !important;
    background-color: transparent !important;
    background-image: none !important;
    position: relative;
    z-index: 1;
}
[data-testid="stMetric"],
div[data-testid="stVerticalBlockBorderWrapper"] > div {
    background: rgba(15, 20, 32, 0.86) !important;
    border: 1px solid rgba(255, 255, 255, 0.07) !important;
    border-radius: 14px !important;
    padding: 18px 22px !important;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4) !important;
    transition: transform 0.2s ease, border-color 0.2s ease !important;
}
[data-testid="stMetric"]:hover {
    transform: translateY(-2px) !important;
    border-color: rgba(255, 255, 255, 0.2) !important;
}
[data-testid="stMetricLabel"] p {
    color: #64748B !important;
    font-size: 0.72rem !important;
    text-transform: uppercase !important;
    letter-spacing: 1px !important;
    font-weight: 600 !important;
}
[data-testid="stMetricValue"],
[data-testid="stMetricValue"] div {
    font-family: "JetBrains Mono", monospace !important;
    font-size: 2.2rem !important;
    font-weight: 700 !important;
    color: #FFFFFF !important;
    letter-spacing: -0.5px !important;
}
html, body, p, label, h1, h2, h3, h4 {
    font-family: Inter, -apple-system, sans-serif;
}
.top-nav {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    margin: 0 0 1.1rem 0;
    padding: 0.15rem 0 0.85rem 0;
    border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}
.brand-lockup {
    display: flex;
    align-items: center;
    gap: 0.7rem;
}
.brand-name {
    font-weight: 700;
    font-size: 1.28rem;
    letter-spacing: -0.04em;
    color: #FFFFFF;
}
.live-radar {
    display: inline-flex;
    align-items: center;
    gap: 0.38rem;
    color: #94A3B8;
    font-size: 0.78rem;
    letter-spacing: 0.04em;
}
.live-radar .pulse {
    width: 0.48rem;
    height: 0.48rem;
    border-radius: 50%;
    background: #10B981;
    box-shadow: 0 0 10px rgba(16, 185, 129, 0.7);
}
.live-radar.mode-sim {
    color: #FBBF24;
}
.live-radar.mode-sim .pulse {
    background: #D97706;
    box-shadow: 0 0 10px rgba(217, 119, 6, 0.7);
}
.live-radar.mode-crisis {
    color: #FCA5A5;
    font-weight: 600;
}
.live-radar.mode-crisis .pulse {
    background: #DC2626;
    box-shadow: 0 0 12px rgba(220, 38, 38, 0.8);
}
.cockpit-card.crisis {
    border-color: rgba(220, 38, 38, 0.38);
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4), 0 0 28px rgba(220, 38, 38, 0.12);
}
.cockpit-card, .feed-card {
    background: rgba(15, 20, 32, 0.72);
    border: 1px solid rgba(255, 255, 255, 0.07);
    border-radius: 14px;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
    padding: 1.15rem 1.25rem 1.2rem 1.25rem;
    min-height: 290px;
}
.stress-readout, .sahm-value, .feed-workers, .feed-date, .nav-meta {
    font-family: "JetBrains Mono", monospace !important;
}
.stress-readout {
    font-size: 2.55rem;
    font-weight: 700;
    letter-spacing: -1px;
    color: #FFFFFF;
    line-height: 1;
}
.stress-track {
    margin: 0.85rem 0 1rem 0;
    height: 10px;
    border-radius: 999px;
    background: rgba(255, 255, 255, 0.06);
    overflow: hidden;
}
.stress-fill {
    height: 100%;
    border-radius: 999px;
    background: linear-gradient(90deg, #059669 0%, #D97706 52%, #DC2626 100%);
    box-shadow: 0 0 16px rgba(59, 91, 255, 0.28);
}
.sahm-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 0.75rem;
    margin: 0 0 0.85rem 0;
    padding: 0.55rem 0.7rem;
    border: 1px solid rgba(255, 255, 255, 0.06);
    border-radius: 10px;
}
.sahm-label { color: #64748B; font-size: 0.72rem; letter-spacing: 1px; text-transform: uppercase; font-weight: 600; }
.sahm-value { color: #FFFFFF; font-size: 1.05rem; font-weight: 600; }
.cockpit-memo { color: #94A3B8; font-size: 0.9rem; line-height: 1.55; margin: 0; }
.feed-row {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 0.2rem 0.8rem;
    padding: 0.62rem 0;
    border-bottom: 1px solid rgba(255, 255, 255, 0.05);
}
.feed-row:last-child { border-bottom: 0; }
.feed-company { color: #FFFFFF; font-weight: 700; font-size: 0.92rem; line-height: 1.3; }
.feed-meta { display: flex; gap: 0.45rem; align-items: center; margin-top: 0.18rem; }
.feed-workers { color: #FB7185; font-weight: 700; font-size: 0.92rem; text-align: right; }
.feed-date { color: #64748B; font-size: 0.72rem; }
.stButton > button,
.stDownloadButton button,
button[kind="primary"],
.stFormSubmitButton button[kind="primary"] {
    border-radius: 999px !important;
}
div.st-key-inline_subscribe {
    display: flex !important;
    align-items: flex-end !important;
    height: 100% !important;
}
div.st-key-inline_subscribe .stButton,
div.st-key-inline_subscribe button {
    width: 100% !important;
    height: 2.75rem !important;
    min-height: 2.75rem !important;
    margin: 0 !important;
}
[data-testid="stExpander"] {
    background: rgba(15, 20, 32, 0.55) !important;
    border: 1px solid rgba(255, 255, 255, 0.07) !important;
    border-radius: 14px !important;
}
.stPlotlyChart, [data-testid="stPlotlyChart"] {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
}
</style>
    """,
    unsafe_allow_html=True,
)

FINTECH_CSS = """
<style>
@keyframes ambientShift {
    0% { background-position: 0% 0%; }
    50% { background-position: 100% 100%; }
    100% { background-position: 0% 0%; }
}

html, body {
    background: transparent !important;
    background-color: transparent !important;
    color: #F8FAFC !important;
    font-family: Inter, -apple-system, sans-serif;
}
.block-container { padding-top: 1.6rem; max-width: 1280px; }
header[data-testid="stHeader"] {
    background: transparent !important;
    backdrop-filter: none !important;
    -webkit-backdrop-filter: none !important;
    box-shadow: none !important;
    pointer-events: none;
}
header[data-testid="stHeader"] [data-testid="collapsedControl"],
header[data-testid="stHeader"] [data-testid="stSidebarCollapsedControl"],
header[data-testid="stHeader"] [data-testid="stExpandSidebarButton"],
header[data-testid="stHeader"] button {
    pointer-events: auto !important;
    background: rgba(14, 18, 27, 0.85) !important;
    border: 1px solid rgba(255, 255, 255, 0.08) !important;
    border-radius: 12px !important;
    color: #FFFFFF !important;
}
footer, [data-testid="stToolbar"], [data-testid="stDecoration"] {
    visibility: hidden;
    height: 0;
}
[data-testid="collapsedControl"],
[data-testid="stSidebarCollapsedControl"],
[data-testid="stExpandSidebarButton"] {
    display: flex !important;
    visibility: visible !important;
    opacity: 1 !important;
    z-index: 999999 !important;
    position: fixed !important;
    top: 0.65rem !important;
    left: 0.65rem !important;
    width: 2.4rem !important;
    height: 2.4rem !important;
    align-items: center !important;
    justify-content: center !important;
    background: rgba(14, 18, 27, 0.85) !important;
    backdrop-filter: blur(24px) !important;
    -webkit-backdrop-filter: blur(24px) !important;
    border: 1px solid rgba(255, 255, 255, 0.08) !important;
    border-radius: 12px !important;
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.12), 0 16px 36px rgba(0, 0, 0, 0.5) !important;
    color: #FFFFFF !important;
}
[data-testid="collapsedControl"] svg,
[data-testid="stSidebarCollapsedControl"] svg,
[data-testid="stExpandSidebarButton"] svg,
[data-testid="collapsedControl"] span,
[data-testid="stSidebarCollapsedControl"] span {
    fill: #ffffff !important;
    color: #ffffff !important;
    stroke: #ffffff !important;
}

h1, h2, h3, p, label { font-family: Inter, -apple-system, sans-serif; }
h1 { letter-spacing: -1px; font-weight: 700; color: #FFFFFF; }
h2, h3 { color: #FFFFFF; }
.hero-kicker {
    color: #6B7A90;
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 1.2px;
    text-transform: uppercase;
    margin-bottom: 0.35rem;
}
.takeaway-card, .landing-hero, .sim-side-head, .newsletter-card,
.strain-card, .major-actions-card, .coping-item, .major-action,
.stPlotlyChart, [data-testid="stPlotlyChart"],
div[data-testid="stExpander"], [data-testid="stDataFrame"],
[data-testid="stDataFrameResizable"] {
    background: rgba(14, 18, 27, 0.7) !important;
    backdrop-filter: blur(24px);
    -webkit-backdrop-filter: blur(24px);
    border: 1px solid rgba(255, 255, 255, 0.07) !important;
    border-radius: 14px !important;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
    transition: border-color 0.25s ease, transform 0.25s ease, box-shadow 0.25s ease;
}
.takeaway-card:hover, .landing-hero:hover, .strain-card:hover,
.coping-item:hover, .major-action:hover, .major-actions-card:hover {
    border-color: rgba(255, 255, 255, 0.2) !important;
    transform: translateY(-2px);
}
.takeaway-card {
    padding: 1.15rem 1.25rem;
    min-height: 240px;
}
.takeaway-card h3 {
    margin: 0 0 0.75rem 0;
    font-size: 1.05rem;
    color: #FFFFFF;
}
.takeaway-card p { color: #6B7A90; line-height: 1.55; margin: 0 0 0.85rem 0; }
.flag-pill {
    display: inline-block;
    margin: 0 0.35rem 0.45rem 0;
    padding: 0.22rem 0.65rem;
    border-radius: 999px;
    background: rgba(239, 68, 68, 0.15);
    border: 1px solid rgba(239, 68, 68, 0.28);
    box-shadow: 0 0 14px rgba(239, 68, 68, 0.16);
    color: #FCA5A5;
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.04em;
}
.flag-pill.ok {
    background: rgba(16, 185, 129, 0.15);
    border-color: rgba(16, 185, 129, 0.28);
    box-shadow: 0 0 14px rgba(16, 185, 129, 0.16);
    color: #6EE7B7;
}
[data-testid="stMetric"],
div[data-testid="stVerticalBlockBorderWrapper"] > div {
    background: rgba(15, 20, 32, 0.86) !important;
    border: 1px solid rgba(255, 255, 255, 0.07) !important;
    border-radius: 14px !important;
    padding: 18px 22px !important;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4) !important;
    transition: transform 0.2s ease, border-color 0.2s ease !important;
}
[data-testid="stMetric"]:hover {
    transform: translateY(-2px) !important;
    border-color: rgba(255, 255, 255, 0.2) !important;
}
[data-testid="stMetricLabel"] p,
div[data-testid="stMetric"] label {
    color: #64748B !important;
    font-size: 0.72rem !important;
    text-transform: uppercase !important;
    letter-spacing: 1px !important;
    font-weight: 600 !important;
}
[data-testid="stMetricValue"],
[data-testid="stMetricValue"] div {
    font-family: "JetBrains Mono", monospace !important;
    font-size: 2.2rem !important;
    font-weight: 700 !important;
    color: #FFFFFF !important;
    letter-spacing: -0.5px !important;
}
.stDownloadButton button, .stButton button, .stFormSubmitButton button {
    font-weight: 600 !important;
    border-radius: 10px !important;
}
.stDownloadButton button, button[kind="primary"], .stFormSubmitButton button[kind="primary"] {
    background: #3B5BFF !important;
    color: #FFFFFF !important;
    border: none !important;
    border-radius: 999px !important;
    font-weight: 600 !important;
    padding: 10px 20px !important;
    box-shadow: 0 4px 14px rgba(59, 91, 255, 0.35) !important;
}
button[kind="secondary"] {
    background: rgba(14, 18, 27, 0.7) !important;
    color: #FFFFFF !important;
    border: 1px solid rgba(255, 255, 255, 0.08) !important;
}
section[data-testid="stSidebar"] .stButton > button:not([kind="primary"]) {
    border-radius: 12px !important;
    background: rgba(14, 18, 27, 0.7) !important;
    border: 1px solid rgba(255, 255, 255, 0.08) !important;
    color: #FFFFFF !important;
    font-family: Inter, -apple-system, sans-serif !important;
    font-size: 0.85rem !important;
    font-weight: 500 !important;
    letter-spacing: 0.01em !important;
    text-transform: none !important;
    width: 100% !important;
    box-shadow: 0 12px 32px rgba(0, 0, 0, 0.25) !important;
    min-height: 2.55rem !important;
    transition: border-color 0.16s ease, color 0.16s ease, box-shadow 0.16s ease !important;
}
section[data-testid="stSidebar"] .stButton > button:not([kind="primary"]):hover,
section[data-testid="stSidebar"] .st-key-sim_reset_live .stButton > button:not([kind="primary"]):hover,
section[data-testid="stSidebar"] .st-key-sim_crisis_2008 .stButton > button:not([kind="primary"]):hover,
section[data-testid="stSidebar"] .stButton > button:not([kind="primary"]):focus {
    background: rgba(14, 18, 27, 0.85) !important;
    border-color: #3B5BFF !important;
    color: #3B5BFF !important;
    box-shadow: 0 4px 14px rgba(59, 91, 255, 0.28) !important;
}
.landing-hero {
    padding: 1.4rem 1.5rem 1.25rem 1.5rem;
    margin-bottom: 0.35rem;
}
.landing-hero h1 {
    margin: 0;
    font-size: clamp(1.5rem, 3.2vw, 2.2rem);
    line-height: 1.15;
    color: #FFFFFF;
}
.landing-hero h1 .accent { color: #3B5BFF; }
.landing-sub {
    margin: 0.7rem 0 0 0;
    max-width: 46rem;
    color: #6B7A90;
    font-size: 1rem;
    line-height: 1.55;
}
.hero-sim-pill, .live-pill {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    margin: 0.85rem 0.45rem 0 0;
    padding: 0.28rem 0.7rem;
    border-radius: 999px;
    background: rgba(16, 185, 129, 0.12);
    border: 1px solid rgba(16, 185, 129, 0.28);
    box-shadow: 0 0 12px rgba(16, 185, 129, 0.16);
    color: #10B981;
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.5px;
    text-transform: uppercase;
}
.live-pill .live-dot {
    width: 0.42rem;
    height: 0.42rem;
    border-radius: 50%;
    background: #10B981;
    box-shadow: 0 0 10px rgba(16, 185, 129, 0.55);
}
.sim-side-head {
    margin: 0.35rem 0 0.55rem 0;
    padding: 0.85rem 0.9rem 0.75rem 0.9rem;
}
.sim-side-head h3 {
    margin: 0.2rem 0 0.35rem 0;
    font-size: 1.02rem;
    color: #FFFFFF;
}
.sim-side-head p {
    margin: 0;
    color: #6B7A90;
    font-size: 0.82rem;
    line-height: 1.45;
}
.newsletter-card {
    margin-top: 1.4rem;
    padding: 1.15rem 1.25rem 0.35rem 1.25rem;
}
.newsletter-card h3 {
    margin: 0 0 0.35rem 0;
    font-size: 1.05rem;
    color: #FFFFFF;
}
.newsletter-card p {
    margin: 0 0 0.85rem 0;
    color: #6B7A90;
    font-size: 0.92rem;
    line-height: 1.5;
}
div[data-testid="stDialog"] > div:first-child {
    background: rgba(11, 14, 23, 0.72);
}
div[data-testid="stDialog"] div[role="dialog"] {
    background: rgba(14, 18, 27, 0.92);
    backdrop-filter: blur(24px);
    -webkit-backdrop-filter: blur(24px);
    border: 1px solid rgba(255, 255, 255, 0.07);
    border-radius: 14px;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
}
div[data-testid="stDialog"] button,
div[data-testid="stDialog"] .stFormSubmitButton button {
    background: #3B5BFF !important;
    color: #FFFFFF !important;
    border: none !important;
    border-radius: 10px !important;
    font-weight: 600 !important;
    box-shadow: 0 4px 14px rgba(59, 91, 255, 0.35) !important;
}
.legal-footer {
    margin-top: 1.35rem;
    padding-top: 0.85rem;
    border-top: 1px solid rgba(255, 255, 255, 0.06);
}
.legal-copy {
    margin: 0.45rem 0 0.15rem 0;
    color: #6B7A90;
    font-size: 0.78rem;
    letter-spacing: 0.02em;
    line-height: 1.45;
}
div.st-key-legal_privacy .stButton > button,
div.st-key-legal_disclaimer .stButton > button,
div.st-key-legal_privacy .stButton > button[kind="secondary"],
div.st-key-legal_disclaimer .stButton > button[kind="secondary"] {
    background: transparent !important;
    background-image: none !important;
    border: 0 !important;
    box-shadow: none !important;
    border-radius: 0 !important;
    min-height: 1.55rem !important;
    height: auto !important;
    padding: 0.2rem 0.15rem !important;
    width: auto !important;
    color: #6B7A90 !important;
    font-family: Inter, -apple-system, sans-serif !important;
    font-size: 0.78rem !important;
    font-weight: 500 !important;
    letter-spacing: 0.03em !important;
    text-decoration: underline !important;
    text-underline-offset: 0.2em !important;
    text-decoration-color: rgba(139, 156, 179, 0.45) !important;
}
div.st-key-legal_privacy .stButton > button:hover,
div.st-key-legal_disclaimer .stButton > button:hover {
    background: transparent !important;
    color: #3B5BFF !important;
    text-decoration-color: #3B5BFF !important;
}
div[data-testid="stTabs"] [role="tablist"],
.stTabs [role="tablist"],
.stTabs [data-baseweb="tab-list"] {
    background: rgba(8, 11, 18, 0.78) !important;
    backdrop-filter: blur(24px) !important;
    -webkit-backdrop-filter: blur(24px) !important;
    border: 1px solid rgba(255, 255, 255, 0.08) !important;
    border-radius: 999px !important;
    padding: 6px !important;
    gap: 6px !important;
    border-bottom: none !important;
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.08), 0 12px 28px rgba(0, 0, 0, 0.35);
}
button[data-testid="stTab"],
.stTabs [data-baseweb="tab"] {
    background: transparent !important;
    color: #6B7A90 !important;
    border-radius: 10px !important;
    font-weight: 500 !important;
    font-size: 0.9rem !important;
    padding: 8px 20px !important;
    border: none !important;
    box-shadow: none !important;
    transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1) !important;
}
button[data-testid="stTab"]:hover,
.stTabs [data-baseweb="tab"]:hover {
    color: #E2E8F0 !important;
    background: rgba(255, 255, 255, 0.05) !important;
    cursor: pointer;
}
button[data-testid="stTab"][aria-selected="true"],
.stTabs [aria-selected="true"] {
    background: rgba(59, 91, 255, 0.16) !important;
    color: #FFFFFF !important;
    font-weight: 600 !important;
    box-shadow: 0 0 0 1px rgba(59, 91, 255, 0.45), 0 6px 18px rgba(59, 91, 255, 0.22) !important;
    border: 1px solid rgba(59, 91, 255, 0.35) !important;
}
button[data-testid="stTab"]::before,
button[data-testid="stTab"]::after,
.stTabs [data-baseweb="tab"]::before,
.stTabs [data-baseweb="tab"]::after,
div[data-testid="stTabs"] [data-baseweb="tab-highlight"],
div[data-testid="stTabs"] [data-baseweb="tab-border"],
.stTabs [data-baseweb="tab-highlight"],
.stTabs [data-baseweb="tab-border"] {
    display: none !important;
}
div.st-key-main_tab [data-testid="stPills"] > div,
div.st-key-main_tab .stPills > div,
div.st-key-main_tab [role="radiogroup"],
div.st-key-main_tab [data-baseweb="button-group"],
div.st-key-labor_geo [role="radiogroup"],
div.st-key-labor_geo [data-baseweb="button-group"],
div.st-key-labor_timeframe [role="radiogroup"],
div.st-key-labor_timeframe [data-baseweb="button-group"] {
    background: rgba(8, 11, 18, 0.78) !important;
    backdrop-filter: blur(24px) !important;
    -webkit-backdrop-filter: blur(24px) !important;
    border: 1px solid rgba(255, 255, 255, 0.08) !important;
    border-radius: 999px !important;
    padding: 6px !important;
    gap: 6px !important;
    border-bottom: none !important;
    flex-wrap: wrap !important;
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.08), 0 12px 28px rgba(0, 0, 0, 0.35);
}
div.st-key-main_tab button {
    background: transparent !important;
    color: #6B7A90 !important;
    border-radius: 10px !important;
    font-weight: 500 !important;
    font-size: 0.9rem !important;
    padding: 8px 20px !important;
    border: none !important;
    box-shadow: none !important;
    transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1) !important;
}
div.st-key-main_tab button:hover {
    color: #E2E8F0 !important;
    background: rgba(255, 255, 255, 0.05) !important;
    cursor: pointer;
}
div.st-key-main_tab button[aria-selected="true"],
div.st-key-main_tab button[aria-checked="true"],
div.st-key-main_tab button[aria-pressed="true"],
div.st-key-main_tab button[kind="primary"] {
    background: rgba(59, 91, 255, 0.16) !important;
    color: #FFFFFF !important;
    font-weight: 600 !important;
    box-shadow: 0 0 0 1px rgba(59, 91, 255, 0.45), 0 6px 18px rgba(59, 91, 255, 0.22) !important;
    border: 1px solid rgba(59, 91, 255, 0.35) !important;
}
div.st-key-main_tab button p {
    color: inherit !important;
    margin: 0 !important;
}
div.st-key-main_tab button::before,
div.st-key-main_tab button::after {
    display: none !important;
}
div[data-testid="stButtonGroup"] button {
    border-radius: 999px !important;
    font-weight: 500 !important;
    background: transparent !important;
    border: none !important;
    color: #6B7A90 !important;
}
div[data-testid="stButtonGroup"] button[kind="primary"],
div[data-testid="stButtonGroup"] button[aria-pressed="true"],
div[data-testid="stButtonGroup"] button[aria-checked="true"],
div[data-testid="stButtonGroup"] button[kind="pillsActive"],
div[data-testid="stButtonGroup"] button[data-testid="stBaseButton-pillsActive"] {
    background: rgba(59, 91, 255, 0.16) !important;
    border: 1px solid rgba(59, 91, 255, 0.35) !important;
    color: #FFFFFF !important;
    box-shadow: 0 0 0 1px rgba(59, 91, 255, 0.25), 0 6px 18px rgba(59, 91, 255, 0.18) !important;
}
div.st-key-main_tab [data-testid="stButtonGroup"] button,
div.st-key-main_tab [data-testid="stPills"] button {
    background: transparent !important;
    color: #6B7A90 !important;
    border-radius: 10px !important;
    font-weight: 500 !important;
    font-size: 0.9rem !important;
    padding: 8px 20px !important;
    border: none !important;
    box-shadow: none !important;
    transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1) !important;
}
div.st-key-main_tab [data-testid="stButtonGroup"] button:hover,
div.st-key-main_tab [data-testid="stPills"] button:hover {
    color: #E2E8F0 !important;
    background: rgba(255, 255, 255, 0.05) !important;
    cursor: pointer;
}
div.st-key-main_tab [data-testid="stButtonGroup"] button[aria-selected="true"],
div.st-key-main_tab [data-testid="stButtonGroup"] button[aria-checked="true"],
div.st-key-main_tab [data-testid="stButtonGroup"] button[aria-pressed="true"],
div.st-key-main_tab [data-testid="stButtonGroup"] button[kind="primary"],
div.st-key-main_tab [data-testid="stButtonGroup"] button[kind="pillsActive"],
div.st-key-main_tab [data-testid="stButtonGroup"] button[data-testid="stBaseButton-pillsActive"],
div.st-key-main_tab [data-testid="stPills"] button[aria-selected="true"],
div.st-key-main_tab [data-testid="stPills"] button[aria-checked="true"],
div.st-key-main_tab [data-testid="stPills"] button[aria-pressed="true"],
div.st-key-main_tab [data-testid="stPills"] button[kind="primary"],
div.st-key-main_tab [data-testid="stPills"] button[kind="pillsActive"],
div.st-key-main_tab button[kind="pillsActive"],
div.st-key-main_tab button[data-testid="stBaseButton-pillsActive"] {
    background: rgba(59, 91, 255, 0.16) !important;
    color: #FFFFFF !important;
    font-weight: 600 !important;
    box-shadow: 0 0 0 1px rgba(59, 91, 255, 0.45), 0 6px 18px rgba(59, 91, 255, 0.22) !important;
    border: 1px solid rgba(59, 91, 255, 0.35) !important;
}
.strain-card {
    padding: 1.15rem 1.25rem 1.05rem 1.25rem;
    margin: 0.35rem 0 1.1rem 0;
}
.strain-card h3 {
    margin: 0 0 0.35rem 0;
    font-size: 1.12rem;
    color: #FFFFFF;
}
.strain-card .strain-copy {
    color: #6B7A90;
    font-size: 0.9rem;
    line-height: 1.5;
    margin: 0 0 0.85rem 0;
}
.coping-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 0.75rem;
}
.coping-item {
    padding: 0.85rem 0.9rem;
    min-height: 148px;
}
.coping-item h4 {
    margin: 0.45rem 0 0.35rem 0;
    font-size: 0.92rem;
    color: #FFFFFF;
}
.coping-item p {
    margin: 0;
    color: #6B7A90;
    font-size: 0.82rem;
    line-height: 1.45;
}
.feed-heartbeat {
    display: inline-flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.42rem 0.55rem;
    margin: 0.78rem 0 0 0;
    padding: 0.38rem 0.85rem;
    max-width: 100%;
    background: rgba(14, 18, 27, 0.7);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 999px;
    color: #6B7A90;
    font-family: "JetBrains Mono", monospace;
    font-size: 0.75rem;
    font-weight: 500;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    line-height: 1.45;
}
.feed-heartbeat .hb-dot {
    width: 0.48rem;
    height: 0.48rem;
    border-radius: 50%;
    background: #10B981;
    box-shadow: 0 0 10px rgba(16, 185, 129, 0.55);
}
.feed-heartbeat .hb-sep { color: rgba(255, 255, 255, 0.1); }
.status-badge, .alert-badge {
    display: inline-flex;
    align-items: center;
    gap: 0.32rem;
    padding: 0.16rem 0.58rem;
    border-radius: 999px;
    font-size: 0.68rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    font-family: Inter, -apple-system, sans-serif;
}
.status-badge::before, .alert-badge::before {
    content: "●";
    font-size: 0.62rem;
    line-height: 1;
}
.status-badge {
    margin: 0.28rem 0.15rem 0.75rem 0.15rem;
}
.status-badge.healthy, .alert-badge.watch, .alert-badge.active {
    color: #10B981;
    background: rgba(16, 185, 129, 0.12);
    border: 1px solid rgba(16, 185, 129, 0.28);
    box-shadow: 0 0 12px rgba(16, 185, 129, 0.16);
}
.status-badge.caution, .alert-badge.warn {
    color: #FDE68A;
    background: rgba(245, 158, 11, 0.12);
    border: 1px solid rgba(245, 158, 11, 0.28);
    box-shadow: 0 0 12px rgba(245, 158, 11, 0.14);
}
.status-badge.stress, .alert-badge.alert {
    color: #FCA5A5;
    background: rgba(239, 68, 68, 0.12);
    border: 1px solid rgba(239, 68, 68, 0.28);
    box-shadow: 0 0 12px rgba(239, 68, 68, 0.16);
}
.major-actions-card {
    padding: 1.05rem 1.15rem 1rem 1.15rem;
    margin: 0.15rem 0 1rem 0;
}
.major-actions-card h3 {
    margin: 0 0 0.75rem 0;
    font-size: 1.05rem;
    color: #FFFFFF;
}
.major-actions-grid {
    display: grid;
    grid-template-columns: repeat(5, minmax(0, 1fr));
    gap: 0.65rem;
}
.major-action {
    padding: 0.8rem 0.85rem 0.75rem 0.85rem;
    min-height: 132px;
}
.major-action .state-chip {
    display: inline-block;
    margin-bottom: 0.45rem;
    padding: 0.08rem 0.38rem;
    border: 1px solid rgba(255, 255, 255, 0.06);
    border-radius: 999px;
    color: #3B5BFF;
    font-family: "JetBrains Mono", monospace;
    font-size: 0.68rem;
    font-weight: 600;
    letter-spacing: 0.06em;
}
.major-action .company {
    color: #FFFFFF;
    font-size: 0.9rem;
    font-weight: 700;
    line-height: 1.3;
    margin: 0 0 0.45rem 0;
}
.major-action .workers-hit {
    color: #fb7185;
    font-family: "JetBrains Mono", monospace;
    font-size: 0.92rem;
    font-weight: 700;
    margin: 0 0 0.3rem 0;
}
.major-action .notice-date {
    color: #6B7A90;
    font-family: "JetBrains Mono", monospace;
    font-size: 0.72rem;
}
.major-actions-empty {
    color: #6B7A90;
    font-size: 0.88rem;
    margin: 0;
}
div[data-testid="stExpander"] {
    margin: 1.15rem 0 0.45rem 0;
}
div[data-testid="stExpander"] p { color: #6B7A90; }
.metric-note, .readout-hint, .sahm-hint {
    color: #6B7A90;
    font-size: 0.68rem;
    line-height: 1.35;
    margin: -0.35rem 0.05rem 0.7rem 0.05rem;
}
.readout-hint { margin: 0.35rem 0 0.15rem 0; }
.sahm-hint { margin: 0.4rem 0 0.55rem 0; }
.section-head { margin: 0.35rem 0 1.05rem 0; }
.section-head h2 {
    margin: 0 0 0.28rem 0;
    font-size: 1.22rem;
    letter-spacing: -0.03em;
    color: #FFFFFF;
    font-weight: 650;
}
.section-head p {
    margin: 0;
    color: #6B7A90;
    font-size: 0.86rem;
    line-height: 1.45;
    max-width: 40rem;
}
.subhead {
    margin: 1.4rem 0 0.7rem 0;
    padding-top: 0.9rem;
    border-top: 1px solid rgba(255, 255, 255, 0.06);
    color: #64748B;
    font-size: 0.68rem;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    font-weight: 600;
}
.section-rail { margin: 1.2rem 0 0.2rem 0; }
.js-plotly-plot .modebar { display: none !important; }
@media (max-width: 1100px) {
    .major-actions-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 900px) {
    .coping-grid { grid-template-columns: 1fr; }
    .major-actions-grid { grid-template-columns: 1fr; }
}
@media (max-width: 768px) {
    .block-container {
        padding-top: 1.05rem !important;
        padding-left: 0.85rem !important;
        padding-right: 0.85rem !important;
    }
    .landing-hero {
        padding: 1rem 1.05rem 0.95rem 1.05rem;
        border-radius: 16px;
    }
    .landing-hero h1 {
        font-size: 1.32rem !important;
        line-height: 1.2;
    }
    .landing-sub {
        font-size: 0.88rem;
        margin-top: 0.45rem;
        max-width: 100%;
    }
    .feed-heartbeat {
        font-size: 0.64rem;
        letter-spacing: 0.04em;
        padding: 0.32rem 0.65rem;
    }
    .takeaway-card { min-height: 0; }
    div[data-testid="stHorizontalBlock"] {
        flex-wrap: wrap !important;
        row-gap: 0.55rem !important;
    }
    div[data-testid="stHorizontalBlock"] > div[data-testid="column"],
    div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"],
    div[data-testid="stHorizontalBlock"] > .stColumn {
        flex: 1 1 min(100%, 16.5rem) !important;
        min-width: min(100%, 16.5rem) !important;
        width: auto !important;
    }
    [data-testid="stButtonGroup"],
    div[data-testid="stPills"] > div,
    .stPills > div,
    div[role="radiogroup"] {
        flex-wrap: wrap !important;
        justify-content: flex-start !important;
        row-gap: 0.4rem !important;
        max-width: 100% !important;
        overflow: visible !important;
    }
    .stPlotlyChart,
    [data-testid="stPlotlyChart"],
    .js-plotly-plot,
    .plot-container,
    .js-plotly-plot .plotly {
        width: 100% !important;
        max-width: 100% !important;
        margin-left: 0 !important;
        margin-right: 0 !important;
    }
    .stPlotlyChart {
        margin-top: 0.35rem !important;
        margin-bottom: 0.55rem !important;
    }
}
.sim-banner {
    display: inline-block;
    margin: 0 0 0.75rem 0;
    padding: 0.32rem 0.7rem;
    border-radius: 999px;
    background: rgba(251, 191, 36, 0.14);
    border: 1px solid rgba(251, 191, 36, 0.45);
    color: #fde68a;
    font-size: 0.75rem;
    font-weight: 700;
    letter-spacing: 0.08em;
}
.sim-banner.crisis {
    background: rgba(239, 68, 68, 0.18);
    border-color: rgba(248, 113, 113, 0.55);
    color: #fecaca;
    letter-spacing: 0.06em;
}
section[data-testid="stSidebar"] {
    background: rgba(5, 7, 11, 0.72) !important;
    backdrop-filter: blur(24px);
    -webkit-backdrop-filter: blur(24px);
    border-right: 1px solid rgba(255, 255, 255, 0.08);
}
[data-testid="stSlider"] [data-baseweb="slider"] > div:first-child {
    background: rgba(255, 255, 255, 0.08) !important;
}
[data-testid="stSlider"] [data-baseweb="slider"] div[role="progressbar"],
[data-testid="stSlider"] [data-baseweb="slider"] > div > div {
    background-color: #38BDF8 !important;
}
[data-testid="stSlider"] [role="slider"],
[data-testid="stSlider"] [data-baseweb="thumb"] {
    background-color: #38BDF8 !important;
    border-color: #38BDF8 !important;
    box-shadow: 0 0 0 4px rgba(56, 189, 248, 0.22) !important;
}
[data-testid="stDataFrame"],
[data-testid="stDataFrameResizable"] {
    overflow: hidden;
}
div[data-testid="stCaption"] { color: #6B7A90 !important; }
.stPlotlyChart, [data-testid="stPlotlyChart"] {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    padding: 0 !important;
}
.stApp {
    background-color: #07090E !important;
    background-image:
        radial-gradient(at 0% 0%, rgba(30, 58, 138, 0.25) 0px, transparent 50%),
        radial-gradient(at 100% 100%, rgba(15, 118, 110, 0.2) 0px, transparent 50%),
        linear-gradient(rgba(255, 255, 255, 0.02) 1px, transparent 1px),
        linear-gradient(90deg, rgba(255, 255, 255, 0.02) 1px, transparent 1px) !important;
    background-size: 100% 100%, 100% 100%, 40px 40px, 40px 40px !important;
}
[data-testid="stAppViewContainer"],
[data-testid="stHeader"],
[data-testid="stMain"],
[data-testid="stMainBlockContainer"],
[data-testid="stAppViewBlockContainer"],
.stMain,
.stAppViewContainer,
.main,
.main .block-container {
    background: transparent !important;
    background-color: transparent !important;
    background-image: none !important;
    position: relative;
    z-index: 1;
}
[data-testid="stIconMaterial"],
[data-testid="stExpander"] summary [data-testid="stIconMaterial"],
[data-testid="stMarkdownContainer"] [data-testid="stIconMaterial"] {
    font-family: "Material Symbols Rounded" !important;
    font-weight: 400 !important;
    font-style: normal !important;
    font-size: 1.25rem !important;
    line-height: 1 !important;
    letter-spacing: normal !important;
    text-transform: none !important;
    white-space: nowrap !important;
    overflow-wrap: normal !important;
    width: 1.25rem !important;
    height: 1.25rem !important;
    overflow: hidden !important;
    color: inherit !important;
}
</style>
"""
st.markdown(FINTECH_CSS, unsafe_allow_html=True)


def _normalize_email(value: str) -> str:
    return (value or "").strip().lower()


def _valid_email(email: str) -> bool:
    return bool(EMAIL_RE.fullmatch(email))


def _existing_subscribers() -> set[str]:
    if not SUBSCRIBERS_CSV.exists():
        return set()
    with SUBSCRIBERS_CSV.open(newline="") as handle:
        return {
            _normalize_email(row.get("email", ""))
            for row in csv.DictReader(handle)
            if row.get("email")
        }


def save_subscriber(email: str) -> None:
    is_new_file = not SUBSCRIBERS_CSV.exists()
    with SUBSCRIBERS_CSV.open("a", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["email", "subscribed_at"])
        if is_new_file:
            writer.writeheader()
        writer.writerow(
            {
                "email": email,
                "subscribed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        )


def subscribe_email(raw_email: str) -> Optional[str]:
    email = _normalize_email(raw_email)
    if not _valid_email(email):
        return "Enter a valid email address, like name@company.com."
    if email not in _existing_subscribers():
        save_subscriber(email)
    return None


@st.dialog("Join the Weekly Macro Intelligence Brief")
def subscribe_dialog() -> None:
    st.caption(
        "Receive weekly early-warning flags, high-yield spread breaches, "
        "and new mass layoff filings directly in your inbox."
    )
    with st.form("dialog_subscribe"):
        email = st.text_input("Work or Personal Email", placeholder="you@firm.com")
        submitted = st.form_submit_button("Subscribe", type="primary", use_container_width=True)
    if submitted:
        error = subscribe_email(email)
        if error:
            st.error(error)
        else:
            st.success("You're subscribed to early-warning alerts.")


@st.dialog("Privacy Policy")
def privacy_policy_dialog() -> None:
    st.markdown(
        """
MacroPulse collects **only the email address you submit** when you join layoff
alerts or the weekly brief. We use that address solely to send early-warning
flags, high-yield spread notices, and new mass-layoff filings.

**We do not sell, rent, trade, or otherwise share** subscriber emails with
advertisers, data brokers, or unrelated third parties. Addresses are stored
on our alert list for the purpose of delivering those messages.

**Unsubscribe / deletion:** send a request from the subscribed address asking
to be removed. We will delete the email from the alert list and stop further
messages. We do not use the list for unrelated marketing.

Public dashboard data (WARN notices and FRED series) is sourced from official
government feeds and is not personal information about you.
        """
    )


@st.dialog("Financial Disclaimer & Terms")
def legal_disclaimer_dialog() -> None:
    st.markdown(
        """
MacroPulse is a **research and monitoring dashboard** for informational and
educational purposes only. It is **not financial, investment, tax, legal, or
trading advice**, and it is not an offer or solicitation to buy or sell any
security, instrument, or strategy.

Nothing on this site — including the Macro Stress Index, Economic Stress Test
Simulator, WARN layoff tables, or FRED-derived gauges — is a recommendation
or a prediction of future market, credit, or labor outcomes. Historical and
simulated scenarios can differ materially from actual results. Official series
are revised, can lag, and may be incomplete.

**You are solely responsible** for any decision you make. Consult a licensed
advisor before acting on capital, credit, or employment matters. To the
maximum extent permitted by law, MacroPulse and its operators disclaim all
warranties and are **not liable for any loss or damage** arising from use of
this information.
        """
    )


@st.cache_data(ttl=3600, show_spinner=False)
def _load_master_data_cached(_mtime: float) -> pd.DataFrame:
    frame = pd.read_csv(US_WARN_CSV, parse_dates=["Notice Date"])
    if "Employees Affected" in frame.columns:
        frame["Employees Affected"] = pd.to_numeric(
            frame["Employees Affected"], errors="coerce"
        )
    return frame


def load_master_data() -> pd.DataFrame:
    mtime = US_WARN_CSV.stat().st_mtime if US_WARN_CSV.exists() else 0.0
    return _load_master_data_cached(mtime)


@st.cache_data(ttl=3600, show_spinner=False)
def _load_macro_history_cached(_mtime: float) -> pd.DataFrame:
    frame = pd.read_csv(HISTORY_CSV)
    if "date" in frame.columns:
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame = frame.sort_values("date")
    for column in frame.columns:
        if column == "date":
            continue
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def load_macro_history() -> pd.DataFrame:
    mtime = HISTORY_CSV.stat().st_mtime if HISTORY_CSV.exists() else 0.0
    return _load_macro_history_cached(mtime)


def _read_warn() -> pd.DataFrame:
    return load_master_data().copy()


def _read_history() -> pd.DataFrame:
    return load_macro_history().copy()


def _file_age_seconds(path: Path) -> Optional[float]:
    if not path.exists():
        return None
    return time.time() - path.stat().st_mtime


def _file_is_fresh(path: Path, max_age: float = DATA_MAX_AGE_SECONDS) -> bool:
    age = _file_age_seconds(path)
    return age is not None and age <= max_age


def _history_schema_ok() -> bool:
    if not HISTORY_CSV.exists():
        return False
    columns = set(pd.read_csv(HISTORY_CSV, nrows=0).columns)
    return set(REQUIRED_HISTORY_COLS).issubset(columns)


def _history_is_current() -> bool:
    return _history_schema_ok() and _file_is_fresh(HISTORY_CSV)


def _needs_data_refresh() -> bool:
    if not US_WARN_CSV.exists() or not _file_is_fresh(US_WARN_CSV):
        return True
    return not _history_is_current()


def ensure_data(force: bool = False) -> None:
    if force or not US_WARN_CSV.exists() or not _file_is_fresh(US_WARN_CSV):
        collect_us_warn()
    if force or not _history_schema_ok() or not _file_is_fresh(HISTORY_CSV):
        collect_macro_signals()


def _mix_hex(start: str, end: str, t: float) -> Tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    s = [int(start[i : i + 2], 16) for i in (1, 3, 5)]
    e = [int(end[i : i + 2], 16) for i in (1, 3, 5)]
    return tuple(int(s[i] + (e[i] - s[i]) * t) for i in range(3))


def _gauge_arc_steps() -> List[dict]:
    emerald, amber, crimson = "#059669", "#D97706", "#DC2626"
    steps = []
    bands = 24
    for i in range(bands):
        t = i / (bands - 1)
        if t <= 0.5:
            red, green, blue = _mix_hex(emerald, amber, t / 0.5)
        else:
            red, green, blue = _mix_hex(amber, crimson, (t - 0.5) / 0.5)
        steps.append(
            {
                "range": [i * 100.0 / bands, (i + 1) * 100.0 / bands],
                "color": f"rgba({red}, {green}, {blue}, 0.38)",
            }
        )
    return steps


def gauge_figure(
    index_value: float, simulated: bool = False, crisis: bool = False
) -> go.Figure:
    if crisis:
        title = "CRITICAL RECESSION / SYSTEMIC CRISIS"
        title_color = "#FECACA"
        bar_color = "#DC2626"
    elif simulated:
        title = "MACRO STRESS INDEX  ·  SIMULATED"
        title_color = "#FBBF24"
        bar_color = "#D97706"
    else:
        title = "MACRO STRESS INDEX"
        title_color = "#64748B"
        bar_color = "#3B5BFF"
    fig = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=index_value,
            number={"suffix": "%", "font": {"size": 46, "color": "#FFFFFF", "family": "Inter"}},
            title={
                "text": title,
                "font": {"size": 13, "color": title_color},
            },
            gauge={
                "axis": {"range": [0, 100], "tickcolor": "rgba(255,255,255,0.18)"},
                "bar": {
                    "color": bar_color,
                    "thickness": 0.28,
                },
                "bgcolor": "rgba(0,0,0,0)",
                "borderwidth": 0,
                "steps": _gauge_arc_steps(),
            },
        )
    )
    fig.update_layout(
        height=300,
        margin=dict(l=20, r=20, t=40, b=10),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"color": "#FFFFFF", "family": "Inter, sans-serif"},
    )
    return fig


def indicators_by_id(snapshot: dict) -> dict:
    return {item["series_id"]: item for item in snapshot["indicators"]}


def format_indicator_value(item: dict) -> str:
    value = item["value"]
    unit = item["display_unit"]
    if unit == "claims":
        return f"{value:,.0f}"
    if unit in {"spread_pct", "pct", "yoy_pct"}:
        return f"{value:.1f}%"
    if unit == "pp":
        return f"{value:.2f} pp"
    if unit == "months":
        return f"{value:.1f} mo"
    if unit == "mil":
        return f"{value:.2f} mil"
    return f"{value:.2f}"


def format_revolving_credit(value: Optional[float]) -> str:
    if value is None:
        return "—"
    # Documented as billions of dollars; some FRED vintages arrive in millions.
    billions = value / 1000.0 if value > 10_000 else value
    if billions >= 1000:
        return f"${billions / 1000:.2f}T"
    return f"${billions:.0f}B"


STATUS_HEALTHY = "healthy"
STATUS_CAUTION = "caution"
STATUS_STRESS = "stress"
STATE_ABBREV = {
    "California": "CA",
    "Texas": "TX",
    "New York": "NY",
    "Florida": "FL",
    "CA": "CA",
    "TX": "TX",
    "NY": "NY",
    "FL": "FL",
}


def status_badge_html(level: str, label: str) -> str:
    return (
        f'<span class="status-badge {html.escape(level)}">'
        f"{html.escape(label)}</span>"
    )


def render_status_badge(level: str, label: str) -> None:
    st.markdown(status_badge_html(level, label), unsafe_allow_html=True)


def metric_note(text: str) -> None:
    st.markdown(f'<p class="metric-note">{html.escape(text)}</p>', unsafe_allow_html=True)


def section_header(kicker: str, title: str, lede: str) -> None:
    st.markdown(
        f"""
        <div class="section-head">
          <div class="hero-kicker">{html.escape(kicker)}</div>
          <h2>{html.escape(title)}</h2>
          <p>{html.escape(lede)}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def subsection(label: str) -> None:
    st.markdown(f'<div class="subhead">{html.escape(label)}</div>', unsafe_allow_html=True)


def _missing_status() -> Tuple[str, str]:
    return STATUS_CAUTION, "No data"


def band_status(
    value: Optional[float],
    *,
    stress_at: float,
    caution_at: float,
    higher_is_worse: bool = True,
    healthy_label: str = "Healthy",
    caution_label: str = "Caution",
    stress_label: str = "Warning",
) -> Tuple[str, str]:
    if value is None:
        return _missing_status()
    if higher_is_worse:
        if value > stress_at:
            return STATUS_STRESS, stress_label
        if value >= caution_at:
            return STATUS_CAUTION, caution_label
        return STATUS_HEALTHY, healthy_label
    if value < stress_at:
        return STATUS_STRESS, stress_label
    if value <= caution_at:
        return STATUS_CAUTION, caution_label
    return STATUS_HEALTHY, healthy_label


def indicator_status(item: dict) -> Tuple[str, str]:
    series_id = item.get("series_id")
    value = item.get("value")
    if value is None:
        return _missing_status()
    if series_id == "T10Y2Y":
        if value < 0:
            return STATUS_STRESS, "Inverted"
        if value <= 0.30:
            return STATUS_CAUTION, "Late-cycle"
        return STATUS_HEALTHY, "Normal"
    if series_id == "SAHMREALTIME":
        if value >= 0.50:
            return STATUS_STRESS, "Triggered"
        if value >= 0.30:
            return STATUS_CAUTION, "Watch"
        return STATUS_HEALTHY, "Healthy"
    if series_id == "BAMLH0A0HYM2":
        bps = value * 100.0
        if bps >= 500:
            return STATUS_STRESS, "Warning"
        if bps >= 400:
            return STATUS_CAUTION, "Caution"
        return STATUS_HEALTHY, "Healthy"
    if series_id == "UMCSENT":
        return band_status(value, stress_at=70.0, caution_at=80.0, higher_is_worse=False)
    if series_id == "MSACSR":
        return band_status(value, stress_at=8.0, caution_at=6.0)
    if series_id == "PERMIT_YOY":
        return band_status(value, stress_at=-10.0, caution_at=0.0, higher_is_worse=False)
    if series_id == "TOTALSA":
        return band_status(value, stress_at=15.0, caution_at=16.0, higher_is_worse=False)
    if series_id == "PSAVERT":
        if value < 3.5:
            return STATUS_STRESS, "Warning"
        if value <= 4.5:
            return STATUS_CAUTION, "Late-cycle"
        return STATUS_HEALTHY, "Healthy"
    if series_id == "ICSA":
        return band_status(value, stress_at=300_000, caution_at=250_000, higher_is_worse=True)
    if item.get("flagged"):
        return STATUS_STRESS, "Warning"
    return STATUS_HEALTHY, "Healthy"


def delinquency_status(value: Optional[float], flagged: bool = False) -> Tuple[str, str]:
    if value is None:
        return _missing_status()
    if flagged or value > 3.0:
        return STATUS_STRESS, "Warning"
    if value >= 2.5:
        return STATUS_CAUTION, "Caution"
    return STATUS_HEALTHY, "Healthy"


def mortgage_status(value: Optional[float]) -> Tuple[str, str]:
    return band_status(value, stress_at=8.0, caution_at=6.5)


def rent_status(value: Optional[float]) -> Tuple[str, str]:
    return band_status(value, stress_at=6.0, caution_at=4.0)


def revolving_status(value: Optional[float]) -> Tuple[str, str]:
    if value is None:
        return _missing_status()
    return STATUS_CAUTION, "Monitored"


def series_cycle_low(history: pd.DataFrame, column: str, years: int = 5) -> Optional[float]:
    if column not in history.columns or "date" not in history.columns:
        return None
    latest = pd.to_datetime(history["date"], errors="coerce").max()
    if pd.isna(latest):
        values = pd.to_numeric(history[column], errors="coerce").dropna()
    else:
        cutoff = latest - pd.DateOffset(years=years)
        mask = pd.to_datetime(history["date"], errors="coerce") >= cutoff
        values = pd.to_numeric(history.loc[mask, column], errors="coerce").dropna()
    if values.empty:
        return None
    return float(values.min())


def unemployment_status(
    value: Optional[float],
    cycle_low: Optional[float],
    flagged: bool = False,
) -> Tuple[str, str]:
    if value is None:
        return _missing_status()
    rise = None if cycle_low is None else value - cycle_low
    if flagged or (rise is not None and rise > 0.5):
        return STATUS_STRESS, "Rising"
    if rise is not None and rise > 0.2:
        return STATUS_CAUTION, "Drifting"
    return STATUS_HEALTHY, "Healthy"


def claims_status(value: Optional[float], flagged: bool, national: bool) -> Tuple[str, str]:
    if value is None:
        return _missing_status()
    if flagged:
        return STATUS_STRESS, "Warning"
    if national:
        return band_status(value, stress_at=300_000, caution_at=250_000)
    return STATUS_HEALTHY, "Healthy"


def warn_velocity_status(labor: dict) -> Tuple[str, str]:
    last = labor.get("last_30_workers") or 0
    prior = labor.get("prior_30_workers") or 0
    if last <= 0:
        return STATUS_HEALTHY, "Quiet"
    if prior <= 0:
        return STATUS_CAUTION, "New wave"
    change = (last - prior) / prior
    if change >= 0.5:
        return STATUS_STRESS, "Accelerating"
    if change > 0:
        return STATUS_CAUTION, "Rising"
    return STATUS_HEALTHY, "Cooling"


def state_abbrev(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "—"
    return STATE_ABBREV.get(text, text[:2].upper())


def major_layoff_card_html(frame: pd.DataFrame) -> str:
    if (
        frame is None
        or not isinstance(frame, pd.DataFrame)
        or frame.empty
        or "Employees Affected" not in frame.columns
    ):
        top_filings = pd.DataFrame()
    else:
        ranked = frame.copy()
        ranked["Employees Affected"] = pd.to_numeric(
            ranked["Employees Affected"], errors="coerce"
        )
        top_filings = ranked.nlargest(5, "Employees Affected")
    if top_filings.empty:
        body = '<p class="major-actions-empty">No WARN filings in this geography.</p>'
    else:
        cards = []
        for _, row in top_filings.iterrows():
            company = html.escape(str(row.get("Company") or "Unknown"))
            abbrev = html.escape(state_abbrev(row.get("State")))
            workers = int(pd.to_numeric(row.get("Employees Affected"), errors="coerce") or 0)
            date_val = pd.to_datetime(row.get("Notice Date"), errors="coerce")
            date_text = date_val.strftime("%Y-%m-%d") if pd.notna(date_val) else "—"
            cards.append(
                "<div class='major-action'>"
                f"<span class='state-chip'>[{abbrev}]</span>"
                f"<div class='company'>{company}</div>"
                f"<div class='workers-hit'>{workers:,} workers</div>"
                f"<div class='notice-date'>{date_text}</div>"
                "</div>"
            )
        body = "<div class='major-actions-grid'>" + "".join(cards) + "</div>"
    return (
        "<div class='major-actions-card'>"
        "<h3>Major Corporate Disclosures</h3>"
        f"{body}</div>"
    )


def coping_index_html() -> str:
    return """
    <div class="strain-card">
      <h3>Consumer Coping Index</h3>
      <p class="strain-copy">
        Qualitative street-stress trackers sitting alongside FRED liquidity
        readings — how households are stretching cash when official series lag.
      </p>
      <div class="coping-grid">
        <div class="coping-item">
          <span class="alert-badge warn">Elevated</span>
          <h4>401(k) Hardships</h4>
          <p>
            Vanguard/Fidelity data shows withdrawals hover near record
            <strong>~6.0% baseline</strong> for housing emergencies.
          </p>
        </div>
        <div class="coping-item">
          <span class="alert-badge active">Active</span>
          <h4>BNPL for Essentials</h4>
          <p>
            Over <strong>10%</strong> of surveyed working-age consumers report
            splitting groceries &amp; utility runs into installments.
          </p>
        </div>
        <div class="coping-item">
          <span class="alert-badge alert">Warning</span>
          <h4>Utility Arrears</h4>
          <p>
            Over <strong>$22B</strong> in past-due residential electric and gas
            debt nationwide.
          </p>
        </div>
      </div>
    </div>
    """


def render_indicator_metric(
    item: dict, label: str | None = None, note: str | None = None
) -> None:
    st.metric(
        label or item["name"],
        format_indicator_value(item),
        help=item["threshold_label"],
    )
    level, status_label = indicator_status(item)
    render_status_badge(level, status_label)
    if note:
        metric_note(note)


PLOTLY_UI = {"displayModeBar": False, "responsive": True}


def plot_chart(fig: go.Figure) -> None:
    st.plotly_chart(fig, use_container_width=True, config=PLOTLY_UI)


def render_chart_grid(history: pd.DataFrame, specs: list) -> None:
    for start in range(0, len(specs), 2):
        columns = st.columns(2)
        for column, spec in zip(columns, specs[start : start + 2]):
            series_id, title, color = spec
            with column:
                plot_chart(series_figure(history, series_id, title, color))


CHART_CANVAS = "rgba(0,0,0,0)"
CHART_SURFACE = "rgba(0,0,0,0)"
CHART_GRID = "rgba(255, 255, 255, 0.04)"
CHART_FONT = "#FFFFFF"
CHART_MUTED = "#64748B"
CHART_ACCENT = "#3B5BFF"
CHART_LINE = "#38BDF8"
CHART_FILL = "rgba(56, 189, 248, 0.08)"
CHART_EMERALD = "#38BDF8"
BAR_SLATE = "#2A364F"
BAR_HIGHLIGHT = "#38BDF8"


def apply_chart_theme(fig: go.Figure, height: int = 300, title: str | None = None) -> go.Figure:
    layout = {
        "template": "plotly_dark",
        "height": height,
        "margin": dict(l=10, r=10, t=48, b=10),
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "rgba(0,0,0,0)",
        "font": {"color": CHART_FONT, "family": "Inter, sans-serif"},
        "hovermode": "x unified",
    }
    if title:
        layout["title"] = {"text": title, "font": {"size": 14, "color": CHART_MUTED}}
    fig.update_layout(**layout)
    fig.update_xaxes(
        showgrid=False,
        zeroline=False,
        showline=False,
        ticks="",
        gridcolor=CHART_GRID,
        zerolinecolor=CHART_GRID,
        linecolor="rgba(0,0,0,0)",
        tickfont={"color": CHART_MUTED, "family": "JetBrains Mono, monospace"},
    )
    fig.update_yaxes(
        showgrid=True,
        zeroline=False,
        showline=False,
        ticks="",
        gridcolor=CHART_GRID,
        zerolinecolor=CHART_GRID,
        linecolor="rgba(0,0,0,0)",
        tickfont={"color": CHART_MUTED, "family": "JetBrains Mono, monospace"},
    )
    return fig


def _history_dates(history: pd.DataFrame) -> pd.Series:
    if history is None or history.empty or "date" not in history.columns:
        return pd.Series(dtype="datetime64[ns]")
    return pd.to_datetime(history["date"], errors="coerce")


def _history_numeric(history: pd.DataFrame, column: str) -> pd.Series:
    if history is None or history.empty or column not in history.columns:
        return pd.Series(dtype=float)
    return pd.to_numeric(history[column], errors="coerce")


def empty_chart(title: str, message: str = "Series unavailable", height: int = 360) -> go.Figure:
    fig = go.Figure()
    apply_chart_theme(fig, height=height, title=title)
    fig.update_layout(
        annotations=[
            {
                "text": message,
                "xref": "paper",
                "yref": "paper",
                "x": 0.5,
                "y": 0.5,
                "showarrow": False,
                "font": {"color": CHART_MUTED},
            }
        ]
    )
    return fig


def series_figure(history: pd.DataFrame, column: str, title: str, color: str) -> go.Figure:
    dates = _history_dates(history)
    values = _history_numeric(history, column)
    if dates.empty or values.dropna().empty:
        return empty_chart(title, "Series unavailable", height=300)
    fig = go.Figure(
        go.Scatter(
            x=dates,
            y=values,
            mode="lines",
            name=title,
            line={
                "color": CHART_LINE,
                "width": 2.2,
                "shape": "spline",
                "smoothing": 1.3,
            },
            fill="tozeroy",
            fillcolor=CHART_FILL,
            hovertemplate="%{x|%b %d, %Y}<br>%{y:.2f}<extra></extra>",
        )
    )
    apply_chart_theme(fig, height=300, title=title)
    fig.update_layout(showlegend=False)
    fig.update_xaxes(rangeslider_visible=False)
    return fig


def dual_axis_figure(
    history: pd.DataFrame,
    left_col: str,
    left_name: str,
    left_color: str,
    right_col: str,
    right_name: str,
    right_color: str,
    title: str,
) -> go.Figure:
    dates = _history_dates(history)
    left_values = _history_numeric(history, left_col)
    right_values = _history_numeric(history, right_col)
    if dates.empty or (left_values.dropna().empty and right_values.dropna().empty):
        return empty_chart(title, "Consumer series unavailable")
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=dates,
            y=left_values,
            name=left_name,
            mode="lines",
            line={
                "color": CHART_LINE,
                "width": 2.2,
                "shape": "spline",
                "smoothing": 1.3,
            },
            fill="tozeroy",
            fillcolor=CHART_FILL,
            hovertemplate="%{x|%b %d, %Y}<br>%{y:.2f}<extra>" + left_name + "</extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=dates,
            y=right_values,
            name=right_name,
            mode="lines",
            line={
                "color": CHART_ACCENT,
                "width": 2.2,
                "shape": "spline",
                "smoothing": 1.3,
            },
            yaxis="y2",
            hovertemplate="%{x|%b %d, %Y}<br>%{y:.2f}<extra>" + right_name + "</extra>",
        )
    )
    apply_chart_theme(fig, height=360, title=title)
    fig.update_layout(
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.02,
            "x": 0,
            "font": {"color": CHART_MUTED},
        },
        yaxis={"title": {"text": left_name, "font": {"color": CHART_MUTED}}},
        yaxis2={
            "title": {"text": right_name, "font": {"color": CHART_MUTED}},
            "overlaying": "y",
            "side": "right",
            "showgrid": True,
            "zeroline": False,
            "showline": False,
            "gridcolor": CHART_GRID,
            "zerolinecolor": CHART_GRID,
            "tickfont": {"color": CHART_MUTED, "family": "JetBrains Mono, monospace"},
        },
    )
    fig.update_xaxes(rangeslider_visible=False)
    return fig


def takeaway_html(
    snapshot: dict,
    labor: dict,
    simulated: bool = False,
    extra_flags: Optional[List[str]] = None,
    crisis: bool = False,
) -> str:
    on_flags: List[str] = []
    for item in snapshot["indicators"]:
        if item["flagged"] and item["series_id"] not in {"MSACSR", "PERMIT_YOY"}:
            on_flags.append(item["name"])
    if snapshot.get("housing_flag"):
        reason = "; ".join(snapshot.get("housing_reasons") or ["housing stress"])
        on_flags.append(f"Housing ({reason})")
    if labor["rising"]:
        on_flags.append("WARN layoffs accelerating")
    extra_flags = extra_flags or []
    for name in extra_flags:
        on_flags.append(name)

    unique = list(dict.fromkeys(on_flags))
    if unique:
        extra_n = len(extra_flags)
        extra_bit = (
            f" Simulation adds {extra_n} extra trigger{'s' if extra_n != 1 else ''} vs live."
            if simulated and extra_n
            else (" Simulation is on; no extra thresholds breached vs live." if simulated else "")
        )
        summary = (
            f"{len(unique)} signal{'s' if len(unique) != 1 else ''} currently ON. "
            "Stress is concentrated in the highlighted areas below — this is a monitor, not a forecast."
            + extra_bit
        )
        pills = "".join(f'<span class="flag-pill">{name}</span>' for name in unique)
    else:
        summary = (
            "No stress flags are on. Markets, housing, and claims are inside calm ranges "
            "for now. Keep watching WARN flows."
        )
        pills = '<span class="flag-pill ok">All clear</span>'
    if crisis:
        banner = (
            '<div class="sim-banner crisis">CRITICAL RECESSION / SYSTEMIC CRISIS</div>'
        )
    elif simulated:
        banner = '<div class="sim-banner">SIMULATED STRESS MODE ACTIVE</div>'
    else:
        banner = ""
    return f"""
    <div class="takeaway-card">
      <div class="hero-kicker">Executive intelligence</div>
      {banner}
      <h3>Takeaway</h3>
      <p>{summary}</p>
      {pills}
    </div>
    """


def _implied_sahm(snapshot: dict, sim_unrate: Optional[float]) -> Optional[float]:
    """Map an unemployment shock onto Sahm: 3-mo UNRATE minus prior 12-mo low."""
    sahm = next(
        (
            item
            for item in snapshot.get("indicators") or []
            if item.get("series_id") == "SAHMREALTIME"
        ),
        {},
    )
    live_sahm = sahm.get("value")
    if sim_unrate is None:
        return None if live_sahm is None else float(live_sahm)
    live_unrate = ((snapshot.get("labor_markets") or {}).get("All States") or {}).get(
        "unemployment"
    )
    if live_unrate is None or pd.isna(live_unrate):
        live_unrate = 4.2
    prior_low = float(live_unrate) - (0.0 if live_sahm is None else float(live_sahm))
    implied = float(sim_unrate) - prior_low
    if live_sahm is not None:
        return max(float(live_sahm), implied)
    return implied


def cockpit_html(
    index_value: float,
    snapshot: dict,
    labor: dict,
    simulated: bool = False,
    extra_flags: Optional[List[str]] = None,
    crisis: bool = False,
    sim_unrate: Optional[float] = None,
) -> str:
    sahm_value = _implied_sahm(snapshot, sim_unrate if simulated else None)
    sahm_level, sahm_label = indicator_status(
        {"series_id": "SAHMREALTIME", "value": sahm_value, "flagged": False}
    )
    takeaway = takeaway_html(snapshot, labor, simulated=simulated, extra_flags=extra_flags, crisis=crisis)
    start = takeaway.find("<p>")
    end = takeaway.find("</p>")
    memo = takeaway[start + 3 : end] if start != -1 and end != -1 else (
        "No stress flags are on. Markets, housing, and claims remain inside calm ranges."
    )
    width = max(2.0, min(100.0, float(index_value)))
    mode = "CRISIS" if crisis else ("SIM" if simulated else "LIVE")
    mode_class = "mode-crisis" if crisis else ("mode-sim" if simulated else "mode-live")
    sahm_text = "—" if sahm_value is None else f"{float(sahm_value):.2f}"
    card_class = "cockpit-card crisis" if crisis else "cockpit-card"
    return f"""
    <div class="{card_class}">
      <div class="hero-kicker">Macro Stress &amp; Capital Conditions</div>
      <div style="display:flex;align-items:flex-end;justify-content:space-between;gap:0.8rem;margin:0.35rem 0 0.15rem 0">
        <div class="stress-readout">{index_value:.1f}%</div>
        <div class="live-radar {mode_class}"><span class="pulse"></span><span>{mode}</span></div>
      </div>
      <div class="stress-track"><div class="stress-fill" style="width:{width:.1f}%"></div></div>
      <p class="readout-hint">0–100 blend of jobs, housing, and credit vs crisis peaks.</p>
      <div class="sahm-row">
        <span class="sahm-label">Sahm Rule</span>
        <span class="sahm-value">{html.escape(sahm_text)}</span>
        <span class="status-badge {html.escape(sahm_level)}">{html.escape(sahm_label)}</span>
      </div>
      <p class="sahm-hint">Unemployment vs its 12-month low. 0.50 has marked every U.S. recession since 1970.</p>
      <p class="cockpit-memo">{memo}</p>
    </div>
    """


def live_feed_html(frame: pd.DataFrame, limit: int = 6) -> str:
    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        rows = '<p class="major-actions-empty">No recent WARN filings.</p>'
    else:
        ranked = frame.copy()
        if "Notice Date" in ranked.columns:
            ranked["_date"] = pd.to_datetime(ranked["Notice Date"], errors="coerce")
        else:
            ranked["_date"] = pd.NaT
        if "Employees Affected" in ranked.columns:
            ranked["_workers"] = pd.to_numeric(ranked["Employees Affected"], errors="coerce").fillna(0)
        else:
            ranked["_workers"] = 0
        latest = ranked.sort_values(["_date", "_workers"], ascending=[False, False]).head(limit)
        items = []
        for _, row in latest.iterrows():
            company = html.escape(str(row.get("Company") or "Unknown"))
            abbrev = html.escape(state_abbrev(row.get("State")))
            workers = int(row["_workers"] or 0)
            date_val = row["_date"]
            date_text = date_val.strftime("%Y-%m-%d") if pd.notna(date_val) else "—"
            items.append(
                "<div class='feed-row'>"
                "<div>"
                f"<div class='feed-company'>{company}</div>"
                f"<div class='feed-meta'><span class='state-chip'>[{abbrev}]</span>"
                f"<span class='feed-date'>{date_text}</span></div>"
                "</div>"
                f"<div class='feed-workers'>{workers:,}</div>"
                "</div>"
            )
        rows = "".join(items) if items else '<p class="major-actions-empty">No recent WARN filings.</p>'
    return f"""
    <div class="feed-card">
      <div class="hero-kicker">Live Layoff Feed</div>
      {rows}
    </div>
    """


STATE_OPTIONS = ["All States", "California", "Texas", "New York", "Florida"]
TIMEFRAME_OPTIONS = ["Last 30 Days", "Last 90 Days", "Full History"]
MORTGAGE_STRESS_THRESHOLD = 7.0
SECTOR_CORPORATE = "Corporate & Professional Services"
SECTOR_ORDER = [
    "Tech & Software",
    "Logistics & Warehousing",
    "Manufacturing & Industrial",
    "Healthcare & Life Sciences",
    "Retail, Food & Hospitality",
    SECTOR_CORPORATE,
]
SECTOR_COLORS = {
    "Tech & Software": "#3B5BFF",
    "Logistics & Warehousing": "#fbbf24",
    "Manufacturing & Industrial": "#c4b5fd",
    "Healthcare & Life Sciences": "#f9a8d4",
    "Retail, Food & Hospitality": "#93c5fd",
    SECTOR_CORPORATE: "#94a3b8",
}
SECTOR_PATTERNS = [
    (
        "Tech & Software",
        re.compile(
            r"\b(software|cloud|ai|cyber|digital|tech|technology|technologies|"
            r"saas|semiconductor|internet|infosys|netflix|airtable)\b",
            re.I,
        ),
    ),
    (
        "Logistics & Warehousing",
        re.compile(
            r"\b(logistics|supply|freight|delivery|warehouse|warehousing|"
            r"transport|transportation|trucking|fulfillment)\b",
            re.I,
        ),
    ),
    (
        "Manufacturing & Industrial",
        re.compile(
            r"\b(manufacturing|manufacturer|mfg|aero|aerospace|motors|motor|"
            r"packaging|industrial|factory|foundry)\b",
            re.I,
        ),
    ),
    (
        "Healthcare & Life Sciences",
        re.compile(
            r"\b(health|healthcare|pharma|pharmaceutical|medical|hospital|"
            r"hospitals|biotech|clinic|nursing|care)\b",
            re.I,
        ),
    ),
    (
        "Retail, Food & Hospitality",
        re.compile(
            r"\b(retail|foods|food|restaurant|restaurants|hotel|hotels|"
            r"apparel|dining|grocery|supermarket|safeway)\b",
            re.I,
        ),
    ),
]


def classify_sector(company: object) -> str:
    text = str(company or "")
    for name, pattern in SECTOR_PATTERNS:
        if pattern.search(text):
            return name
    return SECTOR_CORPORATE


def normalize_city(value: object) -> Optional[str]:
    text = " ".join(str(value or "").split()).strip()
    if not text or text.lower() in {"nan", "none", "unknown", "n/a"}:
        return None
    return text


def _workers_series(frame: pd.DataFrame) -> pd.Series:
    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        return pd.Series(dtype="float64")
    if "Employees Affected" not in frame.columns:
        return pd.Series(0, index=frame.index, dtype="float64")
    return pd.to_numeric(frame["Employees Affected"], errors="coerce").fillna(0)


def sector_bar_figure(frame: pd.DataFrame, geo_label: str) -> go.Figure:
    tagged = frame.copy()
    tagged["Sector"] = tagged["Company"].map(classify_sector)
    tagged["workers"] = _workers_series(tagged)
    totals = (
        tagged.groupby("Sector", as_index=True)["workers"]
        .sum()
        .reindex(SECTOR_ORDER)
        .fillna(0)
        .sort_values(ascending=False)
    )
    bar_colors = [
        BAR_HIGHLIGHT if i == 0 else BAR_SLATE for i in range(len(totals))
    ]
    fig = go.Figure(
        go.Bar(
            x=totals.values,
            y=totals.index,
            orientation="h",
            marker={"color": bar_colors},
            hovertemplate="%{y}<br>%{x:,.0f} workers<extra></extra>",
        )
    )
    apply_chart_theme(fig, height=320, title=f"Impacted workers by sector · {geo_label}")
    fig.update_layout(showlegend=False, yaxis={"autorange": "reversed"})
    fig.update_xaxes(title="Workers")
    return fig


def metro_bar_figure(frame: pd.DataFrame, geo_label: str) -> go.Figure:
    cities = frame.copy()
    cities["City"] = cities["City"].map(normalize_city)
    cities["workers"] = _workers_series(cities)
    cities = cities.dropna(subset=["City"])
    top = (
        cities.groupby("City", as_index=False)["workers"]
        .sum()
        .sort_values("workers", ascending=False)
        .head(5)
    )
    if top.empty:
        fig = go.Figure()
        apply_chart_theme(fig, height=320, title=f"Top 5 metros · {geo_label}")
        fig.update_layout(
            annotations=[
                {
                    "text": "No city data in this geography",
                    "xref": "paper",
                    "yref": "paper",
                    "x": 0.5,
                    "y": 0.5,
                    "showarrow": False,
                    "font": {"color": CHART_MUTED},
                }
            ],
        )
        return fig
    bar_colors = [
        BAR_HIGHLIGHT if i == 0 else BAR_SLATE for i in range(len(top))
    ]
    fig = go.Figure(
        go.Bar(
            x=top["workers"],
            y=top["City"],
            orientation="h",
            marker={"color": bar_colors},
            hovertemplate="%{y}<br>%{x:,.0f} workers<extra></extra>",
        )
    )
    apply_chart_theme(
        fig, height=320, title=f"Top 5 metros / cities by layoffs · {geo_label}"
    )
    fig.update_layout(showlegend=False, yaxis={"autorange": "reversed"})
    fig.update_xaxes(title="Workers affected")
    return fig


def _optional_live(value: Optional[float], fallback: float) -> float:
    if value is None or pd.isna(value):
        return fallback
    return float(value)


CRISIS_UNRATE = 10.0
CRISIS_MORTGAGE = 6.4
CRISIS_DELINQUENCY = 6.8
CRISIS_SPREAD_BPS = 1200.0
CRISIS_INDEX_THRESHOLD = 90.0


def apply_shock_simulation(
    snapshot: dict,
    unrate: float,
    mortgage: float,
    delinquency: float,
    spread_bps: float,
) -> tuple[float, List[str], bool]:
    """Weighted 0–100 shock index from the four controllable simulator levers."""
    un_score = _clip_score(unrate, 4.0, 10.0)
    dq_score = _clip_score(delinquency, 2.5, 7.0)
    spread_score = _clip_score(spread_bps, 500.0, 1200.0)
    mortgage_score = _clip_score(mortgage, 7.0, 11.0)
    # 2008 broke via housing credit, not the 30-yr coupon; take the worse of the two.
    housing_score = max(mortgage_score, dq_score)
    index = (
        0.30 * un_score
        + 0.25 * dq_score
        + 0.25 * spread_score
        + 0.20 * housing_score
    )
    index = round(min(100.0, index), 1)
    crisis = index >= CRISIS_INDEX_THRESHOLD

    live_markets = (snapshot.get("labor_markets") or {}).get("All States") or {}
    live_consumer = snapshot.get("consumer") or {}
    live_hy = next(
        (
            item
            for item in snapshot.get("indicators") or []
            if item.get("series_id") == "BAMLH0A0HYM2"
        ),
        None,
    )
    live_unrate = _optional_live(live_markets.get("unemployment"), 4.2)
    live_mortgage = _optional_live(live_consumer.get("mortgage_rate"), 6.5)
    live_spread_bps = _optional_live(
        None if live_hy is None else live_hy.get("value"), 3.5
    ) * 100.0
    live_cc = live_consumer.get("cc_delinquency")
    live_auto = live_consumer.get("auto_delinquency")
    live_dq_flag = bool(
        (live_cc is not None and float(live_cc) > CREDIT_CARD_DELINQUENCY_FLAG_THRESHOLD)
        or (
            live_auto is not None
            and float(live_auto) > AUTO_DELINQUENCY_FLAG_THRESHOLD
        )
    )

    extra_flags: List[str] = []
    if unrate >= UNEMPLOYMENT_FLAG_THRESHOLD and live_unrate < UNEMPLOYMENT_FLAG_THRESHOLD:
        extra_flags.append("Simulated unemployment")
    if mortgage > MORTGAGE_STRESS_THRESHOLD and live_mortgage <= MORTGAGE_STRESS_THRESHOLD:
        extra_flags.append("Simulated 30-yr mortgage")
    if delinquency > CREDIT_CARD_DELINQUENCY_FLAG_THRESHOLD and not live_dq_flag:
        extra_flags.append("Simulated auto/card delinquency")
    if spread_bps > 500 and live_spread_bps <= 500:
        extra_flags.append("Simulated high-yield spreads")
    if crisis:
        extra_flags.append("CRITICAL RECESSION / SYSTEMIC CRISIS")
    return index, extra_flags, crisis


def _reset_simulator_to_live(
    unrate: float, mortgage: float, delinquency: float, spread_bps: float
) -> None:
    st.session_state.sim_enabled = False
    st.session_state.sim_unrate = float(round(unrate, 1))
    st.session_state.sim_mortgage = float(round(mortgage, 1))
    st.session_state.sim_dq = float(round(delinquency, 2))
    st.session_state.sim_spread = float(round(spread_bps, 0))


def _apply_2008_shock() -> None:
    st.session_state.sim_enabled = True
    st.session_state.sim_unrate = CRISIS_UNRATE
    st.session_state.sim_mortgage = CRISIS_MORTGAGE
    st.session_state.sim_dq = CRISIS_DELINQUENCY
    st.session_state.sim_spread = CRISIS_SPREAD_BPS


def thirty_day_delta_label(labor: dict) -> str:
    last = labor["last_30_workers"]
    prior = labor["prior_30_workers"]
    if prior <= 0:
        return "no prior-30 comparison" if last == 0 else "new vs prior 30 days"
    change = (last - prior) / prior * 100
    return f"{change:+.0f}% vs prior 30 days"


def labor_feed_for_state(snapshot: dict, state_choice: str) -> dict:
    markets = snapshot.get("labor_markets") or {}
    return markets.get(state_choice) or markets.get("All States") or {}


def warn_for_state(frame: pd.DataFrame, state_choice: str) -> pd.DataFrame:
    if frame is None or not isinstance(frame, pd.DataFrame):
        return load_master_data().copy()
    if state_choice == "All States" or "State" not in frame.columns:
        return frame.copy()
    states = frame["State"].fillna("").astype(str).str.strip()
    return frame.loc[states == state_choice].copy()


def _coerce_warn_frame(frame: object) -> pd.DataFrame:
    if frame is None or not isinstance(frame, pd.DataFrame):
        frame = load_master_data()
    if frame is None or not isinstance(frame, pd.DataFrame):
        return pd.DataFrame()
    out = frame.copy()
    if "Notice Date" in out.columns:
        out["Notice Date"] = pd.to_datetime(out["Notice Date"], errors="coerce")
    if "Employees Affected" in out.columns:
        out["Employees Affected"] = pd.to_numeric(
            out["Employees Affected"], errors="coerce"
        )
    return out


@st.fragment
def render_labor_panel() -> None:
    try:
        _render_labor_panel()
    except Exception as exc:
        traceback.print_exc()
        st.error(f"Error loading Labor feed: {exc}")


def _render_labor_panel() -> None:
    warn = _coerce_warn_frame(load_master_data())
    history = load_macro_history().copy()
    snapshot = latest_snapshot(history)

    if "labor_geo" not in st.session_state:
        prior = st.session_state.get("state_filter", "All States")
        st.session_state.labor_geo = prior if prior in STATE_OPTIONS else "All States"
    if "labor_timeframe" not in st.session_state:
        prior_tf = st.session_state.get("warn_timeframe", "Full History")
        st.session_state.labor_timeframe = (
            prior_tf if prior_tf in TIMEFRAME_OPTIONS else "Full History"
        )

    section_header(
        "Labor",
        "WARN layoff radar",
        "Mass-layoff filings plus official unemployment and claims for the selected region.",
    )
    subsection("Region")
    state_choice = st.pills(
        "Geography",
        STATE_OPTIONS,
        key="labor_geo",
        label_visibility="collapsed",
    )
    if state_choice not in STATE_OPTIONS:
        state_choice = "All States"

    filtered_df = warn_for_state(warn, state_choice)
    if filtered_df is None or not isinstance(filtered_df, pd.DataFrame):
        filtered_df = load_master_data()
    filtered_df = _coerce_warn_frame(filtered_df)

    labor = warn_status(filtered_df)
    feed = labor_feed_for_state(snapshot, state_choice)
    geo_ids = GEO_LABOR_SERIES.get(state_choice, GEO_LABOR_SERIES["All States"])
    unrate_id = feed.get("unemployment_id") or geo_ids["unemployment"]
    claims_id = feed.get("claims_id") or geo_ids["claims"]
    geo_label = "National" if state_choice == "All States" else state_choice

    subsection("Snapshot")
    st.markdown(major_layoff_card_html(filtered_df), unsafe_allow_html=True)
    w1, w2, w3 = st.columns(3)
    with w1:
        st.metric(
            "Total Workers Tracked",
            f"{labor['workers']:,}",
            help="Cumulative WARN employees in the selected geography.",
        )
        render_status_badge(STATUS_HEALTHY, "Tracked")
        metric_note("Workers named on WARN notices in this geography.")
    with w2:
        st.metric(
            "30-Day Layoff Velocity",
            f"{labor['last_30_workers']:,}",
            delta=thirty_day_delta_label(labor),
            delta_color="inverse",
            help="Workers on notices dated in the latest 30 days vs the prior 30 days, for the selected geography.",
        )
        render_status_badge(*warn_velocity_status(labor))
        metric_note("Workers on notices in the last 30 days vs the prior 30.")
    with w3:
        st.metric(
            "Total Notices Filed",
            f"{labor['notices']:,}",
            help="WARN filings in the selected geography.",
        )
        render_status_badge(STATUS_HEALTHY, "Tracked")
        metric_note("Count of mass-layoff / plant-closure filings.")

    subsection("Official series")
    claims_value = feed.get("claims")
    unrate_value = feed.get("unemployment")
    cycle_low = series_cycle_low(history, unrate_id)
    f1, f2 = st.columns(2)
    with f1:
        st.metric(
            f"{geo_label} unemployment rate ({unrate_id})",
            f"{unrate_value:.1f}%" if unrate_value is not None else "—",
            help=f"FRED {unrate_id}. Flagged at {UNEMPLOYMENT_FLAG_THRESHOLD:.1f}% or higher.",
        )
        render_status_badge(
            *unemployment_status(
                unrate_value,
                cycle_low,
                bool(feed.get("unemployment_flagged")),
            )
        )
        metric_note("Share of the labor force without a job. Rising vs the cycle low is the stress flag.")
    with f2:
        st.metric(
            f"{geo_label} initial claims (4-wk avg, {claims_id})",
            f"{claims_value:,.0f}" if claims_value is not None else "—",
            help=f"FRED {claims_id}.",
        )
        render_status_badge(
            *claims_status(
                claims_value,
                bool(feed.get("claims_flagged")),
                national=state_choice == "All States",
            )
        )
        metric_note("New weekly jobless filings, 4-week average. A climb means layoffs are hitting now.")
    render_chart_grid(
        history,
        [
            (unrate_id, f"{geo_label} unemployment rate ({unrate_id})", CHART_EMERALD),
            (claims_id, f"{geo_label} initial claims ({claims_id})", CHART_ACCENT),
        ],
    )

    sector_col, metro_col = st.columns(2)
    with sector_col:
        plot_chart(sector_bar_figure(filtered_df, geo_label))
    with metro_col:
        plot_chart(metro_bar_figure(filtered_df, geo_label))

    subsection("Filings")
    st.caption("Timeframe")
    timeframe = st.pills(
        "Timeframe",
        TIMEFRAME_OPTIONS,
        key="labor_timeframe",
        label_visibility="collapsed",
    )
    if timeframe not in TIMEFRAME_OPTIONS:
        timeframe = "Full History"

    query = st.text_input(
        "Search companies, cities, or counties",
        placeholder="Search the filtered table",
        key="labor_search",
    )
    table = filtered_df.copy() if isinstance(filtered_df, pd.DataFrame) else load_master_data()
    table = _coerce_warn_frame(table)
    if "Notice Date" in table.columns:
        notice_dates = pd.to_datetime(table["Notice Date"], errors="coerce")
        latest_notice = notice_dates.max()
        if timeframe == "Last 30 Days" and pd.notna(latest_notice):
            table = table.loc[notice_dates >= latest_notice - pd.Timedelta(days=30)].copy()
        elif timeframe == "Last 90 Days" and pd.notna(latest_notice):
            table = table.loc[notice_dates >= latest_notice - pd.Timedelta(days=90)].copy()
    if (query or "").strip():
        needle = query.strip().lower()
        mask = False
        for column in ("Company", "City", "County", "State"):
            if column in table.columns:
                mask = mask | table[column].fillna("").astype(str).str.lower().str.contains(
                    needle, regex=False
                )
        if mask is not False:
            table = table.loc[mask].copy()

    total_pages = max(1, (len(table) + PAGE_SIZE - 1) // PAGE_SIZE)
    page_key = f"labor_page_{state_choice}_{timeframe}"
    current_page = st.session_state.get(page_key, 1)
    try:
        current_page = int(current_page)
    except (TypeError, ValueError):
        current_page = 1
    st.session_state[page_key] = min(max(1, current_page), total_pages)
    page = st.number_input(
        "Page",
        min_value=1,
        max_value=int(total_pages),
        step=1,
        key=page_key,
    )
    start = (int(page) - 1) * PAGE_SIZE
    page_rows = table.iloc[start : start + PAGE_SIZE].copy()
    if "Notice Date" in page_rows.columns:
        page_rows["Notice Date"] = pd.to_datetime(
            page_rows["Notice Date"], errors="coerce"
        ).dt.strftime("%Y-%m-%d")
    st.caption(
        f"{geo_label} · {timeframe} · {len(table):,} notices · showing "
        f"{start + 1 if len(table) else 0}–{min(start + PAGE_SIZE, len(table))}"
    )
    st.dataframe(page_rows, use_container_width=True, hide_index=True, height=420)
    st.download_button(
        "Download filtered CSV",
        data=table.to_csv(index=False).encode("utf-8"),
        file_name="us_warn_filtered.csv",
        mime="text/csv",
        use_container_width=False,
        key=f"labor_download_{state_choice}_{timeframe}",
    )


_last_refresh_try = st.session_state.get("_data_refresh_tried_at")
_refresh_due = _needs_data_refresh()
_retry_ok = _last_refresh_try is None or (
    time.time() - float(_last_refresh_try) >= REFRESH_RETRY_SECONDS
)
if _refresh_due and _retry_ok:
    st.session_state._data_refresh_tried_at = time.time()
    with st.spinner("Refreshing WARN filings and FRED feeds..."):
        try:
            ensure_data(force=False)
            st.cache_data.clear()
        except Exception:
            traceback.print_exc()

warn = _read_warn()
history = _read_history()
snapshot = latest_snapshot(history)
series = indicators_by_id(snapshot)
national_labor = warn_status(warn)

nav_left, nav_right = st.columns([3.4, 1.1])
with nav_left:
    st.markdown(
        f"""
        <div class="top-nav" style="border-bottom:0;margin:0;padding:0.2rem 0">
          <div class="brand-lockup">
            <span class="brand-name">MacroPulse</span>
            <span class="live-radar"><span class="pulse"></span> Live Radar</span>
            <span class="nav-meta" style="color:#64748B;font-size:0.72rem">{national_labor["notices"]:,} filings</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
with nav_right:
    if st.button("Get Alerts", type="primary", use_container_width=False, key="header_alerts"):
        subscribe_dialog()

consumer_live = snapshot.get("consumer") or {}
national_live = (snapshot.get("labor_markets") or {}).get("All States") or {}
live_unrate = _optional_live(national_live.get("unemployment"), 4.2)
live_mortgage = _optional_live(consumer_live.get("mortgage_rate"), 6.5)
live_dq = _optional_live(consumer_live.get("cc_delinquency"), 2.85)
live_spread_bps = _optional_live(series["BAMLH0A0HYM2"]["value"], 3.5) * 100.0

if "sim_enabled" not in st.session_state:
    st.session_state.sim_enabled = False
if "sim_unrate" not in st.session_state:
    st.session_state.sim_unrate = float(round(live_unrate, 1))
if "sim_mortgage" not in st.session_state:
    st.session_state.sim_mortgage = float(round(live_mortgage, 1))
if "sim_dq" not in st.session_state:
    st.session_state.sim_dq = float(round(live_dq, 2))
if "sim_spread" not in st.session_state:
    st.session_state.sim_spread = float(round(live_spread_bps, 0))

with st.sidebar:
    st.markdown(
        """
        <div class="sim-side-head">
          <div class="hero-kicker">Scenario Lab</div>
          <h3>Stress Test Simulator</h3>
          <p>
            Shock unemployment, mortgages, delinquencies, and high-yield
            spreads. Toggle off to restore live FRED readings.
          </p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    simulated = st.toggle(
        "Enable Shock Simulation",
        key="sim_enabled",
    )
    preset_left, preset_right = st.columns(2)
    with preset_left:
        st.button(
            "Reset to Live Data",
            use_container_width=True,
            key="sim_reset_live",
            on_click=_reset_simulator_to_live,
            args=(live_unrate, live_mortgage, live_dq, live_spread_bps),
        )
    with preset_right:
        st.button(
            "2008 Financial Crisis",
            use_container_width=True,
            key="sim_crisis_2008",
            on_click=_apply_2008_shock,
        )
    st.caption("Calibrated to historical Fed & BLS crisis peaks.")
    sim_unrate = st.slider(
        "Simulated Unemployment Rate",
        min_value=3.5,
        max_value=10.0,
        step=0.1,
        format="%.1f%%",
        key="sim_unrate",
        disabled=not simulated,
        help="Share of the labor force without a job. 10% was the 2009 peak.",
    )
    sim_mortgage = st.slider(
        "Simulated 30-Yr Mortgage Rate",
        min_value=5.0,
        max_value=12.0,
        step=0.1,
        format="%.1f%%",
        key="sim_mortgage",
        disabled=not simulated,
        help="Cost of a 30-year home loan. Higher rates freeze housing demand.",
    )
    sim_dq = st.slider(
        "Simulated Auto / Card Delinquencies",
        min_value=2.0,
        max_value=10.0,
        step=0.05,
        format="%.2f%%",
        key="sim_dq",
        disabled=not simulated,
        help="Share of auto/card loans 30+ days late. Rising missed payments = household strain.",
    )
    sim_spread = st.slider(
        "Simulated High-Yield Credit Spread (BAMLH0A0HYM2)",
        min_value=300.0,
        max_value=1200.0,
        step=10.0,
        format="%.0f bps",
        key="sim_spread",
        disabled=not simulated,
        help="Extra yield junk bonds pay over Treasuries. Wider = default fear.",
    )

extra_flags: List[str] = []
crisis = False
index_value = float(snapshot["macro_stress_index"])
if simulated:
    index_value, extra_flags, crisis = apply_shock_simulation(
        dict(snapshot), sim_unrate, sim_mortgage, sim_dq, sim_spread
    )

cockpit, feed = st.columns([1.8, 1.2], gap="large")
with cockpit:
    st.markdown(
        cockpit_html(
            index_value,
            snapshot,
            national_labor,
            simulated=simulated,
            extra_flags=extra_flags,
            crisis=crisis,
            sim_unrate=sim_unrate if simulated else None,
        ),
        unsafe_allow_html=True,
    )
with feed:
    st.markdown(live_feed_html(warn), unsafe_allow_html=True)

if "labor_geo" not in st.session_state:
    st.session_state.labor_geo = "All States"
if "labor_timeframe" not in st.session_state:
    st.session_state.labor_timeframe = "Full History"

NAV_SECTIONS = ["Markets", "Housing", "Consumer", "Labor"]
_LEGACY_TABS = {
    "Macro & Financial Conditions": "Markets",
    "Housing & Automotive Strain": "Housing",
    "Consumer & Cost of Living": "Consumer",
    "Labor & WARN Layoff Radar": "Labor",
}
if st.session_state.get("main_tab") in _LEGACY_TABS:
    st.session_state.main_tab = _LEGACY_TABS[st.session_state.main_tab]
if st.session_state.get("main_tab") not in NAV_SECTIONS:
    st.session_state.main_tab = NAV_SECTIONS[0]
st.markdown('<div class="section-rail"></div>', unsafe_allow_html=True)
selected_tab = st.pills(
    "Section",
    NAV_SECTIONS,
    key="main_tab",
    label_visibility="collapsed",
) or st.session_state.main_tab

if selected_tab == "Markets":
    section_header(
        "Markets",
        "Macro & financial conditions",
        "Rates, recession rules, and credit stress that usually move first.",
    )
    subsection("Now")
    m1, m2, m3, m4 = st.columns(4)
    with m1:
        render_indicator_metric(
            series["T10Y2Y"],
            "Yield curve",
            "10y minus 2y Treasuries. Negative inversion often precedes recessions.",
        )
    with m2:
        render_indicator_metric(
            series["SAHMREALTIME"],
            "Sahm Rule",
            "Unemployment vs its 12-month low. 0.50 has marked every U.S. recession since 1970.",
        )
    with m3:
        render_indicator_metric(
            series["BAMLH0A0HYM2"],
            "High-yield spreads",
            "Junk-bond extra yield. Wider spreads mean credit is getting scarce.",
        )
    with m4:
        render_indicator_metric(
            series["UMCSENT"],
            "Consumer sentiment",
            "Household confidence. Sub-70 is recession-era caution.",
        )
    subsection("Trend")
    render_chart_grid(
        history,
        [
            ("T10Y2Y", "Yield curve", CHART_ACCENT),
            ("SAHMREALTIME", "Sahm Rule", CHART_EMERALD),
            ("BAMLH0A0HYM2", "High-yield spreads", CHART_EMERALD),
            ("UMCSENT", "Consumer sentiment", CHART_EMERALD),
        ],
    )

elif selected_tab == "Housing":
    section_header(
        "Housing",
        "Housing & automotive strain",
        "Builders, buyers, and big-ticket demand — the real-economy choke points.",
    )
    subsection("Now")
    h1, h2, h3, h4 = st.columns(4)
    with h1:
        render_indicator_metric(
            series["MSACSR"],
            "New home supply",
            "Months of unsold new homes. High = a glut and builder stress.",
        )
    with h2:
        render_indicator_metric(
            series["PERMIT_YOY"],
            "Building permits YoY",
            "YoY change in new housing permits. Negative = builders pulling back.",
        )
    with h3:
        render_indicator_metric(
            series["TOTALSA"],
            "Vehicle sales",
            "Annualized light-vehicle sales. Weak = households delaying big purchases.",
        )
    with h4:
        render_indicator_metric(
            series["PSAVERT"],
            "Personal saving rate",
            "Share of income saved. Below ~3.5% means little cash buffer.",
        )
    subsection("Trend")
    render_chart_grid(
        history,
        [
            ("MSACSR", "New home supply", CHART_EMERALD),
            ("PERMIT_YOY", "Building permits YoY", CHART_ACCENT),
            ("TOTALSA", "Vehicle sales", CHART_EMERALD),
            ("PSAVERT", "Personal saving rate", CHART_EMERALD),
        ],
    )

elif selected_tab == "Consumer":
    section_header(
        "Consumer",
        "Cost of living & household strain",
        "Shelter costs, missed payments, and whether households still have a cash buffer.",
    )
    consumer = snapshot.get("consumer") or {}
    mortgage = consumer.get("mortgage_rate")
    rent_yoy = consumer.get("rent_yoy")
    auto_dq = consumer.get("auto_delinquency")
    auto_flag = bool(consumer.get("auto_delinquency_flagged"))
    subsection("Now")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric(
            "Current 30-Yr Mortgage Rate",
            f"{mortgage:.2f}%" if mortgage is not None else "—",
            help="FRED MORTGAGE30US",
        )
        render_status_badge(*mortgage_status(mortgage))
        metric_note("Cost of a 30-year home loan. Higher rates freeze housing demand.")
    with c2:
        st.metric(
            "Year-over-Year Rent Inflation",
            f"{rent_yoy:.1f}%" if rent_yoy is not None else "—",
            help="YoY change in FRED CUSR0000SEHA (CPI rent of primary residence)",
        )
        render_status_badge(*rent_status(rent_yoy))
        metric_note("Shelter inflation vs a year ago. Sticky rents squeeze wages.")
    with c3:
        st.metric(
            "Auto Delinquency Rate",
            f"{auto_dq:.2f}%" if auto_dq is not None else "—",
            help=f"FRED DRALACBS. Flagged above {AUTO_DELINQUENCY_FLAG_THRESHOLD:.1f}%.",
        )
        render_status_badge(*delinquency_status(auto_dq, auto_flag))
        metric_note("Share of car loans 30+ days late. Rising missed payments = strain.")
    subsection("Trend")
    left, right = st.columns(2)
    try:
        with left:
            plot_chart(
                dual_axis_figure(
                    history,
                    "DRALACBS",
                    "Auto delinquencies",
                    CHART_ACCENT,
                    "PSAVERT",
                    "Personal saving rate",
                    CHART_EMERALD,
                    "Auto delinquencies vs personal saving rate",
                )
            )
        with right:
            plot_chart(
                dual_axis_figure(
                    history,
                    "MORTGAGE30US",
                    "30-yr mortgage rate",
                    CHART_ACCENT,
                    "CSUSHPINSA",
                    "Case-Shiller home price index",
                    CHART_EMERALD,
                    "Mortgage rates vs Case-Shiller home prices",
                )
            )
    except Exception:
        traceback.print_exc()
        st.info(
            "Consumer charts are temporarily unavailable. Live FRED metrics above remain in view."
        )

    saving_rate = consumer.get("saving_rate")
    saving_flag = bool(consumer.get("saving_rate_flagged"))
    cc_dq = consumer.get("cc_delinquency")
    cc_flag = bool(consumer.get("cc_delinquency_flagged"))
    revolving = consumer.get("revolving_credit")
    subsection("Liquidity")
    l1, l2, l3 = st.columns(3)
    with l1:
        st.metric(
            "Personal Saving Rate",
            f"{saving_rate:.1f}%" if saving_rate is not None else "—",
            help="FRED PSAVERT. Flagged below 3.5%.",
        )
        render_status_badge(
            *indicator_status(
                {
                    "series_id": "PSAVERT",
                    "value": saving_rate,
                    "flagged": saving_flag,
                }
            )
        )
        metric_note("Share of income saved. Below ~3.5% means little cash buffer.")
    with l2:
        st.metric(
            "Credit Card Delinquency Rate",
            f"{cc_dq:.2f}%" if cc_dq is not None else "—",
            help=(
                "FRED DRCCLACBS (commercial bank credit-card delinquency). "
                f"Flagged above {CREDIT_CARD_DELINQUENCY_FLAG_THRESHOLD:.1f}%."
            ),
        )
        render_status_badge(*delinquency_status(cc_dq, cc_flag))
        metric_note("Share of card balances 30+ days late. Commercial-bank average.")
    with l3:
        st.metric(
            "Total Revolving Debt",
            format_revolving_credit(revolving),
            help="FRED REVOLSL, revolving consumer credit, seasonally adjusted.",
        )
        render_status_badge(*revolving_status(revolving))
        metric_note("Outstanding revolving credit. Rising balances with weak saving is a strain flag.")
    st.markdown(coping_index_html(), unsafe_allow_html=True)

elif selected_tab == "Labor":
    try:
        render_labor_panel()
    except Exception as exc:
        traceback.print_exc()
        st.error(f"Error loading Labor feed: {exc}")

with st.expander("Data Sources & Methodology"):
    st.markdown(
        """
**Federal Reserve Economic Data (FRED):** St. Louis Fed API feeds for bond yields,
debt series, and headline labor.

**State WARN Act Disclosures:** Official state registry feeds from California EDD,
Texas Workforce Commission (TWC), New York State Department of Labor, and
FloridaCommerce (REACT).

**Methodology note:** WARN Act data reflects formal 60-day advance mass layoff and
plant closure notices under state and federal thresholds (typically 50-100+ workers).
WARN data serves as a leading indicator of planned corporate workforce cuts, while
Initial Jobless Claims track real-time weekly benefit claims.
        """
    )

subsection("Alerts")
inline_email, inline_button = st.columns([3.4, 1.1], vertical_alignment="bottom")
with inline_email:
    footer_email = st.text_input(
        "Work or Personal Email",
        placeholder="you@firm.com",
        key="inline_email",
        label_visibility="collapsed",
    )
with inline_button:
    inline_clicked = st.button(
        "Subscribe",
        type="primary",
        use_container_width=True,
        key="inline_subscribe",
    )
if inline_clicked:
    error = subscribe_email(footer_email)
    if error:
        st.error(error)
    else:
        st.success("You're subscribed to early-warning alerts.")

st.markdown('<div class="legal-footer"></div>', unsafe_allow_html=True)
legal_copy, legal_privacy, legal_terms = st.columns([3.4, 1.15, 1.45])
with legal_copy:
    st.markdown(
        '<p class="legal-copy">MacroPulse © 2026. Real-Time Macro &amp; Labor '
        "Layoff Intelligence.</p>",
        unsafe_allow_html=True,
    )
with legal_privacy:
    if st.button("Privacy Policy", key="legal_privacy"):
        privacy_policy_dialog()
with legal_terms:
    if st.button("Legal Disclaimer", key="legal_disclaimer"):
        legal_disclaimer_dialog()
