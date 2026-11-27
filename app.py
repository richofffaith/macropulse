"""Fintech-style dashboard for multi-state WARN notices and FRED stress signals."""

from __future__ import annotations

import base64
import csv
from contextlib import contextmanager
import html
import json
import math
import os
import random
import re
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from billing import (
    authenticate,
    account_profile,
    billing_portal_url,
    change_password,
    create_account,
    create_checkout_session,
    fulfill_checkout,
    payments_configured,
    payments_test_mode,
    plan_by_id,
    restore_member,
)
from conflict import war_snapshot
from headlines import fetch_all_headlines
from company_reports import (
    company_reports_are_fresh,
    load_company_reports,
    refresh_company_reports,
)
from collect_macro_signals import (
    AUTO_DELINQUENCY_FLAG_THRESHOLD,
    AUTO_DQ_CAUTION_THRESHOLD,
    AUTO_DQ_STRESS_THRESHOLD,
    AUTO_REPO_PIPELINE_THRESHOLD,
    CREDIT_CARD_DELINQUENCY_FLAG_THRESHOLD,
    GEO_LABOR_SERIES,
    HISTORY_CSV,
    INDICATORS,
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
    "TERMCBCCALLNS",
    "DRCCLOBS",
    "MVLOAS",
    "RIFLPBCNC48",
    "DTCTSL",
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
    .mp-intro .mp-seg,
    .mp-intro .brand-name,
    .mp-intro .live-radar,
    .mp-intro .nav-meta,
    div.st-key-main_tab button,
    .series-tape-track {
        animation: none !important;
        transform: none !important;
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
    flex-wrap: wrap;
    gap: 0.7rem;
}
.mp-mark {
    width: 34px;
    height: 34px;
    flex-shrink: 0;
    display: block;
    overflow: visible;
}
.mp-mark .mp-seg {
    transform-origin: 16px 16px;
}
.mp-intro .mp-seg {
    --tx: 0px;
    --ty: 10px;
    animation: mpSegIn 0.7s ease-out both;
}
.mp-intro .brand-name {
    animation: mpRise 0.55s ease-out 0.72s both;
}
.mp-intro .live-radar,
.mp-intro .nav-meta {
    animation: mpRise 0.5s ease-out 0.88s both;
}
@keyframes mpSegIn {
    0% { opacity: 0; transform: translate(var(--tx), var(--ty)) scale(0.6); }
    100% { opacity: 1; transform: translate(0, 0) scale(1); }
}
@keyframes mpRise {
    0% { opacity: 0; transform: translateY(12px); }
    100% { opacity: 1; transform: translateY(0); }
}
@keyframes mpTabIn {
    0% { opacity: 0; transform: translateY(10px) scale(0.96); }
    100% { opacity: 1; transform: translateY(0) scale(1); }
}
.brand-lockup:hover .mp-mark {
    transform: translateY(-2px) scale(1.07);
    transition: transform 0.28s cubic-bezier(0.34, 1.8, 0.64, 1);
}
.mp-mark {
    transition: transform 0.28s cubic-bezier(0.34, 1.8, 0.64, 1);
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
    background: #C8F542;
    box-shadow: 0 0 10px rgba(200, 245, 66, 0.7);
}
.live-radar,
.live-radar .pulse,
.cockpit-card {
    transition: color 250ms ease-in-out, background 250ms ease-in-out,
                border-color 250ms ease-in-out, box-shadow 250ms ease-in-out;
}
.live-radar.mode-sim {
    color: #7DD3FC;
    font-weight: 700;
    letter-spacing: 0.06em;
    text-transform: uppercase;
}
.live-radar.mode-sim .pulse {
    background: #38BDF8;
    box-shadow: 0 0 10px rgba(56, 189, 248, 0.85);
}
.cockpit-card.sim {
    border-color: rgba(200, 245, 66, 0.55);
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4), 0 0 0 1px rgba(200, 245, 66, 0.28),
                0 0 22px rgba(200, 245, 66, 0.16);
}
.cockpit-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
}
.cockpit-head .hero-kicker { margin-bottom: 0; }
.sim-exit {
    appearance: none;
    border: 0;
    background: #C8F542;
    color: #0B0F08;
    font-family: Inter, -apple-system, sans-serif;
    font-size: 0.75rem;
    font-weight: 700;
    letter-spacing: -0.01em;
    border-radius: 999px;
    padding: 0.45rem 0.85rem;
    line-height: 1;
    cursor: pointer;
    transition: background 150ms ease-out, transform 150ms ease-out;
}
.sim-exit:hover { background: #D6FF63; transform: translateY(-2px); }
.sim-exit:active { transform: scale(0.97); transition: transform 150ms ease-out; }
.live-radar.zone-healthy { color: #C8F542; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; }
.live-radar.zone-healthy .pulse { background: #C8F542; box-shadow: 0 0 10px rgba(200, 245, 66, 0.7); }
.live-radar.zone-strained { color: #D97706; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; }
.live-radar.zone-strained .pulse { background: #D97706; box-shadow: 0 0 10px rgba(217, 119, 6, 0.55); }
.live-radar.zone-high { color: #F87171; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; }
.live-radar.zone-high .pulse { background: #F87171; box-shadow: 0 0 10px rgba(248, 113, 113, 0.5); }
.live-radar.zone-melt { color: #FCA5A5; font-weight: 700; letter-spacing: 0.05em; text-transform: uppercase; }
.live-radar.zone-melt .pulse { background: #DC2626; box-shadow: 0 0 12px rgba(220, 38, 38, 0.65); }
.cockpit-card.is-shake { animation: mpShake 240ms ease-in-out; }
@keyframes mpShake {
    0%, 100% { transform: translateX(0); }
    25% { transform: translateX(-3px); }
    50% { transform: translateX(3px); }
    75% { transform: translateX(-3px); }
}
.cockpit-lights.is-pulse .flag-pill { animation: mpPillPulse 240ms ease-in-out; }
@keyframes mpPillPulse {
    0%, 100% { transform: scale(1); }
    50% { transform: scale(1.06); }
}
.impact-line {
    margin: 0.15rem 0 0.8rem 0;
    color: #CBD5E1;
    font-size: 0.92rem;
    line-height: 1.4;
    transition: none;
}
.cockpit-filing {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    justify-content: space-between;
    gap: 0.35rem 0.85rem;
    margin: 0 0 0.85rem 0;
    padding: 0.72rem 0.85rem;
    border-radius: 12px;
    background: rgba(200, 245, 66, 0.08);
    border: 1px solid rgba(200, 245, 66, 0.35);
}
.cockpit-filing span {
    color: #94A3B8;
    font-size: 0.72rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
}
.cockpit-filing strong {
    color: #F8FAFC;
    font-size: 1.08rem;
    font-weight: 650;
    letter-spacing: -0.02em;
}
.cockpit-filing b {
    color: #C8F542;
    font-variant-numeric: tabular-nums;
}
.live-radar.mode-crisis {
    color: #FCA5A5;
    font-weight: 600;
}
.live-radar.mode-crisis .pulse {
    background: #F87171;
    box-shadow: 0 0 10px rgba(248, 113, 113, 0.35);
}
.cockpit-card.crisis {
    border-color: rgba(220, 38, 38, 0.38);
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4), 0 0 28px rgba(220, 38, 38, 0.12);
}
.cockpit-card, .feed-card, .major-action, .oem-card, .coping-item,
.newsletter-card, .mp-paywall, .takeaway-card, .strain-card, .mp-hook {
    min-width: 0;
    max-width: 100%;
    overflow-wrap: anywhere;
}
.major-action .company, .feed-company, .oem-card h4, .oem-card p, .coping-item p,
.brand-lockup, .top-nav {
    min-width: 0;
    overflow-wrap: anywhere;
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
.stress-readout, .sahm-value, .feed-workers, .feed-date, .nav-meta,
.mp-metric-value, .mp-metric-delta, .lp-device-score, .lp-device-rows b, .lp-pill b,
.mp-eco-val strong, .mp-scr-metric strong {
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
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
    background: linear-gradient(90deg, #C8F542 0%, #D97706 52%, #DC2626 100%);
    box-shadow: 0 0 16px rgba(59, 91, 255, 0.28);
    transition: width 250ms cubic-bezier(0.22, 1, 0.36, 1);
}
.cockpit-card {
    @starting-style {
        border-color: var(--start-border, rgba(255, 255, 255, 0.07));
        box-shadow: var(--start-shadow, 0 8px 24px rgba(0, 0, 0, 0.4));
    }
}
.live-radar {
    @starting-style {
        color: var(--start-color, #94A3B8);
    }
}
.live-radar.is-in {
    animation: mpPillIn 180ms ease-in-out;
}
.stress-fill {
    @starting-style {
        width: var(--start-width, 2%);
    }
}
.cockpit-score {
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    gap: 0.8rem;
    margin: 0.55rem 0 0.15rem 0;
}
.flag-pill.is-in {
    animation: mpPillIn 180ms ease-in-out;
}
.flag-pill.is-out {
    animation: mpPillOut 180ms ease-in-out forwards;
    pointer-events: none;
}
@keyframes mpPillIn {
    from { opacity: 0; transform: scale(0.97); }
    to { opacity: 1; transform: scale(1); }
}
@keyframes mpPillOut {
    from { opacity: 1; transform: scale(1); }
    to { opacity: 0; transform: scale(0.97); }
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
.cockpit-lights {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
    margin: 0.1rem 0 0.75rem 0;
}
.cockpit-lights .flag-pill { margin: 0; }
.feed-row {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 0.2rem 0.8rem;
    padding: 0.62rem 0;
    border-bottom: 1px solid rgba(255, 255, 255, 0.05);
    transition: background 0.18s ease, transform 150ms ease-out;
    border-radius: 8px;
}
.feed-row:hover {
    background: rgba(255, 255, 255, 0.04);
    transform: translateX(0.35rem);
}
.feed-row:last-child { border-bottom: 0; }
.war-board { margin: 0.2rem 0 1.2rem 0; }
.war-hero {
    display: grid;
    grid-template-columns: minmax(0, 1.1fr) minmax(16rem, 0.9fr);
    gap: 1.2rem 1.6rem;
    align-items: center;
    padding: 1.15rem 1.2rem 1.2rem;
    border-radius: 16px;
    background: rgba(15, 20, 32, 0.55);
    border: 1px solid rgba(255, 255, 255, 0.07);
}
.war-kicker {
    margin: 0;
    color: #C8F542;
    font-size: 0.72rem;
    font-weight: 700;
    letter-spacing: 0.12em;
    text-transform: uppercase;
}
.war-score-row { display: flex; align-items: flex-end; gap: 0.75rem; margin: 0.15rem 0 0.35rem; }
.war-score {
    margin: 0;
    color: #FFFFFF;
    font-family: "JetBrains Mono", monospace;
    font-size: 4rem;
    font-weight: 650;
    letter-spacing: -0.05em;
    line-height: 0.9;
    font-variant-numeric: tabular-nums;
}
.war-score-of { color: #94A3B8; font-size: 0.84rem; line-height: 1.3; padding-bottom: 0.3rem; }
.war-hero p { margin: 0; color: #CBD5E1; font-size: 0.98rem; line-height: 1.45; max-width: 36rem; }
.war-return { margin: 0.7rem 0 0; color: #7DD3FC; font-size: 0.86rem; }
a.war-feature, a.war-feature:hover, a.war-feature:visited {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr);
    gap: 0.3rem 1rem;
    align-items: center;
    margin: 0.85rem 0 0.2rem;
    padding: 0.95rem 1.05rem;
    border-radius: 16px;
    text-decoration: none;
    color: inherit;
    background: rgba(15, 20, 32, 0.55);
    border: 1px solid rgba(200, 245, 66, 0.7);
    transition: transform 150ms ease-out, border-color 0.2s ease;
}
a.war-feature:hover { transform: translateY(-2px); border-color: #C8F542; }
.war-feature b {
    grid-row: span 2;
    color: #FFFFFF;
    font-family: "JetBrains Mono", monospace;
    font-size: 2.8rem;
    font-weight: 650;
    letter-spacing: -0.04em;
    font-variant-numeric: tabular-nums;
}
.war-feature p { margin: 0; color: #F8FAFC; font-size: 1rem; font-weight: 650; line-height: 1.35; }
.war-feature small { color: #94A3B8; font-size: 0.78rem; line-height: 1.35; }
.war-move { color: #7DD3FC; }
.war-sales { display: flex; flex-direction: column; gap: 0.45rem; margin: 0.15rem 0 0.9rem; }
a.war-sale, a.war-sale:hover, a.war-sale:visited {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 0.8rem;
    padding: 0.72rem 0.85rem;
    border-radius: 14px;
    background: rgba(15, 20, 32, 0.55);
    border: 1px solid rgba(255, 255, 255, 0.07);
    text-decoration: none;
    color: inherit;
}
a.war-sale b {
    color: #FFFFFF;
    font-family: "JetBrains Mono", monospace;
    font-size: 1.15rem;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
}
a.war-sale span { color: #F8FAFC; font-weight: 600; }
a.war-sale small { display: block; margin-top: 0.15rem; color: #64748B; font-size: 0.75rem; }
a.war-guess .war-bet-track span { background: #38BDF8; }
.war-track {
    position: relative;
    height: 0.55rem;
    margin: 0.95rem 0 0.4rem;
    border-radius: 999px;
    background: linear-gradient(90deg, #15803D 0%, #C8F542 30%, #FACC15 44%, #F97316 58%, #EF4444 74%, #7F1D1D 100%);
}
.war-normal, .war-now {
    position: absolute;
    top: -0.28rem;
    width: 2px;
    height: 1.1rem;
    border-radius: 2px;
}
.war-normal { background: #FFFFFF; box-shadow: 0 0 0 1px rgba(7, 9, 14, 0.85); }
.war-now {
    width: 0.7rem;
    height: 0.7rem;
    top: -0.1rem;
    margin-left: -0.35rem;
    border-radius: 50%;
    background: #FFFFFF;
    box-shadow: 0 0 0 4px rgba(255,255,255,0.16);
}
.war-scale {
    display: flex;
    justify-content: space-between;
    color: #64748B;
    font-size: 0.72rem;
    letter-spacing: 0.04em;
}
.war-spark { width: 100%; height: 4.6rem; display: block; }
.war-chips {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 0.7rem;
    margin: 0.85rem 0 0.2rem;
}
.war-chip {
    padding: 0.75rem 0.8rem;
    border-radius: 14px;
    background: rgba(8, 11, 18, 0.45);
    border: 1px solid rgba(255, 255, 255, 0.06);
}
.war-chip b {
    display: block;
    color: #FFFFFF;
    font-family: "JetBrains Mono", monospace;
    font-size: 1.35rem;
    font-weight: 650;
    letter-spacing: -0.04em;
    font-variant-numeric: tabular-nums;
}
.war-chip span { color: #94A3B8; font-size: 0.78rem; line-height: 1.35; }
.war-bets { display: flex; flex-direction: column; gap: 0.55rem; margin: 0.35rem 0 0.8rem; }
a.war-bet, a.war-bet:hover, a.war-bet:visited {
    display: block;
    text-decoration: none;
    color: inherit;
    padding: 0.75rem 0.85rem;
    border-radius: 14px;
    background: rgba(15, 20, 32, 0.55);
    border: 1px solid rgba(255, 255, 255, 0.07);
    transition: transform 150ms ease-out, border-color 0.2s ease;
}
a.war-bet:hover { transform: translateY(-2px); border-color: rgba(200, 245, 66, 0.45); }
.war-bet-top { display: flex; align-items: baseline; justify-content: space-between; gap: 0.8rem; }
.war-bet-q { color: #F8FAFC; font-size: 0.95rem; font-weight: 600; line-height: 1.35; }
.war-bet-top b {
    color: #FFFFFF;
    font-family: "JetBrains Mono", monospace;
    font-size: 1.35rem;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
}
.war-bet-track {
    height: 0.42rem;
    margin: 0.55rem 0 0.4rem;
    border-radius: 999px;
    background: rgba(255, 255, 255, 0.06);
    overflow: hidden;
}
.war-bet-track span {
    display: block;
    height: 100%;
    border-radius: inherit;
    background: #C8F542;
    min-width: 2px;
}
.war-bet-meta { color: #64748B; font-size: 0.75rem; }
.news-board { margin: 0.15rem 0 1.15rem 0; }
.news-board .subhead {
    border-top: 0;
    padding-top: 0;
    margin-top: 0.15rem;
}
.news-board a,
.news-board a:hover,
.news-board a:focus,
.news-board a:visited,
.news-row,
.news-row:hover,
.news-title,
.news-toll,
.news-meta {
    text-decoration: none !important;
    text-decoration-line: none !important;
}
.news-row {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr);
    column-gap: 0.75rem;
    row-gap: 0.12rem;
    align-items: center;
    padding: 0.72rem 0.15rem;
    border-bottom: 0;
    color: inherit;
}
.news-row:hover { background: rgba(255, 255, 255, 0.03); }
.news-toll {
    grid-row: span 2;
    align-self: center;
    font-size: 0.68rem;
    font-weight: 700;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    border-radius: 999px;
    padding: 0.28rem 0.55rem;
    white-space: nowrap;
}
.news-toll.strain {
    color: #F87171;
    border: 1px solid rgba(248, 113, 113, 0.45);
    background: rgba(248, 113, 113, 0.08);
}
.news-toll.relief {
    color: #C8F542;
    border: 1px solid rgba(200, 245, 66, 0.45);
    background: rgba(200, 245, 66, 0.08);
}
.news-toll.mixed {
    color: #FBBF24;
    border: 1px solid rgba(251, 191, 36, 0.4);
    background: rgba(251, 191, 36, 0.08);
}
.news-toll.watch {
    color: #94A3B8;
    border: 1px solid rgba(148, 163, 184, 0.35);
}
.news-title {
    color: #FFFFFF;
    font-weight: 600;
    font-size: 0.95rem;
    line-height: 1.35;
}
.news-meta { color: #64748B; font-size: 0.75rem; }
.news-note {
    margin: 0.4rem 0 0 0;
    color: #64748B;
    font-size: 0.75rem;
}
.edition-line {
    color: #64748B;
    font-size: 0.68rem;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    font-weight: 600;
    margin: 0 0 0.45rem 0;
}
.series-tape {
    overflow: hidden;
    margin: 0.15rem 0 0.85rem 0;
    mask-image: linear-gradient(90deg, transparent, #000 6%, #000 94%, transparent);
    -webkit-mask-image: linear-gradient(90deg, transparent, #000 6%, #000 94%, transparent);
}
.series-tape-track {
    display: flex;
    gap: 1.6rem;
    width: max-content;
    animation: mpTape 32s linear infinite;
}
.series-tape:hover .series-tape-track { animation-play-state: paused; }
.tape-chip {
    font-family: "JetBrains Mono", monospace;
    font-size: 0.72rem;
    color: #94A3B8;
    white-space: nowrap;
}
.tape-chip strong { color: #E2E8F0; font-weight: 600; }
@keyframes mpTape {
    from { transform: translateX(0); }
    to { transform: translateX(-50%); }
}
a.mp-metric {
    display: block;
    text-decoration: none;
    color: inherit;
}
.mp-metric {
    background: rgba(15, 20, 32, 0.55);
    border: 1px solid rgba(255, 255, 255, 0.07);
    border-radius: 14px;
    padding: 0.9rem 1rem 0.85rem 1rem;
    min-height: 7.4rem;
    transition: transform 0.28s cubic-bezier(0.34, 1.8, 0.64, 1),
                border-color 0.2s ease, box-shadow 0.2s ease;
}
.mp-metric:hover {
    transform: translateY(-3px);
    border-color: rgba(59, 91, 255, 0.35);
    box-shadow: 0 10px 24px rgba(59, 91, 255, 0.12);
}
.mp-metric-title {
    color: #FFFFFF;
    font-size: 0.92rem;
    font-weight: 600;
    margin: 0;
}
.mp-metric-meta {
    color: #64748B;
    font-size: 0.68rem;
    letter-spacing: 0.04em;
    margin: 0.12rem 0 0.55rem 0;
    font-family: "JetBrains Mono", monospace;
}
.mp-metric-row {
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    gap: 0.6rem;
}
.mp-metric-value {
    font-family: "JetBrains Mono", monospace;
    font-size: 1.55rem;
    font-weight: 700;
    letter-spacing: -0.04em;
    color: #FFFFFF;
    line-height: 1;
}
.mp-metric-delta {
    color: #94A3B8;
    font-size: 0.68rem;
    margin-top: 0.28rem;
    font-family: "JetBrains Mono", monospace;
}
.mp-metric-delta.up { color: #F87171; }
.mp-metric-delta.down { color: #C8F542; }
.mp-spark { width: 92px; height: 36px; flex-shrink: 0; overflow: visible; }
.mp-spark polyline { fill: none; stroke: #38BDF8; stroke-width: 1.8; }
.cockpit-asof {
    color: #64748B;
    font-size: 0.68rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    margin: 0.55rem 0 0 0;
}
.subhead-meta {
    margin-left: 0.55rem;
    color: #64748B;
    font-size: 0.72rem;
    font-weight: 500;
    letter-spacing: 0.06em;
    text-transform: uppercase;
}
.feed-company { color: #FFFFFF; font-weight: 700; font-size: 0.92rem; line-height: 1.3; }
.feed-meta { display: flex; gap: 0.45rem; align-items: center; margin-top: 0.18rem; }
.feed-workers { color: #FB7185; font-weight: 700; font-size: 0.92rem; text-align: right; }
.feed-date { color: #64748B; font-size: 0.72rem; }
.stButton > button,
.stDownloadButton button,
button[kind="primary"],
button[kind="primaryFormSubmit"],
.stFormSubmitButton button[kind="primary"],
.stFormSubmitButton button[kind="primaryFormSubmit"] {
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
div.st-key-header_alerts button {
    transition: transform 150ms ease-out, box-shadow 0.2s ease !important;
}
div.st-key-header_alerts button:hover {
    transform: translateY(-2px) !important;
}
div.st-key-header_alerts button:active {
    transform: scale(0.97) !important;
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
    0% { transform: translate3d(0, 0, 0); }
    50% { transform: translate3d(2%, 2%, 0); }
    100% { transform: translate3d(0, 0, 0); }
}

html, body {
    background: #07090E !important;
    background-color: #07090E !important;
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
    z-index: 40 !important;
    overflow: visible !important;
}
header[data-testid="stHeader"] [data-testid="collapsedControl"],
header[data-testid="stHeader"] [data-testid="stSidebarCollapsedControl"],
header[data-testid="stHeader"] [data-testid="stExpandSidebarButton"],
header[data-testid="stHeader"] button {
    pointer-events: auto !important;
}
footer, [data-testid="stToolbar"], [data-testid="stDecoration"] {
    visibility: hidden;
    height: 0;
}
header[data-testid="stHeader"] {
    background: transparent !important;
    background-color: transparent !important;
    border-bottom: none !important;
    box-shadow: none !important;
    z-index: 40 !important;
    overflow: visible !important;
}
[data-testid="collapsedControl"],
[data-testid="stSidebarCollapsedControl"],
[data-testid="stExpandSidebarButton"] {
    display: flex !important;
    visibility: visible !important;
    opacity: 1 !important;
    z-index: 41 !important;
    position: absolute !important;
    top: 0.45rem !important;
    left: 0.65rem !important;
    width: auto !important;
    height: 2.4rem !important;
    padding: 0 0.75rem 0 0.55rem !important;
    gap: 0.35rem !important;
    align-items: center !important;
    justify-content: center !important;
    background: rgba(14, 18, 27, 0.85) !important;
    border: 1px solid rgba(255, 255, 255, 0.08) !important;
    border-radius: 12px !important;
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.12), 0 16px 36px rgba(0, 0, 0, 0.5) !important;
    color: #FFFFFF !important;
}
[data-testid="collapsedControl"]::after,
[data-testid="stSidebarCollapsedControl"]::after,
[data-testid="stExpandSidebarButton"]::after {
    content: "Economy Simulator";
    font-family: Inter, -apple-system, sans-serif;
    font-size: 0.8rem;
    font-weight: 600;
    letter-spacing: -0.01em;
    color: #FFFFFF;
    white-space: nowrap;
    line-height: 1;
}
[data-testid="collapsedControl"] svg,
[data-testid="stSidebarCollapsedControl"] svg,
[data-testid="stExpandSidebarButton"] svg,
[data-testid="collapsedControl"] span,
[data-testid="stSidebarCollapsedControl"] span,
[data-testid="stExpandSidebarButton"] span {
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
.site-intro {
    max-width: 44rem;
    margin: 0 0 0.9rem 0;
    color: #94A3B8;
    font-size: 0.95rem;
    font-weight: 600;
    line-height: 1.55;
}
.beginner-box ul.beginner-list {
    margin: 0.4rem 0 0 1.15rem;
    padding: 0;
    color: #94A3B8;
    font-size: 0.88rem;
    font-weight: 600;
    line-height: 1.55;
}
.beginner-box li { margin: 0.18rem 0; }
.legend-row {
    display: flex;
    flex-wrap: wrap;
    gap: 0.45rem;
    margin-top: 0.7rem;
}
.takeaway-card, .landing-hero, .newsletter-card,
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
    background: rgba(200, 245, 66, 0.15);
    border-color: rgba(200, 245, 66, 0.28);
    box-shadow: 0 0 14px rgba(200, 245, 66, 0.16);
    color: #C8F542;
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
.stDownloadButton button,
button[kind="primary"],
button[kind="primaryFormSubmit"],
.stFormSubmitButton button[kind="primary"],
.stFormSubmitButton button[kind="primaryFormSubmit"] {
    background: #C8F542 !important;
    color: #0B0F08 !important;
    border: none !important;
    border-radius: 999px !important;
    font-weight: 700 !important;
    padding: 10px 20px !important;
    box-shadow: none !important;
}
button[kind="primary"]:hover,
button[kind="primaryFormSubmit"]:hover,
.stFormSubmitButton button[kind="primary"]:hover,
.stFormSubmitButton button[kind="primaryFormSubmit"]:hover {
    background: #D6FF63 !important;
    color: #0B0F08 !important;
}
button[kind="primary"] p,
button[kind="primaryFormSubmit"] p,
.stFormSubmitButton button[kind="primary"] p,
.stFormSubmitButton button[kind="primaryFormSubmit"] p {
    color: #0B0F08 !important;
}
div.st-key-header_alerts button,
div.st-key-inline_subscribe button {
    background: rgba(200, 245, 66, 0.12) !important;
    color: #FFFFFF !important;
    font-weight: 600 !important;
    border: 1px solid #C8F542 !important;
    box-shadow: 0 0 0 1px rgba(200, 245, 66, 0.55), 0 6px 18px rgba(200, 245, 66, 0.18),
                inset 0 -2px 0 #C8F542 !important;
}
div.st-key-header_alerts button:hover,
div.st-key-inline_subscribe button:hover {
    background: rgba(200, 245, 66, 0.2) !important;
    color: #FFFFFF !important;
    border-color: #D6FF63 !important;
    box-shadow: 0 0 0 1px rgba(214, 255, 99, 0.7), 0 6px 18px rgba(200, 245, 66, 0.22),
                inset 0 -2px 0 #D6FF63 !important;
}
div.st-key-header_alerts button p,
div.st-key-inline_subscribe button p {
    color: #FFFFFF !important;
}
[data-testid="stVerticalBlock"]:has(> .st-key-nav_home) {
    flex-direction: row !important;
    align-items: center !important;
    justify-content: flex-end !important;
    flex-wrap: nowrap !important;
}
div.st-key-nav_home {
    width: 2.75rem !important;
    flex: 0 0 2.75rem !important;
}
div.st-key-nav_home button {
    position: relative !important;
    width: 2.75rem !important;
    min-width: 2.75rem !important;
    height: 2.75rem !important;
    min-height: 2.75rem !important;
    padding: 0 !important;
    gap: 0 !important;
    border-radius: 12px !important;
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
}
div.st-key-nav_home button [data-testid="stMarkdownContainer"],
div.st-key-nav_home button p {
    position: absolute !important;
    width: 1px !important;
    height: 1px !important;
    padding: 0 !important;
    margin: -1px !important;
    overflow: hidden !important;
    clip: rect(0, 0, 0, 0) !important;
    white-space: nowrap !important;
    border: 0 !important;
}
div.st-key-nav_home button > span {
    position: absolute !important;
    left: 50% !important;
    top: 50% !important;
    transform: translate(-50%, -50%) !important;
    width: 20px !important;
    height: 20px !important;
    margin: 0 !important;
    display: block !important;
    background: center / 18px 18px no-repeat url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='white' stroke-width='1.8' stroke-linejoin='round' stroke-linecap='round'><path d='M4 10.5 12 4l8 6.5V20a1 1 0 0 1-1 1h-5v-6H10v6H5a1 1 0 0 1-1-1z'/></svg>") !important;
}
div.st-key-nav_home [data-testid="stIconMaterial"] {
    font-size: 0 !important;
    color: transparent !important;
    background: none !important;
}
div.st-key-nav_unlock,
div.st-key-nav_free_preview,
div.st-key-nav_profile {
    width: auto !important;
    flex: 0 0 auto !important;
}
div.st-key-nav_unlock button,
div.st-key-nav_free_preview button,
div.st-key-nav_profile button {
    width: auto !important;
    white-space: nowrap !important;
    border-radius: 999px !important;
    min-height: 2.75rem !important;
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
    font-weight: 700 !important;
    letter-spacing: 0.01em !important;
    text-transform: none !important;
    width: 100% !important;
    box-shadow: 0 12px 32px rgba(0, 0, 0, 0.25) !important;
    min-height: 2.55rem !important;
    transition: border-color 0.16s ease, color 0.16s ease, box-shadow 0.16s ease,
                background 0.16s ease, transform 150ms ease-out !important;
}
section[data-testid="stSidebar"] .stButton > button:not([kind="primary"]):hover,
section[data-testid="stSidebar"] .st-key-sim_reset_live .stButton > button:not([kind="primary"]):hover,
section[data-testid="stSidebar"] .stButton > button:not([kind="primary"]):focus {
    background: rgba(255, 255, 255, 0.04) !important;
    border-color: rgba(255, 255, 255, 0.22) !important;
    color: #FFFFFF !important;
    box-shadow: none !important;
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
.lp-nav {
    display: none;
}
.lp-header-brand {
    display: flex;
    align-items: center;
    gap: 0.55rem;
    height: 2.5rem;
    margin: 0;
    padding: 0;
    transition: transform 150ms ease-out;
}
div.st-key-lp_header [data-testid="stColumn"]:first-child [data-testid="stVerticalBlock"] {
    position: relative !important;
}
div.st-key-lp_logo {
    position: absolute !important;
    top: 0 !important;
    left: 0 !important;
    width: 11rem !important;
    height: 2.5rem !important;
    margin: 0 !important;
    padding: 0 !important;
    z-index: 4 !important;
}
div.st-key-lp_logo .stButton,
div.st-key-lp_logo button {
    width: 11rem !important;
    height: 2.5rem !important;
    min-height: 2.5rem !important;
    margin: 0 !important;
    padding: 0 !important;
    background: transparent !important;
    border: 0 !important;
    box-shadow: none !important;
    color: transparent !important;
    cursor: pointer !important;
}
div.st-key-lp_logo button p {
    color: transparent !important;
}
div.st-key-lp_header:has(div.st-key-lp_logo button:active) .lp-header-brand {
    transform: scale(0.97);
}
.brand-home {
    display: inline-flex;
    align-items: center;
    gap: 0.7rem;
    transition: transform 150ms ease-out;
}
[data-testid="stVerticalBlock"]:has(.st-key-nav_logo) {
    position: relative !important;
}
div.st-key-nav_logo {
    position: absolute !important;
    top: 0.15rem !important;
    left: 0 !important;
    width: 12.5rem !important;
    height: 2.5rem !important;
    margin: 0 !important;
    padding: 0 !important;
    z-index: 5 !important;
}
div.st-key-nav_logo .stButton,
div.st-key-nav_logo button {
    width: 12.5rem !important;
    height: 2.5rem !important;
    min-height: 2.5rem !important;
    margin: 0 !important;
    padding: 0 !important;
    background: transparent !important;
    border: 0 !important;
    box-shadow: none !important;
    color: transparent !important;
    cursor: pointer !important;
}
div.st-key-nav_logo button p {
    color: transparent !important;
}
[data-testid="stVerticalBlock"]:has(div.st-key-nav_logo button:active) .brand-home {
    transform: scale(0.97);
}
.lp-header-brand .mp-mark {
    width: 1.75rem;
    height: 1.75rem;
}
.lp-header-brand .brand-name {
    display: flex;
    align-items: center;
    height: 2.5rem;
    margin: 0;
    font-size: 1.05rem;
    line-height: 1;
}
.lp-lang {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 0.4rem;
    height: 2.5rem;
    margin: 0;
    color: #E2E8F0;
    font-size: 0.92rem;
    font-weight: 600;
    letter-spacing: -0.01em;
    line-height: 1;
}
.lp-lang svg {
    width: 16px;
    height: 16px;
    opacity: 0.85;
}
.lp-hero-full {
    position: relative;
    min-height: 0;
    overflow: hidden;
    display: flex;
    flex-direction: column;
    background: #05070C;
    width: 100%;
}
.lp-fx {
    position: absolute;
    inset: 0;
    overflow: hidden;
    pointer-events: none;
    z-index: 0;
    contain: layout paint;
}
.lp-aurora {
    position: absolute;
    inset: -20%;
    background:
        radial-gradient(ellipse at 18% 78%, rgba(56, 189, 248, 0.28), transparent 52%),
        radial-gradient(ellipse at 82% 18%, rgba(59, 91, 255, 0.24), transparent 48%),
        radial-gradient(ellipse at 48% 44%, rgba(200, 245, 66, 0.08), transparent 42%),
        radial-gradient(ellipse at 70% 70%, rgba(200, 245, 66, 0.10), transparent 46%);
    animation: lpAurora 28s ease-in-out infinite alternate;
}
.lp-dotgrid {
    position: absolute;
    inset: -8%;
    background-image: radial-gradient(rgba(255,255,255,0.34) 1.1px, transparent 1.2px);
    background-size: 28px 28px;
    opacity: 0.22;
    animation: lpDotsPan 48s linear infinite;
}
.lp-grid {
    position: absolute;
    inset: 0;
    perspective: 520px;
    perspective-origin: 50% 12%;
    overflow: hidden;
    pointer-events: none;
}
.lp-grid-plane {
    position: absolute;
    left: -60%;
    width: 220%;
    height: 240%;
    top: 6%;
    transform: rotateX(66deg);
    transform-origin: 50% 0%;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='56' height='56'%3E%3Cpath d='M0 28H56M28 0V56' stroke='%2338BDF8' stroke-opacity='.30' stroke-width='1' fill='none'/%3E%3Cpath d='M21 28H35M28 21V35' stroke='%23ECF4FF' stroke-opacity='.58' stroke-width='1.35' fill='none'/%3E%3C/svg%3E");
    background-size: 56px 56px;
    opacity: 0.55;
    mask-image: linear-gradient(to bottom, rgba(0,0,0,0.12) 0%, rgba(0,0,0,0.7) 22%, #000 48%, transparent 86%);
    -webkit-mask-image: linear-gradient(to bottom, rgba(0,0,0,0.12) 0%, rgba(0,0,0,0.7) 22%, #000 48%, transparent 86%);
}
.lp-grid-horizon {
    position: absolute;
    left: 8%;
    right: 8%;
    top: 18%;
    height: 1px;
    background: linear-gradient(90deg, transparent, rgba(56,189,248,0.38) 22%, rgba(200,245,66,0.22) 50%, rgba(56,189,248,0.38) 78%, transparent);
    box-shadow: 0 0 18px 2px rgba(56, 189, 248, 0.18);
    opacity: 0.55;
}
.lp-topo {
    position: absolute;
    inset: 0;
    pointer-events: none;
    opacity: 0.55;
    mask-image: radial-gradient(ellipse 46% 40% at 50% 62%, transparent 0 42%, #000 78%);
    -webkit-mask-image: radial-gradient(ellipse 46% 40% at 50% 62%, transparent 0 42%, #000 78%);
}
.lp-topo svg {
    width: 100%;
    height: 100%;
    display: block;
}
.lp-topo path {
    fill: none;
    stroke-linejoin: round;
    stroke-linecap: round;
}
.lp-stars {
    position: absolute;
    inset: 0;
    opacity: 0.55;
    background-image:
        radial-gradient(1.4px 1.4px at 8% 16%, rgba(255,255,255,0.7), transparent),
        radial-gradient(1.2px 1.2px at 22% 38%, rgba(255,255,255,0.45), transparent),
        radial-gradient(1.6px 1.6px at 41% 12%, rgba(255,255,255,0.55), transparent),
        radial-gradient(1.1px 1.1px at 63% 28%, rgba(255,255,255,0.4), transparent),
        radial-gradient(1.5px 1.5px at 81% 18%, rgba(255,255,255,0.6), transparent),
        radial-gradient(1.2px 1.2px at 91% 42%, rgba(255,255,255,0.35), transparent),
        radial-gradient(1.3px 1.3px at 14% 62%, rgba(255,255,255,0.4), transparent),
        radial-gradient(1.1px 1.1px at 34% 71%, rgba(255,255,255,0.3), transparent),
        radial-gradient(1.4px 1.4px at 72% 66%, rgba(255,255,255,0.45), transparent),
        radial-gradient(1.2px 1.2px at 52% 8%, rgba(200,245,66,0.45), transparent);
}
.lp-wave {
    position: absolute;
    inset: -8%;
    width: 116%;
    height: 116%;
    object-fit: cover;
    object-position: center 62%;
    pointer-events: none;
    background: #05070C;
    transform-origin: 50% 62%;
    animation: lpPhotoDrift 26s ease-in-out infinite alternate;
}
.lp-drift {
    position: absolute;
    left: 0;
    bottom: 0;
    width: 200%;
    height: 42%;
    pointer-events: none;
    opacity: 0.8;
    animation: lpWaveSlide 18s linear infinite;
}
@keyframes lpPhotoDrift {
    from { transform: translate3d(0, 0, 0) scale(1); }
    to { transform: translate3d(-1.4%, -1.1%, 0) scale(1.045); }
}
@keyframes lpWaveSlide {
    from { transform: translate3d(0, 0, 0); }
    to { transform: translate3d(-50%, 0, 0); }
}
.lp-vignette {
    position: absolute;
    inset: 0;
    background:
        linear-gradient(90deg, rgba(5,7,12,0.62) 0%, rgba(5,7,12,0.22) 38%, transparent 62%),
        linear-gradient(to bottom, rgba(5,7,12,0.4), transparent 16%, transparent 70%, rgba(5,7,12,0.28) 88%, rgba(5,7,12,0.72) 100%);
}
@keyframes lpAurora {
    from { transform: translate3d(-3%, -2%, 0) scale(1); }
    to { transform: translate3d(4%, 3%, 0) scale(1.08); }
}
@keyframes lpDotsPan {
    from { transform: translate3d(0, 0, 0); }
    to { transform: translate3d(-28px, -28px, 0); }
}
@keyframes lpTwinkle {
    0%, 100% { opacity: 0; transform: scale(0.45); }
    50% { opacity: var(--o, 0.7); transform: scale(1); }
}
@keyframes lpSweep {
    from { transform: rotate(0deg); }
    to { transform: rotate(360deg); }
}
@keyframes lpGrain {
    0% { transform: translate(0, 0); }
    100% { transform: translate(-2%, 1%); }
}
@keyframes lpFloat {
    0%, 100% { transform: translateY(0); }
    50% { transform: translateY(-12px); }
}
@keyframes lpGridDrift {
    from { transform: translate3d(0, 0, 0); }
    to { transform: translate3d(0, 56px, 0); }
}
@keyframes lpTopoDrift {
    from { transform: translate3d(-1.2%, -0.8%, 0) scale(1.02); }
    to { transform: translate3d(1.4%, 1.1%, 0) scale(1.06); }
}
.lp-hero-inner {
    position: relative;
    z-index: 1;
    flex: 1;
    display: grid;
    grid-template-columns: minmax(0, 1.05fr) minmax(17.5rem, 0.85fr);
    gap: 3.5rem;
    align-items: center;
    min-height: min(62vh, 32rem);
    padding: 1.6rem 6vw 8.4rem 6vw;
    max-width: 1280px;
    width: 100%;
    margin: 0 auto;
    box-sizing: border-box;
}
.lp-hero-copy h1 {
    margin: 0;
    max-width: 11ch;
    color: #FFFFFF;
    font-size: clamp(3.1rem, 7.4vw, 6.4rem);
    font-weight: 500;
    letter-spacing: -0.055em;
    line-height: 0.92;
}
.lp-hero-copy p {
    margin: 1.15rem 0 0 0;
    max-width: 34rem;
    color: #CBD5E1;
    font-size: 1.12rem;
    font-weight: 500;
    line-height: 1.45;
}
.lp-pills {
    display: flex;
    flex-wrap: wrap;
    gap: 0.45rem;
    margin-top: 1.4rem;
}
.lp-pill {
    display: inline-flex;
    align-items: baseline;
    gap: 0.4rem;
    padding: 0.38rem 0.72rem;
    border-radius: 999px;
    background: rgba(255,255,255,0.06);
    border: 1px solid rgba(255,255,255,0.1);
    color: #94A3B8;
    font-size: 0.72rem;
    font-weight: 650;
}
.lp-pill b {
    color: #FFFFFF;
    font-family: "JetBrains Mono", monospace;
    font-size: 0.78rem;
}
.lp-device {
    margin-left: auto;
    width: min(100%, 380px);
    padding: 1.35rem 1.4rem 1.2rem 1.4rem;
    border-radius: 28px;
    background: linear-gradient(180deg, rgba(18,22,32,0.78), rgba(8,10,16,0.88));
    border: 1px solid rgba(255,255,255,0.1);
    box-shadow: 0 40px 80px rgba(0,0,0,0.45);
}
.lp-device-kicker {
    color: #64748B;
    font-size: 0.68rem;
    font-weight: 700;
    letter-spacing: 0.14em;
    text-transform: uppercase;
}
.lp-device-score {
    margin: 0.35rem 0 0.55rem 0;
    color: #FFFFFF;
    font-size: 4.2rem;
    font-weight: 500;
    letter-spacing: -0.06em;
    line-height: 1;
}
.lp-device-bar {
    height: 6px;
    border-radius: 99px;
    background: rgba(255,255,255,0.08);
    overflow: hidden;
}
.lp-device-bar i {
    display: block;
    height: 100%;
    border-radius: 99px;
    background: linear-gradient(90deg, #C8F542, #FBBF24, #EF4444);
}
.lp-device-rows { margin-top: 1.05rem; }
.lp-device-rows div {
    display: flex;
    justify-content: space-between;
    padding: 0.52rem 0;
    border-top: 1px solid rgba(255,255,255,0.07);
    color: #94A3B8;
    font-size: 0.86rem;
    font-weight: 600;
}
.lp-device-rows b { color: #FFFFFF; font-family: "JetBrains Mono", monospace; font-weight: 650; }
div.st-key-lp_cta {
    position: relative;
    z-index: 3;
    max-width: 1280px;
    margin: -6.6rem auto 0.4rem auto;
    padding: 0 6vw;
    background: transparent !important;
}
div.st-key-lp_enter button,
div.st-key-lp_enter .stButton > button {
    background: #C8F542 !important;
    color: #0B0F08 !important;
    border: 0 !important;
    border-radius: 999px !important;
    min-height: 3.25rem !important;
    padding: 0.7rem 1.6rem !important;
    font-size: 1.02rem !important;
    font-weight: 700 !important;
    letter-spacing: -0.01em !important;
    box-shadow: none !important;
    white-space: nowrap !important;
    transition: transform 150ms ease-out, background 150ms ease-out !important;
}
div.st-key-lp_enter button:hover {
    background: #D6FF63 !important;
    color: #0B0F08 !important;
}
div.st-key-lp_enter button:active,
div.st-key-lp_enter .stButton > button:active {
    transform: scale(0.97) !important;
    transition: transform 150ms ease-out !important;
}
div.st-key-lp_enter {
    width: fit-content !important;
    max-width: 100% !important;
}
div.st-key-lp_nav_economy button,
div.st-key-lp_nav_cars button,
div.st-key-lp_nav_bills button,
div.st-key-lp_nav_jobs button,
div.st-key-lp_nav_economy .stButton > button,
div.st-key-lp_nav_cars .stButton > button,
div.st-key-lp_nav_bills .stButton > button,
div.st-key-lp_nav_jobs .stButton > button,
div.st-key-lp_login button,
div.st-key-lp_login .stButton > button {
    background: transparent !important;
    border: 0 !important;
    box-shadow: none !important;
    color: #E2E8F0 !important;
    height: 2.5rem !important;
    min-height: 2.5rem !important;
    padding: 0 0.35rem !important;
    font-size: 0.92rem !important;
    font-weight: 600 !important;
    letter-spacing: -0.015em !important;
    line-height: 1 !important;
    border-radius: 0 !important;
    display: inline-flex !important;
    align-items: center !important;
    justify-content: center !important;
}
div.st-key-lp_nav_economy button:hover,
div.st-key-lp_nav_cars button:hover,
div.st-key-lp_nav_bills button:hover,
div.st-key-lp_nav_jobs button:hover {
    color: #FFFFFF !important;
    background: transparent !important;
    transform: none !important;
}
div.st-key-lp_login button:hover {
    background: transparent !important;
    color: #FFFFFF !important;
    transform: none !important;
}
div.st-key-lp_signup button,
div.st-key-lp_signup .stButton > button {
    background: #C8F542 !important;
    color: #0B0F08 !important;
    border: 0 !important;
    border-radius: 999px !important;
    height: 2.5rem !important;
    min-height: 2.5rem !important;
    padding: 0 1.1rem !important;
    font-size: 0.92rem !important;
    font-weight: 700 !important;
    letter-spacing: -0.01em !important;
    line-height: 1 !important;
    box-shadow: none !important;
    display: inline-flex !important;
    align-items: center !important;
    justify-content: center !important;
}
div.st-key-lp_nav_economy button p,
div.st-key-lp_nav_cars button p,
div.st-key-lp_nav_bills button p,
div.st-key-lp_nav_jobs button p,
div.st-key-lp_login button p,
div.st-key-lp_signup button p {
    margin: 0 !important;
    line-height: 1 !important;
    font-size: inherit !important;
}
div.st-key-lp_signup button:hover {
    background: #D6FF63 !important;
    color: #0B0F08 !important;
    transform: none !important;
}
.lp-jobs {
    position: relative;
    width: 100%;
    overflow: hidden;
    background: #05070c;
}
.lp-jobs-frame {
    position: relative;
    width: 100%;
    aspect-ratio: 1280 / 728;
}
.lp-jobs-photo {
    position: absolute;
    inset: 0;
    display: block;
    width: 100%;
    height: 100%;
    object-fit: cover;
    border: 0;
    pointer-events: none;
}
.lp-jobs-screen {
    position: absolute;
    left: 20.6%;
    top: 11.4%;
    width: 58.8%;
    height: 48.7%;
    overflow: hidden;
    border-radius: 2px 2px 1px 1px;
    background: #07090E;
    pointer-events: none;
    container-type: size;
}
.lp-jobs-screen::after {
    content: "";
    position: absolute;
    inset: 0;
    z-index: 3;
    pointer-events: none;
    background: linear-gradient(180deg, rgba(255,255,255,0.05), transparent 18%, transparent 78%, rgba(0,0,0,0.18));
}
.mp-eco {
    position: absolute;
    inset: 0;
    display: flex;
    flex-direction: column;
    gap: 1.6cqh;
    padding: 3.2cqh 2.6cqw 2.4cqh;
    box-sizing: border-box;
    color: #E2E8F0;
    font-family: Inter, -apple-system, sans-serif;
    background:
        radial-gradient(ellipse at 50% 0%, rgba(30,58,138,0.22), transparent 55%),
        #070b12;
}
.mp-eco h3 {
    margin: 0;
    color: #F8FAFC;
    font-size: 2.15cqw;
    font-weight: 600;
    letter-spacing: -0.03em;
    line-height: 1.15;
}
.mp-eco-sub {
    margin: 0.35cqh 0 0;
    color: #64748B;
    font-size: 1.15cqw;
}
.mp-eco-kicker {
    margin-top: 0.6cqh;
    color: #64748B;
    font-size: 0.95cqw;
    font-weight: 700;
    letter-spacing: 0.14em;
    text-transform: uppercase;
}
.mp-eco-cards {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 1.1cqw;
    min-height: 0;
}
.mp-eco-card {
    min-width: 0;
    padding: 1.3cqh 1.1cqw 1.1cqh;
    border-radius: 0.7cqw;
    background: rgba(15, 20, 32, 0.9);
    border: 1px solid rgba(255,255,255,0.07);
}
.mp-eco-card p {
    margin: 0;
    color: #F8FAFC;
    font-size: 1.15cqw;
    font-weight: 650;
    line-height: 1.2;
}
.mp-eco-card em {
    display: block;
    margin: 0.25cqh 0 0.7cqh;
    color: #64748B;
    font-size: 0.85cqw;
    font-style: normal;
}
.mp-eco-val {
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    gap: 0.4cqw;
}
.mp-eco-val strong {
    color: #FFFFFF;
    font-family: "JetBrains Mono", monospace;
    font-size: 1.85cqw;
    font-weight: 700;
    letter-spacing: -0.04em;
    line-height: 1;
}
.mp-eco-card svg { width: 7.2cqw; height: 3.4cqh; display: block; }
.mp-eco-card svg polyline { fill: none; stroke: #38BDF8; stroke-width: 1.6; }
.mp-eco-note {
    margin: 0.7cqh 0 0;
    color: #94A3B8;
    font-size: 0.9cqw;
    line-height: 1.3;
}
.mp-eco-charts {
    flex: 1;
    min-height: 0;
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 1.1cqw;
}
.mp-eco-chart {
    min-width: 0;
    min-height: 0;
    display: flex;
    flex-direction: column;
    padding: 1.1cqh 1.1cqw 0.8cqh;
    border-radius: 0.7cqw;
    background: rgba(15, 20, 32, 0.72);
    border: 1px solid rgba(255,255,255,0.06);
}
.mp-eco-chart span {
    color: #94A3B8;
    font-size: 0.95cqw;
    font-weight: 650;
}
.mp-eco-chart svg { flex: 1; width: 100%; min-height: 0; display: block; }
.mp-scr {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    box-sizing: border-box;
    display: flex;
    flex-direction: column;
    color: #E2E8F0;
    font-family: Inter, -apple-system, sans-serif;
    background:
        radial-gradient(at 0% 0%, rgba(30, 58, 138, 0.22) 0px, transparent 50%),
        radial-gradient(at 100% 100%, rgba(15, 118, 110, 0.16) 0px, transparent 50%),
        #07090E;
}
.mp-scr-chrome {
    display: flex;
    align-items: center;
    gap: 0.7cqw;
    height: 6.2cqh;
    padding: 0 1.1cqw;
    background: #0c1018;
    border-bottom: 1px solid rgba(255,255,255,0.06);
    flex-shrink: 0;
}
.mp-scr-dots { display: flex; gap: 0.42cqw; }
.mp-scr-dots i {
    width: 0.7cqw;
    height: 0.7cqw;
    border-radius: 50%;
    background: #3f3f46;
}
.mp-scr-dots i:nth-child(1) { background: #ff5f57; }
.mp-scr-dots i:nth-child(2) { background: #febc2e; }
.mp-scr-dots i:nth-child(3) { background: #28c840; }
.mp-scr-url {
    flex: 1;
    height: 3.6cqh;
    border-radius: 0.4cqw;
    background: rgba(255,255,255,0.05);
    color: #64748B;
    font-size: 1.15cqw;
    font-family: "JetBrains Mono", monospace;
    display: flex;
    align-items: center;
    padding: 0 0.8cqw;
}
.mp-scr-app {
    flex: 1;
    min-height: 0;
    padding: 1.5cqh 1.35cqw 1.4cqh;
    display: flex;
    flex-direction: column;
    gap: 1.1cqh;
}
.mp-scr-nav {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.8cqw;
    flex-shrink: 0;
}
.mp-scr-brand {
    display: flex;
    align-items: center;
    gap: 0.55cqw;
}
.mp-scr-brand svg { width: 2.05cqw; height: 2.05cqw; display: block; }
.mp-scr-brand b {
    color: #FFFFFF;
    font-size: 1.45cqw;
    font-weight: 700;
    letter-spacing: -0.04em;
}
.mp-scr-live {
    display: inline-flex;
    align-items: center;
    gap: 0.4cqw;
    color: #94A3B8;
    font-size: 1.05cqw;
    font-weight: 600;
}
.mp-scr-live i {
    width: 0.55cqw;
    height: 0.55cqw;
    border-radius: 50%;
    background: #C8F542;
    box-shadow: 0 0 8px rgba(200, 245, 66,0.7);
}
.mp-scr-nav-meta { color: #64748B; font-size: 1.05cqw; font-family: "JetBrains Mono", monospace; }
.mp-scr-actions { display: flex; gap: 0.55cqw; }
.mp-scr-actions span {
    display: inline-flex;
    align-items: center;
    height: 4.4cqh;
    padding: 0 0.9cqw;
    border-radius: 0.45cqw;
    font-size: 1.1cqw;
    font-weight: 650;
    border: 1px solid rgba(255,255,255,0.12);
    color: #E2E8F0;
}
.mp-scr-actions span.primary {
    background: #C8F542;
    color: #0B0F08;
    border-color: transparent;
}
.mp-scr-ed {
    margin: 0;
    color: #64748B;
    font-size: 0.95cqw;
    font-weight: 600;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    flex-shrink: 0;
}
.mp-scr-tape {
    display: flex;
    gap: 1.15cqw;
    overflow: hidden;
    color: #94A3B8;
    font-family: "JetBrains Mono", monospace;
    font-size: 1.05cqw;
    white-space: nowrap;
    flex-shrink: 0;
}
.mp-scr-tape strong { color: #E2E8F0; font-weight: 600; }
.mp-scr-hero {
    display: grid;
    grid-template-columns: 1.7fr 1.15fr;
    gap: 0.9cqw;
    min-height: 0;
    flex: 3.1 1 0;
}
.mp-scr-card {
    background: rgba(15, 20, 32, 0.86);
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 0.7cqw;
    box-shadow: 0 8px 24px rgba(0,0,0,0.4);
    padding: 1.3cqh 1.1cqw;
    min-width: 0;
    min-height: 0;
    overflow: hidden;
}
.mp-scr-kicker {
    color: #64748B;
    font-size: 0.95cqw;
    font-weight: 700;
    letter-spacing: 0.14em;
    text-transform: uppercase;
}
.mp-scr-read {
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    margin: 0.5cqh 0 0.7cqh;
}
.mp-scr-read strong {
    color: #FFFFFF;
    font-family: "JetBrains Mono", monospace;
    font-size: 3.35cqw;
    font-weight: 700;
    letter-spacing: -1px;
    line-height: 1;
}
.mp-scr-track {
    height: 0.85cqh;
    border-radius: 99px;
    background: rgba(255,255,255,0.06);
    overflow: hidden;
    margin-bottom: 0.7cqh;
}
.mp-scr-track i {
    display: block;
    height: 100%;
    border-radius: 99px;
    background: linear-gradient(90deg, #C8F542 0%, #D97706 52%, #DC2626 100%);
}
.mp-scr-hint { margin: 0; color: #64748B; font-size: 1.05cqw; }
.mp-scr-sahm {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.6cqw;
    margin: 0.9cqh 0 0.7cqh;
    padding: 0.55cqh 0.7cqw;
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 0.5cqw;
    color: #64748B;
    font-size: 0.95cqw;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
}
.mp-scr-sahm b {
    color: #FFFFFF;
    font-family: "JetBrains Mono", monospace;
    font-size: 1.2cqw;
    letter-spacing: 0;
    text-transform: none;
}
.mp-scr-badge {
    display: inline-flex;
    align-items: center;
    padding: 0.15cqh 0.55cqw;
    border-radius: 99px;
    font-size: 0.85cqw;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
}
.mp-scr-badge.healthy { color: #C8F542; background: rgba(200, 245, 66,0.12); border: 1px solid rgba(200, 245, 66,0.28); }
.mp-scr-badge.caution { color: #FDE68A; background: rgba(245,158,11,0.12); border: 1px solid rgba(245,158,11,0.28); }
.mp-scr-badge.stress { color: #FCA5A5; background: rgba(239,68,68,0.12); border: 1px solid rgba(239,68,68,0.28); }
.mp-scr-memo { margin: 0; color: #94A3B8; font-size: 1.1cqw; line-height: 1.4; }
.mp-scr-feed-row {
    display: flex;
    justify-content: space-between;
    gap: 0.55cqw;
    padding: 0.55cqh 0;
    border-bottom: 1px solid rgba(255,255,255,0.05);
}
.mp-scr-feed-row:last-child { border-bottom: 0; }
.mp-scr-co { color: #F8FAFC; font-size: 1.1cqw; font-weight: 650; }
.mp-scr-meta { color: #64748B; font-size: 0.9cqw; font-family: "JetBrains Mono", monospace; margin-top: 0.15cqh; }
.mp-scr-workers { color: #FFFFFF; font-family: "JetBrains Mono", monospace; font-size: 1.15cqw; font-weight: 650; }
.mp-scr-pills { display: flex; gap: 0.55cqw; flex-shrink: 0; }
.mp-scr-pills span,
.mp-scr-pills b {
    height: 4.2cqh;
    padding: 0 0.9cqw;
    border-radius: 99px;
    display: inline-flex;
    align-items: center;
    font-size: 1.1cqw;
    font-weight: 650;
}
.mp-scr-pills span {
    color: #94A3B8;
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.06);
}
.mp-scr-pills b {
    background: rgba(200,245,66,0.16);
    color: #C8F542;
    font-weight: 700;
    border: 1px solid rgba(200,245,66,0.28);
}
.mp-scr-metrics {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 0.7cqw;
    flex-shrink: 0;
}
.mp-scr-metric {
    background: rgba(15, 20, 32, 0.55);
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 0.7cqw;
    padding: 0.9cqh 0.85cqw;
}
.mp-scr-metric p { margin: 0; color: #FFFFFF; font-size: 1.05cqw; font-weight: 600; }
.mp-scr-metric em {
    display: block;
    margin: 0.15cqh 0 0.55cqh;
    color: #64748B;
    font-size: 0.8cqw;
    font-style: normal;
    font-family: "JetBrains Mono", monospace;
}
.mp-scr-metric-row { display: flex; align-items: flex-end; justify-content: space-between; gap: 0.4cqw; }
.mp-scr-metric strong {
    color: #FFFFFF;
    font-family: "JetBrains Mono", monospace;
    font-size: 1.55cqw;
    font-weight: 700;
    letter-spacing: -0.04em;
}
.mp-scr-metric svg { width: 5.4cqw; height: 2.3cqh; display: block; }
.mp-scr-metric svg polyline { fill: none; stroke: #38BDF8; stroke-width: 1.7; }
.mp-scr-chart {
    flex: 2.15 1 0;
    min-height: 0;
    padding: 0.9cqh 0.9cqw 0.7cqh;
    display: flex;
    flex-direction: column;
}
.mp-scr-chart .mp-scr-kicker { margin-bottom: 0.35cqh; flex-shrink: 0; }
.mp-scr-plot {
    flex: 1;
    min-height: 0;
    position: relative;
}
.mp-scr-plot svg {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    display: block;
}
.lp-jobs-copy {
    position: absolute;
    left: 50%;
    bottom: 1.2%;
    transform: translateX(-50%);
    z-index: 4;
    width: min(42rem, 78%);
    margin: 0;
    padding: 0;
    text-align: center;
    text-shadow: 0 10px 32px rgba(5, 7, 12, 0.7);
}
.lp-jobs-copy h2 {
    margin: 0 auto;
    max-width: 16ch;
    color: #FFFFFF;
    font-size: clamp(2.1rem, 4vw, 3.6rem);
    font-weight: 500;
    letter-spacing: -0.045em;
    line-height: 1.02;
}
.lp-jobs-copy p,
.lp-hh-copy p,
.lp-if-copy p {
    margin: 1rem 0 0 0;
    max-width: 34rem;
    color: #94A3B8;
    font-size: 1.05rem;
    font-weight: 500;
    line-height: 1.5;
}
.lp-jobs-copy p { margin-left: auto; margin-right: auto; }
.lp-hh, .lp-if {
    position: relative;
    width: 100%;
    overflow: hidden;
    background: #05070c;
}
.lp-hh::before {
    content: "";
    position: absolute;
    inset: 0;
    z-index: 1;
    pointer-events: none;
    background: linear-gradient(90deg, rgba(5,7,12,0.82) 0%, rgba(5,7,12,0.5) 26%, rgba(5,7,12,0.08) 46%, transparent 60%);
}
.lp-hh-photo, .lp-if-photo {
    display: block;
    width: 100%;
    height: auto;
    border: 0;
    pointer-events: none;
}
img.lp-wave,
img.lp-jobs-photo,
img.lp-hh-photo,
img.lp-if-photo {
    object-fit: cover !important;
    max-width: none !important;
    max-height: none !important;
}
.lp-hh-copy, .lp-if-copy {
    position: absolute;
    top: 42%;
    transform: translateY(-50%);
    width: min(28rem, 34%);
    z-index: 2;
    text-shadow: 0 8px 28px rgba(5, 7, 12, 0.55);
}
.lp-hh-copy { left: 6vw; }
.lp-if-copy { right: 5vw; }
.lp-hh-copy h2, .lp-if-copy h2 {
    margin: 0;
    max-width: 12ch;
    color: #FFFFFF;
    font-size: clamp(2.1rem, 4vw, 3.6rem);
    font-weight: 500;
    letter-spacing: -0.045em;
    line-height: 1.02;
}
.lp-hh-screen, .lp-if-screen {
    position: absolute;
    overflow: hidden;
    background: #07090E;
    pointer-events: none;
    container-type: size;
}
.lp-hh-screen {
    left: 60.2%;
    top: 42.8%;
    width: 29%;
    height: 23.2%;
    clip-path: polygon(5% 2%, 99% 0.5%, 98% 97%, 1% 99%);
}
.lp-if-screen {
    left: 16.8%;
    top: 12.2%;
    width: 47.4%;
    height: 50.2%;
    clip-path: polygon(1% 3%, 36% 8%, 64% 17%, 98% 33%, 100% 96%, 1.5% 99%);
}
.mp-mini, .mp-sim {
    position: absolute;
    inset: 0;
    display: flex;
    flex-direction: column;
    box-sizing: border-box;
    color: #E2E8F0;
    font-family: Inter, -apple-system, sans-serif;
    background:
        radial-gradient(at 0% 0%, rgba(30, 58, 138, 0.28), transparent 55%),
        #07090E;
}
.mp-mini { padding: 7cqh 6cqw 6cqh; gap: 0; justify-content: space-between; }
.mp-mini-top, .mp-sim-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1cqw;
    flex-shrink: 0;
}
.mp-mini-brand, .mp-sim-brand {
    display: flex;
    align-items: center;
    gap: 1.4cqw;
    color: #FFFFFF;
    font-weight: 700;
    letter-spacing: -0.03em;
}
.mp-mini-brand { font-size: 4.6cqw; }
.mp-mini-brand svg { width: 5.2cqw; height: 5.2cqw; }
.mp-mini-top > b {
    color: #C8F542;
    font-size: 3.4cqw;
    letter-spacing: 0.14em;
    text-transform: uppercase;
}
.mp-mini-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 2cqw;
    border-top: 1px solid rgba(255,255,255,0.08);
    min-height: 0;
}
.mp-mini-row span {
    color: #94A3B8;
    font-size: 3.5cqw;
    font-weight: 600;
}
.mp-mini-row strong {
    color: #FFFFFF;
    font-family: "JetBrains Mono", monospace;
    font-size: 4.4cqw;
    font-weight: 650;
}
.mp-sim { padding: 3.2cqh 3.2cqw 2.4cqh 2.6cqw; gap: 1.6cqh; }
.mp-sim-brand { font-size: 1.7cqw; }
.mp-sim-brand svg { width: 2.3cqw; height: 2.3cqw; }
.mp-sim-live {
    display: inline-flex;
    align-items: center;
    gap: 0.45cqw;
    color: #FBBF24;
    font-size: 1.15cqw;
    font-weight: 700;
    letter-spacing: 0.08em;
}
.mp-sim-live i {
    width: 0.6cqw;
    height: 0.6cqw;
    border-radius: 50%;
    background: #FBBF24;
}
.mp-sim-score {
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    flex-shrink: 0;
}
.mp-sim-score small {
    display: block;
    color: #64748B;
    font-size: 1.05cqw;
    font-weight: 700;
    letter-spacing: 0.12em;
    text-transform: uppercase;
}
.mp-sim-score strong {
    color: #FFFFFF;
    font-family: "JetBrains Mono", monospace;
    font-size: 4.2cqw;
    font-weight: 700;
    letter-spacing: -0.04em;
    line-height: 0.9;
}
.mp-sim-note {
    margin: 0;
    color: #94A3B8;
    font-size: 1.35cqw;
    line-height: 1.35;
}
.mp-sim-rows { display: flex; flex-direction: column; gap: 1.5cqh; flex: 1; min-height: 0; justify-content: space-evenly; }
.mp-sim-row span {
    display: flex;
    justify-content: space-between;
    color: #94A3B8;
    font-size: 1.25cqw;
    font-weight: 600;
    margin-bottom: 0.45cqh;
}
.mp-sim-row b { color: #F8FAFC; font-family: "JetBrains Mono", monospace; }
.mp-sim-track {
    height: 0.7cqh;
    border-radius: 99px;
    background: rgba(255,255,255,0.08);
    position: relative;
}
.mp-sim-track i {
    display: block;
    height: 100%;
    border-radius: 99px;
    background: linear-gradient(90deg, #38BDF8, #C8F542);
}
.mp-sim-track em {
    position: absolute;
    top: 50%;
    width: 1.15cqw;
    height: 1.15cqw;
    margin: -0.57cqw 0 0 -0.57cqw;
    border-radius: 50%;
    background: #FFFFFF;
    box-shadow: 0 0 0 2px rgba(7,9,14,0.8);
}
.mp-sim-actions { display: flex; gap: 0.8cqw; flex-shrink: 0; }
.mp-sim-actions span {
    height: 5.2cqh;
    padding: 0 1.3cqw;
    border-radius: 99px;
    display: inline-flex;
    align-items: center;
    font-size: 1.25cqw;
    font-weight: 700;
    border: 1px solid rgba(255,255,255,0.16);
    color: #E2E8F0;
}
.mp-sim-actions span.primary {
    background: linear-gradient(90deg, #1d4ed8, #38bdf8);
    color: #F8FAFC;
    border-color: transparent;
}
.lp-band {
    position: relative;
    overflow: hidden;
    display: grid;
    grid-template-columns: minmax(0, 1.05fr) minmax(0, 0.9fr);
    gap: 5rem;
    align-items: center;
    padding: 7.5rem 8vw;
    border: 0;
    background: transparent;
}
.lp-band > * { position: relative; z-index: 1; }
.lp-band > .lp-topo {
    position: absolute;
    inset: 0;
    z-index: 0;
}
.lp-band::before {
    content: "";
    position: absolute;
    inset: 0;
    pointer-events: none;
    background:
        radial-gradient(ellipse at 12% 80%, rgba(59, 91, 255, 0.14), transparent 52%),
        radial-gradient(ellipse at 88% 20%, rgba(56, 189, 248, 0.10), transparent 50%);
}
.lp-band.reverse::before {
    background:
        radial-gradient(ellipse at 80% 70%, rgba(200, 245, 66, 0.16), transparent 48%),
        radial-gradient(ellipse at 18% 18%, rgba(200, 245, 66, 0.08), transparent 44%);
}
.lp-band.reverse { grid-template-columns: minmax(0, 0.9fr) minmax(0, 1.05fr); }
.lp-band.reverse .lp-band-copy { order: 2; }
.lp-band h2 {
    margin: 0;
    max-width: 12ch;
    color: #FFFFFF;
    font-size: clamp(2.4rem, 5vw, 4.4rem);
    font-weight: 500;
    letter-spacing: -0.045em;
    line-height: 1.02;
}
.lp-band p {
    margin: 1rem 0 0 0;
    max-width: 34rem;
    color: #94A3B8;
    font-size: 1.05rem;
    font-weight: 500;
    line-height: 1.5;
}
.lp-band-kicker {
    margin-bottom: 0.7rem;
    color: #C8F542;
    font-size: 0.72rem;
    font-weight: 700;
    letter-spacing: 0.16em;
    text-transform: uppercase;
}
.lp-band-visual {
    min-height: 0;
    margin: 0;
    padding: 0;
    border: 0;
    border-radius: 0;
    background: none;
    color: #F8FAFC;
    font-size: clamp(1.7rem, 3.4vw, 2.9rem);
    font-weight: 500;
    letter-spacing: -0.045em;
    line-height: 1.08;
    text-align: left;
}
.lp-band-visual em {
    display: block;
    margin: 0 0 0.4rem 0;
    color: #C8F542;
    font-style: normal;
    font-size: clamp(3.1rem, 6.2vw, 5.4rem);
    font-weight: 500;
    letter-spacing: -0.06em;
    line-height: 0.92;
}
.lp-band-visual span {
    display: block;
    max-width: 18ch;
    color: #94A3B8;
    font-size: 1.05rem;
    font-weight: 500;
    letter-spacing: -0.01em;
    line-height: 1.4;
}
.lp-foot {
    padding: 1.2rem 8vw 2.6rem 8vw;
    border: 0;
    color: #64748B;
    font-size: 0.78rem;
}
.lp-why {
    position: relative;
    padding: 5.5rem 6vw 2.4rem;
    background:
        radial-gradient(ellipse at 50% 0%, rgba(200, 245, 66, 0.06), transparent 42%),
        #07090e;
}
.lp-why h2 {
    margin: 0 auto 3.2rem;
    max-width: 14ch;
    color: #FFFFFF;
    font-size: clamp(2.6rem, 5vw, 4rem);
    font-weight: 500;
    letter-spacing: -0.045em;
    line-height: 1.02;
    text-align: center;
}
.lp-why-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 2.8rem 4.5rem;
    max-width: 58rem;
    margin: 0 auto;
}
.lp-why-grid article svg {
    display: block;
    width: 1.7rem;
    height: 1.7rem;
    stroke: #C8F542;
    fill: none;
    stroke-width: 1.5;
    stroke-linecap: round;
    stroke-linejoin: round;
}
.lp-why-grid h3 {
    margin: 0.95rem 0 0;
    max-width: 22ch;
    color: #F8FAFC;
    font-size: 1.35rem;
    font-weight: 600;
    letter-spacing: -0.03em;
    line-height: 1.25;
}
.lp-why-grid p {
    margin: 0.55rem 0 0;
    max-width: 36ch;
    color: #94A3B8;
    font-size: 0.98rem;
    font-weight: 500;
    line-height: 1.5;
}
.lp-play {
    padding: 1.2rem 6vw 5.4rem;
    background: #07090e;
}
.lp-play-head {
    max-width: 46rem;
    margin: 0 auto 2.6rem;
    text-align: center;
}
.lp-play-badge {
    display: inline-flex;
    align-items: center;
    margin-bottom: 1rem;
    padding: 0.38rem 0.75rem;
    border-radius: 999px;
    border: 1px solid rgba(200, 245, 66, 0.45);
    color: #C8F542;
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 0.16em;
    text-transform: uppercase;
}
.lp-play h2 {
    margin: 0 auto;
    max-width: 18ch;
    color: #FFFFFF;
    font-size: clamp(2.1rem, 4.2vw, 3.35rem);
    font-weight: 500;
    letter-spacing: -0.045em;
    line-height: 1.05;
}
.lp-play-head p {
    margin: 1rem auto 0;
    max-width: 40rem;
    color: #94A3B8;
    font-size: 1.05rem;
    font-weight: 500;
    line-height: 1.5;
}
.lp-play-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 1rem;
    max-width: 72rem;
    margin: 0 auto;
}
.lp-play-grid article {
    display: flex;
    flex-direction: column;
    min-width: 0;
    padding: 1.35rem 1.25rem 1.15rem;
    border-radius: 1.35rem;
    background:
        linear-gradient(180deg, rgba(255,255,255,0.07), rgba(255,255,255,0.025));
    border: 1px solid rgba(255,255,255,0.1);
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.1);
}
.lp-play-name {
    color: #C8F542;
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 0.13em;
    text-transform: uppercase;
}
.lp-play-hook {
    margin-top: 0.85rem;
    color: #38BDF8;
    font-family: "JetBrains Mono", monospace;
    font-size: 0.74rem;
    font-weight: 650;
    letter-spacing: -0.02em;
    line-height: 1.35;
}
.lp-play-grid h3 {
    margin: 0.7rem 0 0;
    color: #F8FAFC;
    font-size: 1.32rem;
    font-weight: 600;
    letter-spacing: -0.03em;
    line-height: 1.2;
}
.lp-play-grid article > p {
    margin: 0.75rem 0 0;
    color: #94A3B8;
    font-size: 0.95rem;
    font-weight: 500;
    line-height: 1.55;
    flex: 1;
}
.lp-play-tag {
    align-self: flex-start;
    margin-top: 1.25rem;
    padding: 0.38rem 0.62rem;
    border-radius: 999px;
    background: rgba(200, 245, 66, 0.1);
    color: #C8F542;
    font-family: "JetBrains Mono", monospace;
    font-size: 0.66rem;
    font-style: normal;
    font-weight: 700;
    letter-spacing: 0.08em;
}
@media (prefers-reduced-motion: reduce) {
    .lp-aurora, .lp-dotgrid, .lp-star, .lp-sweep, .lp-grain, .lp-device, .lp-band::before, .lp-band-visual, .lp-grid-plane, .lp-topo svg, .lp-wave, .lp-drift {
        animation: none !important;
    }
}
.hero-sim-pill, .live-pill {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    margin: 0.85rem 0.45rem 0 0;
    padding: 0.28rem 0.7rem;
    border-radius: 999px;
    background: rgba(200, 245, 66, 0.12);
    border: 1px solid rgba(200, 245, 66, 0.28);
    box-shadow: 0 0 12px rgba(200, 245, 66, 0.16);
    color: #C8F542;
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.5px;
    text-transform: uppercase;
}
.live-pill .live-dot {
    width: 0.42rem;
    height: 0.42rem;
    border-radius: 50%;
    background: #C8F542;
    box-shadow: 0 0 10px rgba(200, 245, 66, 0.55);
}
.sim-panel {
    margin: 0.1rem 0 0.85rem 0;
    padding: 0 0 0.95rem 0;
    border-bottom: 1px solid rgba(255, 255, 255, 0.07);
    background: transparent !important;
    box-shadow: none !important;
}
.sim-brand {
    display: flex;
    align-items: center;
    gap: 0.65rem;
    margin: 0 0 0.7rem 0;
}
.sim-brand:hover .mp-mark {
    transform: translateY(-2px) scale(1.07);
}
.sim-brand .mp-mark,
.sim-mark {
    width: 30px;
    height: 30px;
}
.sim-brand-copy { min-width: 0; flex: 1; }
.sim-brand .hero-kicker {
    color: #6B7A90;
    letter-spacing: 0.14em;
    font-weight: 600;
}
.sim-brand-name {
    font-size: 1.02rem;
    font-weight: 650;
    letter-spacing: -0.04em;
    line-height: 1.1;
}
.sim-brand .live-radar {
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    font-size: 0.72rem;
}
.sim-panel h3 {
    margin: 0 0 0.35rem 0;
    font-size: 1.15rem;
    font-weight: 500;
    letter-spacing: -0.03em;
    color: #FFFFFF;
}
.sim-panel p {
    margin: 0;
    color: #94A3B8;
    font-size: 0.84rem;
    font-weight: 500;
    line-height: 1.45;
}
.sim-lever-meta {
    display: flex;
    justify-content: space-between;
    gap: 0.6rem;
    margin: 0.15rem 0 -0.35rem 0;
    font-family: "JetBrains Mono", monospace;
    font-size: 0.68rem;
    font-weight: 700;
    letter-spacing: 0.04em;
    color: #94A3B8;
}
.sim-lever-meta strong { color: #CBD5E1; font-weight: 600; }
.sim-now {
    display: inline-flex;
    align-items: baseline;
    justify-content: flex-end;
    gap: 0.4rem;
    flex-wrap: wrap;
}
.sim-delta {
    display: inline-flex;
    align-items: center;
    color: #7DD3FC;
    font-style: normal;
    font-weight: 700;
    white-space: nowrap;
    padding: 0.05rem 0.38rem;
    border-radius: 999px;
    background: rgba(56, 189, 248, 0.12);
    border: 1px solid rgba(56, 189, 248, 0.45);
}
.sim-delta.is-in { animation: mpDelta 180ms ease-out; }
@keyframes mpDelta {
    from { opacity: 0; transform: scale(0.97); }
    to { opacity: 1; transform: scale(1); }
}
section[data-testid="stSidebar"] .subhead {
    color: #64748B;
    font-weight: 700;
}
.mp-paywall {
    margin: 0.35rem 0 1.15rem 0;
    padding: 1.15rem 1.25rem 1.05rem 1.25rem;
    border-radius: 16px;
    background: rgba(200, 245, 66, 0.08);
    border: 1px solid #C8F542;
    box-shadow: 0 0 0 1px rgba(200, 245, 66, 0.35), 0 10px 28px rgba(0, 0, 0, 0.28);
}
.mp-paywall .hero-kicker {
    color: #C8F542;
    margin: 0;
}
.mp-paywall h3 {
    margin: 0.3rem 0 0.35rem 0;
    color: #FFFFFF;
    font-size: 1.15rem;
}
.mp-paywall p {
    margin: 0;
    color: #CBD5E1;
    line-height: 1.45;
}
.mp-preview-banner {
    margin: 0.35rem 0 0.85rem 0;
    padding: 0.55rem 0.85rem;
    border-radius: 999px;
    border: 1px solid rgba(200, 245, 66, 0.45);
    color: #E2E8F0;
    font-size: 0.86rem;
    background: rgba(200, 245, 66, 0.08);
}
.mp-blur-table,
div[class*="st-key-blur_"],
div[class*="st-key-blur_"] * {
    pointer-events: none !important;
    user-select: none !important;
}
.mp-blur-table,
div[class*="st-key-blur_"] {
    filter: blur(5px);
    margin: 0.35rem 0 0.8rem 0;
}
div[class*="st-key-cta_"] {
    flex-direction: row !important;
    align-items: center !important;
    justify-content: space-between !important;
    gap: 0.75rem !important;
    margin: 0.85rem 0 0.15rem 0 !important;
}
div[class*="st-key-cta_"] > div:last-child {
    width: auto !important;
    flex: 0 0 auto !important;
}
div[class*="st-key-cta_"] button {
    width: auto !important;
    border-radius: 999px !important;
    white-space: nowrap !important;
}
.mp-lock-kicker {
    margin: 0;
    color: #C8F542;
    font-family: "JetBrains Mono", monospace;
    font-size: 0.72rem;
    font-weight: 700;
    letter-spacing: 0.06em;
    text-transform: uppercase;
}
.mp-hook {
    margin: 0 0 0.75rem 0;
    padding: 0.85rem 1rem;
    border-radius: 14px;
    background: rgba(15, 20, 32, 0.72);
    border: 1px solid rgba(255, 255, 255, 0.07);
}
.mp-hook .hero-kicker {
    margin-bottom: 0.28rem;
}
.mp-hook p {
    margin: 0;
    color: #F8FAFC;
    font-size: 0.98rem;
    line-height: 1.3;
    letter-spacing: -0.02em;
}
.mp-hook b {
    color: #FFFFFF;
    font-weight: 650;
    font-variant-numeric: tabular-nums;
}
.mp-market-read {
    margin: 0.1rem 0 0.85rem 0;
    color: #CBD5E1;
    font-size: 0.95rem;
    line-height: 1.4;
}
.mp-blur-table table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.92rem;
}
.mp-blur-table th,
.mp-blur-table td {
    text-align: left;
    padding: 0.55rem 0.4rem;
    border-bottom: 1px solid rgba(255, 255, 255, 0.06);
    color: #E2E8F0;
}
.mp-plans-lead {
    margin: 0 0 0.75rem 0;
    color: #CBD5E1;
    font-size: 0.95rem;
    line-height: 1.45;
}
.mp-plans-lead strong {
    display: block;
    margin-bottom: 0.15rem;
    color: #FFFFFF;
    font-size: 1.15rem;
    font-weight: 650;
    letter-spacing: -0.03em;
}
.mp-plans-gets {
    list-style: none;
    margin: 0 0 0.9rem 0;
    padding: 0.75rem 0.9rem;
    border-radius: 14px;
    background: rgba(255, 255, 255, 0.03);
    border: 1px solid rgba(255, 255, 255, 0.08);
}
.mp-plans-gets li {
    display: flex;
    gap: 0.55rem;
    align-items: flex-start;
    margin: 0.42rem 0;
    color: #E2E8F0;
    font-size: 0.9rem;
    line-height: 1.35;
}
.mp-plans-gets li::before {
    content: "";
    width: 0.42rem;
    height: 0.42rem;
    margin-top: 0.38rem;
    border-radius: 50%;
    background: #C8F542;
    box-shadow: 0 0 8px rgba(200, 245, 66, 0.45);
    flex: 0 0 auto;
}
.mp-plans-note {
    margin: 0.15rem 0 0 0;
    color: #64748B;
    font-size: 0.78rem;
}
div.st-key-checkout_panel {
    max-width: 640px;
    margin: 0.4rem auto 2rem auto;
}
.mp-checkout-kicker {
    margin: 0 0 0.25rem 0;
    color: #C8F542;
    font-family: "JetBrains Mono", monospace;
    font-size: 0.68rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
}
.mp-checkout-title {
    margin: 0 0 0.35rem 0;
    color: #FFFFFF;
    font-size: 1.7rem;
    font-weight: 650;
    letter-spacing: -0.03em;
    line-height: 1.15;
}
.mp-checkout-charge {
    margin: 0 0 0.85rem 0;
    color: #CBD5E1;
    font-size: 0.95rem;
    line-height: 1.45;
}
div.st-key-profile_panel {
    max-width: 680px;
    margin: 0.4rem auto 2rem auto;
}
div.st-key-profile_plan,
div.st-key-profile_password,
div.st-key-profile_signout {
    margin: 0 0 0.85rem 0;
    padding: 0.95rem 1rem 1rem 1rem;
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 16px;
    background: rgba(255, 255, 255, 0.03);
}
.mp-profile-facts {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0.7rem;
    margin: 0 0 1rem 0;
}
.mp-profile-facts div {
    padding: 0.75rem 0.85rem;
    border-radius: 14px;
    border: 1px solid rgba(255, 255, 255, 0.08);
    background: rgba(255, 255, 255, 0.03);
}
.mp-profile-facts span {
    display: block;
    margin-bottom: 0.2rem;
    color: #94A3B8;
    font-family: "JetBrains Mono", monospace;
    font-size: 0.66rem;
    font-weight: 700;
    letter-spacing: 0.07em;
    text-transform: uppercase;
}
.mp-profile-facts strong {
    color: #FFFFFF;
    font-size: 0.98rem;
    font-weight: 650;
    letter-spacing: -0.02em;
}
.mp-profile-card-kicker {
    margin: 0 0 0.2rem 0;
    color: #C8F542;
    font-family: "JetBrains Mono", monospace;
    font-size: 0.66rem;
    font-weight: 700;
    letter-spacing: 0.07em;
    text-transform: uppercase;
}
.mp-profile-card-title {
    margin: 0 0 0.45rem 0;
    color: #FFFFFF;
    font-family: Inter, -apple-system, sans-serif;
    font-size: 1.15rem;
    font-weight: 650;
    letter-spacing: -0.02em;
    line-height: 1.25;
}
.mp-profile-name {
    margin: 0 0 0.35rem 0;
    color: #FFFFFF;
    font-family: Inter, -apple-system, sans-serif;
    font-size: 1.45rem;
    font-weight: 650;
    letter-spacing: 0.08em;
    line-height: 1.15;
    text-transform: uppercase;
}
.mp-profile-copy {
    margin: 0 0 1rem 0;
    color: #CBD5E1;
    font-family: Inter, -apple-system, sans-serif;
    font-size: 0.95rem;
    font-weight: 400;
    line-height: 1.55;
    letter-spacing: 0;
}
div.st-key-profile_plan [data-testid="stMarkdown"],
div.st-key-profile_signout [data-testid="stMarkdown"],
div.st-key-profile_password [data-testid="stMarkdown"] {
    margin-bottom: 0 !important;
}
div.st-key-profile_plan [data-testid="stMarkdownContainer"] p,
div.st-key-profile_signout [data-testid="stMarkdownContainer"] p,
div.st-key-profile_password [data-testid="stMarkdownContainer"] p,
div.st-key-profile_plan [data-testid="stMarkdownContainer"] h3,
div.st-key-profile_signout [data-testid="stMarkdownContainer"] h3,
div.st-key-profile_password [data-testid="stMarkdownContainer"] h3 {
    font-family: Inter, -apple-system, sans-serif !important;
}
[data-testid="stTextInputRootElement"] {
    position: relative;
    align-items: center;
}
[data-testid="stTextInputRootElement"] input {
    width: 100%;
    min-width: 0;
    box-sizing: border-box;
    padding-right: 2.8rem !important;
}
[data-testid="stTextInputRootElement"] button {
    position: absolute !important;
    right: 0.35rem !important;
    top: 50% !important;
    transform: translateY(-50%) !important;
    z-index: 2;
    flex: 0 0 2.25rem !important;
    width: 2.25rem !important;
    min-width: 2.25rem !important;
    height: 2.25rem !important;
    min-height: 2.25rem !important;
    margin: 0 !important;
    padding: 0 !important;
    border: 0 !important;
    border-radius: 8px !important;
    background: transparent !important;
    box-shadow: none !important;
    color: #94A3B8 !important;
    font-size: 0 !important;
    line-height: 0 !important;
}
[data-testid="stTextInputRootElement"] button svg {
    width: 1.15rem !important;
    height: 1.15rem !important;
    display: block !important;
    overflow: hidden !important;
}
[data-testid="stTextInputRootElement"] button svg title {
    display: none !important;
}
.mp-plan-price {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 0.15rem 0.45rem;
    min-width: 0;
}
.mp-plan-note {
    flex: 1 0 100%;
    color: #94A3B8;
    font-family: "JetBrains Mono", monospace;
    font-size: 0.66rem;
    font-weight: 700;
    letter-spacing: 0.07em;
    text-transform: uppercase;
}
.mp-plan-price strong {
    color: #FFFFFF;
    font-size: 1.7rem;
    font-weight: 650;
    letter-spacing: -0.03em;
    line-height: 1;
}
.mp-plan-price em {
    color: #94A3B8;
    font-style: normal;
    font-size: 0.92rem;
}
.mp-plan-perk {
    flex: 1 0 100%;
    max-width: 100%;
    margin-top: 0.35rem;
    color: #CBD5E1;
    font-size: 0.82rem;
    line-height: 1.35;
    overflow-wrap: break-word;
}
.mp-plan-featured .mp-plan-note {
    color: #C8F542;
}
div[class*="st-key-planrow_"] {
    flex-direction: row !important;
    align-items: center !important;
    justify-content: space-between !important;
    gap: 0.85rem !important;
    margin: 0 0 0.45rem 0 !important;
    padding: 0.9rem 1.15rem !important;
    border-radius: 14px !important;
    background: rgba(255, 255, 255, 0.03) !important;
    border: 1px solid rgba(255, 255, 255, 0.08) !important;
    overflow: hidden !important;
    box-sizing: border-box !important;
}
div.st-key-planrow_year {
    background: rgba(200, 245, 66, 0.08) !important;
    border: 1px solid #C8F542 !important;
    box-shadow: 0 0 0 1px rgba(200, 245, 66, 0.22);
}
div[class*="st-key-planrow_"] > div:first-child {
    flex: 1 1 0 !important;
    width: auto !important;
    min-width: 0 !important;
    max-width: 100% !important;
}
div[class*="st-key-planrow_"] [data-testid="stMarkdown"],
div[class*="st-key-planrow_"] [data-testid="stMarkdownContainer"] {
    max-width: 100%;
    margin-bottom: 0 !important;
    overflow-wrap: break-word;
}
div[class*="st-key-planrow_"] > div:last-child {
    width: auto !important;
    flex: 0 0 auto !important;
}
div.st-key-price_grid {
    max-width: 980px;
    margin: 0.2rem auto 2rem auto;
}
div.st-key-price_grid [data-testid="stHorizontalBlock"] {
    align-items: stretch !important;
    gap: 0.85rem !important;
}
div.st-key-price_grid [data-testid="stColumn"] {
    display: flex !important;
}
div.st-key-price_month,
div.st-key-price_year,
div.st-key-price_lifetime {
    position: relative;
    display: flex !important;
    flex-direction: column !important;
    flex: 1 1 auto;
    height: 100%;
    margin: 0 !important;
    padding: 1.15rem 1.05rem 1rem !important;
    border-radius: 16px !important;
    border: 1px solid rgba(255, 255, 255, 0.08) !important;
    background: rgba(14, 18, 27, 0.92) !important;
    box-shadow: none !important;
    transition: transform 220ms ease-in-out, opacity 220ms ease-in-out, border-color 200ms ease-in-out, background 200ms ease-in-out !important;
}
div.st-key-price_grid:has(div.st-key-price_month:hover) div.st-key-price_year,
div.st-key-price_grid:has(div.st-key-price_month:hover) div.st-key-price_lifetime,
div.st-key-price_grid:has(div.st-key-price_year:hover) div.st-key-price_month,
div.st-key-price_grid:has(div.st-key-price_year:hover) div.st-key-price_lifetime,
div.st-key-price_grid:has(div.st-key-price_lifetime:hover) div.st-key-price_month,
div.st-key-price_grid:has(div.st-key-price_lifetime:hover) div.st-key-price_year {
    opacity: 0.78;
}
div.st-key-price_month:hover,
div.st-key-price_year:hover,
div.st-key-price_lifetime:hover {
    opacity: 1 !important;
    transform: translateY(-4px);
}
div.st-key-price_month:has(.is-on),
div.st-key-price_year:has(.is-on),
div.st-key-price_lifetime:has(.is-on) {
    border-color: #C8F542 !important;
    background: rgba(200, 245, 66, 0.08) !important;
    box-shadow: 0 0 0 1px rgba(200, 245, 66, 0.22) !important;
}
.mp-price-badge {
    display: inline-flex;
    align-items: center;
    align-self: flex-start;
    min-height: 1.35rem;
    margin: 0 0 0.85rem 0;
    padding: 0.28rem 0.7rem;
    border-radius: 999px;
    color: #0B0F08;
    background: #C8F542;
    font-family: "JetBrains Mono", monospace;
    font-size: 0.66rem;
    font-weight: 700;
    letter-spacing: 0.07em;
    text-transform: uppercase;
}
.mp-price-badge.is-empty {
    visibility: hidden;
    background: transparent;
}
.mp-price-name {
    margin: 0;
    color: #FFFFFF;
    font-family: Inter, -apple-system, sans-serif;
    font-size: 1.02rem;
    font-weight: 650;
    letter-spacing: -0.02em;
}
.mp-price-amount {
    display: flex;
    align-items: baseline;
    gap: 0.4rem;
    margin: 0.45rem 0 0.2rem 0;
}
.mp-price-amount strong {
    color: #FFFFFF;
    font-family: Inter, -apple-system, sans-serif;
    font-size: 2.35rem;
    font-weight: 650;
    letter-spacing: -0.04em;
    line-height: 0.95;
}
.mp-price-amount.is-hero strong {
    font-size: 2.85rem;
    color: #FFFFFF;
}
.mp-price-amount em {
    color: #94A3B8;
    font-style: normal;
    font-size: 0.92rem;
}
.mp-price-hook {
    margin: 0.15rem 0 0.9rem 0;
    color: #C8F542;
    font-family: Inter, -apple-system, sans-serif;
    font-size: 0.9rem;
    font-weight: 650;
    letter-spacing: -0.01em;
    line-height: 1.35;
}
.mp-price-lead b {
    color: #FFFFFF;
    font-weight: 650;
}
.mp-price-compare {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 0.75rem;
    margin: 0.35rem 0 0.2rem 0;
}
.mp-price-compare div {
    padding: 0.75rem 0.85rem;
    border-radius: 14px;
    border: 1px solid rgba(255, 255, 255, 0.08);
    background: rgba(255, 255, 255, 0.03);
}
.mp-price-compare span {
    display: block;
    margin-bottom: 0.2rem;
    color: #94A3B8;
    font-family: "JetBrains Mono", monospace;
    font-size: 0.64rem;
    font-weight: 700;
    letter-spacing: 0.07em;
    text-transform: uppercase;
}
.mp-price-compare strong {
    color: #FFFFFF;
    font-size: 0.95rem;
    font-weight: 650;
    letter-spacing: -0.02em;
}
.mp-price-compare .is-deal strong {
    color: #C8F542;
}
div.st-key-price_lifetime {
    border-color: rgba(200, 245, 66, 0.55) !important;
    box-shadow: 0 18px 44px rgba(0, 0, 0, 0.32) !important;
}
div.st-key-price_lifetime::before {
    content: "";
    position: absolute;
    left: 1rem;
    right: 1rem;
    top: 0;
    height: 2px;
    border-radius: 999px;
    background: #C8F542;
}
div.st-key-price_month .mp-plans-gets,
div.st-key-price_year .mp-plans-gets,
div.st-key-price_lifetime .mp-plans-gets {
    flex: 1 1 auto;
    margin: 0 0 1rem 0;
    padding: 0;
    border: 0;
    background: transparent;
}
div.st-key-price_month .mp-plans-gets li,
div.st-key-price_year .mp-plans-gets li,
div.st-key-price_lifetime .mp-plans-gets li {
    margin: 0.32rem 0;
}
div.st-key-price_hit_month,
div.st-key-price_hit_year,
div.st-key-price_hit_lifetime {
    position: absolute !important;
    inset: 0 !important;
    z-index: 1;
    margin: 0 !important;
    padding: 0 !important;
}
div.st-key-price_hit_month button,
div.st-key-price_hit_year button,
div.st-key-price_hit_lifetime button {
    width: 100% !important;
    height: 100% !important;
    min-height: 100% !important;
    padding: 0 !important;
    opacity: 0 !important;
    background: transparent !important;
    border: 0 !important;
    box-shadow: none !important;
}
div.st-key-price_buy_month,
div.st-key-price_buy_year,
div.st-key-price_buy_lifetime {
    position: relative;
    z-index: 3;
    margin-top: auto;
    width: 100% !important;
}
div.st-key-price_buy_month button,
div.st-key-price_buy_year button,
div.st-key-price_buy_lifetime button {
    width: 100% !important;
    min-height: 48px !important;
    transition: transform 150ms ease-in-out, background 200ms ease-in-out, color 200ms ease-in-out !important;
}
div.st-key-price_buy_month button:hover,
div.st-key-price_buy_year button:hover,
div.st-key-price_buy_lifetime button:hover {
    transform: translateY(-2px) !important;
}
div.st-key-price_buy_month button:active,
div.st-key-price_buy_year button:active,
div.st-key-price_buy_lifetime button:active {
    transform: scale(0.97) !important;
    transition-duration: 150ms !important;
}
.mp-spin {
    width: 0.9rem;
    height: 0.9rem;
    margin-right: 0.45rem;
    border: 2px solid currentColor;
    border-right-color: transparent;
    border-radius: 50%;
    display: inline-block;
    vertical-align: -2px;
    animation: mpSpin 700ms linear infinite;
}
@keyframes mpSpin {
    to { transform: rotate(360deg); }
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
    background: #C8F542 !important;
    color: #0B0F08 !important;
    border: none !important;
    border-radius: 10px !important;
    font-weight: 700 !important;
    box-shadow: none !important;
}
div[data-testid="stDialog"] button p,
div[data-testid="stDialog"] .stFormSubmitButton button p {
    color: #0B0F08 !important;
}
div[data-testid="stDialog"] [data-testid="stTextInputRootElement"] button,
div[data-testid="stDialog"] [data-testid="stTextInputRootElement"] button p {
    background: transparent !important;
    color: #94A3B8 !important;
    width: 2.25rem !important;
    min-width: 2.25rem !important;
    min-height: 2.25rem !important;
    height: 2.25rem !important;
    padding: 0 !important;
    border: 0 !important;
    border-radius: 8px !important;
    box-shadow: none !important;
    font-size: 0 !important;
}
div[data-testid="stDialog"] div[class*="st-key-plan_"] button {
    width: auto !important;
    min-height: 2.35rem !important;
    padding: 0.3rem 1rem !important;
    border-radius: 999px !important;
}
div[data-testid="stDialog"] div.st-key-plan_lifetime button,
div[data-testid="stDialog"] div.st-key-plan_founding button,
div[data-testid="stDialog"] div.st-key-plan_month button {
    background: transparent !important;
    border: 1px solid rgba(255, 255, 255, 0.22) !important;
}
div[data-testid="stDialog"] div.st-key-plan_lifetime button p,
div[data-testid="stDialog"] div.st-key-plan_founding button p,
div[data-testid="stDialog"] div.st-key-plan_month button p {
    color: #FFFFFF !important;
}
div[data-testid="stDialog"]:has(.mp-war-pay) > div:first-child {
    backdrop-filter: blur(10px);
    -webkit-backdrop-filter: blur(10px);
}
div[data-testid="stDialog"]:has(.mp-war-pay).mp-enter {
    animation: none;
}
div[data-testid="stDialog"]:has(.mp-war-pay) [role="dialog"] {
    border: 1px solid rgba(200, 245, 66, 0.28);
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.12), 0 0 22px rgba(200, 245, 66, 0.08);
    animation: mpWarPayIn 220ms ease-out;
}
@keyframes mpWarPayIn {
    from { transform: scale(0.97); opacity: 0; }
    to { transform: scale(1); opacity: 1; }
}
div[data-testid="stDialog"]:has(.mp-war-pay) [role="dialog"] > div:first-child {
    flex-direction: column;
    align-items: flex-start;
    gap: 0.45rem;
}
div[data-testid="stDialog"]:has(.mp-war-pay) [role="dialog"] > div:first-child::before {
    content: "●  PRO MODULE";
    display: inline-flex;
    align-items: center;
    padding: 0.16rem 0.48rem;
    border-radius: 999px;
    border: 1px solid rgba(200, 245, 66, 0.35);
    background: rgba(200, 245, 66, 0.1);
    color: #C8F542;
    font-size: 0.62rem;
    font-weight: 650;
    letter-spacing: 0.12em;
    line-height: 1.4;
}
.mp-war-pay p { margin: 0 0 0.7rem; color: #CBD5E1; font-size: 0.95rem; line-height: 1.45; }
.mp-war-gets {
    list-style: none;
    margin: 0 0 0.85rem;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 0.42rem;
}
.mp-war-gets li {
    display: flex;
    align-items: flex-start;
    gap: 0.5rem;
    color: #E2E8F0;
    font-size: 0.92rem;
    line-height: 1.35;
}
.mp-war-gets li::before {
    content: "";
    width: 0.85rem;
    height: 0.85rem;
    margin-top: 0.12rem;
    flex: 0 0 auto;
    background: #C8F542;
    -webkit-mask: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'><path fill='black' d='M9.55 17.6 4.4 12.45l1.45-1.45 3.7 3.7 8.6-8.6 1.45 1.45z'/></svg>") center / contain no-repeat;
    mask: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'><path fill='black' d='M9.55 17.6 4.4 12.45l1.45-1.45 3.7 3.7 8.6-8.6 1.45 1.45z'/></svg>") center / contain no-repeat;
}
.mp-war-price {
    margin: 0.15rem 0 0.4rem;
    padding: 0.55rem 0.75rem;
    border-radius: 10px;
    text-align: center;
    color: #F8FAFC;
    background: rgba(255, 255, 255, 0.04);
    border: 1px solid rgba(255, 255, 255, 0.08);
    font-family: "JetBrains Mono", monospace;
    font-size: 0.84rem;
    letter-spacing: 0.01em;
}
.mp-war-fine {
    margin: 0 0 0.85rem !important;
    color: #64748B !important;
    font-size: 0.75rem !important;
}
div[data-testid="stDialog"]:has(.mp-war-pay) button[aria-label="Close"] {
    width: 1.75rem !important;
    height: 1.75rem !important;
    min-width: 1.75rem !important;
    min-height: 1.75rem !important;
    padding: 0 !important;
    border-radius: 999px !important;
    background: rgba(255, 255, 255, 0.04) !important;
    color: #64748B !important;
    border: 1px solid rgba(255, 255, 255, 0.1) !important;
    box-shadow: none !important;
}
div[data-testid="stDialog"]:has(.mp-war-pay) button[aria-label="Close"]:hover {
    background: rgba(255, 255, 255, 0.08) !important;
    color: #94A3B8 !important;
}
div[data-testid="stDialog"]:has(.mp-war-pay) button[aria-label="Close"] svg {
    fill: currentColor;
    width: 0.85rem;
    height: 0.85rem;
}
div[data-testid="stDialog"]:has(.mp-war-pay) div.st-key-war_lock_stay button {
    background: transparent !important;
    color: #94A3B8 !important;
    border: none !important;
    box-shadow: none !important;
    font-weight: 500 !important;
}
div[data-testid="stDialog"]:has(.mp-war-pay) div.st-key-war_lock_stay button p {
    color: #94A3B8 !important;
    font-weight: 500 !important;
}
div[data-testid="stDialog"]:has(.mp-war-pay) div.st-key-war_lock_stay button:hover {
    background: transparent !important;
    color: #CBD5E1 !important;
}
div[data-testid="stDialog"]:has(.mp-war-pay) div.st-key-war_lock_stay button:hover p {
    color: #CBD5E1 !important;
}
div[data-testid="stDialog"]:has(.mp-war-pay) div.st-key-war_lock_plans button {
    transition: transform 150ms ease-out !important;
}
div[data-testid="stDialog"]:has(.mp-war-pay) div.st-key-war_lock_plans button:hover {
    transform: translateY(-2px) !important;
}
div[data-testid="stDialog"]:has(.mp-war-pay) div.st-key-war_lock_plans button:active,
div[data-testid="stDialog"]:has(.mp-war-pay) div.st-key-war_lock_plans button:hover:active {
    transform: scale(0.97) !important;
    transition: transform 150ms ease-out !important;
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
    padding: 12px 24px !important;
    border: none !important;
    box-shadow: none !important;
    transition: background 200ms ease-in-out, color 200ms ease-in-out,
                box-shadow 200ms ease-in-out, border-color 200ms ease-in-out,
                transform 150ms ease-out !important;
}
div.st-key-main_tab button:hover {
    color: #E2E8F0 !important;
    background: rgba(255, 255, 255, 0.05) !important;
    cursor: pointer;
    transform: none !important;
}
div.st-key-main_tab button[aria-selected="true"],
div.st-key-main_tab button[aria-checked="true"],
div.st-key-main_tab button[aria-pressed="true"],
div.st-key-main_tab button[kind="primary"] {
    background: rgba(200, 245, 66, 0.12) !important;
    color: #FFFFFF !important;
    font-weight: 600 !important;
    box-shadow: 0 0 0 1px rgba(200, 245, 66, 0.55), 0 6px 18px rgba(200, 245, 66, 0.18),
                inset 0 -2px 0 #C8F542 !important;
    border: 1px solid #C8F542 !important;
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
    background: rgba(200, 245, 66, 0.12) !important;
    border: 1px solid #C8F542 !important;
    color: #FFFFFF !important;
    box-shadow: 0 0 0 1px rgba(200, 245, 66, 0.45), 0 6px 18px rgba(200, 245, 66, 0.16) !important;
}
div.st-key-main_tab [data-testid="stButtonGroup"] button,
div.st-key-main_tab [data-testid="stPills"] button {
    background: transparent !important;
    color: #6B7A90 !important;
    border-radius: 10px !important;
    font-weight: 500 !important;
    font-size: 0.9rem !important;
    padding: 12px 24px !important;
    border: none !important;
    box-shadow: none !important;
    transition: background 200ms ease-in-out, color 200ms ease-in-out,
                box-shadow 200ms ease-in-out, border-color 200ms ease-in-out,
                transform 150ms ease-out !important;
}
div.st-key-main_tab [data-testid="stButtonGroup"] button:hover,
div.st-key-main_tab [data-testid="stPills"] button:hover {
    color: #E2E8F0 !important;
    background: rgba(255, 255, 255, 0.05) !important;
    cursor: pointer;
    transform: none !important;
}
div.st-key-main_tab button:active,
div.st-key-main_tab button:hover:active,
div.st-key-main_tab [data-testid="stButtonGroup"] button:active,
div.st-key-main_tab [data-testid="stPills"] button:active,
div.st-key-main_tab [data-testid="stPills"] button:hover:active,
div.st-key-main_tab button.mp-sink,
div.st-key-main_tab button.mp-sink:hover,
div.st-key-main_tab [data-testid="stPills"] button.mp-sink,
div.st-key-main_tab [data-testid="stPills"] button.mp-sink:hover {
    transform: scale(0.97) !important;
    transition: transform 150ms ease-out !important;
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
    background: rgba(200, 245, 66, 0.12) !important;
    color: #FFFFFF !important;
    font-weight: 600 !important;
    box-shadow: 0 0 0 1px rgba(200, 245, 66, 0.55), 0 6px 18px rgba(200, 245, 66, 0.18),
                inset 0 -2px 0 #C8F542 !important;
    border: 1px solid #C8F542 !important;
}
div.st-key-main_tab button[aria-checked="true"] p,
div.st-key-main_tab button[kind="pillsActive"] p,
div.st-key-main_tab button[data-testid="stBaseButton-pillsActive"] p {
    color: #FFFFFF !important;
}
[data-testid="stElementContainer"]:has(.mp-war-lock) {
    display: none !important;
    height: 0 !important;
    margin: 0 !important;
}
.stApp:has(.mp-war-lock) div.st-key-main_tab [data-testid="stPills"] button:last-child,
.stApp:has(.mp-war-lock) div.st-key-main_tab [data-testid="stButtonGroup"] button:last-child {
    opacity: 0.4 !important;
    transition: opacity 180ms ease-out, background 200ms ease-in-out, color 200ms ease-in-out,
                box-shadow 200ms ease-in-out, border-color 200ms ease-in-out,
                transform 150ms ease-out !important;
}
.stApp:has(.mp-war-lock) div.st-key-main_tab [data-testid="stPills"] button:last-child:hover,
.stApp:has(.mp-war-lock) div.st-key-main_tab [data-testid="stButtonGroup"] button:last-child:hover {
    opacity: 1 !important;
    color: #E2E8F0 !important;
    background: rgba(255, 255, 255, 0.05) !important;
}
.stApp:has(.mp-war-lock) div.st-key-main_tab [data-testid="stPills"] button:last-child p,
.stApp:has(.mp-war-lock) div.st-key-main_tab [data-testid="stButtonGroup"] button:last-child p {
    display: inline-flex !important;
    align-items: center;
    gap: 0.32rem;
}
.stApp:has(.mp-war-lock) div.st-key-main_tab [data-testid="stPills"] button:last-child p::after,
.stApp:has(.mp-war-lock) div.st-key-main_tab [data-testid="stButtonGroup"] button:last-child p::after {
    content: "";
    width: 0.72rem;
    height: 0.72rem;
    flex: 0 0 auto;
    background-color: currentColor;
    -webkit-mask: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'><path fill='black' d='M12 2a5 5 0 0 0-5 5v3H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8a2 2 0 0 0-2-2h-1V7a5 5 0 0 0-5-5zm-3 8V7a3 3 0 0 1 6 0v3H9z'/></svg>") center / contain no-repeat;
    mask: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'><path fill='black' d='M12 2a5 5 0 0 0-5 5v3H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8a2 2 0 0 0-2-2h-1V7a5 5 0 0 0-5-5zm-3 8V7a3 3 0 0 1 6 0v3H9z'/></svg>") center / contain no-repeat;
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
.oem-grid {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 0.7rem;
    margin: 0.35rem 0 1.1rem 0;
}
.oem-card {
    padding: 0.9rem 0.95rem 0.85rem 0.95rem;
    min-height: 168px;
}
.oem-badge {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: 2.4rem;
    padding: 0.18rem 0.45rem;
    border-radius: 8px;
    background: rgba(59, 91, 255, 0.16);
    border: 1px solid rgba(59, 91, 255, 0.35);
    color: #E2E8F0;
    font-family: "JetBrains Mono", monospace;
    font-size: 0.68rem;
    font-weight: 800;
    letter-spacing: 0.06em;
}
.oem-card h4 {
    margin: 0.45rem 0 0.12rem 0;
    font-size: 0.92rem;
    font-weight: 800;
    color: #FFFFFF;
}
.oem-brands {
    margin: 0 0 0.45rem 0;
    color: #94A3B8;
    font-size: 0.72rem;
    font-weight: 700;
}
.oem-figure {
    margin: 0.45rem 0 0.1rem 0;
    color: #F8FAFC;
    font-family: "JetBrains Mono", monospace;
    font-size: 1.35rem;
    font-weight: 700;
    letter-spacing: -0.03em;
}
.oem-lease {
    margin: 0.2rem 0 0.15rem 0;
    color: #E2E8F0;
    font-family: "Inter", sans-serif;
    font-size: 0.78rem;
    font-weight: 650;
    line-height: 1.35;
}
.oem-source {
    margin: 0.35rem 0 0 0;
    color: #64748B;
    font-size: 0.72rem;
    font-weight: 600;
    line-height: 1.35;
}
.oem-card p {
    margin: 0.4rem 0 0 0;
    color: #6B7A90;
    font-size: 0.8rem;
    font-weight: 600;
    line-height: 1.4;
}
.issuer-grid {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 0.7rem;
    margin: 0.35rem 0 1.1rem 0;
}
@media (max-width: 1100px) and (min-width: 768px) {
    .oem-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .issuer-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
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
    background: #C8F542;
    box-shadow: 0 0 10px rgba(200, 245, 66, 0.55);
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
    color: #C8F542;
    background: rgba(200, 245, 66, 0.12);
    border: 1px solid rgba(200, 245, 66, 0.28);
    box-shadow: 0 0 12px rgba(200, 245, 66, 0.16);
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
.metric-note, .sahm-hint {
    color: #6B7A90;
    font-size: 0.68rem;
    line-height: 1.35;
    margin: -0.35rem 0.05rem 0.7rem 0.05rem;
}
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
@media (max-width: 1100px) and (min-width: 768px) {
    .major-actions-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 900px) and (min-width: 768px) {
    .coping-grid { grid-template-columns: 1fr; }
    .major-actions-grid { grid-template-columns: 1fr; }
}
.sim-banner {
    display: inline-block;
    margin: 0 0 0.75rem 0;
    padding: 0.32rem 0.7rem;
    border-radius: 999px;
    background: rgba(255, 255, 255, 0.04);
    border: 1px solid rgba(255, 255, 255, 0.14);
    color: #E2E8F0;
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
    background-color: #07090E !important;
    background-image: none !important;
    border-right: 1px solid rgba(255, 255, 255, 0.06);
    display: flex !important;
    flex-direction: column !important;
    position: relative !important;
    z-index: 1 !important;
    top: auto !important;
    left: auto !important;
    height: 100vh !important;
    max-height: 100vh !important;
    overflow: hidden !important;
    transition: none !important;
}
[data-testid="stAppViewContainer"] > section[data-testid="stSidebar"] + div {
    transition: none !important;
}
[data-testid="stSidebarContent"] {
    display: flex !important;
    flex-direction: column !important;
    flex: 1 1 auto !important;
    height: 100% !important;
    max-height: 100vh !important;
    min-height: 0 !important;
    overflow: hidden !important;
    background: transparent !important;
}
section[data-testid="stSidebar"] > div:not([data-testid="stSidebarContent"]) {
    flex: 0 0 auto !important;
    height: 0 !important;
    overflow: visible !important;
    background: transparent !important;
}
[data-testid="stSidebarUserContent"] {
    flex: 1 1 auto !important;
    min-height: 0 !important;
    overflow-x: hidden !important;
    overflow-y: auto !important;
    overscroll-behavior: contain;
    background: transparent !important;
}
[data-testid="stSidebarHeader"],
[data-testid="stSidebarHeader"] * {
    background: transparent !important;
}
[data-testid="stSidebarHeader"] {
    flex: 0 0 auto !important;
    border-bottom: 1px solid rgba(255, 255, 255, 0.06) !important;
}
section[data-testid="stSidebar"] [data-testid="stSlider"] {
    background: rgba(15, 20, 32, 0.62) !important;
    border: 1px solid rgba(255, 255, 255, 0.07) !important;
    border-radius: 14px !important;
    padding: 0.7rem 0.85rem 0.45rem 0.85rem !important;
    margin: 0.35rem 0 0.55rem 0 !important;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.28) !important;
    transition: transform 160ms ease-in-out, border-color 160ms ease-in-out,
                box-shadow 160ms ease-in-out !important;
}
section[data-testid="stSidebar"] [data-testid="stSlider"]:hover,
section[data-testid="stSidebar"] [data-testid="stSlider"]:focus-within {
    transform: translateY(-2px) !important;
    border-color: rgba(200, 245, 66, 0.55) !important;
    box-shadow: 0 12px 28px rgba(0, 0, 0, 0.34) !important;
}
section[data-testid="stSidebar"] [data-testid="stSlider"] label p,
section[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p,
section[data-testid="stSidebar"] [data-testid="stCheckbox"] label p,
section[data-testid="stSidebar"] [data-testid="stToggle"] label p {
    color: #FFFFFF !important;
    font-family: Inter, -apple-system, sans-serif !important;
    font-size: 0.86rem !important;
    font-weight: 600 !important;
    letter-spacing: -0.01em !important;
    text-transform: none !important;
}
section[data-testid="stSidebar"] [data-testid="stSlider"] [data-testid="stTickBarMin"],
section[data-testid="stSidebar"] [data-testid="stSlider"] [data-testid="stTickBarMax"],
section[data-testid="stSidebar"] [data-testid="stSlider"] p {
    font-weight: 700 !important;
    font-family: "JetBrains Mono", monospace !important;
}
section[data-testid="stSidebar"] [data-testid="stCheckbox"],
section[data-testid="stSidebar"] [data-testid="stToggle"] {
    background: rgba(15, 20, 32, 0.62) !important;
    border: 1px solid rgba(255, 255, 255, 0.07) !important;
    border-radius: 14px !important;
    padding: 0.55rem 0.8rem !important;
    margin: 0 0 0.7rem 0 !important;
}
section[data-testid="stSidebar"] [data-baseweb="checkbox"] > div {
    background-color: transparent !important;
    border-color: transparent !important;
}
section[data-testid="stSidebar"] [data-baseweb="checkbox"] > div:first-child {
    background-color: rgba(255, 255, 255, 0.16) !important;
}
section[data-testid="stSidebar"] [data-baseweb="checkbox"]:has(input:checked) > div:first-child {
    background-color: #38BDF8 !important;
    background-image: linear-gradient(90deg, #1d4ed8, #38bdf8) !important;
    box-shadow: 0 0 10px rgba(56, 189, 248, 0.55) !important;
}
section[data-testid="stSidebar"] [data-baseweb="checkbox"] > div:first-child > div {
    background-color: #E2E8F0 !important;
}
section[data-testid="stSidebar"] [data-baseweb="checkbox"]:has(input:checked) > div:first-child > div {
    background-color: #F8FAFC !important;
}
section[data-testid="stSidebar"] .st-key-sim_reset_live .stButton > button:not([kind="primary"]) {
    border-color: rgba(255, 255, 255, 0.14) !important;
    color: #E2E8F0 !important;
    font-weight: 600 !important;
    box-shadow: none !important;
}
section[data-testid="stSidebar"] .st-key-sim_reset_live .stButton > button,
section[data-testid="stSidebar"] .st-key-sim_crisis_2008 .stButton > button,
section[data-testid="stSidebar"] .st-key-sim_soft_landing .stButton > button,
section[data-testid="stSidebar"] .st-key-sim_chaos_roll .stButton > button {
    transition: transform 150ms ease-out, background 150ms ease-out,
                border-color 150ms ease-out, color 150ms ease-out,
                box-shadow 150ms ease-out !important;
}
section[data-testid="stSidebar"] .st-key-sim_reset_live button:hover,
section[data-testid="stSidebar"] .st-key-sim_soft_landing button:hover,
section[data-testid="stSidebar"] .st-key-sim_chaos_roll button:hover {
    transform: translateY(-2px) !important;
    background: rgba(255, 255, 255, 0.04) !important;
    color: #FFFFFF !important;
    border-color: rgba(255, 255, 255, 0.22) !important;
    box-shadow: none !important;
}
section[data-testid="stSidebar"] .st-key-sim_reset_live button:active,
section[data-testid="stSidebar"] .st-key-sim_crisis_2008 button:active,
section[data-testid="stSidebar"] .st-key-sim_soft_landing button:active,
section[data-testid="stSidebar"] .st-key-sim_chaos_roll button:active {
    transform: scale(0.97) !important;
    transition: transform 150ms ease-out !important;
}
section[data-testid="stSidebar"] .st-key-sim_crisis_2008 .stButton > button:not([kind="primary"]) {
    background: linear-gradient(90deg, #1d4ed8, #38bdf8) !important;
    border: 0 !important;
    color: #F8FAFC !important;
    font-weight: 700 !important;
    border-radius: 999px !important;
    box-shadow: 0 0 12px rgba(56, 189, 248, 0.4) !important;
}
section[data-testid="stSidebar"] .st-key-sim_crisis_2008 .stButton > button:not([kind="primary"]):hover,
section[data-testid="stSidebar"] .st-key-sim_crisis_2008 .stButton > button:not([kind="primary"]):focus {
    transform: translateY(-2px) !important;
    transition: transform 160ms ease-in-out, background 160ms ease-in-out !important;
    background: linear-gradient(90deg, #2563eb, #7dd3fc) !important;
    color: #F8FAFC !important;
    border-color: transparent !important;
    box-shadow: 0 0 14px rgba(56, 189, 248, 0.55) !important;
}
section[data-testid="stSidebar"] .st-key-sim_crisis_2008 button p {
    color: #F8FAFC !important;
}
section[data-testid="stSidebar"] [data-testid="stSlider"] [data-baseweb="slider"] > div:first-child {
    background: transparent !important;
}
section[data-testid="stSidebar"] [data-testid="stSlider"] [data-baseweb="slider"] > div:first-child > div {
    height: 6px !important;
    border-radius: 999px !important;
    background-color: #12151c !important;
    background-image: linear-gradient(90deg, rgba(148, 163, 184, 0.28) 0%, rgba(203, 213, 225, 0.55) var(--mp-fill, 0%), #12151c var(--mp-fill, 0%)) !important;
    box-shadow: inset 0 1px 2px rgba(0, 0, 0, 0.65) !important;
}
section[data-testid="stSidebar"] [data-testid="stSlider"] [role="slider"],
section[data-testid="stSidebar"] [data-testid="stSlider"] [data-baseweb="thumb"] {
    background-color: transparent !important;
    border-color: transparent !important;
    box-shadow: none !important;
    transition: none !important;
}
section[data-testid="stSidebar"] [data-testid="stSliderThumbValue"] {
    color: #94A3B8 !important;
    font-family: "JetBrains Mono", ui-monospace, monospace !important;
    font-size: 0.72rem !important;
    font-weight: 600 !important;
    font-variant-numeric: tabular-nums;
}
section[data-testid="stSidebar"] [data-testid="stSliderTickBar"] {
    display: none !important;
}
section[data-testid="stSidebar"]:has([data-testid="stCheckbox"] input:checked) [data-testid="stSlider"] [data-baseweb="slider"] > div:first-child > div {
    background-color: #10141c !important;
    background-image: linear-gradient(90deg, #1d4ed8 0%, #38bdf8 var(--mp-fill, 0%), #10141c var(--mp-fill, 0%)) !important;
    box-shadow: 0 0 10px rgba(56, 189, 248, 0.35), inset 0 1px 1px rgba(0, 0, 0, 0.45) !important;
}
section[data-testid="stSidebar"]:has([data-testid="stCheckbox"] input:checked) [data-testid="stSlider"] [role="slider"],
section[data-testid="stSidebar"]:has([data-testid="stCheckbox"] input:checked) [data-testid="stSlider"] [data-baseweb="thumb"] {
    background-color: transparent !important;
    border-color: transparent !important;
    box-shadow: 0 0 8px 1px rgba(56, 189, 248, 0.85) !important;
}
section[data-testid="stSidebar"]:has([data-testid="stCheckbox"] input:checked) [data-testid="stSliderThumbValue"] {
    color: #7DD3FC !important;
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
:root { interpolate-size: allow-keywords; }
.theme-switching,
.theme-switching * { transition: none !important; }
@keyframes mpEnter {
    from { opacity: 0; transform: scale(0.97); }
    to { opacity: 1; transform: scale(1); }
}
.mp-enter { animation: mpEnter 180ms ease-out; }
@keyframes mpSectionOut {
    from { opacity: 1; }
    to { opacity: 0; }
}
@keyframes mpSectionIn {
    from { opacity: 0; }
    to { opacity: 1; }
}
.mp-section-out { animation: mpSectionOut 420ms ease-in forwards; }
.mp-section-in { animation: mpSectionIn 420ms cubic-bezier(0.22, 0.61, 0.36, 1) both; }
.mp-section-veil {
    position: fixed;
    z-index: 30;
    background: #07090E;
    opacity: 0;
    pointer-events: none;
    transition: opacity 200ms ease-out;
}
.mp-section-veil.is-on { opacity: 1; }
.mp-section-veil.is-out { opacity: 0; transition-duration: 320ms; transition-timing-function: cubic-bezier(0.22, 0.61, 0.36, 1); }
.mp-page-veil {
    position: fixed;
    inset: 0;
    z-index: 80;
    background: #07090E;
    opacity: 0;
    pointer-events: none;
    transition: opacity 320ms ease-out;
}
.mp-page-veil.is-on { opacity: 1; }
.mp-page-veil.is-out {
    opacity: 0;
    transition-duration: 560ms;
    transition-timing-function: cubic-bezier(0.22, 0.61, 0.36, 1);
}
@keyframes inputErrorShake {
    0% { transform: translateX(0); border-color: #DC2626; }
    16% { transform: translateX(-10px); border-color: #DC2626; }
    33% { transform: translateX(10px); border-color: #DC2626; }
    50% { transform: translateX(-6px); border-color: #DC2626; }
    66% { transform: translateX(6px); border-color: #DC2626; }
    83% { transform: translateX(-3px); border-color: #DC2626; }
    100% { transform: translateX(0); }
}
.input-error-shake {
    animation: inputErrorShake 360ms ease-out;
}
button,
[role="tab"],
div.st-key-main_tab button {
    transition: transform 150ms ease-out;
}
button:active,
[role="tab"]:active,
div.st-key-main_tab button:active {
    transform: scale(0.97) !important;
    transition: transform 150ms ease-out !important;
}
[data-testid="stExpander"] details,
[data-testid="stExpanderDetails"] {
    interpolate-size: allow-keywords;
    transition: height 220ms ease-out;
}
[data-testid="stTooltipContent"],
[role="tooltip"] {
    transition: opacity 80ms ease-out;
    transition-delay: 300ms;
}
body.mp-tip-session [data-testid="stTooltipContent"],
body.mp-tip-session [role="tooltip"] {
    transition-delay: 0ms;
}
@media (prefers-reduced-motion: reduce) {
    .mp-enter, .mp-section-out, .mp-section-in,
    .input-error-shake, .flag-pill.is-in, .flag-pill.is-out {
        animation: none !important;
    }
}

@media (max-width: 767px) {
    html {
        font-size: calc(16px * 100vw / 800px);
    }
    [data-testid="stHorizontalBlock"] {
        flex-wrap: nowrap !important;
    }
    [data-testid="stHorizontalBlock"] > [data-testid="stColumn"],
    [data-testid="stHorizontalBlock"] > [data-testid="column"] {
        min-width: 0 !important;
    }
    .lp-jobs-frame { container-type: inline-size; }
    .lp-jobs-screen { container-type: normal; }
    .lp-jobs-screen .mp-eco {
        width: 753px;
        height: 355px;
        inset: auto;
        top: 0;
        left: 0;
        transform: scale(calc(58.8cqw / 753px));
        transform-origin: top left;
        container-type: size;
    }
}
/* Data page, phones only. A real phone keeps a 1280 layout viewport, so this
   class (set from screen width) is what reaches it. Desktop never has it. */
html.mp-phone {
    color-scheme: dark;
    overflow-x: hidden !important;
    overscroll-behavior-x: none;
    touch-action: pan-y;
}
html.mp-phone body,
html.mp-phone .stApp,
html.mp-phone [data-testid="stAppViewContainer"],
html.mp-phone [data-testid="stMain"] {
    overflow-x: hidden !important;
    max-width: 100% !important;
    overscroll-behavior-x: none;
    touch-action: pan-y;
}
html.mp-phone [data-testid="stHorizontalBlock"] { flex-wrap: nowrap !important; }
html.mp-phone .lp-hero-copy h1 { font-size: clamp(3.1rem, 9.25vw, 6.4rem); }
html.mp-phone .lp-jobs-copy h2,
html.mp-phone .lp-hh-copy h2,
html.mp-phone .lp-if-copy h2 { font-size: clamp(2.1rem, 5vw, 3.6rem); }
html.mp-phone .lp-band h2 { font-size: clamp(2.4rem, 6.25vw, 4.4rem); }
html.mp-phone .lp-band-visual { font-size: clamp(1.7rem, 4.25vw, 2.9rem); }
html.mp-phone .lp-band-visual em { font-size: clamp(3.1rem, 7.75vw, 5.4rem); }
html.mp-phone .lp-why h2 { font-size: clamp(2.6rem, 6.25vw, 4rem); }
html.mp-phone .lp-play h2 { font-size: clamp(2.1rem, 5.25vw, 3.35rem); }
html.mp-phone .oem-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); }
html.mp-phone .issuer-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); }
html.mp-phone .coping-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
html.mp-phone .major-actions-grid { grid-template-columns: repeat(5, minmax(0, 1fr)); }
html.mp-phone .brand-lockup .mp-mark {
    width: 1.35rem;
    height: 1.35rem;
}
html.mp-phone .brand-lockup .brand-name {
    font-size: 0.92rem;
    line-height: 1;
}
html.mp-phone .brand-home,
html.mp-phone .brand-lockup {
    gap: 0.4rem;
}
html.mp-phone div.st-key-nav_logo {
    top: 0.2rem !important;
    width: 7.2rem !important;
    height: 1.45rem !important;
}
html.mp-phone div.st-key-nav_logo .stButton {
    width: 100% !important;
    height: 100% !important;
    min-height: 0 !important;
    margin: 0 !important;
    padding: 0 !important;
    line-height: 0 !important;
    font-size: 0 !important;
}
html.mp-phone div.st-key-nav_logo button {
    display: block !important;
    width: 100% !important;
    height: 100% !important;
    min-height: 0 !important;
    margin: 0 !important;
    padding: 0 !important;
    line-height: 1 !important;
    vertical-align: top !important;
}
html.mp-phone body:has(.brand-lockup) [data-testid="stMain"] div.stElementContainer:not(.st-key-nav_logo):not(.st-key-nav_home):not(.st-key-nav_unlock):not(.st-key-nav_profile):not(.st-key-nav_free_preview) .stButton > button,
html.mp-phone body:has(.brand-lockup) [data-testid="stMain"] .stDownloadButton button,
html.mp-phone body:has(.brand-lockup) [data-testid="stSidebar"] .stButton > button,
html.mp-phone body:has(.brand-lockup) [data-testid="stDialog"] .stButton > button,
html.mp-phone body:has(.brand-lockup) div.st-key-main_tab button,
html.mp-phone body:has(.brand-lockup) div.st-key-labor_geo button,
html.mp-phone body:has(.brand-lockup) div.st-key-labor_timeframe button {
    padding: 0.32rem 0.72rem !important;
    font-size: 0.78rem !important;
    line-height: 1.15 !important;
    min-height: 0 !important;
    height: auto !important;
}
html.mp-phone body:has(.brand-lockup) [data-testid="stMain"] .stButton > button p,
html.mp-phone body:has(.brand-lockup) [data-testid="stMain"] .stDownloadButton button p,
html.mp-phone body:has(.brand-lockup) [data-testid="stSidebar"] .stButton > button p,
html.mp-phone body:has(.brand-lockup) [data-testid="stDialog"] .stButton > button p,
html.mp-phone body:has(.brand-lockup) div.st-key-main_tab button p,
html.mp-phone body:has(.brand-lockup) div.st-key-labor_geo button p,
html.mp-phone body:has(.brand-lockup) div.st-key-labor_timeframe button p {
    font-size: inherit !important;
    line-height: 1.15 !important;
    margin: 0 !important;
}
html.mp-phone div.st-key-nav_home {
    width: 1.8rem !important;
    flex: 0 0 1.8rem !important;
}
html.mp-phone div.st-key-nav_home button {
    width: 1.8rem !important;
    min-width: 1.8rem !important;
    height: 1.8rem !important;
    min-height: 1.8rem !important;
    padding: 0 !important;
}
html.mp-phone div.st-key-nav_home button > span {
    width: 1rem !important;
    height: 1rem !important;
    background-size: 0.95rem 0.95rem !important;
}
html.mp-phone div.st-key-nav_unlock button,
html.mp-phone div.st-key-nav_profile button,
html.mp-phone div.st-key-nav_free_preview button {
    min-height: 1.8rem !important;
    height: 1.8rem !important;
    padding: 0 0.72rem !important;
    font-size: 0.78rem !important;
    line-height: 1 !important;
}
html.mp-phone div.st-key-nav_unlock button p,
html.mp-phone div.st-key-nav_profile button p,
html.mp-phone div.st-key-nav_free_preview button p {
    font-size: inherit !important;
    line-height: 1 !important;
    margin: 0 !important;
}
html.mp-phone body:has(.brand-lockup) div.st-key-inline_subscribe,
html.mp-phone body:has(.brand-lockup) div.st-key-inline_subscribe .stButton,
html.mp-phone body:has(.brand-lockup) div.st-key-inline_subscribe button {
    height: auto !important;
    min-height: 0 !important;
}
html.mp-phone body:has(.brand-lockup) div.st-key-main_tab [data-testid="stPills"] > div,
html.mp-phone body:has(.brand-lockup) div.st-key-main_tab [role="radiogroup"],
html.mp-phone body:has(.brand-lockup) div.st-key-main_tab [data-baseweb="button-group"],
html.mp-phone body:has(.brand-lockup) div.st-key-labor_geo [role="radiogroup"],
html.mp-phone body:has(.brand-lockup) div.st-key-labor_geo [data-baseweb="button-group"],
html.mp-phone body:has(.brand-lockup) div.st-key-labor_timeframe [role="radiogroup"],
html.mp-phone body:has(.brand-lockup) div.st-key-labor_timeframe [data-baseweb="button-group"] {
    padding: 0.2rem !important;
    gap: 0.2rem !important;
}
</style>
"""


def _install_ui_motion() -> None:
    """One parent-page watcher for entrances, tab slides, and tooltip warm-up."""
    import streamlit.components.v1 as components

    components.html(
        """
        <script>
        (function () {
          const parent = window.parent;
          if (!parent) return;
          parent.__mpSyncPhone = function () {
            if (parent.__mpPhoneQueued) return;
            parent.__mpPhoneQueued = 1;
            parent.requestAnimationFrame(function () {
              parent.__mpPhoneQueued = 0;
              if (parent.__mpRunPhone) parent.__mpRunPhone();
            });
          };
          parent.__mpRunPhone = function () {
            try {
              let meta = parent.document.querySelector('meta[name="viewport"]');
              const screenW = parent.screen && parent.screen.width;
              const narrowScreen = !!(screenW && screenW < 768);
              const phoneViewport = 'width=800, viewport-fit=cover';
              if (meta && narrowScreen && meta.getAttribute('content') !== phoneViewport) {
                meta.setAttribute('content', phoneViewport);
              }
              let theme = parent.document.querySelector('meta[name="theme-color"]');
              if (!theme && parent.document.head) {
                theme = parent.document.createElement('meta');
                theme.setAttribute('name', 'theme-color');
                parent.document.head.appendChild(theme);
              }
              if (theme && theme.getAttribute('content') !== '#07090E') {
                theme.setAttribute('content', '#07090E');
              }
              const narrowViewport = !!(parent.matchMedia && parent.matchMedia('(max-width: 767px)').matches);
              const phone = narrowScreen || narrowViewport;
              parent.document.documentElement.classList.toggle('mp-phone', phone);
              const brand = parent.document.querySelector('.brand-home');
              const hit = parent.document.querySelector('div.st-key-nav_logo');
              if (!hit) return;
              if (!phone || !brand) {
                ['top', 'left', 'width', 'height'].forEach((prop) => hit.style.removeProperty(prop));
                return;
              }
              const host = hit.offsetParent || hit.parentElement;
              if (!host) return;
              const hb = host.getBoundingClientRect();
              const bb = brand.getBoundingClientRect();
              hit.style.setProperty('top', Math.max(0, bb.top - hb.top) + 'px', 'important');
              hit.style.setProperty('left', Math.max(0, bb.left - hb.left) + 'px', 'important');
              hit.style.setProperty('width', bb.width + 'px', 'important');
              hit.style.setProperty('height', bb.height + 'px', 'important');
              const wrap = hit.querySelector('.stButton');
              if (wrap) {
                wrap.style.setProperty('display', 'block', 'important');
                wrap.style.setProperty('height', '100%', 'important');
                wrap.style.setProperty('min-height', '0', 'important');
                wrap.style.setProperty('line-height', '0', 'important');
              }
              const btn = hit.querySelector('button');
              if (btn) {
                btn.style.setProperty('display', 'block', 'important');
                btn.style.setProperty('width', '100%', 'important');
                btn.style.setProperty('height', '100%', 'important');
                btn.style.setProperty('min-height', '0', 'important');
                btn.style.setProperty('margin', '0', 'important');
              }
            } catch (err) {}
          };
          if (!parent.__mpPhoneWatch) {
            parent.__mpPhoneWatch = 1;
            parent.addEventListener('resize', function () {
              if (parent.__mpSyncPhone) parent.__mpSyncPhone();
            });
            if (parent.document.body) {
              new parent.MutationObserver(function () {
                if (parent.__mpSyncPhone) parent.__mpSyncPhone();
                if (parent.__mpSyncSliders) parent.__mpSyncSliders();
              }).observe(parent.document.body, {childList: true, subtree: true});
            }
          }
          parent.__mpSyncSliders = function () {
            if (parent.__mpSliderQueued) return;
            parent.__mpSliderQueued = 1;
            parent.requestAnimationFrame(function () {
              parent.__mpSliderQueued = 0;
              if (parent.__mpRunSliders) parent.__mpRunSliders();
            });
          };
          parent.__mpRunSliders = function () {
            try {
              parent.document.querySelectorAll('section[data-testid="stSidebar"] [role="slider"]').forEach(function (thumb) {
                const track = thumb.parentElement;
                if (!track) return;
                const min = Number(thumb.getAttribute('aria-valuemin'));
                const max = Number(thumb.getAttribute('aria-valuemax'));
                const now = Number(thumb.getAttribute('aria-valuenow'));
                const span = max - min;
                const pct = span ? Math.min(100, Math.max(0, ((now - min) / span) * 100)) : 0;
                track.style.setProperty('--mp-fill', pct.toFixed(2) + '%');
                const val = thumb.querySelector('[data-testid="stSliderThumbValue"]');
                if (!val) return;
                const tr = track.getBoundingClientRect();
                const vr = val.getBoundingClientRect();
                if (!tr.width || !vr.width) return;
                const prev = Number(val.dataset.mpShift || 0);
                const shift = prev + (tr.right - 2 - vr.right);
                val.dataset.mpShift = String(shift);
                val.style.setProperty('transform', 'translateX(' + shift.toFixed(2) + 'px)', 'important');
              });
            } catch (err) {}
          };
          if (!parent.__mpSliderWatch && parent.document.body) {
            parent.__mpSliderWatch = 1;
            new parent.MutationObserver(function () {
              if (parent.__mpSyncSliders) parent.__mpSyncSliders();
            }).observe(parent.document.body, {subtree: true, attributes: true, attributeFilter: ['aria-valuenow']});
          }
          parent.__mpSyncSliders();
          parent.__mpSyncPhone();
          if (parent.__mpUiMotion === 10) return;
          parent.__mpUiMotion = 10;
          const doc = parent.document;
          const entered = parent.__mpEnteredIds || (parent.__mpEnteredIds = {});
          const goneAt = {};
          function arm(el, id) {
            if (!el) return;
            const away = goneAt[id] ? Date.now() - goneAt[id] : 0;
            if (away > 400) delete entered[id];
            goneAt[id] = 0;
            if (el.dataset.mpEntered === "1") return;
            if (entered[id]) {
              el.dataset.mpEntered = "1";
              return;
            }
            entered[id] = Date.now();
            el.dataset.mpEntered = "1";
            el.classList.add("mp-enter");
          }
          function markGone(id, selector) {
            if (doc.querySelector(selector)) return;
            if (!goneAt[id]) goneAt[id] = Date.now();
          }
          function scan() {
            doc.querySelectorAll('[data-testid="stDialog"]').forEach((el) => {
              arm(el, "dialog:" + (el.getAttribute("aria-label") || "open"));
            });
            doc.querySelectorAll('[data-testid="stPopover"]').forEach((el) => {
              arm(el, "popover:" + (el.innerText || "open").slice(0, 40));
            });
            doc.querySelectorAll(".st-key-price_month, .st-key-price_year, .st-key-price_lifetime, .mp-price-card").forEach((el) => {
              const name = el.className || "";
              const id = name.indexOf("price_month") >= 0 ? "price-month"
                : name.indexOf("price_year") >= 0 ? "price-year"
                : name.indexOf("price_lifetime") >= 0 ? "price-lifetime"
                : "price-card";
              arm(el, id);
            });
            markGone("price-month", ".st-key-price_month");
            markGone("price-year", ".st-key-price_year");
            markGone("price-lifetime", ".st-key-price_lifetime");
            if (!doc.querySelector('[data-testid="stDialog"]')) {
              Object.keys(entered).forEach((key) => {
                if (key.indexOf("dialog:") === 0 && !goneAt[key]) goneAt[key] = Date.now();
              });
            }
            if (!doc.querySelector('[data-testid="stPopover"]')) {
              Object.keys(entered).forEach((key) => {
                if (key.indexOf("popover:") === 0 && !goneAt[key]) goneAt[key] = Date.now();
              });
            }
            doc.querySelectorAll('[data-testid="stAlertContentError"]').forEach((content) => {
              const alert = content.closest('[data-testid="stAlert"]') || content;
              if (alert.dataset.mpShook === "1") return;
              alert.classList.add("input-error-shake");
              alert.dataset.mpShook = "1";
              const box = alert.closest('[data-testid="stDialog"], [data-testid="stForm"]');
              if (!box) return;
              box.querySelectorAll('[data-baseweb="input"]').forEach((input) => {
                input.classList.add("input-error-shake");
              });
            });
            const dashReady = parent.__mpPageEnter && doc.querySelector(".brand-lockup") && !doc.querySelector("div.st-key-lp_enter");
            const homeReady = parent.__mpPageLeave && doc.querySelector("div.st-key-lp_header") && !doc.querySelector(".brand-lockup");
            if (dashReady || homeReady) {
              parent.__mpPageEnter = false;
              parent.__mpPageLeave = false;
              clearTimeout(parent.__mpPageTimer);
              const wait = Math.max(0, 340 - (Date.now() - (parent.__mpPageAt || 0)));
              setTimeout(() => parent.requestAnimationFrame(liftPageVeil), wait);
            }
            const tabs = doc.querySelector("div.st-key-main_tab");
            if (tabs) {
              const buttons = [...tabs.querySelectorAll("button")];
              const idx = buttons.findIndex((button) => (
                button.getAttribute("aria-checked") === "true"
                || button.getAttribute("aria-selected") === "true"
                || button.getAttribute("aria-pressed") === "true"
                || button.getAttribute("kind") === "pillsActive"
                || button.getAttribute("data-testid") === "stBaseButton-pillsActive"
              ));
              if (idx >= 0) parent.__mpTabIndex = idx;
              if (!parent.__mpSectionPending) return;
              const sig = sectionSig();
              if (!sig || sig === parent.__mpHeldSig) return;
              parent.__mpHeldSig = sig;
              parent.__mpSectionPending = false;
              clearTimeout(parent.__mpSwitchTimer);
              parent.requestAnimationFrame(() => {
                parent.requestAnimationFrame(() => {
                  const veil = parent.__mpVeil;
                  if (!veil) return;
                  veil.classList.add("is-out");
                  veil.classList.remove("is-on");
                  setTimeout(() => veil.remove(), 360);
                  parent.__mpVeil = null;
                });
              });
            }
          }
          function sectionSig() {
            const heading = doc.querySelector('[data-testid="stMain"] h2');
            return heading ? heading.textContent : "";
          }
          function liftPageVeil() {
            const veil = parent.__mpPageVeil;
            parent.__mpPageEnter = false;
            parent.__mpPageLeave = false;
            if (!veil) return;
            veil.classList.add("is-out");
            veil.classList.remove("is-on");
            setTimeout(() => veil.remove(), 600);
            parent.__mpPageVeil = null;
          }
          function coverPage(direction) {
            if (parent.matchMedia && parent.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
            if (parent.__mpPageVeil) parent.__mpPageVeil.remove();
            const veil = doc.createElement("div");
            veil.className = "mp-page-veil";
            doc.body.appendChild(veil);
            parent.__mpPageVeil = veil;
            parent.__mpPageEnter = direction === "enter";
            parent.__mpPageLeave = direction === "leave";
            parent.__mpPageAt = Date.now();
            parent.requestAnimationFrame(() => veil.classList.add("is-on"));
            clearTimeout(parent.__mpPageTimer);
            parent.__mpPageTimer = setTimeout(liftPageVeil, 4000);
          }
          doc.addEventListener("click", (event) => {
            const target = event.target;
            if (!target.closest) return;
            if (target.closest("div.st-key-lp_enter button")) coverPage("enter");
            else if (target.closest("div.st-key-nav_home button, div.st-key-nav_logo button")) coverPage("leave");
          }, true);
          doc.addEventListener("pointerdown", (event) => {
            const button = event.target.closest && event.target.closest("div.st-key-main_tab button");
            if (!button) return;
            button.classList.add("mp-sink");
          }, true);
          doc.addEventListener("click", (event) => {
            const button = event.target.closest && event.target.closest("div.st-key-main_tab button");
            if (!button) return;
            button.classList.add("mp-sink");
            const tabs = button.closest("div.st-key-main_tab");
            const buttons = [...tabs.querySelectorAll("button")];
            const idx = buttons.indexOf(button);
            if (idx < 0 || idx === parent.__mpTabIndex) return;
            const lockedWar = !!(doc.querySelector(".mp-war-lock") && /war/i.test(button.textContent || ""));
            if (lockedWar) return;
            if (parent.matchMedia && parent.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
            parent.__mpSectionPending = true;
            parent.__mpHeldSig = sectionSig();
            if (parent.__mpVeil) parent.__mpVeil.remove();
            const tabsBox = tabs.getBoundingClientRect();
            const veil = doc.createElement("div");
            veil.className = "mp-section-veil";
            veil.style.top = Math.max(0, tabsBox.bottom) + "px";
            veil.style.left = "0";
            veil.style.right = "0";
            veil.style.bottom = "0";
            doc.body.appendChild(veil);
            parent.__mpVeil = veil;
            parent.requestAnimationFrame(() => veil.classList.add("is-on"));
            clearTimeout(parent.__mpSwitchTimer);
            parent.__mpSwitchTimer = setTimeout(() => {
              const stuck = parent.__mpVeil;
              parent.__mpSectionPending = false;
              if (!stuck) return;
              stuck.classList.add("is-out");
              stuck.classList.remove("is-on");
              setTimeout(() => stuck.remove(), 360);
              parent.__mpVeil = null;
            }, 1600);
          }, true);
          let tipTimer = null;
          doc.addEventListener("pointerover", (event) => {
            const icon = event.target.closest && event.target.closest("[data-testid='stTooltipHoverTarget']");
            if (!icon || doc.body.classList.contains("mp-tip-session")) return;
            setTimeout(() => {
              if (icon.matches(":hover")) doc.body.classList.add("mp-tip-session");
            }, 300);
          }, true);
          doc.addEventListener("pointerout", (event) => {
            const icon = event.target.closest && event.target.closest("[data-testid='stTooltipHoverTarget']");
            if (!icon) return;
            clearTimeout(tipTimer);
            tipTimer = setTimeout(() => {
              if (!doc.querySelector("[data-testid='stTooltipHoverTarget']:hover")) {
                doc.body.classList.remove("mp-tip-session");
              }
            }, 400);
          }, true);
          const themeQuery = parent.matchMedia && parent.matchMedia("(prefers-color-scheme: dark)");
          if (themeQuery && themeQuery.addEventListener) {
            themeQuery.addEventListener("change", () => {
              doc.documentElement.classList.add("theme-switching");
              setTimeout(() => doc.documentElement.classList.remove("theme-switching"), 60);
            });
          }
          const root = doc.body;
          if (root) {
            new parent.MutationObserver(() => {
              if (parent.__mpScanQueued) return;
              parent.__mpScanQueued = 1;
              parent.requestAnimationFrame(() => {
                parent.__mpScanQueued = 0;
                scan();
              });
            }).observe(root, {childList: true, subtree: true});
          }
          scan();
        })();
        </script>
        """,
        height=0,
    )


st.markdown(FINTECH_CSS, unsafe_allow_html=True)
_install_ui_motion()


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


def is_member() -> bool:
    return st.session_state.get("mp_access") in {"year", "lifetime", "founding", "month"}


def _apply_member(record: dict) -> None:
    st.session_state.mp_user = record["email"]
    st.session_state.mp_access = record["plan"]
    st.session_state.mp_email = record["email"]
    st.session_state.mp_customer = record.get("customer_id") or ""


def _log_out() -> None:
    st.session_state.mp_user = None
    st.session_state.mp_access = None
    st.session_state.mp_email = ""
    st.session_state.mp_customer = ""


def _enter_account(email: str) -> None:
    """Log this account in and restore a paid plan when one is already on file."""
    email = _normalize_email(email)
    st.session_state.mp_user = email
    with st.spinner("Checking this account..."):
        record = restore_member(email)
    if record:
        _apply_member(record)
        return
    st.session_state.mp_access = None
    st.session_state.mp_email = email
    st.session_state.mp_customer = ""


def _finish_auth(email: str) -> None:
    _enter_account(email)
    if is_member() or st.session_state.get("account_next") != "checkout":
        st.session_state.account_next = None
        st.session_state.app_view = "dashboard"
    else:
        st.session_state.account_next = None
        st.session_state.app_view = "checkout"
    st.rerun()


def _continue_to_plan(plan_id: str) -> None:
    st.session_state.checkout_plan = plan_id
    if is_member():
        st.session_state.app_view = "dashboard"
    elif st.session_state.get("mp_user"):
        st.session_state.app_view = "checkout"
    else:
        st.session_state.account_mode = "signup"
        st.session_state.account_next = "checkout"
        st.session_state.app_view = "account"
    st.rerun()


def _app_base_url() -> str:
    try:
        current = (st.context.url or "").strip()
    except Exception:
        current = ""
    if current:
        return current.rstrip("/")
    configured = os.environ.get("APP_BASE_URL", "").strip().rstrip("/")
    return configured or "http://127.0.0.1:8501"


def _query_value(name: str) -> str:
    value = st.query_params.get(name, "")
    if isinstance(value, (list, tuple)):
        return str(value[0]).strip() if value else ""
    return str(value or "").strip()


def _redirect(url: str) -> None:
    import streamlit.components.v1 as components

    components.html(
        f"<script>window.top.location.href = {json.dumps(url)};</script>",
        height=0,
    )


def handle_billing_return() -> None:
    checkout = _query_value("checkout")
    session_id = _query_value("session_id")
    billing_return = _query_value("billing")
    if not checkout and billing_return != "return":
        return
    if checkout not in {"", "success", "cancel"} or billing_return not in {"", "return"}:
        return
    if session_id and not re.fullmatch(r"cs_[A-Za-z0-9_]{8,200}", session_id):
        return
    if checkout == "success" and session_id:
        limited = _throttle("checkout_return", limit=6)
        if limited:
            st.session_state.checkout_notice = limited
            st.session_state.app_view = "checkout"
        else:
            try:
                record = fulfill_checkout(session_id)
            except ValueError as exc:
                st.session_state.checkout_notice = str(exc)
                st.session_state.app_view = "checkout"
            else:
                _apply_member(record)
                st.session_state.app_view = "dashboard"
                st.session_state.mp_just_paid = True
    elif checkout == "cancel":
        st.session_state.checkout_notice = "No charge was made."
        st.session_state.app_view = "checkout"
    elif billing_return == "return" and st.session_state.get("mp_email"):
        record = restore_member(st.session_state.mp_email)
        if record:
            _apply_member(record)
            st.session_state.app_view = "dashboard"
        else:
            st.session_state.mp_access = None
            st.session_state.checkout_notice = "This plan is no longer active."
            st.session_state.app_view = "checkout"
    st.query_params.clear()
    st.rerun()


def blurred_notices_html(frame: pd.DataFrame) -> str:
    rows = []
    sample = frame.head(5) if isinstance(frame, pd.DataFrame) else pd.DataFrame()
    for _, row in sample.iterrows():
        when = row.get("Notice Date", "")
        try:
            when = pd.to_datetime(when).strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            when = str(when or "")
        workers = row.get("Employees Affected", "")
        try:
            workers = f"{int(float(workers)):,}"
        except (TypeError, ValueError):
            workers = str(workers or "—")
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(row.get('Company') or 'Company'))}</td>"
            f"<td>{html.escape(str(row.get('City') or ''))}</td>"
            f"<td>{html.escape(str(row.get('State') or ''))}</td>"
            f"<td>{html.escape(workers)}</td>"
            f"<td>{html.escape(when)}</td>"
            "</tr>"
        )
    body = "".join(rows) or "<tr><td>Company</td><td>City</td><td>State</td><td>—</td><td>—</td></tr>"
    return (
        '<div class="mp-blur-table"><table>'
        "<thead><tr><th>Company</th><th>City</th><th>State</th>"
        "<th>People named</th><th>Notice date</th></tr></thead>"
        f"<tbody>{body}</tbody></table></div>"
    )


def unlock_dialog() -> None:
    st.session_state.app_view = "pricing"
    if not st.session_state.get("price_selected"):
        st.session_state.price_selected = "lifetime"
    st.rerun()


@st.dialog("War is on the full site")
def war_locked_dialog() -> None:
    st.markdown(
        """
<div class="mp-war-pay">
<p>The free site stops at the War tab. Full access opens:</p>
<ul class="mp-war-gets">
<li>How unusual this week's war news is, next to every day since 1985</li>
<li>What one cash bet costs right now, in cents, on a single question</li>
<li>Headlines from the last couple of days</li>
<li>Arms sales Congress was just told about, with the date and the amount</li>
<li>A separate list of play-money guesses</li>
</ul>
<div class="mp-war-price">$8/mo · $50/yr · $70 Lifetime</div>
<p class="mp-war-fine">None of it is a forecast.</p>
</div>
        """,
        unsafe_allow_html=True,
    )
    stay, go = st.columns(2)
    with stay:
        st.button("Not now", key="war_lock_stay", use_container_width=True)
    with go:
        if st.button("Unlock full site", key="war_lock_plans", type="primary", use_container_width=True):
            unlock_dialog()


_PRICE_GETS = (
    "Company names, about 60 days early",
    "Layoff filings from California, Texas, New York, and Florida",
    "Search notices by company, city, and state",
    "The history behind each warning light",
    "The stock market, the fear gauge, gas, and oil",
    "Car debt, and how each maker's loans are holding up",
    "Rent, home loans, savings, and credit cards",
    "Late payments at the big card companies",
    "An email when a light changes",
)

_PRICE_CARDS = (
    {
        "id": "month",
        "name": "Monthly",
        "price": "$8",
        "cadence": "per month",
        "action": "Start monthly",
        "hook": "Try the full site this month.",
        "bullets": _PRICE_GETS + ("Stop whenever you want",),
    },
    {
        "id": "year",
        "name": "Yearly",
        "price": "$50",
        "cadence": "per year",
        "action": "Start yearly",
        "hook": "Save $46 versus paying monthly.",
        "bullets": _PRICE_GETS + ("One bill, then a quiet year",),
    },
    {
        "id": "lifetime",
        "name": "Lifetime",
        "price": "$70",
        "cadence": "once",
        "action": "Get lifetime access",
        "hook": "Less than 9 months of monthly.",
        "recommended": True,
        "bullets": _PRICE_GETS + ("Pay once. No renewal, ever",),
    },
)


def render_pricing_page() -> None:
    import streamlit.components.v1 as components

    selected = st.session_state.get("price_selected") or "lifetime"
    if st.button("Back", key="price_back"):
        st.session_state.app_view = "dashboard"
        st.rerun()
    with st.container(key="price_grid"):
        st.markdown(
            '<p class="mp-checkout-kicker">Plans</p>'
            '<h2 class="mp-checkout-title">Get the head start.</h2>'
            '<p class="mp-checkout-charge mp-price-lead">Same full site on every plan. '
            'Twelve months at $8 is <b>$96</b>. Lifetime is <b>$70, once</b>.</p>'
            '<div class="mp-price-compare">'
            '<div><span>Monthly for a year</span><strong>$96</strong></div>'
            '<div><span>Yearly</span><strong>$50 · save $46</strong></div>'
            '<div class="is-deal"><span>Lifetime</span><strong>$70, then never again</strong></div>'
            '</div>',
            unsafe_allow_html=True,
        )
        columns = st.columns(3, gap="medium")
        for column, card in zip(columns, _PRICE_CARDS):
            plan_id = card["id"]
            picked = " is-on" if selected == plan_id else ""
            badge = (
                '<span class="mp-price-badge">Recommended</span>'
                if card.get("recommended")
                else '<span class="mp-price-badge is-empty">Recommended</span>'
            )
            amount_class = "mp-price-amount is-hero" if card.get("recommended") else "mp-price-amount"
            items = "".join(f"<li>{html.escape(item)}</li>" for item in card["bullets"])
            with column:
                with st.container(key=f"price_{plan_id}"):
                    st.markdown(
                        f'<div class="mp-price-card{picked}">'
                        f"{badge}"
                        f'<p class="mp-price-name">{html.escape(card["name"])}</p>'
                        f'<p class="{amount_class}"><strong>{html.escape(card["price"])}</strong>'
                        f'<em>{html.escape(card["cadence"])}</em></p>'
                        f'<p class="mp-price-hook">{html.escape(card["hook"])}</p>'
                        f'<ul class="mp-plans-gets">{items}</ul>'
                        "</div>",
                        unsafe_allow_html=True,
                    )
                    if st.button(f"Select {card['name']}", key=f"price_hit_{plan_id}"):
                        st.session_state.price_selected = plan_id
                        st.rerun()
                    if st.button(
                        card["action"],
                        key=f"price_buy_{plan_id}",
                        type="primary" if plan_id == "lifetime" else "secondary",
                    ):
                        st.session_state.price_selected = plan_id
                        _continue_to_plan(plan_id)
        components.html(
            """
            <script>
            const root = window.parent.document;
            root.querySelectorAll('div[class*="st-key-price_buy_"] button').forEach((btn) => {
              if (btn.dataset.mpBound === "1") return;
              btn.dataset.mpBound = "1";
              btn.addEventListener("click", () => {
                btn.setAttribute("aria-busy", "true");
                btn.textContent = "";
                const spin = root.createElement("span");
                spin.className = "mp-spin";
                btn.append(spin, root.createTextNode(" Initializing Checkout..."));
              }, true);
            });
            </script>
            """,
            height=0,
        )


def render_account_page() -> None:
    mode = st.session_state.get("account_mode") or "signup"
    plan = None
    if st.session_state.get("account_next") == "checkout":
        plan = plan_by_id(st.session_state.get("checkout_plan") or "")
    if st.button("Back", key="account_back"):
        st.session_state.app_view = (
            "pricing" if st.session_state.get("account_next") == "checkout" else "dashboard"
        )
        st.rerun()
    with st.container(key="checkout_panel"):
        if mode == "login":
            title = "Log in"
            lead = "Use the email and password for your MacroPulse account."
        else:
            title = "Create your account"
            lead = "Sign up before checkout. You will use this email and password to get back in."
        plan_line = ""
        if plan:
            plan_line = (
                f'<p class="mp-checkout-charge">Then continue with {html.escape(plan["label"])} '
                f'{html.escape(plan["period"])}.</p>'
            )
        st.markdown(
            f'<p class="mp-checkout-kicker">Account</p>'
            f'<h2 class="mp-checkout-title">{html.escape(title)}</h2>'
            f'<p class="mp-checkout-charge">{html.escape(lead)}</p>'
            f"{plan_line}",
            unsafe_allow_html=True,
        )
        if mode == "login":
            with st.form("account_login_form"):
                email = st.text_input("Email", placeholder="you@email.com")
                password = st.text_input("Password", type="password")
                submitted = st.form_submit_button("Log in", type="primary")
            if submitted:
                error = _throttle("login") or authenticate(email, password)
                if error:
                    st.error(error)
                else:
                    _finish_auth(email)
            if st.button("Need an account? Sign up", key="account_show_signup"):
                st.session_state.account_mode = "signup"
                st.rerun()
            return
        with st.form("account_signup_form"):
            email = st.text_input("Email", placeholder="you@email.com")
            password = st.text_input("Password", type="password")
            confirm = st.text_input("Confirm password", type="password")
            submitted = st.form_submit_button("Create account", type="primary")
        if submitted:
            if password != confirm:
                st.error("Those passwords do not match.")
            else:
                error = _throttle("signup", limit=5) or create_account(email, password)
                if error:
                    st.error(error)
                else:
                    _finish_auth(email)
        if st.button("Already have an account? Log in", key="account_show_login"):
            st.session_state.account_mode = "login"
            st.rerun()


def _joined_label(stamp: str) -> str:
    parsed = pd.to_datetime(stamp, errors="coerce", utc=True)
    if pd.isna(parsed):
        return "—"
    return parsed.strftime("%b ") + str(int(parsed.day)) + parsed.strftime(", %Y")


def render_profile_page() -> None:
    email = _normalize_email(st.session_state.get("mp_user") or "")
    if not email:
        st.session_state.account_mode = "login"
        st.session_state.account_next = None
        st.session_state.app_view = "account"
        st.rerun()
    if st.button("Back", key="profile_back"):
        st.session_state.app_view = "dashboard"
        st.rerun()
    profile = account_profile(email) or {}
    plan = plan_by_id(st.session_state.get("mp_access") or "") if is_member() else None
    plan_value = f"{plan['label']} {plan['period']}" if plan else "Free"
    with st.container(key="profile_panel"):
        st.markdown(
            '<p class="mp-checkout-kicker">Profile</p>'
            '<h2 class="mp-profile-name">Your account</h2>'
            f'<p class="mp-checkout-charge">{html.escape(email)}</p>'
            '<div class="mp-profile-facts">'
            f"<div><span>Plan</span><strong>{html.escape(plan_value)}</strong></div>"
            f"<div><span>Joined</span><strong>{html.escape(_joined_label(profile.get('created_at') or ''))}</strong></div>"
            "</div>",
            unsafe_allow_html=True,
        )
        with st.container(key="profile_plan"):
            if plan:
                st.markdown(
                    '<p class="mp-profile-card-kicker">Billing</p>'
                    '<h3 class="mp-profile-card-title">Plan and card</h3>'
                    f'<p class="mp-profile-copy">{html.escape(plan["charge"])}</p>',
                    unsafe_allow_html=True,
                )
                customer = st.session_state.get("mp_customer") or ""
                if st.button("Manage billing", key="profile_billing", type="primary"):
                    try:
                        portal = billing_portal_url(customer, f"{_app_base_url()}/?billing=return")
                    except ValueError as exc:
                        st.session_state.profile_portal = ""
                        st.error(str(exc))
                    else:
                        st.session_state.profile_portal = portal
                        _redirect(portal)
                if st.session_state.get("profile_portal"):
                    st.link_button("Open the billing page", st.session_state.profile_portal)
            else:
                st.markdown(
                    '<p class="mp-profile-card-kicker">Billing</p>'
                    '<h3 class="mp-profile-card-title">Plan and card</h3>'
                    '<p class="mp-profile-copy">No paid plan on this account yet.</p>'
                    '<p class="mp-profile-copy">Every plan opens the same full site.</p>',
                    unsafe_allow_html=True,
                )
                if st.button("See the plans", key="profile_plans", type="primary"):
                    unlock_dialog()
        with st.container(key="profile_password"):
            st.markdown(
                '<p class="mp-profile-card-kicker">Settings</p>'
                '<h3 class="mp-profile-card-title">Password</h3>',
                unsafe_allow_html=True,
            )
            with st.form("profile_password_form"):
                current = st.text_input("Current password", type="password")
                new_password = st.text_input("New password", type="password")
                confirm = st.text_input("Confirm new password", type="password")
                save = st.form_submit_button("Update password", type="primary")
            if save:
                if new_password != confirm:
                    st.error("Those passwords do not match.")
                else:
                    error = _throttle("password", limit=5) or change_password(email, current, new_password)
                    if error:
                        st.error(error)
                    else:
                        st.success("Password updated.")
        with st.container(key="profile_signout"):
            st.markdown(
                '<p class="mp-profile-card-kicker">This browser</p>'
                '<h3 class="mp-profile-card-title">Sign out</h3>',
                unsafe_allow_html=True,
            )
            if st.button("Log out", key="profile_logout"):
                _log_out()
                st.session_state.profile_portal = ""
                st.session_state.app_view = "landing"
                st.rerun()


def render_checkout_page() -> None:
    if not st.session_state.get("mp_user"):
        st.session_state.account_mode = "signup"
        st.session_state.account_next = "checkout"
        st.session_state.app_view = "account"
        st.rerun()
    plan = plan_by_id(st.session_state.get("checkout_plan") or "year") or plan_by_id("year")
    notice = st.session_state.pop("checkout_notice", None)
    email = _normalize_email(st.session_state.get("mp_user") or "")
    if st.button("Back", key="checkout_back"):
        st.session_state.app_view = "pricing"
        st.rerun()
    with st.container(key="checkout_panel"):
        st.markdown(
            '<p class="mp-checkout-kicker">Checkout</p>'
            f'<h2 class="mp-checkout-title">{html.escape(plan["label"])} {html.escape(plan["period"])}</h2>'
            f'<p class="mp-checkout-charge">{html.escape(plan["charge"])}</p>'
            f'<p class="mp-checkout-charge">Account · {html.escape(email)}</p>'
            '<ul class="mp-plans-gets">'
            "<li>Company names, about 60 days before the layoff headline.</li>"
            "<li>Layoff filings from California, Texas, New York, and Florida.</li>"
            "<li>Search notices by company, city, and state.</li>"
            "<li>The history behind each warning light.</li>"
            "<li>The stock market, the fear gauge, gas, and oil.</li>"
            "<li>Car debt, and how each maker's loans are holding up.</li>"
            "<li>Rent, home loans, savings, and credit cards.</li>"
            "<li>Late payments at the big card companies.</li>"
            "<li>An email when a light changes.</li>"
            "</ul>",
            unsafe_allow_html=True,
        )
        if notice:
            st.warning(notice)
        if is_member():
            st.success("This account already has the full site.")
            if st.button("Go to the full site", key="checkout_already_member", type="primary"):
                st.session_state.app_view = "dashboard"
                st.rerun()
            return
        if payments_test_mode():
            st.info("Test mode. Card 4242 4242 4242 4242, any future date, any CVC. No real charge.")
        if not payments_configured():
            st.error(
                "Card checkout is not connected yet. Add the Stripe secret key on the server, then restart."
            )
            return
        if st.button(f"Pay {plan['label']}", key="checkout_pay", type="primary"):
            limited = _throttle("checkout", limit=5)
            if limited:
                st.error(limited)
                return
            base = _app_base_url()
            try:
                url = create_checkout_session(
                    plan["id"],
                    email,
                    f"{base}/?checkout=success&session_id={{CHECKOUT_SESSION_ID}}",
                    f"{base}/?checkout=cancel",
                )
            except ValueError as exc:
                st.error(str(exc))
            else:
                _redirect(url)
                st.link_button("Continue to card payment", url)


def paywall_block(key: str, title: str, detail: str) -> None:
    st.markdown(
        '<div class="mp-paywall">'
        '<div class="hero-kicker">Full site</div>'
        f"<h3>{html.escape(title)}</h3>"
        f"<p>{html.escape(detail)}</p>"
        "</div>",
        unsafe_allow_html=True,
    )
    if st.button("See the plans", key=key, type="primary"):
        unlock_dialog()


@contextmanager
def locked_region(key: str, title: str):
    """Show the real section. Blur it until a paid plan is active."""
    if is_member():
        with st.container():
            yield
        return
    with st.container(key=f"cta_{key}"):
        st.markdown(
            f'<div class="mp-lock-kicker">Full site · {html.escape(title)}</div>',
            unsafe_allow_html=True,
        )
        if st.button("See the plans", key=key, type="primary"):
            unlock_dialog()
    with st.container(key=f"blur_{key}"):
        yield


@st.dialog("Log in")
def login_dialog() -> None:
    st.caption("Use the email and password for your MacroPulse account.")
    with st.form("lp_login_form"):
        email = st.text_input("Email", placeholder="you@email.com")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in", type="primary", use_container_width=True)
    if submitted:
        error = _throttle("login") or authenticate(email, password)
        if error:
            st.error(error)
            return
        _finish_auth(email)


@st.dialog("Sign up")
def signup_dialog() -> None:
    st.caption("Create the account you will use to get back in. Checkout comes after this.")
    with st.form("lp_signup_form"):
        email = st.text_input("Email", placeholder="you@email.com")
        password = st.text_input("Password", type="password")
        confirm = st.text_input("Confirm password", type="password")
        submitted = st.form_submit_button("Sign up", type="primary", use_container_width=True)
    if submitted:
        if password != confirm:
            st.error("Those passwords do not match.")
            return
        error = _throttle("signup", limit=5) or create_account(email, password)
        if error:
            st.error(error)
            return
        _finish_auth(email)


@st.dialog("Get a simple weekly email")
def subscribe_dialog() -> None:
    st.caption(
        "We'll email you when warning lights change: more layoff notices, "
        "jumpier loans, or families falling behind on bills. No jargon dump."
    )
    with st.form("dialog_subscribe"):
        email = st.text_input("Your email", placeholder="you@email.com")
        submitted = st.form_submit_button("Email me updates", type="primary", use_container_width=True)
    if submitted:
        error = subscribe_email(email)
        if error:
            st.error(error)
        else:
            st.success("You're on the list. We'll email you when warning lights change.")


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

**Payments:** card payments are processed by Stripe. MacroPulse stores the
email on your account, a scrambled hash of your password, the plan you chose,
and a Stripe customer id. We never see or store your card number or your
password itself. That same address gets a short note when the account is
created, and another when a plan is paid.

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

**Paid plans:** yearly, monthly, and founding plans renew at the same price
until you cancel in the billing portal. Lifetime access is a single charge
and does not renew. Canceling stops the next charge. It does not refund a
period that already started.

**You are solely responsible** for any decision you make. Consult a licensed
advisor before acting on capital, credit, or employment matters. To the
maximum extent permitted by law, MacroPulse and its operators disclaim all
warranties and are **not liable for any loss or damage** arising from use of
this information.
        """
    )


@st.cache_data(ttl=3600, show_spinner=False)
def _load_master_data_cached(file_mtime: float) -> pd.DataFrame:
    frame = pd.read_csv(US_WARN_CSV, parse_dates=["Notice Date"])
    if "Employees Affected" in frame.columns:
        frame["Employees Affected"] = pd.to_numeric(
            frame["Employees Affected"], errors="coerce"
        )
    return frame


def load_master_data() -> pd.DataFrame:
    if not US_WARN_CSV.exists():
        return pd.DataFrame()
    try:
        return _load_master_data_cached(US_WARN_CSV.stat().st_mtime)
    except Exception:
        _log_failure()
        return pd.DataFrame()


@st.cache_data(ttl=3600, show_spinner=False)
def _load_macro_history_cached(file_mtime: float) -> pd.DataFrame:
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
    if not HISTORY_CSV.exists():
        return pd.DataFrame()
    try:
        return _load_macro_history_cached(HISTORY_CSV.stat().st_mtime)
    except Exception:
        _log_failure()
        return pd.DataFrame()


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
    if not company_reports_are_fresh():
        return True
    return not _history_is_current()


def ensure_data(force: bool = False) -> None:
    if force or not US_WARN_CSV.exists() or not _file_is_fresh(US_WARN_CSV):
        collect_us_warn()
    if force or not _history_schema_ok() or not _file_is_fresh(HISTORY_CSV):
        collect_macro_signals()
    if force or not company_reports_are_fresh():
        try:
            refresh_company_reports()
        except Exception:
            _log_failure()


_refresh_guard = threading.Lock()
_refresh_running = False
_refresh_started_at = 0.0


def _background_refresh() -> None:
    global _refresh_running
    try:
        ensure_data(force=False)
    except Exception:
        _log_failure()
    finally:
        with _refresh_guard:
            _refresh_running = False


def _kick_background_refresh() -> None:
    """Refresh saved files after the page is already showing the last copy."""
    global _refresh_running, _refresh_started_at
    now = time.time()
    with _refresh_guard:
        if _refresh_running or now - _refresh_started_at < REFRESH_RETRY_SECONDS:
            return
        _refresh_running = True
        _refresh_started_at = now
    threading.Thread(
        target=_background_refresh, name="macropulse-refresh", daemon=True
    ).start()


def _mix_hex(start: str, end: str, t: float) -> Tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    s = [int(start[i : i + 2], 16) for i in (1, 3, 5)]
    e = [int(end[i : i + 2], 16) for i in (1, 3, 5)]
    return tuple(int(s[i] + (e[i] - s[i]) * t) for i in range(3))


def _gauge_arc_steps() -> List[dict]:
    emerald, amber, crimson = "#C8F542", "#D97706", "#DC2626"
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


SERIES_FREQ = {
    "T10Y2Y": "Daily",
    "BAMLH0A0HYM2": "Daily",
    "SAHMREALTIME": "Monthly",
    "UNRATE": "Monthly",
    "CAUR": "Monthly",
    "TXUR": "Monthly",
    "NYUR": "Monthly",
    "FLUR": "Monthly",
    "ICSA": "Weekly",
    "CAICLAIMS": "Weekly",
    "TXICLAIMS": "Weekly",
    "NYICLAIMS": "Weekly",
    "FLICLAIMS": "Weekly",
    "UMCSENT": "Monthly",
    "MSACSR": "Monthly",
    "PERMIT_YOY": "Monthly",
    "PERMIT": "Monthly",
    "TOTALSA": "Monthly",
    "PSAVERT": "Monthly",
    "MORTGAGE30US": "Weekly",
    "DRALACBS": "Quarterly",
    "DRCCLACBS": "Quarterly",
    "DRCCLOBS": "Quarterly",
    "TERMCBCCALLNS": "Monthly",
    "MVLOAS": "Monthly",
    "RIFLPBCNC48": "Monthly",
    "DTCTSL": "Monthly",
    "CSUSHPINSA": "Monthly",
    "REVOLSL": "Monthly",
    "CUSR0000SEHA": "Monthly",
    "RENT_YOY": "Monthly",
    "SP500": "Daily",
    "VIXCLS": "Daily",
    "GASREGW": "Weekly",
    "DCOILWTICO": "Daily",
}


def series_prints(history: Optional[pd.DataFrame], column: str) -> pd.DataFrame:
    if history is None or history.empty or column not in history.columns or "date" not in history.columns:
        return pd.DataFrame()
    work = history[["date", column]].copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce")
    work[column] = pd.to_numeric(work[column], errors="coerce")
    work = work.dropna()
    if work.empty:
        return work
    changed = work[column].ne(work[column].shift())
    return work.loc[changed]


def format_print_delta(delta: float, unit: str) -> str:
    if unit == "claims":
        return f"{delta:+,.0f}"
    if unit == "mil":
        return f"{delta:+.2f} mil"
    if unit == "bn":
        scaled = delta / 1000.0 if abs(delta) >= 100 else delta
        if abs(scaled) >= 1000:
            return f"{scaled / 1000:+.2f} trillion"
        if abs(scaled) >= 10:
            return f"{scaled:+.0f} billion"
        return f"{scaled:+.1f} billion"
    if unit == "months":
        return f"{delta:+.1f} mo"
    if unit == "index":
        return f"{delta:+,.0f}"
    if unit == "usd":
        return f"{delta:+.2f}"
    if unit == "vix":
        return f"{delta:+.1f}"
    if abs(delta) < 0.25:
        return f"{delta * 100:+.0f} tiny steps"
    if unit in {"spread_pct", "pct", "yoy_pct"}:
        return f"{delta:+.1f} points"
    return f"{delta:+.2f}"


def sparkline_svg(history: Optional[pd.DataFrame], column: str, points: int = 24) -> str:
    if history is None or history.empty or column not in history.columns:
        return ""
    values = pd.to_numeric(history[column], errors="coerce").dropna().tail(points)
    if len(values) < 3:
        return ""
    nums = [float(v) for v in values]
    lo, hi = min(nums), max(nums)
    span = hi - lo or 1.0
    width, height = 92.0, 36.0
    pts = []
    for i, val in enumerate(nums):
        x = 2 + i * (width - 4) / (len(nums) - 1)
        y = height - 4 - (val - lo) / span * (height - 8)
        pts.append(f"{x:.1f},{y:.1f}")
    return (
        f'<svg class="mp-spark" viewBox="0 0 {width:.0f} {height:.0f}" aria-hidden="true">'
        f'<polyline points="{" ".join(pts)}"/></svg>'
    )


def format_indicator_value(item: dict) -> str:
    value = item.get("value")
    unit = item.get("display_unit") or ""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
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
    if unit == "index":
        return f"{value:,.0f}"
    if unit == "usd":
        return f"${value:,.2f}"
    if unit == "vix":
        return f"{value:.1f}"
    return f"{value:.2f}"


def format_auto_debt(value: Optional[float]) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    billions = value / 1000.0 if value > 10_000 else value
    if billions >= 1000:
        return f"${billions / 1000:.2f}T"
    return f"${billions:.0f}B"


def format_bp(value: Optional[float]) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    return f"{value:+.0f} bp"


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


@st.cache_data(ttl=1800, show_spinner=False)
def _cached_headlines() -> dict:
    return fetch_all_headlines()


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_war() -> dict:
    return war_snapshot()


def _war_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _war_cents(value: float, signed: bool = False) -> str:
    cents = float(value) * 100.0
    number = abs(cents)
    text = f"{number:.0f}¢" if number >= 10 else f"{number:.1f}¢"
    if not signed:
        return text
    return ("+" if cents > 0 else "−") + text


def _war_times(value: float) -> str:
    """Index points where 100 is a normal year, shown as a multiple of that year."""
    return f"{float(value) / 100.0:.1f}×"


def _war_money(volume: float) -> str:
    if volume >= 1_000_000:
        return f"${volume / 1_000_000:.0f} million wagered"
    if volume >= 1_000:
        return f"${volume / 1_000:.0f} thousand wagered"
    return "A thin market"


def _war_spark(values: list) -> str:
    nums = [float(item) for item in values if item is not None]
    if len(nums) < 8:
        return ""
    low = min(min(nums), 80.0)
    high = max(max(nums), 160.0)
    span = high - low or 1.0
    width, height = 280.0, 72.0
    points = []
    for index, value in enumerate(nums):
        x = 2 + index * (width - 4) / (len(nums) - 1)
        y = height - 8 - (value - low) / span * (height - 18)
        points.append(f"{x:.1f},{y:.1f}")
    normal = height - 8 - (100.0 - low) / span * (height - 18)
    return (
        f'<svg class="war-spark" viewBox="0 0 {width:.0f} {height:.0f}" aria-hidden="true">'
        f'<line x1="0" y1="{normal:.1f}" x2="{width:.0f}" y2="{normal:.1f}" '
        f'stroke="rgba(200,245,66,0.55)" stroke-dasharray="3 4"/>'
        f'<polyline points="{" ".join(points)}" fill="none" stroke="#38BDF8" '
        f'stroke-width="1.7" stroke-linejoin="round" stroke-linecap="round"/>'
        "</svg>"
    )


def _war_shift(now: float, then: float | None) -> str:
    if then is None:
        return "—"
    gap = float(now) - float(then)
    if abs(gap) < 5:
        return "Same"
    return "Hotter" if gap > 0 else "Quieter"


def _war_watch(markets: list) -> dict:
    """Keep each day's bet prices so the next visit can show what moved."""
    path = Path("data/war_watch.json")
    today = datetime.now().strftime("%Y-%m-%d")
    days: dict = {}
    try:
        saved = json.loads(path.read_text())
        if isinstance(saved.get("days"), dict):
            days = saved["days"]
    except Exception:
        days = {}
    prior_dates = sorted(day for day in days if day < today)
    prior = days.get(prior_dates[-1], {}) if prior_dates else {}
    if not isinstance(prior, dict):
        prior = {}
    days[today] = {str(row.get("question") or ""): round(float(row["yes"]), 4) for row in markets}
    keep = sorted(days)[-21:]
    try:
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({"days": {day: days[day] for day in keep}}))
    except Exception:
        _log_failure()
    moves = []
    since = prior_dates[-1] if prior_dates else ""
    for row in markets:
        old = prior.get(row.get("question"))
        if old is None:
            continue
        delta = float(row["yes"]) - float(old)
        if abs(delta) < 0.005:
            continue
        moves.append({"question": row.get("question") or "", "delta": delta})
    moves.sort(key=lambda row: abs(row["delta"]), reverse=True)
    return {"since": since, "moves": moves}


def _war_since(iso_day: str) -> str:
    try:
        stamp = datetime.strptime(iso_day, "%Y-%m-%d")
    except ValueError:
        return iso_day
    return stamp.strftime("%b ") + str(stamp.day)


def render_war_panel() -> None:
    """A plain weekly answer, one bet worth remembering, and what moved. Not a forecast."""
    try:
        snapshot = _cached_war() or {}
    except Exception:
        _log_failure()
        snapshot = {}
    gpr = snapshot.get("gpr") or {}
    markets = list(snapshot.get("markets") or [])
    watch = _war_watch(markets) if markets else {"since": "", "moves": []}
    moved = {row["question"]: float(row["delta"]) for row in watch.get("moves") or []}
    invasions = [row for row in markets if row.get("kind") == "Invasions"]
    lead = max(invasions or markets, key=lambda row: float(row["yes"])) if markets else None
    if gpr:
        week = float(gpr["week"])
        trail = [float(item) for item in (gpr.get("trail") or [])]
        week_ago = trail[-8] if len(trail) >= 8 else None
        month_ago = trail[-31] if len(trail) >= 31 else None
        week_word = _war_shift(week, week_ago)
        month_word = _war_shift(week, month_ago)
        rank = int(round(float(gpr["percentile"])))
        spot = min(max(week, 0.0), 250.0) / 250.0 * 100.0
        normal_at = 100.0 / 250.0 * 100.0
        if rank >= 55:
            big = rank
            beside = "of every 100 days since 1985 were quieter"
            claim = f"War stories this week are hotter than {big} of every 100 days since 1985."
        elif rank <= 45:
            big = 100 - rank
            beside = "of every 100 days since 1985 were hotter"
            claim = f"War stories this week are quieter than {big} of every 100 days since 1985."
        else:
            big = rank
            beside = "of every 100 days since 1985 were quieter"
            claim = "War stories this week are close to a typical week since 1985."
        compared = {
            "Hotter": "That's hotter than last week.",
            "Quieter": "That's quieter than last week.",
            "Same": "That's about the same as last week.",
        }.get(week_word, "")
        week_then = f" Then {_war_times(week_ago)}." if week_ago is not None else ""
        month_then = f" Then {_war_times(month_ago)}." if month_ago is not None else ""
        week_link = "As last week." if week_word == "Same" else "Than last week."
        month_link = "As a month ago." if month_word == "Same" else "Than a month ago."
        if watch.get("since") and watch.get("moves"):
            bits = []
            for row in watch["moves"][:2]:
                bits.append(f"{row['question'].rstrip('?')} {_war_cents(row['delta'], signed=True)}")
            comeback = "Since " + _war_since(watch["since"]) + ": " + ". ".join(bits) + "."
        else:
            comeback = "Checked just now. Come back tomorrow and the bets that moved will be marked."
        feature = ""
        if lead:
            feature = (
                '<a class="war-feature" href="{url}" target="_blank" rel="noopener noreferrer">'
                "<b>{cents}</b>"
                "<p>{question}</p>"
                "<small>Traders pay this much for a $1 payout if that one thing happens. Not the chance of every war.</small>"
                "</a>"
            ).format(
                url=html.escape(lead.get("url") or "https://polymarket.com", quote=True),
                cents=html.escape(_war_cents(float(lead["yes"]))),
                question=html.escape(lead.get("question") or ""),
            )
        st.markdown(
            f"""
            <div class="war-board">
              <div class="war-hero">
                <div>
                  <p class="war-kicker">{html.escape(str(gpr.get("label") or "This week"))}</p>
                  <div class="war-score-row">
                    <div class="war-score">{big}</div>
                    <div class="war-score-of">{html.escape(beside)}</div>
                  </div>
                  <p>{html.escape(claim)} {html.escape(compared)} A normal year is 1×. This week is {html.escape(_war_times(week))}.</p>
                  <div class="war-track" aria-hidden="true">
                    <i class="war-normal" style="left:{normal_at:.1f}%"></i>
                    <i class="war-now" style="left:{spot:.1f}%"></i>
                  </div>
                  <div class="war-scale"><span>Quiet</span><span>Normal</span><span>Very hot</span></div>
                </div>
                <div>
                  {_war_spark(trail)}
                  <div class="war-scale"><span>6 months</span><span>Lime line = normal</span></div>
                </div>
              </div>
              <div class="war-chips">
                <div class="war-chip"><b>{html.escape(week_word)}</b><span>{html.escape(week_link)}{html.escape(week_then)} Now {html.escape(_war_times(week))}.</span></div>
                <div class="war-chip"><b>{html.escape(month_word)}</b><span>{html.escape(month_link)}{html.escape(month_then)} Now {html.escape(_war_times(week))}.</span></div>
                <div class="war-chip"><b>{html.escape(_war_cents(float(lead["yes"])) if lead else "—")}</b><span>The bet to remember. One question, not every war.</span></div>
              </div>
              {feature}
              <p class="war-return">{html.escape(comeback)}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.info("The war-news reading isn't available right now. The prices below still update on their own.")

    render_section_news("War", "What showed up", "Last couple of days")
    _render_war_fresh(snapshot)

    lens = st.pills(
        "Show",
        ["All", "Invasions", "Strikes", "Taiwan", "Nuclear"],
        default="All",
        key="war_lens",
        label_visibility="collapsed",
    ) or "All"
    order = st.pills(
        "Order",
        ["Highest price", "Most money"],
        default="Highest price",
        key="war_order",
        label_visibility="collapsed",
    ) or "Highest price"
    shown = markets if lens == "All" else [row for row in markets if row.get("kind") == lens]
    if lead and lens == "All":
        shown = [row for row in shown if row.get("question") != lead.get("question")]
    shown.sort(key=lambda row: float(row["volume"] if order == "Most money" else row["yes"]), reverse=True)
    subsection(
        "The other bets",
        "Each number is cents on a $1 bet. They do not add up.",
    )
    if shown:
        bets = []
        for market in shown:
            width = max(1.5, min(float(market["yes"]) * 100.0, 100.0))
            delta = moved.get(market.get("question"))
            change = ""
            if delta is not None and watch.get("since"):
                change = (
                    f' · <span class="war-move">{html.escape(_war_cents(delta, signed=True))} '
                    f'since {html.escape(_war_since(watch["since"]))}</span>'
                )
            bets.append(
                '<a class="war-bet" href="{url}" target="_blank" rel="noopener noreferrer">'
                '<div class="war-bet-top"><span class="war-bet-q">{question}</span>'
                "<b>{price}</b></div>"
                '<div class="war-bet-track"><span style="width:{width:.1f}%"></span></div>'
                '<div class="war-bet-meta">{money} · {kind}{change}</div>'
                "</a>".format(
                    url=html.escape(market.get("url") or "https://polymarket.com", quote=True),
                    question=html.escape(market.get("question") or ""),
                    price=html.escape(_war_cents(float(market["yes"]))),
                    width=width,
                    money=html.escape(_war_money(float(market.get("volume") or 0))),
                    kind=html.escape(market.get("kind") or ""),
                    change=change,
                )
            )
        st.markdown('<div class="war-bets">' + "".join(bets) + "</div>", unsafe_allow_html=True)
    else:
        st.info("Nothing in this group is priced right now. Try All.")


def _sale_day(iso_day: str) -> str:
    try:
        stamp = datetime.strptime(iso_day, "%Y-%m-%d")
    except ValueError:
        return iso_day
    return stamp.strftime("%b ") + str(stamp.day) + ", " + str(stamp.year)


def _render_war_fresh(snapshot: dict) -> None:
    """Published arms notices and play-money guesses. Not a forecast."""
    sales = list(snapshot.get("sales") or [])
    guesses = sorted(snapshot.get("guesses") or [], key=lambda row: float(row.get("yes") or 0), reverse=True)
    subsection("Arms sales Congress was just told about", "The date is the day the Pentagon dated the notice.")
    if sales:
        blocks = []
        for row in sales:
            blocks.append(
                '<a class="war-sale" href="{url}" target="_blank" rel="noopener noreferrer">'
                "<div><span>{buyer}</span><small>{day}</small></div><b>{amount}</b></a>".format(
                    url=html.escape(row.get("url") or "https://www.federalregister.gov", quote=True),
                    buyer=html.escape(row.get("buyer") or "A government"),
                    day=html.escape(_sale_day(str(row.get("date") or ""))),
                    amount=html.escape(row.get("amount") or ""),
                )
            )
        st.markdown('<div class="war-sales">' + "".join(blocks) + "</div>", unsafe_allow_html=True)
    else:
        st.info("No recent arms-sale notice could be read.")
    subsection("A second crowd's guesses", "Manifold. Play money, separate from the cash bets below.")
    if guesses:
        blocks = []
        for row in guesses:
            width = max(1.5, min(float(row["yes"]) * 100.0, 100.0))
            volume = float(row.get("volume") or 0)
            money = f"{volume / 1000:.0f}k in play money" if volume >= 1000 else "A thin game"
            blocks.append(
                '<a class="war-bet war-guess" href="{url}" target="_blank" rel="noopener noreferrer">'
                '<div class="war-bet-top"><span class="war-bet-q">{question}</span><b>{price}</b></div>'
                '<div class="war-bet-track"><span style="width:{width:.1f}%"></span></div>'
                '<div class="war-bet-meta">{money}</div></a>'.format(
                    url=html.escape(row.get("url") or "https://manifold.markets", quote=True),
                    question=html.escape(row.get("question") or ""),
                    price=html.escape(_war_cents(float(row["yes"]))),
                    width=width,
                    money=html.escape(money),
                )
            )
        st.markdown('<div class="war-bets">' + "".join(blocks) + "</div>", unsafe_allow_html=True)
    else:
        st.info("The play-money guesses aren't available right now.")


def render_section_news(topic: str, heading: str = "In the news", heading_meta: str = "Last couple of days") -> None:
    """Headlines for this section, labeled by the toll in the wording."""
    try:
        items = (_cached_headlines() or {}).get(topic) or []
    except Exception:
        _log_failure()
        items = []
    rows = []
    for item in items:
        meta = " · ".join(part for part in (item.get("source") or "", item.get("when") or "") if part)
        rows.append(
            '<a class="news-row" href="{link}" target="_blank" rel="noopener noreferrer">'
            '<span class="news-toll {toll}">{label}</span>'
            '<span class="news-title">{title}</span>'
            '<span class="news-meta">{meta}</span>'
            "</a>".format(
                link=html.escape(item.get("link") or "", quote=True),
                toll=html.escape(item.get("toll") or "watch"),
                label=html.escape(item.get("toll_label") or "Worth a look"),
                title=html.escape(item.get("title") or ""),
                meta=html.escape(meta),
            )
        )
    body = "".join(rows) if rows else '<p class="news-note">No headline right now clearly touches this page.</p>'
    note = (
        '<p class="news-note">A reading of the headline\'s wording. Not a forecast.</p>'
        if rows
        else ""
    )
    head = (
        '<div class="subhead">'
        + html.escape(heading)
        + '<span class="subhead-meta">'
        + html.escape(heading_meta)
        + "</span></div>"
    )
    st.markdown(
        '<div class="news-board">' + head + body + note + "</div>",
        unsafe_allow_html=True,
    )


def subsection(label: str, meta: str | None = None) -> None:
    extra = (
        f'<span class="subhead-meta">{html.escape(meta)}</span>' if meta else ""
    )
    st.markdown(
        f'<div class="subhead">{html.escape(label)}{extra}</div>',
        unsafe_allow_html=True,
    )


FREQ_PLAIN = {
    "Daily": "Checked every market day",
    "Weekly": "Updated once a week",
    "Monthly": "Updated once a month",
    "Quarterly": "Updated every 3 months",
}


def _landing_topo() -> str:
    paths = []
    for ring in range(14, 2, -2):
        cx, cy = 700.0, 310.0
        rx = 70.0 + ring * 46.0
        ry = 42.0 + ring * 30.0
        pts = []
        steps = 24
        for step in range(steps):
            angle = (step / steps) * math.tau
            wobble = 1.0 + 0.10 * math.sin(angle * 3.0 + ring * 0.45)
            x = cx + math.cos(angle) * rx * wobble
            y = cy + math.sin(angle) * ry * wobble * 0.88
            pts.append((x, y))
        d = "M" + "L".join(f"{x:.1f} {y:.1f}" for x, y in pts) + "Z"
        opacity = 0.14 + (ring / 14.0) * 0.22
        width = 1.05 if ring % 4 == 0 else 0.75
        color = "200,245,66" if ring % 6 == 0 else "56,189,248"
        paths.append(
            f'<path d="{d}" stroke="rgba({color},{opacity:.2f})" stroke-width="{width}"/>'
        )
    for fault in range(3):
        y0 = 50.0 + fault * 90.0
        d = (
            f"M{-20 + fault * 20:.1f} {y0:.1f} "
            f"C {240:.1f} {y0 + 40:.1f}, {640:.1f} {y0 - 36:.1f}, "
            f"{1480:.1f} {y0 + 24:.1f}"
        )
        paths.append(
            f'<path d="{d}" stroke="rgba(165,180,252,{0.2 + fault * 0.04:.2f})" stroke-width="0.9"/>'
        )
    return (
        '<svg viewBox="0 0 1400 900" preserveAspectRatio="xMidYMid slice" aria-hidden="true">'
        + "".join(paths)
        + "</svg>"
    )


_LANDING_TOPO_SVG = _landing_topo()


def _log_failure() -> None:
    """Print a traceback without leaving an API key in the log or the page."""
    text = traceback.format_exc()
    text = re.sub(r"(api_key=)[^&\s\"']+", r"\1[redacted]", text, flags=re.I)
    for name in ("FRED_API_KEY", "STRIPE_SECRET_KEY"):
        key = os.environ.get(name, "").strip()
        if len(key) >= 8:
            text = text.replace(key, "[redacted]")
    print(text)


def _throttle(bucket: str, limit: int = 8, window: float = 600.0) -> Optional[str]:
    """Stop one browser session from repeating an expensive action."""
    now = time.time()
    key = f"_mp_throttle_{bucket}"
    stamps = [stamp for stamp in st.session_state.get(key, []) if now - float(stamp) < window]
    if len(stamps) >= limit:
        st.session_state[key] = stamps
        return "Too many tries. Wait a few minutes and try again."
    stamps.append(now)
    st.session_state[key] = stamps
    return None


def _clean_search(raw: str, limit: int = 80) -> str:
    text = (raw or "")[:limit]
    text = re.sub(r"[\x00-\x1f\x7f]", "", text)
    return text.strip()


def _studio_uri(name: str) -> str:
    path = Path(__file__).resolve().parent / "assets" / name
    try:
        raw = path.read_bytes()
    except OSError:
        return ""
    if not raw:
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")


def _studio_tag(class_name: str, uri: str) -> str:
    if not uri:
        return ""
    return f'<img class="{class_name}" src="{uri}" alt="">'


_JOBS_STUDIO_URI = _studio_uri("jobs-monitor-stage.jpg")
_HH_STUDIO_URI = _studio_uri("households-line-smoke.jpg")
_HERO_WAVE_URI = _studio_uri("hero-wave-smoke.jpg")
_MP_MARK = (
    '<svg class="mp-mark" viewBox="0 0 32 32" aria-hidden="true">'
    '<circle cx="16" cy="16" r="14.6" fill="none" stroke="#D6FF63" stroke-width="1.15" opacity="0.85"/>'
    '<g class="mp-seg" fill="none" stroke="#C8F542" stroke-linecap="round">'
    '<circle cx="16" cy="16" r="13" stroke-width="1.25" opacity="0.55"/>'
    '<circle cx="16" cy="16" r="9" stroke-width="1.1" opacity="0.4"/>'
    '<circle cx="16" cy="16" r="5.5" stroke-width="1" opacity="0.32"/>'
    '<circle cx="16" cy="16" r="1.75" fill="#C8F542" stroke="none"/>'
    '<path d="M11.98 3.64 A 13 13 0 0 1 28.50 12.42" stroke-width="2.05"/>'
    '<path d="M16 7 A 9 9 0 0 1 23.79 11.50" stroke-width="1.9"/>'
    '<path d="M17.52 10.71 A 5.5 5.5 0 0 1 20.21 12.46" stroke-width="1.75"/>'
    "</g></svg>"
)


def _scr_spark(history: Optional[pd.DataFrame], column: str) -> str:
    if history is None or history.empty or column not in history.columns:
        return ""
    values = pd.to_numeric(history[column], errors="coerce").dropna().tail(24)
    if len(values) < 3:
        return ""
    nums = [float(v) for v in values]
    lo, hi = min(nums), max(nums)
    span = hi - lo or 1.0
    pts = []
    for i, val in enumerate(nums):
        x = 2 + i * 74 / (len(nums) - 1)
        y = 26 - (val - lo) / span * 20
        pts.append(f"{x:.1f},{y:.1f}")
    return f'<svg viewBox="0 0 78 30" aria-hidden="true"><polyline points="{" ".join(pts)}"/></svg>'


def _scr_unrate_chart(history: Optional[pd.DataFrame]) -> str:
    if history is None or history.empty or "UNRATE" not in history.columns:
        return ""
    values = pd.to_numeric(history["UNRATE"], errors="coerce").dropna().tail(48)
    if len(values) < 3:
        return ""
    nums = [float(v) for v in values]
    lo, hi = min(nums) - 0.15, max(nums) + 0.15
    span = hi - lo or 1.0
    w, h, left, top, right, bot = 1380, 188, 36, 10, 12, 22
    plot_w, plot_h = w - left - right, h - top - bot
    pts = []
    for i, val in enumerate(nums):
        x = left + i * plot_w / (len(nums) - 1)
        y = top + (1 - (val - lo) / span) * plot_h
        pts.append((x, y))
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = (
        f"M{pts[0][0]:.1f},{top + plot_h:.1f} "
        + " ".join(f"L{x:.1f},{y:.1f}" for x, y in pts)
        + f" L{pts[-1][0]:.1f},{top + plot_h:.1f} Z"
    )
    grids = []
    for i in range(4):
        y = top + plot_h * i / 3
        v = hi - span * i / 3
        grids.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{w - right}" y2="{y:.1f}" '
            f'stroke="rgba(255,255,255,0.06)" stroke-width="1"/>'
            f'<text x="{left - 6}" y="{y + 3:.1f}" text-anchor="end" fill="#64748B" '
            f'font-size="10" font-family="JetBrains Mono,monospace">{v:.1f}</text>'
        )
    return (
        f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" aria-hidden="true">'
        + "".join(grids)
        + f'<path d="{area}" fill="rgba(56,189,248,0.16)"/>'
        f'<polyline points="{line}" fill="none" stroke="#38BDF8" stroke-width="2.2" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
        "</svg>"
    )


def _eco_line(history: Optional[pd.DataFrame], column: str, color: str) -> str:
    if history is None or history.empty or column not in history.columns:
        return ""
    values = pd.to_numeric(history[column], errors="coerce").dropna().tail(60)
    if len(values) < 3:
        return ""
    nums = [float(v) for v in values]
    lo, hi = min(nums), max(nums)
    span = (hi - lo) or 1.0
    width, height, pad = 320.0, 90.0, 6.0
    pts = []
    for i, val in enumerate(nums):
        x = pad + i * (width - pad * 2) / (len(nums) - 1)
        y = height - pad - (val - lo) / span * (height - pad * 2)
        pts.append(f"{x:.1f},{y:.1f}")
    line = " ".join(pts)
    area = f"M{pts[0]} " + " ".join(f"L{p}" for p in pts[1:]) + f" L{width - pad:.1f},{height:.1f} L{pad:.1f},{height:.1f} Z"
    return (
        f'<svg viewBox="0 0 {width:.0f} {height:.0f}" preserveAspectRatio="none" aria-hidden="true">'
        f'<path d="{area}" fill="{color}" fill-opacity="0.16"/>'
        f'<polyline points="{line}" fill="none" stroke="{color}" stroke-width="2" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
        "</svg>"
    )


def _jobs_screen_html(snapshot: dict, labor: dict, series: dict) -> str:
    history = _read_history()
    curve = series.get("T10Y2Y") or {}
    sahm = series.get("SAHMREALTIME") or {}
    hy = series.get("BAMLH0A0HYM2") or {}
    sent = series.get("UMCSENT") or {}
    cards = (
        ("Bond market warning", "Updated every market day", "Below 0 often shows up before a recession.", "T10Y2Y", curve, "—" if curve.get("value") is None else f"{float(curve['value']):.2f} pp"),
        ("Recession warning light", "Updated once a month", "0.50 has marked every U.S. recession since 1970.", "SAHMREALTIME", sahm, "—" if sahm.get("value") is None else f"{float(sahm['value']):.2f}"),
        ("Risky-company loan costs", "Updated every market day", "Extra interest shaky companies pay.", "BAMLH0A0HYM2", hy, "—" if hy.get("value") is None else f"{float(hy['value']):.1f}%"),
        ("How confident families feel", "Updated once a month", "Below 70 is recession-era gloom.", "UMCSENT", sent, "—" if sent.get("value") is None else f"{float(sent['value']):.1f}"),
    )
    card_html = []
    for title, meta, note, col, item, value in cards:
        level, label = indicator_status({"series_id": col, "value": item.get("value"), "flagged": bool(item.get("flagged"))})
        card_html.append(
            '<div class="mp-eco-card">'
            f"<p>{html.escape(title)}</p><em>{html.escape(meta)}</em>"
            f'<div class="mp-eco-val"><strong>{html.escape(value)}</strong>{_scr_spark(history, col)}</div>'
            f'<span class="mp-scr-badge {html.escape(level)}">{html.escape(label)}</span>'
            f'<div class="mp-eco-note">{html.escape(note)}</div></div>'
        )
    charts = (
        ("Bond market warning", "T10Y2Y", "#3B5BFF"),
        ("Recession warning light", "SAHMREALTIME", "#38BDF8"),
    )
    chart_html = "".join(
        f'<div class="mp-eco-chart"><span>{html.escape(title)}</span>{_eco_line(history, col, color)}</div>'
        for title, col, color in charts
    )
    return (
        '<div class="mp-eco">'
        "<h3>Is the country heading toward trouble?</h3>"
        '<p class="mp-eco-sub">Four early warning lights that usually move first.</p>'
        '<div class="mp-eco-kicker">Right now</div>'
        f'<div class="mp-eco-cards">{"".join(card_html)}</div>'
        f'<div class="mp-eco-charts">{chart_html}</div>'
        "</div>"
    )


def landing_html(snapshot: dict, labor: dict, series: dict) -> str:
    consumer = snapshot.get("consumer") or {}
    national = (snapshot.get("labor_markets") or {}).get("All States") or {}
    index_value = float(snapshot.get("macro_stress_index") or 0)
    notices = int(labor.get("notices") or 0)
    unrate = national.get("unemployment")
    unrate_text = "—" if unrate is None else f"{float(unrate):.1f}%"
    apr = consumer.get("cc_apr")
    apr_text = "—" if apr is None else f"{float(apr):.1f}%"
    bar = max(2.0, min(100.0, index_value))
    return (
        '<!-- lp-hero --><div class="lp-hero-full">'
        '<div class="lp-fx">'
        f'{_studio_tag("lp-wave", _HERO_WAVE_URI)}'
        '<svg class="lp-drift" viewBox="0 0 2880 240" preserveAspectRatio="none" aria-hidden="true">'
        '<path fill="none" stroke="#C8F542" stroke-width="2.4" d="M0 120C360 40 360 200 720 120C1080 40 1080 200 1440 120C1800 40 1800 200 2160 120C2520 40 2520 200 2880 120"/>'
        '<path fill="none" stroke="#38BDF8" stroke-width="1.7" opacity="0.85" d="M0 156C360 214 360 98 720 156C1080 214 1080 98 1440 156C1800 214 1800 98 2160 156C2520 214 2520 98 2880 156"/>'
        '</svg>'
        '<div class="lp-vignette"></div></div>'
        '<div class="lp-hero-inner"><div class="lp-hero-copy">'
        "<h1>See it all coming</h1>"
        "<p>Millions of filings and data points, stress-tested daily into an early "
        "warning for jobs, loans, rent, cards, and the economy.</p>"
        '<div class="lp-pills">'
        f'<span class="lp-pill">Stress <b>{index_value:.0f}%</b></span>'
        f'<span class="lp-pill">Jobless <b>{html.escape(unrate_text)}</b></span>'
        f'<span class="lp-pill">Notices <b>{notices:,}</b></span>'
        f'<span class="lp-pill">Card APR <b>{html.escape(apr_text)}</b></span>'
        "</div></div>"
        '<div class="lp-device">'
        '<div class="lp-device-kicker">Economy stress</div>'
        f'<div class="lp-device-score">{index_value:.0f}%</div>'
        f'<div class="lp-device-bar"><i style="width:{bar:.1f}%"></i></div>'
        '<div class="lp-device-rows">'
        f"<div><span>U.S. jobless rate</span><b>{html.escape(unrate_text)}</b></div>"
        f"<div><span>Layoff notices</span><b>{notices:,}</b></div>"
        f"<div><span>Typical card APR</span><b>{html.escape(apr_text)}</b></div>"
        "</div></div></div></div>"
    )


def _money(value: object, digits: int = 1, suffix: str = "%") -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if number != number:
        return "—"
    return f"{number:.{digits}f}{suffix}"


def _bills_screen_html(snapshot: dict) -> str:
    consumer = snapshot.get("consumer") or {}
    rows = (
        ("Home-loan rate", _money(consumer.get("mortgage_rate"), 2)),
        ("Rent vs last year", _money(consumer.get("rent_yoy"), 1)),
        ("Income families save", _money(consumer.get("saving_rate"), 1)),
        ("Typical card APR", _money(consumer.get("cc_apr"), 1)),
        ("Late card payments", _money(consumer.get("cc_delinquency"), 2)),
    )
    body = "".join(
        f'<div class="mp-mini-row"><span>{html.escape(label)}</span>'
        f"<strong>{html.escape(value)}</strong></div>"
        for label, value in rows
    )
    return (
        '<div class="mp-mini"><div class="mp-mini-top"><div class="mp-mini-brand">'
        f"{_MP_MARK}MacroPulse</div><b>Bills</b></div>{body}</div>"
    )


def _sim_row(label: str, value: str, pct: float) -> str:
    width = max(4.0, min(96.0, pct))
    return (
        '<div class="mp-sim-row"><span>'
        f"{html.escape(label)}<b>{html.escape(value)}</b></span>"
        f'<div class="mp-sim-track"><i style="width:{width:.1f}%"></i>'
        f'<em style="left:{width:.1f}%"></em></div></div>'
    )


def _whatif_screen_html(snapshot: dict, series: dict) -> str:
    consumer = snapshot.get("consumer") or {}
    national = (snapshot.get("labor_markets") or {}).get("All States") or {}
    index_value = float(snapshot.get("macro_stress_index") or 0)
    unrate = national.get("unemployment")
    mortgage = consumer.get("mortgage_rate")
    late = consumer.get("cc_delinquency")
    spread = (series.get("BAMLH0A0HYM2") or {}).get("value")
    spread_bps = None if spread is None else float(spread) * 100.0

    def span(value: object, lo: float, hi: float) -> float:
        if value is None:
            return 12.0
        return (float(value) - lo) / (hi - lo) * 100.0

    rows = (
        _sim_row("Jobless rate", _money(unrate, 1), span(unrate, 3.5, 10.0))
        + _sim_row("Home-loan rate", _money(mortgage, 2), span(mortgage, 5.0, 12.0))
        + _sim_row("Late card payments", _money(late, 2), span(late, 2.0, 10.0))
        + _sim_row("Risky-loan extra", _money(spread_bps, 0, " bps"), span(spread_bps, 300.0, 1200.0))
    )
    return (
        '<div class="mp-sim"><div class="mp-sim-top"><div class="mp-sim-brand">'
        f"{_MP_MARK}MacroPulse</div>"
        '<span class="mp-sim-live"><i></i>WHAT IF</span></div>'
        '<div class="mp-sim-score"><div><small>Economy stress</small>'
        f"<strong>{index_value:.1f}%</strong></div></div>"
        "<p class=\"mp-sim-note\">Today’s numbers. Move the sliders to replay 2008.</p>"
        f'<div class="mp-sim-rows">{rows}</div>'
        '<div class="mp-sim-actions"><span>Back to today</span>'
        '<span class="primary">2008</span></div></div>'
    )


def landing_rest_html(snapshot: dict, labor: dict, series: dict) -> str:
    return (
        '<!-- lp-rest --><div class="lp-jobs"><div class="lp-jobs-frame">'
        f'{_studio_tag("lp-jobs-photo", _JOBS_STUDIO_URI)}'
        f'<div class="lp-jobs-screen">{_jobs_screen_html(snapshot, labor, series)}</div>'
        '<div class="lp-jobs-copy"><div class="lp-band-kicker">Jobs</div>'
        "<h2>Know about layoffs ~60 days early</h2>"
        "<p>Large employers have to warn workers before big cuts. Search those notices "
        "by company, city, and state — California, Texas, New York, and Florida.</p>"
        "</div></div></div>"
        '<div class="lp-hh">'
        f'{_studio_tag("lp-hh-photo", _HH_STUDIO_URI)}'
        '<div class="lp-hh-copy"><div class="lp-band-kicker">Households</div>'
        "<h2>Spot cash running out</h2>"
        "<p>Watch home-loan rates, leftover savings, late car payments, and 21% card APRs "
        "together. That’s the sequence families feel before a recession headline.</p>"
        "</div></div>"
        '<div class="lp-why"><h2>The MacroPulse head start</h2>'
        '<div class="lp-why-grid">'
        '<article><svg viewBox="0 0 32 32" aria-hidden="true"><path d="M16 5a6 6 0 0 0-6 6v4.2c0 .7-.2 1.4-.7 2L7 20.5h18l-2.3-3.3a3.5 3.5 0 0 1-.7-2V11a6 6 0 0 0-6-6z"/><path d="M13.2 23.2a2.8 2.8 0 0 0 5.6 0"/></svg>'
        "<h3>You hear about layoffs before the break room does.</h3>"
        "<p>Big employers have to warn workers first. Search those notices by company and city "
        "across California, Texas, New York, and Florida — about 60 days before the headline.</p></article>"
        '<article><svg viewBox="0 0 32 32" aria-hidden="true"><circle cx="16" cy="16" r="10"/><path d="M16 16 L22.5 11"/><circle cx="16" cy="16" r="1.4"/></svg>'
        "<h3>Four lights. The whole economy. No terminal required.</h3>"
        "<p>The bond market, the recession warning, what shaky companies pay to borrow, "
        "and how grim families feel. The gauges that usually move first, already on.</p></article>"
        '<article><svg viewBox="0 0 32 32" aria-hidden="true"><path d="M5 15 L16 6 L27 15"/><path d="M8.5 13.5 V26 H23.5 V13.5"/></svg>'
        "<h3>You catch a household running out of cash while it is still quiet.</h3>"
        "<p>Home-loan rates, leftover savings, late car payments, and card APRs near 21%. "
        "Lined up in the order families feel them, long before it has a name.</p></article>"
        '<article><svg viewBox="0 0 32 32" aria-hidden="true"><path d="M15 8 L6 16 L15 24 Z"/><path d="M26 8 L17 16 L26 24 Z"/></svg>'
        "<h3>You can replay 2008 before your coffee cools.</h3>"
        "<p>Move the sliders. Today’s stress score. A one-minute dress rehearsal for the last crisis "
        "that snaps back and never touches real life.</p></article>"
        "</div></div>"
        '<div class="lp-play"><div class="lp-play-head">'
        '<div class="lp-play-badge">Tactical advantage</div>'
        "<h2>How Everyday People Use MacroPulse as an Unfair Advantage</h2>"
        "<p>The same public numbers, read before a purchase, a move, or a job search.</p></div>"
        '<div class="lp-play-grid">'
        "<article><div class=\"lp-play-name\">The Car Buyer's Edge</div>"
        '<div class="lp-play-hook">New-car sales, debt, and late payments</div>'
        "<h3>Walk onto the lot with the national picture</h3>"
        "<p>The Cars page shows the yearly pace of sales, how much Americans still owe on vehicles, "
        "how many bank car loans are about three months late, and the typical new-car rate. "
        "Those figures come from the Federal Reserve.</p>"
        '<em class="lp-play-tag">[BEFORE YOU BUY A CAR]</em></article>'
        '<article><div class="lp-play-name">The Homebuyer\'s Shield</div>'
        '<div class="lp-play-hook">Mortgage rate, rent, savings, and cards</div>'
        "<h3>See the household bills in one place</h3>"
        "<p>The Bills page shows the 30-year home-loan rate, rent versus last year, the share of "
        "income families still save, card balances, the typical card rate, and how many card "
        "payments are late.</p>"
        '<em class="lp-play-tag">[BEFORE YOU BUY A HOME]</em></article>'
        '<article><div class="lp-play-name">The Career Early-Warning System</div>'
        '<div class="lp-play-hook">WARN notices in four states</div>'
        "<h3>Hear about large layoffs before the headline</h3>"
        "<p>California, Texas, New York, and Florida require many large employers to file a notice "
        "about 60 days before a big layoff or closing. The Jobs page lists those filings by "
        "company, city, and state.</p>"
        '<em class="lp-play-tag">[BEFORE A JOB SEARCH]</em></article>'
        "</div></div>"
    )


def _scroll_landing_to_top() -> None:
    """Home and a refresh should open the homepage at the top, not mid-page."""
    import streamlit.components.v1 as components

    components.html(
        """
        <script>
        const scrollTop = () => {
          const parent = window.parent;
          const doc = parent.document;
          try { parent.history.scrollRestoration = "manual"; } catch (err) {}
          [
            parent,
            doc.scrollingElement,
            doc.documentElement,
            doc.body,
            doc.querySelector('[data-testid="stMain"]'),
            doc.querySelector('[data-testid="stAppViewContainer"]'),
            doc.querySelector("section.main")
          ].forEach((node) => {
            if (!node) return;
            if (node.scrollTo) node.scrollTo(0, 0);
            node.scrollTop = 0;
          });
        };
        scrollTop();
        [40, 160, 400].forEach((ms) => setTimeout(scrollTop, ms));
        </script>
        """,
        height=0,
    )


def render_landing_page(snapshot: dict, labor: dict, series: dict) -> None:
    _scroll_landing_to_top()
    st.markdown(
        """
        <style>
        section[data-testid="stSidebar"],
        [data-testid="collapsedControl"],
        [data-testid="stSidebarCollapsedControl"],
        [data-testid="stExpandSidebarButton"] { display: none !important; }
        header[data-testid="stHeader"],
        [data-testid="stHeader"] {
            display: none !important;
            height: 0 !important;
            min-height: 0 !important;
        }
        .stApp { --header-height: 0rem !important; }
        [data-testid="stMainBlockContainer"] > div > [data-testid="stVerticalBlock"] {
            gap: 0 !important;
        }
        [data-testid="stElementContainer"]:has(style) {
            display: none !important;
            height: 0 !important;
            margin: 0 !important;
            padding: 0 !important;
        }
        div.st-key-lp_header {
            position: sticky;
            top: 0;
            z-index: 60;
            background: rgba(5, 7, 12, 0.94);
            border-bottom: 1px solid rgba(255, 255, 255, 0.06);
            min-height: 3.5rem;
            height: 3.5rem;
            display: flex !important;
            align-items: center !important;
            padding: 0 2.4vw !important;
        }
        div.st-key-lp_header > div {
            width: 100%;
        }
        div.st-key-lp_header [data-testid="stHorizontalBlock"] {
            display: flex !important;
            flex-wrap: nowrap !important;
            align-items: center !important;
            justify-content: center !important;
            gap: 0.35rem !important;
            min-height: 3.5rem !important;
            height: 3.5rem !important;
            width: 100% !important;
        }
        div.st-key-lp_header > div > [data-testid="stHorizontalBlock"] {
            gap: 1.8rem !important;
        }
        div.st-key-lp_header [data-testid="stColumn"] {
            flex: 0 0 auto !important;
            width: auto !important;
            max-width: none !important;
        }
        div.st-key-lp_header .stButton,
        div.st-key-lp_header .stButton > button {
            width: auto !important;
        }
        div.st-key-lp_header div.st-key-lp_logo,
        div.st-key-lp_header div.st-key-lp_logo .stButton,
        div.st-key-lp_header div.st-key-lp_logo button {
            width: 11rem !important;
            min-width: 11rem !important;
            height: 2.5rem !important;
            min-height: 2.5rem !important;
        }
        div.st-key-lp_header [data-testid="stColumn"],
        div.st-key-lp_header [data-testid="stVerticalBlock"],
        div.st-key-lp_header [data-testid="stElementContainer"],
        div.st-key-lp_header [data-testid="stButton"],
        div.st-key-lp_header [data-testid="stMarkdownContainer"] {
            margin: 0 !important;
            padding: 0 !important;
            min-height: 0 !important;
            height: 3.5rem !important;
            display: flex !important;
            align-items: center !important;
            gap: 0 !important;
        }
        div.st-key-lp_header > div > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"],
        div.st-key-lp_header > div > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] [data-testid="stVerticalBlock"],
        div.st-key-lp_header > div > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] [data-testid="stMarkdownContainer"],
        div.st-key-lp_header > div > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] [data-testid="stHorizontalBlock"],
        div.st-key-lp_header > div > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] [data-testid="stButton"] {
            justify-content: center !important;
        }
        div.st-key-lp_header > div > [data-testid="stHorizontalBlock"] {
            justify-content: flex-start !important;
        }
        div.st-key-lp_header > div > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:first-child,
        div.st-key-lp_header > div > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:last-child {
            flex: 1 1 0 !important;
            width: auto !important;
            min-width: 0 !important;
        }
        div.st-key-lp_header > div > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:first-child,
        div.st-key-lp_header > div > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:first-child [data-testid="stVerticalBlock"],
        div.st-key-lp_header > div > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:first-child [data-testid="stMarkdownContainer"] {
            justify-content: flex-start !important;
        }
        div.st-key-lp_header > div > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:last-child,
        div.st-key-lp_header > div > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:last-child [data-testid="stVerticalBlock"] {
            justify-content: flex-end !important;
        }
        div.st-key-lp_header > div > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:last-child [data-testid="stHorizontalBlock"] {
            width: max-content !important;
            margin-left: auto !important;
            justify-content: flex-end !important;
            gap: 0.7rem !important;
        }
        div.st-key-lp_header p {
            margin: 0 !important;
            line-height: 1 !important;
        }
        html, body, .stApp {
            overflow-x: hidden !important;
        }
        [data-testid="stAppViewContainer"],
        [data-testid="stMain"],
        [data-testid="stMainBlockContainer"],
        [data-testid="stMainBlockContainer"].block-container,
        [data-testid="stAppViewBlockContainer"],
        [data-testid="stBottomBlockContainer"],
        .stMain,
        .stMainBlockContainer,
        .block-container,
        .stAppViewContainer {
            max-width: none !important;
            width: 100% !important;
            padding-left: 0 !important;
            padding-right: 0 !important;
            margin-left: 0 !important;
            margin-right: 0 !important;
        }
        [data-testid="stMainBlockContainer"].block-container {
            padding-top: 0 !important;
            padding-bottom: 0 !important;
        }
        .stApp {
            background-color: #05070C !important;
            background-image: none !important;
        }
        .stApp::before, .stApp::after { display: none !important; }
        [data-testid="stVerticalBlockBorderWrapper"],
        [data-testid="stVerticalBlockBorderWrapper"] > div,
        [data-testid="stVerticalBlock"],
        [data-testid="stHorizontalBlock"],
        [data-testid="stElementContainer"],
        [data-testid="stMarkdownContainer"] {
            background: transparent !important;
            border: none !important;
            border-radius: 0 !important;
            box-shadow: none !important;
        }
        [data-testid="stVerticalBlockBorderWrapper"] > div {
            padding: 0 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    with st.container(key="lp_header"):
        left_col, mid_col, right_col = st.columns([1, 2.2, 1], vertical_alignment="center")
        with left_col:
            st.markdown(
                f'<div class="lp-header-brand">{_MP_MARK}'
                '<span class="brand-name">MacroPulse</span></div>',
                unsafe_allow_html=True,
            )
            if st.button("MacroPulse", key="lp_logo"):
                st.session_state.app_view = "landing"
                st.rerun()
        with mid_col:
            econ_col, cars_col, bills_col, jobs_col = st.columns(4, vertical_alignment="center")
            with econ_col:
                if st.button("Economy", use_container_width=True, key="lp_nav_economy"):
                    st.session_state.main_tab = "Economy"
                    st.session_state.app_view = "dashboard"
                    st.rerun()
            with cars_col:
                if st.button("Cars", use_container_width=True, key="lp_nav_cars"):
                    st.session_state.main_tab = "Cars"
                    st.session_state.app_view = "dashboard"
                    st.rerun()
            with bills_col:
                if st.button("Bills", use_container_width=True, key="lp_nav_bills"):
                    st.session_state.main_tab = "Bills"
                    st.session_state.app_view = "dashboard"
                    st.rerun()
            with jobs_col:
                if st.button("Jobs", use_container_width=True, key="lp_nav_jobs"):
                    st.session_state.main_tab = "Jobs"
                    st.session_state.app_view = "dashboard"
                    st.rerun()
        with right_col:
            lang_col, login_col, signup_col = st.columns([1.05, 0.8, 0.95], vertical_alignment="center")
            with lang_col:
                st.markdown(
                    '<div class="lp-lang">'
                    '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">'
                    '<circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.6"/>'
                    '<path d="M3 12h18M12 3c2.5 2.6 3.8 5.7 3.8 9s-1.3 6.4-3.8 9c-2.5-2.6-3.8-5.7-3.8-9S9.5 5.6 12 3z" '
                    'stroke="currentColor" stroke-width="1.6"/>'
                    "</svg>English</div>",
                    unsafe_allow_html=True,
                )
            with login_col:
                if st.button("Log in", use_container_width=True, key="lp_login"):
                    login_dialog()
            with signup_col:
                if st.button("Sign up", use_container_width=True, key="lp_signup"):
                    signup_dialog()
    st.markdown(landing_html(snapshot, labor, series), unsafe_allow_html=True)
    with st.container(key="lp_cta"):
        if st.button("Get started", type="primary", key="lp_enter"):
            st.session_state.app_view = "dashboard"
            st.rerun()
    st.markdown(landing_rest_html(snapshot, labor, series), unsafe_allow_html=True)
    st.markdown(
        '<p class="lp-foot">MacroPulse © 2026. Live U.S. jobs, bills, and layoff notices. Not investment advice.</p>',
        unsafe_allow_html=True,
    )


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


def fear_gauge_status(value: Optional[float]) -> Tuple[str, str]:
    if value is None:
        return _missing_status()
    if value >= 30:
        return STATUS_STRESS, "Nervous"
    if value >= 20:
        return STATUS_CAUTION, "Uneasy"
    return STATUS_HEALTHY, "Calm"


def gas_price_status(value: Optional[float]) -> Tuple[str, str]:
    if value is None:
        return _missing_status()
    if value >= 4.5:
        return STATUS_STRESS, "High"
    if value >= 3.5:
        return STATUS_CAUTION, "Elevated"
    return STATUS_HEALTHY, "Calm"


def oil_price_status(value: Optional[float]) -> Tuple[str, str]:
    if value is None:
        return _missing_status()
    if value >= 100:
        return STATUS_STRESS, "High"
    if value >= 80:
        return STATUS_CAUTION, "Elevated"
    return STATUS_HEALTHY, "Calm"


def delinquency_status(value: Optional[float], flagged: bool = False) -> Tuple[str, str]:
    if value is None:
        return _missing_status()
    if flagged or value > 3.0:
        return STATUS_STRESS, "Warning"
    if value >= 2.5:
        return STATUS_CAUTION, "Caution"
    return STATUS_HEALTHY, "Healthy"


def commercial_auto_dq_status(value: Optional[float]) -> Tuple[str, str]:
    if value is None:
        return _missing_status()
    if value > AUTO_DQ_STRESS_THRESHOLD:
        return STATUS_STRESS, "High repo threat"
    if value >= 2.0:
        return STATUS_CAUTION, "Rising caution"
    return STATUS_HEALTHY, "On track"


def car_loan_rate_status(value: Optional[float]) -> Tuple[str, str]:
    return band_status(
        value,
        stress_at=9.0,
        caution_at=7.5,
        caution_label="Elevated",
        stress_label="Payment strain",
    )


def mortgage_status(value: Optional[float]) -> Tuple[str, str]:
    return band_status(value, stress_at=8.0, caution_at=6.5)


def rent_status(value: Optional[float]) -> Tuple[str, str]:
    return band_status(value, stress_at=6.0, caution_at=4.0)


def revolving_status(value: Optional[float]) -> Tuple[str, str]:
    if value is None:
        return _missing_status()
    return STATUS_CAUTION, "Monitored"


def card_apr_status(value: Optional[float]) -> Tuple[str, str]:
    return band_status(
        value,
        stress_at=22.0,
        caution_at=20.0,
        caution_label="Elevated",
        stress_label="Record high",
    )


def subprime_card_status(value: Optional[float]) -> Tuple[str, str]:
    if value is None:
        return _missing_status()
    if value >= 7.0:
        return STATUS_STRESS, "High stress"
    if value >= 5.0:
        return STATUS_CAUTION, "Elevated"
    return STATUS_HEALTHY, "On track"


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
        body = '<p class="major-actions-empty">No layoff notices in this area.</p>'
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
        "<h3>Biggest layoff notices in this area</h3>"
        f"{body}</div>"
    )


def _published_card(badge: str, name: str, figure: str, measure: str, source: str) -> str:
    return (
        "<div class='oem-card coping-item'>"
        f"<span class='oem-badge'>{html.escape(badge)}</span>"
        f"<h4>{html.escape(name)}</h4>"
        f"<p class='oem-figure'>{html.escape(figure)}</p>"
        f"<p class='oem-lease'>{html.escape(measure)}</p>"
        f"<p class='oem-source'>{html.escape(source)}</p>"
        "</div>"
    )


def _report_cards_html(items: list) -> str:
    return "".join(
        _published_card(
            item["badge"],
            item["name"],
            item["figure"],
            item["measure"],
            item["source"],
        )
        for item in items
    )


def automaker_radar_html() -> str:
    cards = _report_cards_html(load_company_reports()["autos"])
    return (
        "<div class='strain-card' style='margin-bottom:0.35rem'>"
        "<div class='hero-kicker'>Who finances the cars</div>"
        "<h3>What the car-finance companies have published</h3>"
        "<p class='strain-copy'>Toyota, Ford, GM, and Honda are read from the latest filing once a month "
        "and move when a new quarterly report comes out. Hyundai, Nissan, and BMW stay on the last "
        "published pool or annual figure. The date on each card is the date of that report.</p>"
        f"<div class='oem-grid'>{cards}</div>"
        "</div>"
    )


def card_issuer_radar_html() -> str:
    cards = _report_cards_html(load_company_reports()["cards"])
    return (
        "<div class='strain-card' style='margin-bottom:0.35rem'>"
        "<div class='hero-kicker'>Who issued the cards</div>"
        "<h3>What the card companies have published</h3>"
        "<p class='strain-copy'>These are read from each company's monthly report. "
        "Chase, Citi, and Capital One cover the card trust, which is only part of the bank. "
        "American Express is every U.S. consumer card.</p>"
        f"<div class='issuer-grid'>{cards}</div>"
        "</div>"
    )


def coping_index_html() -> str:
    return """
    <div class="strain-card">
      <h3>How families are stretching cash</h3>
      <p class="strain-copy">Three published readings on households short of cash.</p>
      <div class="coping-grid">
        <div class="coping-item">
          <span class="alert-badge warn">2025</span>
          <h4>Raiding retirement savings</h4>
          <p>
            <strong>6%</strong> of people in Vanguard 401(k) plans took a hardship
            withdrawal. The most common reason was avoiding eviction or foreclosure.
            Vanguard, 2026.
          </p>
        </div>
        <div class="coping-item">
          <span class="alert-badge active">Groceries</span>
          <h4>Pay-later for groceries</h4>
          <p>
            Nearly <strong>1 in 10</strong> working-age adults used buy-now-pay-later
            for groceries. Urban Institute.
          </p>
        </div>
        <div class="coping-item">
          <span class="alert-badge alert">Utilities</span>
          <h4>Unpaid electric and gas bills</h4>
          <p>
            Households owe about <strong>$23 billion</strong> in unpaid electric and
            gas bills. About one in six is behind. NEADA, November 2025.
          </p>
        </div>
      </div>
    </div>
    """


def safe_indicator(series: dict, series_id: str) -> dict:
    item = (series or {}).get(series_id)
    if isinstance(item, dict):
        return item
    return {
        "series_id": series_id,
        "value": None,
        "display_unit": "",
        "threshold_label": series_id,
        "flagged": False,
    }


def render_indicator_metric(
    item: dict,
    label: str | None = None,
    note: str | None = None,
    history: Optional[pd.DataFrame] = None,
    status: Optional[Tuple[str, str]] = None,
    value_text: str | None = None,
    meta: str | None = None,
    higher_is_worse: bool = True,
    show_status: bool = True,
) -> None:
    item = item if isinstance(item, dict) else {}
    series_id = str(item.get("series_id") or "")
    unit = str(item.get("display_unit") or "")
    freq = SERIES_FREQ.get(series_id, "FRED")
    prints = series_prints(history, series_id)
    delta_html = ""
    if len(prints) >= 2:
        last = prints.iloc[-1]
        prev = prints.iloc[-2]
        last_val = pd.to_numeric(last.get(series_id), errors="coerce")
        prev_val = pd.to_numeric(prev.get(series_id), errors="coerce")
        if pd.notna(last_val) and pd.notna(prev_val):
            delta = float(last_val) - float(prev_val)
            last_dt = pd.to_datetime(last["date"], errors="coerce")
            if pd.notna(last_dt) and freq in {"Daily", "Weekly"}:
                printed = last_dt.strftime("%b ") + str(int(last_dt.day))
            elif pd.notna(last_dt):
                printed = last_dt.strftime("%b %Y")
            else:
                printed = "—"
            direction = "up" if delta > 0 else ("down" if delta < 0 else "")
            if direction and not higher_is_worse:
                direction = "down" if direction == "up" else "up"
            delta_html = (
                f'<div class="mp-metric-delta {direction}">'
                f"{html.escape(printed)} · {html.escape(format_print_delta(delta, unit))}"
                f"</div>"
            )
    spark = sparkline_svg(history, series_id)
    title = label or item.get("name") or series_id
    shown = value_text if value_text is not None else format_indicator_value(item)
    meta_line = meta or FREQ_PLAIN.get(freq, "Official U.S. data")
    # One line. A blank line makes Streamlit print the closing tag as text.
    metric_html = (
        f'<div class="mp-metric" title="{html.escape(str(item.get("threshold_label") or series_id))}">'
        f'<p class="mp-metric-title">{html.escape(title)}</p>'
        f'<p class="mp-metric-meta">{html.escape(meta_line)}</p>'
        '<div class="mp-metric-row"><div>'
        f'<div class="mp-metric-value">{html.escape(shown)}</div>'
        f"{delta_html}</div>{spark}</div></div>"
    )
    st.markdown(metric_html, unsafe_allow_html=True)
    level, status_label = status if status is not None else indicator_status(item)
    if show_status:
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
CHART_ICE = "#7DD3FC"
CHART_CORAL = "#EF4444"
CHART_INDIGO_FILL = "rgba(59, 91, 255, 0.16)"
CHART_CRIMSON_FILL = "rgba(239, 68, 68, 0.16)"
CHART_EMERALD = "#38BDF8"
BAR_SLATE = "#2A364F"
BAR_HIGHLIGHT = "#38BDF8"


def _chart_title(title: str) -> str:
    """Wrap a chart title so a narrow chart does not clip it."""
    if not title or "<br>" in title or len(title) <= 32:
        return title
    words = title.split()
    lines: List[str] = []
    line = ""
    for word in words:
        trial = f"{line} {word}".strip()
        if line and len(trial) > 28:
            lines.append(line)
            line = word
        else:
            line = trial
    if line:
        lines.append(line)
    return "<br>".join(lines)


def apply_chart_theme(fig: go.Figure, height: int = 300, title: str | None = None) -> go.Figure:
    layout = {
        "template": "plotly_dark",
        "height": height,
        "margin": dict(l=8, r=16, t=12, b=8),
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "rgba(0,0,0,0)",
        "font": {"color": CHART_FONT, "family": "Inter, sans-serif"},
        "hovermode": "x unified",
    }
    if title:
        layout["title"] = {
            "text": _chart_title(title),
            "x": 0,
            "xanchor": "left",
            "automargin": True,
            "font": {"size": 13, "color": CHART_MUTED, "family": "Inter, sans-serif"},
        }
    fig.update_layout(**layout)
    fig.update_xaxes(
        automargin=True,
        showgrid=False,
        zeroline=False,
        showline=False,
        ticks="",
        nticks=4,
        gridcolor=CHART_GRID,
        zerolinecolor=CHART_GRID,
        linecolor="rgba(0,0,0,0)",
        tickfont={"color": CHART_MUTED, "size": 11, "family": "JetBrains Mono, monospace"},
    )
    fig.update_yaxes(
        automargin=True,
        showgrid=True,
        zeroline=False,
        showline=False,
        ticks="",
        gridcolor=CHART_GRID,
        zerolinecolor=CHART_GRID,
        linecolor="rgba(0,0,0,0)",
        tickfont={"color": CHART_MUTED, "size": 11, "family": "JetBrains Mono, monospace"},
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


def latest_history_value(history: pd.DataFrame, column: str) -> Optional[float]:
    values = _history_numeric(history, column).dropna()
    if values.empty:
        return None
    return float(values.iloc[-1])


def market_read_html(history: pd.DataFrame) -> str:
    """One line on whether the market and the household lights agree."""
    if history is None or history.empty:
        return ""
    if "macro_stress_index" not in history.columns or "SP500" not in history.columns:
        return ""
    stress = pd.to_numeric(history["macro_stress_index"], errors="coerce").dropna()
    market = pd.to_numeric(history["SP500"], errors="coerce").dropna()
    if len(stress) < 5 or len(market) < 5 or float(market.iloc[-5]) == 0:
        return ""
    stress_delta = float(stress.iloc[-1] - stress.iloc[-5])
    market_pct = (float(market.iloc[-1]) - float(market.iloc[-5])) / float(market.iloc[-5]) * 100.0
    lights_worse = stress_delta >= 1.5
    lights_better = stress_delta <= -1.5
    market_down = market_pct <= -2.0
    market_up = market_pct >= 2.0
    if lights_worse and market_down:
        text = "Household lights are worsening, and the market is down with them."
    elif lights_worse:
        text = "Household lights are worsening. The market has not repriced."
    elif market_down:
        text = "The market is down. The household lights have not followed."
    elif lights_better and market_up:
        text = "Household lights are calming, and the market is up with them."
    else:
        text = "The market and the household lights are pointing the same way."
    return f'<p class="mp-market-read">{html.escape(text)}</p>'


def _market_item(series_id: str, unit: str, value: Optional[float]) -> dict:
    return {
        "series_id": series_id,
        "value": value,
        "display_unit": unit,
        "threshold_label": series_id,
    }


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
    fig.update_xaxes(rangeslider_visible=False, tickformat="%b %Y")
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
        return empty_chart(title, "Series unavailable")
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=dates,
            y=left_values,
            name=left_name,
            mode="lines",
            line={
                "color": left_color,
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
    apply_chart_theme(fig, height=380, title=title)
    fig.update_layout(
        legend={
            "orientation": "h",
            "yanchor": "top",
            "y": -0.28,
            "x": 0,
            "xanchor": "left",
            "font": {"color": CHART_MUTED, "size": 11},
            "bgcolor": "rgba(0,0,0,0)",
        },
        margin={"l": 8, "r": 16, "t": 12, "b": 72},
        yaxis={
            "automargin": True,
            "showgrid": True,
            "zeroline": False,
            "showline": False,
            "gridcolor": CHART_GRID,
            "tickfont": {"color": CHART_MUTED, "size": 11, "family": "JetBrains Mono, monospace"},
        },
        yaxis2={
            "overlaying": "y",
            "side": "right",
            "automargin": True,
            "showgrid": False,
            "zeroline": False,
            "showline": False,
            "tickfont": {"color": CHART_MUTED, "size": 11, "family": "JetBrains Mono, monospace"},
        },
    )
    fig.update_xaxes(rangeslider_visible=False, tickformat="%b %Y")
    return fig


def history_window(history: pd.DataFrame, years: int = 5) -> pd.DataFrame:
    if history is None or history.empty or "date" not in history.columns:
        return pd.DataFrame()
    dates = pd.to_datetime(history["date"], errors="coerce")
    latest = dates.max()
    if pd.isna(latest):
        return history.copy()
    cutoff = latest - pd.DateOffset(years=years)
    return history.loc[dates >= cutoff].copy()


def series_peak(
    history: pd.DataFrame,
    column: str,
    start: str,
    end: str,
) -> Optional[float]:
    if history is None or history.empty or column not in history.columns:
        return None
    dates = pd.to_datetime(history["date"], errors="coerce")
    mask = (dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))
    values = pd.to_numeric(history.loc[mask, column], errors="coerce").dropna()
    if values.empty:
        return None
    return float(values.max())


def auto_delinquency_figure(history: pd.DataFrame) -> go.Figure:
    title = "How many people are falling behind on car payments?"
    window = history_window(history, years=5)
    dates = _history_dates(window)
    values = _history_numeric(window, "DRALACBS")
    if dates.empty or values.dropna().empty:
        return empty_chart(title, "Car-late-payment series unavailable")
    fig = go.Figure(
        go.Scatter(
            x=dates,
            y=values,
            mode="lines",
            name="Late car loans",
            line={
                "color": CHART_ICE,
                "width": 2.4,
                "shape": "spline",
                "smoothing": 1.3,
            },
            fill="tozeroy",
            fillcolor=CHART_CRIMSON_FILL,
            hovertemplate="%{x|%b %d, %Y}<br>%{y:.2f}%<extra>Late payments</extra>",
        )
    )
    apply_chart_theme(fig, height=380, title=title)
    fig.add_hline(y=2.0, line_dash="dot", line_color="#C8F542", line_width=1.4)
    fig.add_hline(y=2.5, line_dash="dash", line_color="#F87171", line_width=1.5)
    fig.add_annotation(
        xref="paper",
        x=0.98,
        xanchor="right",
        y=2.0,
        yref="y",
        yshift=-16,
        text="Normal, under 2%",
        showarrow=False,
        font={"size": 11, "color": "#C8F542"},
    )
    fig.add_annotation(
        xref="paper",
        x=0.98,
        xanchor="right",
        y=2.5,
        yref="y",
        yshift=14,
        text="Above 2.5%, repossession wave",
        showarrow=False,
        font={"size": 11, "color": "#FCA5A5"},
    )
    peak = float(values.max()) if values.notna().any() else 2.5
    fig.update_layout(showlegend=False)
    fig.update_yaxes(automargin=True, ticksuffix="%", range=[0, max(3.15, peak * 1.2)])
    fig.update_xaxes(rangeslider_visible=False, tickformat="%b %Y")
    return fig


def auto_leases_vs_loans_figure(history: pd.DataFrame) -> go.Figure:
    return dual_axis_figure(
        history,
        "DTCTSL",
        "Leases & maker loans",
        CHART_ICE,
        "MVLOAS",
        "All U.S. car debt",
        CHART_ACCENT,
        "Car-company loans vs all U.S. car debt",
    )


def credit_card_trap_figure(history: pd.DataFrame) -> go.Figure:
    title = "Card balances vs sky-high interest rates"
    window = history_window(history, years=5)
    dates = _history_dates(window)
    debt = _history_numeric(window, "REVOLSL")
    apr = _history_numeric(window, "TERMCBCCALLNS")
    if not debt.dropna().empty and float(debt.dropna().abs().median()) > 10_000:
        debt = debt / 1000.0
    if dates.empty or (debt.dropna().empty and apr.dropna().empty):
        return empty_chart(title, "Credit-card series unavailable")
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=dates,
            y=debt,
            name="Card balances",
            mode="lines",
            line={
                "color": CHART_ACCENT,
                "width": 2.4,
                "shape": "spline",
                "smoothing": 1.3,
            },
            fill="tozeroy",
            fillcolor=CHART_INDIGO_FILL,
            hovertemplate="%{x|%b %d, %Y}<br>$%{y:.0f}B<extra>Card debt</extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=dates,
            y=apr,
            name="Card rate",
            mode="lines",
            line={
                "color": CHART_CORAL,
                "width": 2.4,
                "shape": "spline",
                "smoothing": 1.3,
            },
            yaxis="y2",
            hovertemplate="%{x|%b %d, %Y}<br>%{y:.2f}%<extra>Card APR</extra>",
        )
    )
    apply_chart_theme(fig, height=400, title=title)
    fig.update_layout(
        legend={
            "orientation": "h",
            "yanchor": "top",
            "y": -0.28,
            "x": 0,
            "xanchor": "left",
            "font": {"color": CHART_MUTED, "size": 11},
            "bgcolor": "rgba(0,0,0,0)",
        },
        margin={"l": 8, "r": 16, "t": 12, "b": 72},
        yaxis={
            "automargin": True,
            "showgrid": True,
            "gridcolor": CHART_GRID,
            "zeroline": False,
            "tickfont": {"color": CHART_MUTED, "size": 11, "family": "JetBrains Mono, monospace"},
        },
        yaxis2={
            "overlaying": "y",
            "side": "right",
            "automargin": True,
            "ticksuffix": "%",
            "showgrid": False,
            "zeroline": False,
            "showline": False,
            "tickfont": {"color": CHART_MUTED, "size": 11, "family": "JetBrains Mono, monospace"},
        },
    )
    fig.update_xaxes(rangeslider_visible=False, showgrid=True, gridcolor=CHART_GRID, tickformat="%b %Y")
    return fig


def _active_warning_names(
    snapshot: dict,
    labor: dict,
    extra_flags: Optional[List[str]] = None,
) -> List[str]:
    on_flags: List[str] = []
    for item in snapshot["indicators"]:
        if item["flagged"] and item["series_id"] not in {"MSACSR", "PERMIT_YOY"}:
            on_flags.append(
                {
                    "Yield Curve": "Bond market warning",
                    "Sahm Rule": "Recession warning light",
                    "High-Yield Credit Spread": "Risky-company loan costs",
                    "Consumer Sentiment": "Families uncertain",
                    "Initial Jobless Claims": "More people filing for unemployment",
                    "Total Vehicle Sales": "People delaying car purchases",
                    "Personal Saving Rate": "Families saving very little",
                    "New Home Supply": "Too many new homes unsold",
                    "Building Permits YoY": "Builders pulling back",
                }.get(item["name"], item["name"])
            )
    if snapshot.get("housing_flag"):
        reason = "; ".join(snapshot.get("housing_reasons") or ["housing stress"])
        reason = reason.replace(
            "new-home supply above 8 months", "too many new homes sitting unsold"
        ).replace(
            "building permits down more than 10% YoY",
            "builders pulling back sharply",
        )
        on_flags.append(reason[:1].upper() + reason[1:] if reason else "Housing stress")
    if labor["rising"]:
        on_flags.append("Layoff notices speeding up")
    for name in extra_flags or []:
        on_flags.append(name)
    return list(dict.fromkeys(on_flags))


def takeaway_html(
    snapshot: dict,
    labor: dict,
    simulated: bool = False,
    extra_flags: Optional[List[str]] = None,
    crisis: bool = False,
) -> str:
    unique = _active_warning_names(snapshot, labor, extra_flags)
    if unique:
        extra_n = len(extra_flags or [])
        extra_bit = (
            f" Pretend-mode added {extra_n} extra warning{'s' if extra_n != 1 else ''} vs today."
            if simulated and extra_n
            else (" Pretend-mode is on, but it did not add extra warnings vs today." if simulated else "")
        )
        summary = (
            f"{len(unique)} warning light{'s' if len(unique) != 1 else ''} currently on. "
            "The highlighted items below are what to watch — this is a checkup, not a prediction."
            + extra_bit
        )
        pills = "".join(
            f'<span class="flag-pill">{html.escape(name)}</span>' for name in unique
        )
    else:
        summary = (
            "No warning lights are on. Jobs, housing, and loans look calm for now. "
            "Keep an eye on layoff notices."
        )
        pills = '<span class="flag-pill ok">All clear</span>'
    if crisis:
        banner = (
            '<div class="sim-banner crisis">PRETEND MODE: 2008-STYLE CRISIS</div>'
        )
    elif simulated:
        banner = '<div class="sim-banner">PRETEND MODE IS ON — not real-life numbers</div>'
    else:
        banner = ""
    return f"""
    <div class="takeaway-card">
      <div class="hero-kicker">The simple version</div>
      {banner}
      <h3>Bottom line</h3>
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


def _stress_zone(score: float) -> str:
    if score < 35:
        return "healthy"
    if score < 55:
        return "strained"
    if score < 75:
        return "high"
    return "melt"


_ZONE_LABEL = {
    "healthy": "HEALTHY",
    "strained": "STRAINED",
    "high": "HIGH STRESS",
    "melt": "CRITICAL MELTDOWN",
}
_ZONE_COLOR = {
    "healthy": "#C8F542",
    "strained": "#D97706",
    "high": "#F87171",
    "melt": "#FCA5A5",
}


def _loan_payment(principal: float, annual_percent: float) -> float:
    monthly = float(annual_percent) / 100.0 / 12.0
    if monthly <= 0:
        return principal / 360.0
    growth = (1.0 + monthly) ** 360
    return principal * monthly * growth / (growth - 1.0)


def _real_world_impact(levers: dict, live: dict) -> str:
    """One plain sentence for the lever that moved the score the most."""
    channels = (
        ("unrate", levers["unrate"], 4.0, 10.0),
        ("claims", levers["claims"] * 1000.0, 200_000.0, 450_000.0),
        ("delinquency", levers["delinquency"], 2.5, 7.0),
        ("spread", levers["spread"], 500.0, 1200.0),
        ("mortgage", levers["mortgage"], 7.0, 11.0),
        ("homes", levers["homes"], 5.0, 10.0),
        ("curve", levers["curve"], 0.50, -0.50),
        ("sentiment", levers["sentiment"], 90.0, 50.0),
        ("saving", levers["saving"], 6.0, 2.0),
    )
    live_value = {
        "unrate": live["unrate"],
        "claims": live["claims"] * 1000.0,
        "delinquency": live["delinquency"],
        "spread": live["spread"],
        "mortgage": live["mortgage"],
        "homes": live["homes"],
        "curve": live["curve"],
        "sentiment": live["sentiment"],
        "saving": live["saving"],
    }
    best_name = ""
    best_gap = 0.0
    for name, value, benign, severe in channels:
        gap = abs(_clip_score(value, benign, severe) - _clip_score(live_value[name], benign, severe))
        if gap > best_gap:
            best_gap = gap
            best_name = name
    if best_gap < 1:
        return "Today's picture. Move a slider and this line says what would change."
    if best_name == "mortgage":
        gap = _loan_payment(400_000, levers["mortgage"]) - _loan_payment(400_000, live["mortgage"])
        way = "more" if gap > 0 else "less"
        return f"A $400,000 home loan would cost about ${abs(gap):,.0f} {way} a month."
    if best_name == "unrate":
        people = abs(levers["unrate"] - live["unrate"]) / 100.0 * 168_000_000
        way = "more" if levers["unrate"] > live["unrate"] else "fewer"
        return f"About {people / 1_000_000:.1f} million {way} people would be out of work."
    if best_name == "claims":
        gap = levers["claims"] - live["claims"]
        way = "more" if gap > 0 else "fewer"
        return f"About {abs(gap):,.0f}k {way} people would file for unemployment this week."
    if best_name == "delinquency":
        way = "more" if levers["delinquency"] > live["delinquency"] else "fewer"
        return f"{way.capitalize()} families would be late on car or card bills."
    if best_name == "saving":
        way = "thinner" if levers["saving"] < live["saving"] else "thicker"
        return f"Households would have a {way} cash cushion than today."
    if best_name == "homes":
        way = "more" if levers["homes"] > live["homes"] else "fewer"
        return f"Builders would be sitting on {way} unsold new homes."
    if best_name == "sentiment":
        way = "gloomier" if levers["sentiment"] < live["sentiment"] else "more upbeat"
        return f"Families would feel {way} than they do today."
    if best_name == "spread":
        way = "more" if levers["spread"] > live["spread"] else "less"
        return f"Shaky companies would pay {way} to borrow than they do today."
    way = "flashing a warning" if levers["curve"] < live["curve"] else "looking calmer"
    return f"The bond market would be {way} compared with today."


def cockpit_html(
    index_value: float,
    snapshot: dict,
    labor: dict,
    simulated: bool = False,
    extra_flags: Optional[List[str]] = None,
    crisis: bool = False,
    sim_unrate: Optional[float] = None,
    impact: str = "",
    sandbox: bool = False,
    filing: str = "",
) -> str:
    sahm_value = _implied_sahm(snapshot, sim_unrate if simulated else None)
    sahm_level, sahm_label = indicator_status(
        {"series_id": "SAHMREALTIME", "value": sahm_value, "flagged": False}
    )
    warning_names = _active_warning_names(snapshot, labor, extra_flags)
    mode_key = "crisis" if crisis else ("sim" if simulated else "live")
    prev_mode = st.session_state.get("_mp_mode", mode_key)
    prev_score = st.session_state.get("_mp_score")
    if prev_score is None:
        prev_score = float(index_value)
    prev_score = float(prev_score)
    prev_sahm = st.session_state.get("_mp_sahm", sahm_value)
    prev_pills = st.session_state.get("_mp_pills")
    st.session_state["_mp_mode"] = mode_key
    st.session_state["_mp_score"] = float(index_value)
    st.session_state["_mp_sahm"] = None if sahm_value is None else float(sahm_value)
    run_id = int(st.session_state.get("_mp_run", 0)) + 1
    st.session_state["_mp_run"] = run_id
    current_names = warning_names or ["All clear"]
    st.session_state["_mp_pills"] = list(current_names)
    known = set(prev_pills) if isinstance(prev_pills, list) else None
    light_bits = []
    if warning_names:
        for name in warning_names:
            fresh = " is-in" if known is not None and name not in known else ""
            light_bits.append(f'<span class="flag-pill{fresh}">{html.escape(name)}</span>')
    else:
        fresh = " is-in" if known is not None and "All clear" not in known else ""
        light_bits.append(f'<span class="flag-pill ok{fresh}">All clear</span>')
    lights = "".join(light_bits)
    width = max(2.0, min(100.0, float(index_value)))
    prev_width = max(2.0, min(100.0, prev_score))
    zone = _stress_zone(float(index_value))
    prev_zone = st.session_state.get("_mp_zone", zone)
    st.session_state["_mp_zone"] = zone
    shake = (prev_score < 65 <= float(index_value)) or (
        prev_score < 75 <= float(index_value)
    ) or (prev_score < CRISIS_INDEX_THRESHOLD <= float(index_value))
    card_class = {
        "crisis": "cockpit-card crisis",
        "sim": "cockpit-card sim",
    }.get(mode_key, "cockpit-card")
    if shake:
        card_class += " is-shake"
    lights_class = "cockpit-lights is-pulse" if shake else "cockpit-lights"
    borders = {
        "live": "rgba(255, 255, 255, 0.07)",
        "sim": "rgba(200, 245, 66, 0.55)",
        "crisis": "rgba(220, 38, 38, 0.38)",
    }
    shadows = {
        "live": "0 8px 24px rgba(0, 0, 0, 0.4)",
        "sim": (
            "0 8px 24px rgba(0, 0, 0, 0.4), 0 0 0 1px rgba(200, 245, 66, 0.28), "
            "0 0 22px rgba(200, 245, 66, 0.16)"
        ),
        "crisis": "0 8px 24px rgba(0, 0, 0, 0.4), 0 0 28px rgba(220, 38, 38, 0.12)",
    }
    zone_label = _ZONE_LABEL[zone]
    shown_sahm = "—" if prev_sahm is None else f"{float(prev_sahm):.2f}"
    sahm_target = "" if sahm_value is None else f"{float(sahm_value):.4f}"
    exit_button = ""
    if sandbox:
        exit_button = '<button type="button" class="sim-exit">Reset to Live Reality</button>'
    pill_motion = " is-in" if prev_zone != zone else ""
    impact_text = impact or "Today's picture. Move a slider and this line says what would change."
    return f"""
    <div class="{card_class}" data-run="{run_id}" style="--start-border:{borders.get(prev_mode, borders['live'])};--start-shadow:{shadows.get(prev_mode, shadows['live'])}">
      <div class="cockpit-head">
        <div class="hero-kicker">How stressed is the economy?</div>
        {exit_button}
      </div>
      <div class="cockpit-score">
        <div class="stress-readout" data-from="{prev_score:.4f}" data-to="{float(index_value):.4f}">{prev_score:.1f}%</div>
        <div class="live-radar zone-{zone}{pill_motion}" style="--start-color:{_ZONE_COLOR.get(prev_zone, _ZONE_COLOR['healthy'])}"><span class="pulse"></span><span>{zone_label}</span></div>
      </div>
      <div class="stress-track"><div class="stress-fill" style="width:{width:.1f}%;--start-width:{prev_width:.1f}%"></div></div>
      <p class="impact-line">{html.escape(impact_text)}</p>
      {filing}
      <div class="sahm-row">
        <span class="sahm-label">Recession warning light</span>
        <span class="sahm-value" data-from="{'' if prev_sahm is None else f'{float(prev_sahm):.4f}'}" data-to="{sahm_target}">{html.escape(shown_sahm)}</span>
        <span class="status-badge {html.escape(sahm_level)}">{html.escape(sahm_label)}</span>
      </div>
      <p class="sahm-hint">0.50 has marked every U.S. recession since 1970.</p>
      <div class="{lights_class}">{lights}</div>
      <p class="cockpit-asof">As of {html.escape(str(snapshot.get("as_of") or "—"))}</p>
    </div>
    """


def _play_cockpit_motion() -> None:
    """Count the stress score and recession light into their new values."""
    import streamlit.components.v1 as components

    components.html(
        """
        <script>
        const parent = window.parent;
        if (parent.__mpCockpitWatch !== 2) {
          parent.__mpCockpitWatch = 2;
          const stale = parent.document.getElementById("mp-cockpit-motion");
          if (stale) stale.remove();
          const script = parent.document.createElement("script");
          script.id = "mp-cockpit-motion";
          script.textContent = `
            (function () {
              const ease = (t) => {
                const c1x = 0.22, c1y = 1, c2x = 0.36, c2y = 1;
                const sample = (u, a, b) => 3 * a * u * (1 - u) * (1 - u) + 3 * b * u * u * (1 - u) + u * u * u;
                let u = t;
                for (let i = 0; i < 6; i++) {
                  const x = sample(u, c1x, c2x) - t;
                  const dx = 3 * (1 - u) * (1 - u) * c1x + 6 * (1 - u) * u * (c2x - c1x) + 3 * u * u * (1 - c2x);
                  if (Math.abs(dx) < 1e-4) break;
                  u = Math.min(1, Math.max(0, u - x / dx));
                }
                return sample(u, c1y, c2y);
              };
              const run = (card) => {
                if (!card) return;
                const token = card.dataset.run || "";
                if (!token || card.dataset.eased === token) return;
                const readout = card.querySelector(".stress-readout");
                const sahm = card.querySelector(".sahm-value");
                if (!readout || !Number.isFinite(parseFloat(readout.dataset.to))) return;
                card.dataset.eased = token;
                const from = parseFloat(readout.dataset.from);
                const to = parseFloat(readout.dataset.to);
                const sFrom = sahm ? parseFloat(sahm.dataset.from || sahm.textContent) : NaN;
                const sTo = sahm && sahm.dataset.to !== "" ? parseFloat(sahm.dataset.to) : NaN;
                const scoreMoves = Number.isFinite(from) && Number.isFinite(to) && Math.abs(from - to) > 0.05;
                const sahmMoves = Number.isFinite(sFrom) && Number.isFinite(sTo) && Math.abs(sFrom - sTo) > 0.005;
                if (Number.isFinite(to) && !scoreMoves) readout.textContent = to.toFixed(1) + "%";
                if (sahm && Number.isFinite(sTo) && !sahmMoves) sahm.textContent = sTo.toFixed(2);
                if (!scoreMoves && !sahmMoves) return;
                const t0 = performance.now();
                const frame = () => {
                  if (!readout.isConnected) return;
                  const t = Math.min(1, (performance.now() - t0) / 250);
                  const e = ease(t);
                  if (scoreMoves) readout.textContent = (from + (to - from) * e).toFixed(1) + "%";
                  if (sahmMoves && sahm.isConnected) sahm.textContent = (sFrom + (sTo - sFrom) * e).toFixed(2);
                  if (t < 1) setTimeout(frame, 16);
                };
                frame();
              };
              const scan = () => document.querySelectorAll(".cockpit-card").forEach(run);
              document.addEventListener("click", (event) => {
                const exit = event.target && event.target.closest && event.target.closest(".sim-exit");
                if (!exit) return;
                event.preventDefault();
                const button = document.querySelector(".st-key-sim_reset_live button");
                if (button) button.click();
              }, true);
              const boot = () => {
                if (!document.body) { setTimeout(boot, 16); return; }
                new MutationObserver(scan).observe(document.body, {
                  childList: true,
                  subtree: true,
                  attributes: true,
                  attributeFilter: ["data-to", "data-run"]
                });
                scan();
              };
              boot();
            })();
          `;
          parent.document.documentElement.appendChild(script);
        }
        </script>
        """,
        height=0,
    )


def live_tape_html(series: dict, snapshot: dict, labor: dict) -> str:
    consumer = snapshot.get("consumer") or {}
    national = (snapshot.get("labor_markets") or {}).get("All States") or {}
    chips = []

    def add(label: str, text: str) -> None:
        chips.append(
            f'<span class="tape-chip">{html.escape(label)} '
            f"<strong>{html.escape(text)}</strong></span>"
        )

    curve = series.get("T10Y2Y") or {}
    sahm = series.get("SAHMREALTIME") or {}
    hy = series.get("BAMLH0A0HYM2") or {}
    sent = series.get("UMCSENT") or {}
    if curve.get("value") is not None:
        add("Bond warning", f"{float(curve['value']):+.2f}")
    if sahm.get("value") is not None:
        add("Recession light", f"{float(sahm['value']):.2f}")
    if hy.get("value") is not None:
        add("Risky-loan extra cost", f"{float(hy['value']):.1f}%")
    unrate = national.get("unemployment")
    if unrate is not None:
        add("Jobless rate", f"{float(unrate):.1f}%")
    claims = national.get("claims")
    if claims is not None:
        add("New jobless claims", f"{float(claims):,.0f}")
    if sent.get("value") is not None:
        add("Family confidence", f"{float(sent['value']):.1f}")
    mort = consumer.get("mortgage_rate")
    if mort is not None:
        add("Home loan rate", f"{float(mort):.2f}%")
    notices = labor.get("notices")
    if notices is not None:
        add("Layoff notices", f"{int(notices):,}")
    if not chips:
        return ""
    doubled = "".join(chips + chips)
    return f'<div class="series-tape"><div class="series-tape-track">{doubled}</div></div>'


def filing_hook_facts(frame: Optional[pd.DataFrame]) -> Optional[dict]:
    """Place, headcount, and notice day. Never the company name."""
    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        return None
    if "Notice Date" not in frame.columns:
        return None
    work = frame.copy()
    work["_date"] = pd.to_datetime(work["Notice Date"], errors="coerce")
    work["_workers"] = _workers_series(work)
    work = work.dropna(subset=["_date"])
    work = work.loc[work["_workers"] >= 1]
    if work.empty:
        return None
    latest = work["_date"].max()
    window = work.loc[work["_date"] >= latest - pd.Timedelta(days=45)]
    if window.empty:
        window = work
    window = window.sort_values(["_workers", "_date"], ascending=[False, False])
    picked = None
    for _, row in window.iterrows():
        city = normalize_city(row.get("City"))
        state = " ".join(str(row.get("State") or "").split())
        if state.lower() in {"", "nan", "none"}:
            state = ""
        if city or state:
            picked = (row, city, state)
            break
    if picked is None:
        return None
    row, city, state = picked
    workers = int(row["_workers"])
    when = pd.Timestamp(row["_date"])
    if city and state:
        place = f"{html.escape(city)}, {html.escape(state_abbrev(state))}"
    elif city:
        place = html.escape(city)
    else:
        place = html.escape(state)
    return {
        "place": place,
        "workers": workers,
        "people": "person" if workers == 1 else "people",
        "when": html.escape(f"{when.strftime('%b')} {when.day}"),
    }


def filing_hook_html(frame: pd.DataFrame) -> str:
    """One readable filing: place and headcount, never the company name."""
    facts = filing_hook_facts(frame)
    if not facts:
        return ""
    return (
        '<div class="mp-hook">'
        f'<div class="hero-kicker">Largest notice · {facts["when"]}</div>'
        f"<p>{facts['place']} · <b>{facts['workers']:,}</b> {facts['people']}</p>"
        "</div>"
    )


def filing_strip_html(facts: Optional[dict]) -> str:
    if not facts:
        return ""
    return (
        '<div class="cockpit-filing">'
        f'<span>Largest notice · {facts["when"]}</span>'
        f"<strong>{facts['place']} · <b>{facts['workers']:,}</b> {facts['people']}</strong>"
        "</div>"
    )


_WEEK_EASING = {
    "BAMLH0A0HYM2": "Shaky companies are paying a little less to borrow.",
    "T10Y2Y": "The bond warning eased.",
    "UMCSENT": "Families feel a little more confident.",
    "ICSA": "Fewer people filed for unemployment.",
    "UNRATE": "The jobless rate ticked down.",
    "MSACSR": "Fewer new homes are sitting unsold.",
    "TOTALSA": "Car sales picked up.",
    "PSAVERT": "Families are saving a little more.",
    "SAHMREALTIME": "The recession light eased.",
    "PERMIT_YOY": "Builders pulled back a little less.",
}
_WEEK_TIGHTENING = {
    "BAMLH0A0HYM2": "Shaky companies are paying a little more to borrow.",
    "T10Y2Y": "The bond warning tightened.",
    "UMCSENT": "Families feel a little less confident.",
    "ICSA": "More people filed for unemployment.",
    "UNRATE": "The jobless rate ticked up.",
    "MSACSR": "More new homes are sitting unsold.",
    "TOTALSA": "Car sales slowed.",
    "PSAVERT": "Families are saving a little less.",
    "SAHMREALTIME": "The recession light rose.",
    "PERMIT_YOY": "Builders pulled back a little more.",
}
_WEEK_BILLS = (
    ("UNRATE", 0.05),
    ("ICSA", 500.0),
    ("MORTGAGE30US", 0.01),
    ("TERMCBCCALLNS", 0.01),
    ("PSAVERT", 0.05),
)


def _week_value_held(latest: pd.Series, prior: pd.Series, column: str, tolerance: float) -> bool:
    if column not in latest.index or column not in prior.index:
        return True
    now = latest[column]
    then = prior[column]
    if pd.isna(now) and pd.isna(then):
        return True
    if pd.isna(now) or pd.isna(then):
        return False
    return abs(float(now) - float(then)) <= tolerance


def week_shift_line(history: Optional[pd.DataFrame], detail: bool = True) -> str:
    """What the stress score did versus the prior saved week. Not a forecast."""
    if (
        history is None
        or not isinstance(history, pd.DataFrame)
        or history.empty
        or "macro_stress_index" not in history.columns
        or len(history) < 2
    ):
        return "Checked this week. Come back next week to see what moved."
    latest = history.iloc[-1]
    prior = history.iloc[-2]
    now = round(float(latest["macro_stress_index"]), 1)
    then = round(float(prior["macro_stress_index"]), 1)
    delta = round(now - then, 1)
    if abs(delta) < 0.05:
        short = "Same as last week"
    elif delta > 0:
        short = f"Up {delta:.1f} from last week"
    else:
        short = f"Down {abs(delta):.1f} from last week"
    if not detail:
        return short
    best_id = ""
    best_gap = 0.0
    eased = False
    for item in INDICATORS:
        column = item.series_id
        if column not in latest.index or column not in prior.index:
            continue
        if pd.isna(latest[column]) or pd.isna(prior[column]):
            continue
        now_score = item.score(float(latest[column]))
        then_score = item.score(float(prior[column]))
        gap = abs(now_score - then_score)
        if gap > best_gap:
            best_gap = gap
            best_id = column
            eased = now_score < then_score
    mover = ""
    if best_gap >= 0.4 and best_id in _WEEK_EASING:
        mover = _WEEK_EASING[best_id] if eased else _WEEK_TIGHTENING[best_id]
    bills_held = all(
        _week_value_held(latest, prior, column, tolerance) for column, tolerance in _WEEK_BILLS
    )
    if mover and bills_held and best_id not in {column for column, _tolerance in _WEEK_BILLS}:
        return f"{short}. Jobs and household bills held still. {mover}"
    if mover:
        return f"{short}. {mover}"
    if bills_held:
        return f"{short}. Jobs and household bills did not move."
    return short + "."


def live_feed_html(frame: pd.DataFrame, limit: int = 6) -> str:
    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        rows = '<p class="major-actions-empty">No recent layoff notices.</p>'
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
        rows = "".join(items) if items else '<p class="major-actions-empty">No recent layoff notices.</p>'
    return f"""
    <div class="feed-card">
      <div class="hero-kicker">Latest layoff notices</div>
      {rows}
    </div>
    """


STATE_OPTIONS = ["All States", "California", "Texas", "New York", "Florida"]
TIMEFRAME_OPTIONS = ["Last 30 days", "Last 90 days", "Everything"]
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
    SECTOR_CORPORATE: "#94A3B8",
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
    apply_chart_theme(fig, height=340, title=f"Impacted workers by sector · {geo_label}")
    fig.update_layout(
        showlegend=False,
        yaxis={"autorange": "reversed", "automargin": True},
        margin={"l": 8, "r": 16, "t": 12, "b": 36},
    )
    fig.update_xaxes(title_text="Workers", automargin=True)
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
    fig.update_layout(
        showlegend=False,
        yaxis={"autorange": "reversed", "automargin": True},
        margin={"l": 8, "r": 16, "t": 12, "b": 36},
    )
    fig.update_xaxes(title_text="Workers affected", automargin=True)
    return fig


def _optional_live(value: Optional[float], fallback: float) -> float:
    if value is None or pd.isna(value):
        return fallback
    return float(value)


CRISIS_UNRATE = 10.0
CRISIS_MORTGAGE = 6.4
CRISIS_DELINQUENCY = 6.8
CRISIS_SPREAD_BPS = 1200.0
CRISIS_CURVE = -0.50
CRISIS_SENTIMENT = 55.0
CRISIS_CLAIMS_K = 650.0
CRISIS_SAVING = 1.5
CRISIS_HOMES = 11.5
CRISIS_INDEX_THRESHOLD = 90.0


def _indicator_live(snapshot: dict, series_id: str, fallback: float) -> float:
    item = next(
        (
            row
            for row in snapshot.get("indicators") or []
            if row.get("series_id") == series_id
        ),
        None,
    )
    if item is None:
        return fallback
    return _optional_live(item.get("value"), fallback)


def _sim_delta_html(key: str, current: float, baseline: float, digits: int, suffix: str) -> str:
    delta = float(current) - float(baseline)
    remembered = st.session_state.setdefault("_mp_delta_text", {})
    if abs(delta) < 0.5 * (10 ** (-max(digits, 0))):
        remembered.pop(key, None)
        return ""
    text = f"{delta:+.{digits}f}{suffix}"
    snap = " is-in" if remembered.get(key) != text else ""
    remembered[key] = text
    return f'<em class="sim-delta{snap}">{text}</em>'


def _snap_slider(value: float, low: float, high: float, step: float, digits: int) -> float:
    steps = round((float(value) - low) / step)
    snapped = low + steps * step
    return round(max(low, min(high, snapped)), digits)


def apply_shock_simulation(snapshot: dict, levers: dict) -> tuple[float, List[str], bool]:
    """0–100 shock index from the simulator levers. Each channel is a real recession factor."""
    un_score = _clip_score(levers["unrate"], 4.0, 10.0)
    claims_score = _clip_score(levers["claims_k"] * 1000.0, 200_000.0, 450_000.0)
    dq_score = _clip_score(levers["delinquency"], 2.5, 7.0)
    spread_score = _clip_score(levers["spread_bps"], 500.0, 1200.0)
    mortgage_score = _clip_score(levers["mortgage"], 7.0, 11.0)
    homes_score = _clip_score(levers["homes"], 5.0, 10.0)
    curve_score = _clip_score(levers["curve"], 0.50, -0.50)
    sentiment_score = _clip_score(levers["sentiment"], 90.0, 50.0)
    saving_score = _clip_score(levers["saving"], 6.0, 2.0)
    # 2008 broke through housing inventory and missed payments, not the 30-year coupon.
    housing_score = max(mortgage_score, homes_score)
    jobs_score = 0.5 * un_score + 0.5 * claims_score
    household_score = 0.5 * sentiment_score + 0.5 * saving_score
    index = round(
        min(
            100.0,
            (
                jobs_score
                + dq_score
                + spread_score
                + housing_score
                + curve_score
                + household_score
            )
            / 6.0,
        ),
        1,
    )
    crisis = index >= CRISIS_INDEX_THRESHOLD

    live_markets = (snapshot.get("labor_markets") or {}).get("All States") or {}
    live_consumer = snapshot.get("consumer") or {}
    live_unrate = _optional_live(live_markets.get("unemployment"), 4.2)
    live_mortgage = _optional_live(live_consumer.get("mortgage_rate"), 6.5)
    live_spread_bps = _indicator_live(snapshot, "BAMLH0A0HYM2", 3.5) * 100.0
    live_curve = _indicator_live(snapshot, "T10Y2Y", 0.4)
    live_sentiment = _indicator_live(snapshot, "UMCSENT", 70.0)
    live_claims_k = _indicator_live(snapshot, "ICSA", 220_000.0) / 1000.0
    live_saving = _optional_live(live_consumer.get("saving_rate"), 4.0)
    live_homes = _indicator_live(snapshot, "MSACSR", 7.0)
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
    if levers["unrate"] >= UNEMPLOYMENT_FLAG_THRESHOLD and live_unrate < UNEMPLOYMENT_FLAG_THRESHOLD:
        extra_flags.append("Simulated unemployment")
    if levers["claims_k"] >= 300 and live_claims_k < 300:
        extra_flags.append("Simulated jump in new unemployment filings")
    if levers["mortgage"] > MORTGAGE_STRESS_THRESHOLD and live_mortgage <= MORTGAGE_STRESS_THRESHOLD:
        extra_flags.append("Simulated 30-yr mortgage")
    if levers["homes"] > 8 and live_homes <= 8:
        extra_flags.append("Simulated unsold new homes")
    if levers["delinquency"] > CREDIT_CARD_DELINQUENCY_FLAG_THRESHOLD and not live_dq_flag:
        extra_flags.append("Simulated auto/card delinquency")
    if levers["saving"] < 3.5 and live_saving >= 3.5:
        extra_flags.append("Simulated drop in leftover savings")
    if levers["sentiment"] <= 70 and live_sentiment > 70:
        extra_flags.append("Simulated gloomy families")
    if levers["spread_bps"] > 500 and live_spread_bps <= 500:
        extra_flags.append("Simulated high-yield spreads")
    if levers["curve"] <= 0 and live_curve > 0:
        extra_flags.append("Simulated bond-market warning")
    if crisis:
        extra_flags.append("CRITICAL RECESSION / SYSTEMIC CRISIS")
    return index, extra_flags, crisis


def _reset_simulator_to_live(values: dict) -> None:
    st.session_state.sim_enabled = False
    for key, value in values.items():
        st.session_state[key] = value


def _apply_2008_shock() -> None:
    st.session_state.sim_enabled = True
    st.session_state.sim_unrate = CRISIS_UNRATE
    st.session_state.sim_mortgage = CRISIS_MORTGAGE
    st.session_state.sim_dq = CRISIS_DELINQUENCY
    st.session_state.sim_spread = CRISIS_SPREAD_BPS
    st.session_state.sim_curve = CRISIS_CURVE
    st.session_state.sim_sentiment = CRISIS_SENTIMENT
    st.session_state.sim_claims = CRISIS_CLAIMS_K
    st.session_state.sim_saving = CRISIS_SAVING
    st.session_state.sim_homes = CRISIS_HOMES


def _apply_soft_landing() -> None:
    """A boom: every lever sits on the calm side of its range."""
    st.session_state.sim_enabled = True
    st.session_state.sim_unrate = 3.5
    st.session_state.sim_mortgage = 5.0
    st.session_state.sim_dq = 2.0
    st.session_state.sim_spread = 300.0
    st.session_state.sim_curve = 1.50
    st.session_state.sim_sentiment = 105.0
    st.session_state.sim_claims = 150.0
    st.session_state.sim_saving = 8.0
    st.session_state.sim_homes = 3.5


def _apply_chaos_roll() -> None:
    """Shuffle every lever inside its real slider steps."""
    st.session_state.sim_enabled = True
    rng = random.Random()
    specs = (
        ("sim_unrate", 3.5, 10.0, 0.1, 1),
        ("sim_mortgage", 5.0, 12.0, 0.1, 1),
        ("sim_dq", 2.0, 10.0, 0.05, 2),
        ("sim_spread", 300.0, 1200.0, 10.0, 0),
        ("sim_curve", -1.5, 2.5, 0.05, 2),
        ("sim_sentiment", 40.0, 110.0, 1.0, 0),
        ("sim_claims", 150.0, 700.0, 10.0, 0),
        ("sim_saving", 1.0, 12.0, 0.1, 1),
        ("sim_homes", 3.0, 14.0, 0.1, 1),
    )
    for key, low, high, step, digits in specs:
        st.session_state[key] = _snap_slider(rng.uniform(low, high), low, high, step, digits)


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
    except Exception:
        _log_failure()
        st.error("The jobs list could not load. Reload the page to try the saved copy again.")


def _render_labor_panel() -> None:
    warn = _coerce_warn_frame(load_master_data())
    history = load_macro_history().copy()
    snapshot = latest_snapshot(history)

    if "labor_geo" not in st.session_state:
        prior = st.session_state.get("state_filter", "All States")
        st.session_state.labor_geo = prior if prior in STATE_OPTIONS else "All States"
    if st.session_state.get("labor_timeframe") not in TIMEFRAME_OPTIONS:
        prior_tf = st.session_state.get("warn_timeframe", "Everything")
        st.session_state.labor_timeframe = (
            prior_tf if prior_tf in TIMEFRAME_OPTIONS else "Everything"
        )

    section_header(
        "Jobs",
        "Layoff notices and unemployment",
        "Company layoff notices plus the official jobless rate.",
    )
    render_section_news("Jobs")
    subsection("Pick a place")
    state_choice = st.pills(
        "Pick a place",
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

    subsection("Layoff notices")
    w1, w2, w3 = st.columns(3)
    with w1:
        st.metric(
            "Workers named on notices",
            f"{labor['workers']:,}",
            help="People listed on company layoff notices in this area. These are planned cuts, not every job loss.",
        )
        render_status_badge(STATUS_HEALTHY, "On the list")
    with w2:
        st.metric(
            "Workers on notices in the last 30 days",
            f"{labor['last_30_workers']:,}",
            delta=thirty_day_delta_label(labor),
            delta_color="inverse",
            help="Compared with the 30 days before that. Up means layoff notices are speeding up.",
        )
        render_status_badge(*warn_velocity_status(labor))
    with w3:
        st.metric(
            "Number of company notices",
            f"{labor['notices']:,}",
            help="Count of large layoff or plant-closure notices in this area.",
        )
        render_status_badge(STATUS_HEALTHY, "On the list")

    subsection("Official job numbers")
    claims_value = feed.get("claims")
    unrate_value = feed.get("unemployment")
    cycle_low = series_cycle_low(history, unrate_id)
    f1, f2 = st.columns(2)
    with f1:
        render_indicator_metric(
            {
                "series_id": unrate_id,
                "value": unrate_value,
                "display_unit": "pct",
                "threshold_label": (
                    f"FRED {unrate_id}. Flagged at {UNEMPLOYMENT_FLAG_THRESHOLD:.1f}% or higher."
                ),
            },
            f"{geo_label} jobless rate",
            "Share of people who want a job but do not have one. A rise from last year's low is the warning.",
            history=history,
            status=unemployment_status(
                unrate_value,
                cycle_low,
                bool(feed.get("unemployment_flagged")),
            ),
        )
    with f2:
        render_indicator_metric(
            {
                "series_id": claims_id,
                "value": claims_value,
                "display_unit": "claims",
                "threshold_label": f"FRED {claims_id}.",
            },
            f"{geo_label} new unemployment claims",
            "People who just started filing for unemployment, smoothed over 4 weeks. A climb means job losses are hitting now.",
            history=history,
            status=claims_status(
                claims_value,
                bool(feed.get("claims_flagged")),
                national=state_choice == "All States",
            ),
        )
    layoff_cards = major_layoff_card_html(filtered_df)
    if is_member():
        st.markdown(layoff_cards, unsafe_allow_html=True)
    else:
        st.markdown(
            f'<div class="mp-blur-table">{layoff_cards}</div>',
            unsafe_allow_html=True,
        )
    with locked_region("pay_jobs", "The history and the company-by-company list"):
            render_chart_grid(
                history,
                [
                    (unrate_id, f"{geo_label} jobless rate", CHART_EMERALD),
                    (claims_id, f"{geo_label} new unemployment claims", CHART_ACCENT),
                ],
            )

            sector_col, metro_col = st.columns(2)
            with sector_col:
                plot_chart(sector_bar_figure(filtered_df, geo_label))
            with metro_col:
                plot_chart(metro_bar_figure(filtered_df, geo_label))

            subsection("Search the notices")
            st.caption("How far back?")
            timeframe = st.pills(
                "Timeframe",
                TIMEFRAME_OPTIONS,
                key="labor_timeframe",
                label_visibility="collapsed",
            )
            if timeframe not in TIMEFRAME_OPTIONS:
                timeframe = "Everything"

            query = _clean_search(
                st.text_input(
                    "Search companies, cities, or counties",
                    placeholder="Type a company or city",
                    key="labor_search",
                    max_chars=80,
                )
            )
            table = filtered_df.copy() if isinstance(filtered_df, pd.DataFrame) else load_master_data()
            table = _coerce_warn_frame(table)
            if "Notice Date" in table.columns:
                notice_dates = pd.to_datetime(table["Notice Date"], errors="coerce")
                latest_notice = notice_dates.max()
                if timeframe == "Last 30 days" and pd.notna(latest_notice):
                    table = table.loc[notice_dates >= latest_notice - pd.Timedelta(days=30)].copy()
                elif timeframe == "Last 90 days" and pd.notna(latest_notice):
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
                "Download this list as a spreadsheet",
                data=table.to_csv(index=False).encode("utf-8"),
                file_name="us_warn_filtered.csv",
                mime="text/csv",
                use_container_width=False,
                key=f"labor_download_{state_choice}_{timeframe}",
        )


if _needs_data_refresh():
    # The page only reads the saved files. scripts/update_data.py refreshes them.
    _kick_background_refresh()

warn = _read_warn()
history = _read_history()
if history.empty or "macro_stress_index" not in getattr(history, "columns", []):
    st.warning(
        "Official numbers are not ready yet. The last saved copy is missing, "
        "so reload in a minute."
    )
    st.stop()
snapshot = latest_snapshot(history)
series = indicators_by_id(snapshot)
national_labor = warn_status(warn)

if "app_view" not in st.session_state:
    st.session_state.app_view = "landing"
handle_billing_return()
if st.session_state.app_view == "account":
    render_account_page()
    st.stop()
if st.session_state.app_view == "profile":
    render_profile_page()
    st.stop()
if st.session_state.app_view == "pricing":
    render_pricing_page()
    st.stop()
if st.session_state.app_view == "checkout":
    render_checkout_page()
    st.stop()
if st.session_state.app_view != "dashboard":
    render_landing_page(snapshot, national_labor, series)
    st.stop()

intro_cls = ""
nav_left, nav_right = st.columns([2.7, 1.6])
with nav_left:
    st.markdown(
        f"""
        <div class="top-nav {html.escape(intro_cls)}" style="border-bottom:0;margin:0;padding:0.2rem 0">
          <div class="brand-lockup">
            <span class="brand-home">
            {_MP_MARK}
            <span class="brand-name">MacroPulse</span>
            </span>
            <span class="live-radar"><span class="pulse"></span> Live numbers</span>
            <span class="nav-meta" style="color:#64748B;font-size:0.72rem">{national_labor["notices"]:,} layoff notices</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if st.button("MacroPulse", key="nav_logo"):
        st.session_state.app_view = "landing"
        st.rerun()
with nav_right:
    if st.button("Home", icon=":material/home:", key="nav_home"):
        st.session_state.app_view = "landing"
        st.rerun()
    if st.session_state.get("mp_user"):
        if st.button("Profile", key="nav_profile"):
            st.session_state.app_view = "profile"
            st.rerun()
    if not is_member() and st.button("Unlock full site", key="nav_unlock", type="primary"):
        unlock_dialog()

_plan_labels = {
    "year": "$50 a year",
    "lifetime": "$70 once",
    "founding": "$49 a year",
    "month": "$8 a month",
}
if is_member():
    _paid_label = _plan_labels.get(st.session_state.get("mp_access"), "full site")
    _paid_email = st.session_state.get("mp_email") or ""
    _paid_prefix = "Payment received. " if st.session_state.pop("mp_just_paid", False) else ""
    _paid_who = f" · {html.escape(_paid_email)}" if _paid_email else ""
    st.markdown(
        (
            '<div class="mp-preview-banner">'
            f"{html.escape(_paid_prefix)}Full site · {html.escape(_paid_label)}{_paid_who}"
            "</div>"
        ),
        unsafe_allow_html=True,
    )

as_of = snapshot.get("as_of")
as_dt = pd.to_datetime(as_of, errors="coerce")
as_of_pretty = (
    as_dt.strftime("%b ") + str(int(as_dt.day)) if pd.notna(as_dt) else "—"
)
today = datetime.now()
edition = f"{today.strftime('%A, %B')} {today.day}, {today.year}"
st.markdown(
    f'<p class="edition-line">{html.escape(edition)} · Numbers through {html.escape(as_of_pretty)}</p>'
    + live_tape_html(series, snapshot, national_labor),
    unsafe_allow_html=True,
)

consumer_live = snapshot.get("consumer") or {}
national_live = (snapshot.get("labor_markets") or {}).get("All States") or {}
live_unrate = _optional_live(national_live.get("unemployment"), 4.2)
live_mortgage = _optional_live(consumer_live.get("mortgage_rate"), 6.5)
live_dq = _optional_live(consumer_live.get("cc_delinquency"), 2.85)
live_spread_bps = _indicator_live(snapshot, "BAMLH0A0HYM2", 3.5) * 100.0
live_curve = _indicator_live(snapshot, "T10Y2Y", 0.4)
live_sentiment = _indicator_live(snapshot, "UMCSENT", 70.0)
live_claims_k = _indicator_live(snapshot, "ICSA", 220_000.0) / 1000.0
live_saving = _optional_live(consumer_live.get("saving_rate"), 4.0)
live_homes = _indicator_live(snapshot, "MSACSR", 7.0)
live_sim = {
    "sim_unrate": _snap_slider(live_unrate, 3.5, 10.0, 0.1, 1),
    "sim_mortgage": _snap_slider(live_mortgage, 5.0, 12.0, 0.1, 1),
    "sim_dq": _snap_slider(live_dq, 2.0, 10.0, 0.05, 2),
    "sim_spread": _snap_slider(live_spread_bps, 300.0, 1200.0, 10.0, 0),
    "sim_curve": _snap_slider(live_curve, -1.5, 2.5, 0.05, 2),
    "sim_sentiment": _snap_slider(live_sentiment, 40.0, 110.0, 1.0, 0),
    "sim_claims": _snap_slider(live_claims_k, 150.0, 700.0, 10.0, 0),
    "sim_saving": _snap_slider(live_saving, 1.0, 12.0, 0.1, 1),
    "sim_homes": _snap_slider(live_homes, 3.0, 14.0, 0.1, 1),
}

if "sim_enabled" not in st.session_state:
    st.session_state.sim_enabled = False
for _sim_key, _sim_value in live_sim.items():
    if _sim_key not in st.session_state:
        st.session_state[_sim_key] = _sim_value

with st.sidebar:
    sim_armed = bool(st.session_state.get("sim_enabled"))
    crisis_armed = sim_armed and all(
        abs(float(st.session_state[key]) - target) < tolerance
        for key, target, tolerance in (
            ("sim_unrate", CRISIS_UNRATE, 0.06),
            ("sim_mortgage", CRISIS_MORTGAGE, 0.06),
            ("sim_dq", CRISIS_DELINQUENCY, 0.06),
            ("sim_spread", CRISIS_SPREAD_BPS, 1.0),
            ("sim_curve", CRISIS_CURVE, 0.03),
            ("sim_sentiment", CRISIS_SENTIMENT, 0.6),
            ("sim_claims", CRISIS_CLAIMS_K, 1.0),
            ("sim_saving", CRISIS_SAVING, 0.06),
            ("sim_homes", CRISIS_HOMES, 0.06),
        )
    )
    sim_mode = "CRISIS" if crisis_armed else ("SIM" if sim_armed else "LIVE")
    sim_mode_class = (
        "mode-crisis" if crisis_armed else ("mode-sim" if sim_armed else "mode-live")
    )
    st.markdown(
        f"""
        <div class="sim-panel">
          <div class="sim-brand">
            {_MP_MARK}
            <div class="sim-brand-copy">
              <div class="brand-name sim-brand-name">MacroPulse</div>
              <div class="hero-kicker" style="margin:0">What if?</div>
            </div>
            <span class="live-radar {html.escape(sim_mode_class)}"><span class="pulse"></span>{html.escape(sim_mode)}</span>
          </div>
          <h3>What if?</h3>
          <p>Turn this on and move the sliders. Teaching tool only — not real life.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    simulated = st.toggle(
        "Turn on pretend mode",
        key="sim_enabled",
        help="When this is off, you always see real numbers from today.",
    )
    st.button(
        "Reset",
        use_container_width=True,
        key="sim_reset_live",
        on_click=_reset_simulator_to_live,
        args=(live_sim,),
    )
    st.button(
        "2008",
        use_container_width=True,
        key="sim_crisis_2008",
        on_click=_apply_2008_shock,
    )
    st.button(
        "Soft Landing",
        use_container_width=True,
        key="sim_soft_landing",
        on_click=_apply_soft_landing,
    )
    st.button(
        "Chaos Roll",
        use_container_width=True,
        key="sim_chaos_roll",
        on_click=_apply_chaos_roll,
    )
    st.markdown(
        '<p class="sim-lever-meta" style="margin:0.2rem 0 0.75rem 0">'
        "2008 is a crisis. Soft Landing is a boom. Chaos Roll shuffles every slider.</p>",
        unsafe_allow_html=True,
    )
    subsection("Move these sliders")
    st.markdown(
        f'<div class="sim-lever-meta"><span>Share of people without a job</span>'
        f'<span class="sim-now"><strong>Today {live_unrate:.1f}%</strong>'
        f'{_sim_delta_html("unrate", st.session_state["sim_unrate"], live_sim["sim_unrate"], 1, "%")}</span></div>',
        unsafe_allow_html=True,
    )
    sim_unrate = st.slider(
        "Unemployment rate",
        min_value=3.5,
        max_value=10.0,
        step=0.1,
        format="%.1f%%",
        key="sim_unrate",
        disabled=not simulated,
        help="Percent of people who want a job but do not have one. About 10% was the 2009 peak.",
    )
    st.markdown(
        f'<div class="sim-lever-meta"><span>Cost of a 30-year home loan</span>'
        f'<span class="sim-now"><strong>Today {live_mortgage:.2f}%</strong>'
        f'{_sim_delta_html("mortgage", st.session_state["sim_mortgage"], live_sim["sim_mortgage"], 1, "%")}</span></div>',
        unsafe_allow_html=True,
    )
    sim_mortgage = st.slider(
        "Home loan interest rate",
        min_value=5.0,
        max_value=12.0,
        step=0.1,
        format="%.1f%%",
        key="sim_mortgage",
        disabled=not simulated,
        help="The interest rate on a typical 30-year mortgage. Higher means homes are harder to afford.",
    )
    st.markdown(
        f'<div class="sim-lever-meta"><span>Missed car and card payments</span>'
        f'<span class="sim-now"><strong>Today {live_dq:.2f}%</strong>'
        f'{_sim_delta_html("dq", st.session_state["sim_dq"], live_sim["sim_dq"], 2, "%")}</span></div>',
        unsafe_allow_html=True,
    )
    sim_dq = st.slider(
        "People behind on car or card bills",
        min_value=2.0,
        max_value=10.0,
        step=0.05,
        format="%.2f%%",
        key="sim_dq",
        disabled=not simulated,
        help="Percent of those loans that are more than a month late. Rising means families are running out of cash.",
    )
    st.markdown(
        f'<div class="sim-lever-meta"><span>Extra interest shaky companies pay</span>'
        f'<span class="sim-now"><strong>Today {live_spread_bps:.0f} extra</strong>'
        f'{_sim_delta_html("spread", st.session_state["sim_spread"], live_sim["sim_spread"], 0, "")}</span></div>',
        unsafe_allow_html=True,
    )
    sim_spread = st.slider(
        "Fear in risky company loans",
        min_value=300.0,
        max_value=1200.0,
        step=10.0,
        format="%.0f extra",
        key="sim_spread",
        disabled=not simulated,
        help="How much extra interest risky companies must pay to borrow. Bigger number = lenders are scared.",
    )
    st.markdown(
        f'<div class="sim-lever-meta"><span>Long rates minus short rates</span>'
        f'<span class="sim-now"><strong>Today {live_curve:+.2f}</strong>'
        f'{_sim_delta_html("curve", st.session_state["sim_curve"], live_sim["sim_curve"], 2, "")}</span></div>',
        unsafe_allow_html=True,
    )
    sim_curve = st.slider(
        "Bond market warning",
        min_value=-1.5,
        max_value=2.5,
        step=0.05,
        format="%+.2f",
        key="sim_curve",
        disabled=not simulated,
        help="Long-term rates minus short-term rates. Below zero has shown up before recessions.",
    )
    st.markdown(
        f'<div class="sim-lever-meta"><span>Household survey, 100 is a strong year</span>'
        f'<span class="sim-now"><strong>Today {live_sentiment:.0f}</strong>'
        f'{_sim_delta_html("sentiment", st.session_state["sim_sentiment"], live_sim["sim_sentiment"], 0, "")}</span></div>',
        unsafe_allow_html=True,
    )
    sim_sentiment = st.slider(
        "How confident families feel",
        min_value=40.0,
        max_value=110.0,
        step=1.0,
        format="%.0f",
        key="sim_sentiment",
        disabled=not simulated,
        help="Household survey. Below 70 is recession-era gloom.",
    )
    st.markdown(
        f'<div class="sim-lever-meta"><span>People filing for unemployment this week</span>'
        f'<span class="sim-now"><strong>Today {live_claims_k:.0f}k</strong>'
        f'{_sim_delta_html("claims", st.session_state["sim_claims"], live_sim["sim_claims"], 0, "k")}</span></div>',
        unsafe_allow_html=True,
    )
    sim_claims = st.slider(
        "New unemployment filings",
        min_value=150.0,
        max_value=700.0,
        step=10.0,
        format="%.0fk",
        key="sim_claims",
        disabled=not simulated,
        help="People filing for unemployment each week, in thousands. Above 300k means layoffs are spreading.",
    )
    st.markdown(
        f'<div class="sim-lever-meta"><span>Share of income still saved</span>'
        f'<span class="sim-now"><strong>Today {live_saving:.1f}%</strong>'
        f'{_sim_delta_html("saving", st.session_state["sim_saving"], live_sim["sim_saving"], 1, "%")}</span></div>',
        unsafe_allow_html=True,
    )
    sim_saving = st.slider(
        "Leftover savings",
        min_value=1.0,
        max_value=12.0,
        step=0.1,
        format="%.1f%%",
        key="sim_saving",
        disabled=not simulated,
        help="Share of income families still save. Below 3.5% means there is little cushion.",
    )
    st.markdown(
        f'<div class="sim-lever-meta"><span>Months of new homes sitting unsold</span>'
        f'<span class="sim-now"><strong>Today {live_homes:.1f}</strong>'
        f'{_sim_delta_html("homes", st.session_state["sim_homes"], live_sim["sim_homes"], 1, "")}</span></div>',
        unsafe_allow_html=True,
    )
    sim_homes = st.slider(
        "Unsold new homes",
        min_value=3.0,
        max_value=14.0,
        step=0.1,
        format="%.1f mo",
        key="sim_homes",
        disabled=not simulated,
        help="How many months of new homes are sitting unsold. Above 8 months means builders are stuck.",
    )

extra_flags: List[str] = []
crisis = False
index_value = float(snapshot["macro_stress_index"])
if simulated:
    index_value, extra_flags, crisis = apply_shock_simulation(
        dict(snapshot),
        {
            "unrate": sim_unrate,
            "mortgage": sim_mortgage,
            "delinquency": sim_dq,
            "spread_bps": sim_spread,
            "curve": sim_curve,
            "sentiment": sim_sentiment,
            "claims_k": sim_claims,
            "saving": sim_saving,
            "homes": sim_homes,
        },
    )

_impact_levers = {
    "unrate": float(sim_unrate),
    "mortgage": float(sim_mortgage),
    "delinquency": float(sim_dq),
    "spread": float(sim_spread),
    "curve": float(sim_curve),
    "sentiment": float(sim_sentiment),
    "claims": float(sim_claims),
    "saving": float(sim_saving),
    "homes": float(sim_homes),
}
_impact_live = {
    "unrate": float(live_sim["sim_unrate"]),
    "mortgage": float(live_sim["sim_mortgage"]),
    "delinquency": float(live_sim["sim_dq"]),
    "spread": float(live_sim["sim_spread"]),
    "curve": float(live_sim["sim_curve"]),
    "sentiment": float(live_sim["sim_sentiment"]),
    "claims": float(live_sim["sim_claims"]),
    "saving": float(live_sim["sim_saving"]),
    "homes": float(live_sim["sim_homes"]),
}
impact_line = _real_world_impact(_impact_levers, _impact_live)
sandbox_active = simulated or any(
    abs(_impact_levers[name] - _impact_live[name]) > 1e-6 for name in _impact_levers
)

feed_html = live_feed_html(warn)
live_line = impact_line if sandbox_active else week_shift_line(history)
filing_card = filing_strip_html(filing_hook_facts(warn))
if is_member():
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
                impact=live_line,
                sandbox=sandbox_active,
                filing=filing_card,
            ),
            unsafe_allow_html=True,
        )
    with feed:
        st.markdown(feed_html, unsafe_allow_html=True)
else:
    st.markdown(
        cockpit_html(
            index_value,
            snapshot,
            national_labor,
            simulated=simulated,
            extra_flags=extra_flags,
            crisis=crisis,
            sim_unrate=sim_unrate if simulated else None,
            impact=live_line,
            sandbox=sandbox_active,
            filing=filing_card,
        ),
        unsafe_allow_html=True,
    )

_play_cockpit_motion()

if "labor_geo" not in st.session_state:
    st.session_state.labor_geo = "All States"
if "labor_timeframe" not in st.session_state:
    st.session_state.labor_timeframe = "Everything"

NAV_SECTIONS = ["Economy", "Cars", "Bills", "Jobs", "War"]
_LEGACY_TABS = {
    "Markets": "Economy",
    "Macro & Financial Conditions": "Economy",
    "Housing & Automotive Strain": "Cars",
    "Housing": "Cars",
    "Consumer": "Bills",
    "Consumer & Cost of Living": "Bills",
    "Labor": "Jobs",
    "Labor & WARN Layoff Radar": "Jobs",
}
if st.session_state.get("main_tab") in _LEGACY_TABS:
    st.session_state.main_tab = _LEGACY_TABS[st.session_state.main_tab]
if st.session_state.get("main_tab") not in NAV_SECTIONS:
    st.session_state.main_tab = NAV_SECTIONS[0]

@st.fragment
def _render_topics() -> None:
    """Redraw only this section when Economy, Cars, Bills, Jobs, or War is clicked."""
    history = _read_history()
    if st.session_state.pop("_block_war", False) and not is_member():
        previous = st.session_state.get("_open_tab") or "Economy"
        if previous not in NAV_SECTIONS or previous == "War":
            previous = "Economy"
        st.session_state.main_tab = previous
        st.session_state._open_war_pitch = True
    if not is_member():
        st.markdown('<div class="mp-war-lock"></div>', unsafe_allow_html=True)
    st.markdown('<div class="section-rail"></div>', unsafe_allow_html=True)
    selected_tab = st.pills(
        "Topic",
        NAV_SECTIONS,
        key="main_tab",
        label_visibility="collapsed",
    ) or st.session_state.main_tab
    if selected_tab == "War" and not is_member():
        st.session_state._block_war = True
        st.rerun()
    st.session_state._open_tab = selected_tab
    if st.session_state.pop("_open_war_pitch", False) and not is_member():
        war_locked_dialog()

    if selected_tab == "Economy":
        section_header(
            "Economy",
            "Is the country heading toward trouble?",
            "Four early warning lights that usually move first.",
        )
        render_section_news("Economy")
        subsection("Right now")
        m1, m2, m3, m4 = st.columns(4)
        with m1:
            render_indicator_metric(
                series["T10Y2Y"],
                "Bond market warning",
                "Below 0 often shows up before a recession.",
                history=history,
                meta="Updated every market day",
            )
        with m2:
            render_indicator_metric(
                series["SAHMREALTIME"],
                "Recession warning light",
                "A jump to 0.50 has marked every U.S. recession since 1970.",
                history=history,
                meta="Updated once a month",
            )
        with m3:
            render_indicator_metric(
                series["BAMLH0A0HYM2"],
                "Risky-company loan costs",
                "Extra interest shaky companies pay. Bigger = lenders are scared.",
                history=history,
                meta="Updated every market day",
            )
        with m4:
            render_indicator_metric(
                series["UMCSENT"],
                "How confident families feel",
                "Household survey. Below 70 is recession-era gloom.",
                history=history,
                meta="Updated once a month",
            )
        subsection("What the market is pricing")
        with locked_region("pay_market", "The market, gas, and oil"):
            market_line = market_read_html(history)
            if market_line:
                st.markdown(market_line, unsafe_allow_html=True)
            p1, p2, p3, p4 = st.columns(4)
            with p1:
                render_indicator_metric(
                    _market_item("SP500", "index", latest_history_value(history, "SP500")),
                    "S&P 500",
                    history=history,
                    meta="The broad market",
                    higher_is_worse=False,
                    show_status=False,
                )
            with p2:
                vix_value = latest_history_value(history, "VIXCLS")
                render_indicator_metric(
                    _market_item("VIXCLS", "vix", vix_value),
                    "Fear gauge",
                    history=history,
                    status=fear_gauge_status(vix_value),
                    meta="Higher means traders are nervous",
                )
            with p3:
                gas_value = latest_history_value(history, "GASREGW")
                render_indicator_metric(
                    _market_item("GASREGW", "usd", gas_value),
                    "Gas",
                    history=history,
                    status=gas_price_status(gas_value),
                    meta="Regular gas, what drivers pay",
                )
            with p4:
                oil_value = latest_history_value(history, "DCOILWTICO")
                render_indicator_metric(
                    _market_item("DCOILWTICO", "usd", oil_value),
                    "Oil",
                    history=history,
                    status=oil_price_status(oil_value),
                    meta="What oil companies get for a barrel",
                )
        subsection("The picture over time")
        with locked_region("pay_economy_charts", "Whether these lights have been getting worse"):
            render_chart_grid(
                history,
                [
                    ("T10Y2Y", "Bond market warning", CHART_ACCENT),
                    ("SAHMREALTIME", "Recession warning light", CHART_EMERALD),
                    ("BAMLH0A0HYM2", "Risky-company loan costs", CHART_EMERALD),
                    ("UMCSENT", "How confident families feel", CHART_EMERALD),
                    ("SP500", "S&P 500", CHART_ACCENT),
                    ("VIXCLS", "Fear gauge", CHART_EMERALD),
                    ("GASREGW", "Gas", CHART_EMERALD),
                    ("DCOILWTICO", "Oil", CHART_EMERALD),
                ],
            )
    elif selected_tab == "Cars":
        section_header(
            "Cars",
            "Car loans, leases & repossessions",
            "What families owe, how many are months behind, and what each finance company has published.",
        )
        render_section_news("Cars")
        subsection("Are people still buying cars?")
        render_indicator_metric(
            safe_indicator(series, "TOTALSA"),
            "New cars sold (yearly pace)",
            "Yearly pace of car and truck sales. Weak means people are delaying big purchases.",
            history=history,
            meta="Updated once a month",
        )
        with locked_region("pay_cars", "Car payments, history, and which makers are exposed"):
            plot_chart(series_figure(history, "TOTALSA", "New cars sold (yearly pace)", CHART_EMERALD))
            automotive = snapshot.get("automotive") or {}
            auto_debt = automotive.get("auto_debt")
            if auto_debt is None:
                auto_debt = latest_history_value(history, "MVLOAS")
            captive_leases = automotive.get("captive_leases")
            if captive_leases is None:
                captive_leases = latest_history_value(history, "DTCTSL")
            auto_dq = automotive.get("auto_delinquency")
            if auto_dq is None:
                auto_dq = (snapshot.get("consumer") or {}).get("auto_delinquency")
            if auto_dq is None:
                auto_dq = latest_history_value(history, "DRALACBS")
            car_rate = automotive.get("car_loan_rate")
            if car_rate is None:
                car_rate = latest_history_value(history, "RIFLPBCNC48")
            subsection("What families owe on cars")
            a1, a2, a3, a4 = st.columns(4)
            with a1:
                render_indicator_metric(
                    {
                        "series_id": "MVLOAS",
                        "value": auto_debt,
                        "display_unit": "bn",
                        "threshold_label": "FRED MVLOAS",
                    },
                    "Total U.S. car debt",
                    "All money still owed on cars and trucks in the United States.",
                    history=history,
                    status=revolving_status(auto_debt),
                    value_text=format_auto_debt(auto_debt),
                    meta="All outstanding vehicle debt",
                )
            with a2:
                render_indicator_metric(
                    {
                        "series_id": "DTCTSL",
                        "value": captive_leases,
                        "display_unit": "bn",
                        "threshold_label": "FRED DTCOLNVHFNM + DTCORVHFNM (finance-company auto loans and leases)",
                    },
                    "Leases & car-company loans",
                    "Cars leased or financed through Toyota, Ford, GM, and other makers — not a regular bank.",
                    history=history,
                    status=revolving_status(captive_leases),
                    value_text=format_auto_debt(captive_leases),
                    meta="Cars leased or financed through makers",
                )
            with a3:
                render_indicator_metric(
                    {
                        "series_id": "DRALACBS",
                        "value": auto_dq,
                        "display_unit": "pct",
                        "threshold_label": "FRED DRALACBS",
                        "flagged": bool(automotive.get("repo_velocity_flagged")),
                    },
                    "Cars at risk of being taken back",
                    "Share of bank car loans that are about 3 months late. That is when repossession usually starts.",
                    history=history,
                    status=commercial_auto_dq_status(auto_dq),
                    value_text=f"{auto_dq:.2f}%" if auto_dq is not None else "—",
                    meta="Payments 3 months past due",
                )
            with a4:
                render_indicator_metric(
                    {
                        "series_id": "RIFLPBCNC48",
                        "value": car_rate,
                        "display_unit": "pct",
                        "threshold_label": "FRED TERMCBAUTO48NS",
                    },
                    "Typical new-car loan interest rate",
                    "The average interest rate a new-car buyer pays. Higher means a fatter monthly bill.",
                    history=history,
                    status=car_loan_rate_status(car_rate),
                    value_text=f"{car_rate:.2f}%" if car_rate is not None else "—",
                    meta="Average interest rate for new car buyers",
                )
            subsection("The picture over time")
            auto_left, auto_right = st.columns(2)
            try:
                with auto_left:
                    plot_chart(auto_delinquency_figure(history))
                with auto_right:
                    plot_chart(auto_leases_vs_loans_figure(history))
            except Exception:
                _log_failure()
                st.info("Car-payment charts are temporarily unavailable. The numbers above are still live.")
            st.markdown(automaker_radar_html(), unsafe_allow_html=True)

    elif selected_tab == "Bills":
        section_header(
            "Bills",
            "Rent, home loans, savings, and credit cards",
            "Housing costs, leftover cash, and card debt.",
        )
        render_section_news("Bills")
        consumer = snapshot.get("consumer") or {}
        mortgage = consumer.get("mortgage_rate")
        rent_yoy = consumer.get("rent_yoy")
        subsection("Housing costs")
        c1, c2 = st.columns(2)
        with c1:
            render_indicator_metric(
                {
                    "series_id": "MORTGAGE30US",
                    "value": mortgage,
                    "display_unit": "pct",
                    "threshold_label": "FRED MORTGAGE30US",
                },
                "Home-loan interest rate",
                "What a typical 30-year mortgage costs. Higher rates make buying a home harder.",
                history=history,
                status=mortgage_status(mortgage),
                value_text=f"{mortgage:.2f}%" if mortgage is not None else "—",
                meta="Updated once a week",
            )
        with c2:
            render_indicator_metric(
                {
                    "series_id": "RENT_YOY",
                    "value": rent_yoy,
                    "display_unit": "yoy_pct",
                    "threshold_label": "YoY change in FRED CUSR0000SEHA",
                },
                "Rent vs last year",
                "How much rent has gone up compared with a year ago. Rising rent eats paychecks.",
                history=history,
                status=rent_status(rent_yoy),
                meta="Updated once a month",
            )
        with locked_region("pay_bills", "Unsold homes, savings, cards, and the history"):
            c3, c4 = st.columns(2)
            with c3:
                render_indicator_metric(
                    safe_indicator(series, "MSACSR"),
                    "Unsold new homes",
                    "How many months it would take to sell today's new homes. A high number means homes are piling up.",
                    history=history,
                    meta="Updated once a month",
                )
            with c4:
                render_indicator_metric(
                    safe_indicator(series, "PERMIT_YOY"),
                    "New building permits vs last year",
                    "Whether builders are getting permission to start more homes than a year ago. Negative means they are pulling back.",
                    history=history,
                    meta="Updated once a month",
                )
            subsection("The picture over time")
            left, right = st.columns(2)
            try:
                with left:
                    plot_chart(
                        dual_axis_figure(
                            history,
                            "MORTGAGE30US",
                            "Home-loan rate",
                            CHART_ACCENT,
                            "RENT_YOY",
                            "Rent vs last year",
                            CHART_EMERALD,
                            "Home-loan rates vs rent increases",
                        )
                    )
                with right:
                    plot_chart(
                        dual_axis_figure(
                            history,
                            "MSACSR",
                            "Unsold new homes (months)",
                            CHART_ACCENT,
                            "PERMIT_YOY",
                            "Building permits vs last year",
                            CHART_EMERALD,
                            "Homes sitting unsold vs new building",
                        )
                    )
            except Exception:
                _log_failure()
                st.info(
                    "Housing charts are temporarily unavailable. The numbers above are still live."
                )

            saving_rate = consumer.get("saving_rate")
            saving_flag = bool(consumer.get("saving_rate_flagged"))
            cc_dq = consumer.get("cc_delinquency")
            if cc_dq is None:
                cc_dq = latest_history_value(history, "DRCCLACBS")
            cc_flag = bool(consumer.get("cc_delinquency_flagged"))
            revolving = consumer.get("revolving_credit")
            if revolving is None:
                revolving = latest_history_value(history, "REVOLSL")
            cc_apr = consumer.get("cc_apr")
            if cc_apr is None:
                cc_apr = latest_history_value(history, "TERMCBCCALLNS")
            cc_subprime = consumer.get("cc_subprime_dq")
            if cc_subprime is None:
                cc_subprime = latest_history_value(history, "DRCCLOBS")
            subsection("Leftover cash after bills")
            render_indicator_metric(
                {
                    "series_id": "PSAVERT",
                    "value": saving_rate,
                    "display_unit": "pct",
                    "threshold_label": "FRED PSAVERT. Flagged below 3.5%.",
                    "flagged": saving_flag,
                },
                "Share of income families save",
                "Out of every paycheck, how much is left after spending. Below about 3.5% means little emergency cushion.",
                history=history,
            )
            subsection("Credit cards")
            k1, k2, k3, k4 = st.columns(4)
            with k1:
                render_indicator_metric(
                    {
                        "series_id": "REVOLSL",
                        "value": revolving,
                        "display_unit": "bn",
                        "threshold_label": "FRED REVOLSL, revolving consumer credit",
                    },
                    "Total card balances",
                    "How much Americans still owe on credit cards, all added up.",
                    history=history,
                    status=revolving_status(revolving),
                    value_text=format_revolving_credit(revolving),
                    meta="Total credit card debt carried by Americans",
                )
            with k2:
                render_indicator_metric(
                    {
                        "series_id": "TERMCBCCALLNS",
                        "value": cc_apr,
                        "display_unit": "pct",
                        "threshold_label": "FRED TERMCBCCALLNS, commercial-bank card APR",
                    },
                    "Typical card interest rate",
                    "The average interest charged if you do not pay the card off in full.",
                    history=history,
                    status=card_apr_status(cc_apr),
                    value_text=f"{cc_apr:.2f}%" if cc_apr is not None else "—",
                    meta="Average interest rate charged on unpaid balances",
                )
            with k3:
                render_indicator_metric(
                    {
                        "series_id": "DRCCLACBS",
                        "value": cc_dq,
                        "display_unit": "pct",
                        "threshold_label": (
                            "FRED DRCCLACBS. Flagged above "
                            f"{CREDIT_CARD_DELINQUENCY_FLAG_THRESHOLD:.1f}%."
                        ),
                        "flagged": cc_flag,
                    },
                    "Cards that are a month+ late",
                    "Share of bank card loans that are more than 30 days behind.",
                    history=history,
                    status=delinquency_status(cc_dq, cc_flag),
                    value_text=f"{cc_dq:.2f}%" if cc_dq is not None else "—",
                    meta="Card accounts 30+ days behind on payments",
                )
            with k4:
                render_indicator_metric(
                    {
                        "series_id": "DRCCLOBS",
                        "value": cc_subprime,
                        "display_unit": "pct",
                        "threshold_label": "FRED DRCCLOBS, banks outside the top 100",
                    },
                    "Late payments at smaller lenders",
                    "Late card payments at smaller banks, where more borrowers have thinner savings.",
                    history=history,
                    status=subprime_card_status(cc_subprime),
                    value_text=f"{cc_subprime:.2f}%" if cc_subprime is not None else "—",
                    meta="Late payments at smaller banks & subprime lenders",
                )
            subsection("The picture over time")
            try:
                plot_chart(credit_card_trap_figure(history))
            except Exception:
                _log_failure()
                st.info("Credit-card charts are temporarily unavailable. The numbers above are still live.")
            st.markdown(card_issuer_radar_html(), unsafe_allow_html=True)
            st.markdown(coping_index_html(), unsafe_allow_html=True)

    elif selected_tab == "Jobs":
        try:
            render_labor_panel()
        except Exception:
            _log_failure()
            st.error("The jobs page could not load. Reload the page to try the saved copy again.")
    elif selected_tab == "War":
        section_header(
            "War",
            "What are the odds the U.S. goes to war?",
            "Nobody publishes the true chance. This says how unusual the war news is, what one bet costs, and what showed up in the last couple of days.",
        )
        try:
            render_war_panel()
        except Exception:
            _log_failure()
            st.info("The war readings could not load. Reload the page to try again.")

    if selected_tab == "Jobs" and not is_member():
        with st.container(key="cta_pay_feed"):
            st.markdown(
                '<div class="mp-lock-kicker">Full site · Which companies just filed</div>',
                unsafe_allow_html=True,
            )
            if st.button("See the plans", key="pay_feed", type="primary"):
                unlock_dialog()
        st.markdown(f'<div class="mp-blur-table">{feed_html}</div>', unsafe_allow_html=True)

_render_topics()

with st.expander("Where do these numbers come from?"):
    st.markdown(
        """
**Public U.S. numbers** come from the Federal Reserve's FRED database (the
St. Louis Fed). That includes unemployment, new jobless claims, home-loan
rates, leftover savings, how confident families feel, the bond market, car
sales, car debt, card interest rates, and late payments on cards and car loans.
The S&P 500, the fear gauge, regular gas, and the oil price come from the same
feed. They sit beside the stress score and are not part of it.
Unsold new homes and building permits come from the same public feed.

**Layoff notices** come from state governments in California, Texas, New York,
and Florida. A rule often called the WARN Act says large employers must tell
workers about big layoffs or plant closings, usually about 60 days ahead. The
usual cutoff is 50–100+ workers, so smaller cuts may not appear.

**Car companies and card companies.** The card trusts, and Toyota, Ford, GM, and
Honda, are read from the latest filing once a month. Hyundai, Nissan, and BMW
stay on the last published pool or annual figure. The date on the card is the
date of that report. A credit score on a loan pool covers only the loans sold
to investors. Chase, Citi, and Capital One cover the card trust, which is only
part of the bank.

**War readings.** The big number compares this week's Caldara and Iacoviello geopolitical risk index with days since 1985. 1× is their average for 1985–2019. "Than last week" compares that 7-day average with seven days earlier. The cash cents are live Polymarket prices on one question each. A separate list is Manifold play-money guesses, not cash. Arms sales are unclassified Defense Security Cooperation Agency notices. The date under each one is the date printed on that notice. A headline list is a reading of the wording from the last couple of days. None of these is a forecast that the United States will go to war.

**How to use this:** layoff notices are an early heads-up of *planned* cuts.
New unemployment claims show people who already lost work and filed for benefits.
None of this is a stock tip or a prediction of your personal finances.
        """
    )

subsection("Get email updates")
inline_email, inline_button = st.columns([3.4, 1.1], vertical_alignment="bottom")
with inline_email:
    footer_email = st.text_input(
        "Your email",
        placeholder="you@email.com",
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
    if is_member():
        error = _throttle("subscribe", limit=4) or subscribe_email(footer_email)
        if error:
            st.error(error)
        else:
            st.success("You're on the list. We'll email you when warning lights change.")
    else:
        unlock_dialog()

st.markdown('<div class="legal-footer"></div>', unsafe_allow_html=True)
legal_copy, legal_privacy, legal_terms = st.columns([3.4, 1.15, 1.45])
with legal_copy:
    st.markdown(
        '<p class="legal-copy">MacroPulse © 2026. A plain-English checkup of '
        "U.S. jobs, bills, and layoff notices.</p>",
        unsafe_allow_html=True,
    )
with legal_privacy:
    if st.button("Privacy Policy", key="legal_privacy"):
        privacy_policy_dialog()
with legal_terms:
    if st.button("Legal Disclaimer", key="legal_disclaimer"):
        legal_disclaimer_dialog()
