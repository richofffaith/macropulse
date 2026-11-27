"""Follow-up notes from MacroPulse. Resend sends them. A missing key skips quietly."""

from __future__ import annotations

import requests

from runtime_secrets import load_secret

RESEND_URL = "https://api.resend.com/emails"


def mail_configured() -> bool:
    key = load_secret("RESEND_API_KEY")
    sender = load_secret("MAIL_FROM")
    return key.startswith("re_") and "@" in sender


def send_followup(email: str, subject: str, text: str) -> bool:
    """Send one plain note. Return False when mail is off or Resend refuses it."""
    if not mail_configured():
        return False
    try:
        response = requests.post(
            RESEND_URL,
            headers={
                "Authorization": f"Bearer {load_secret('RESEND_API_KEY')}",
                "Content-Type": "application/json",
            },
            json={
                "from": load_secret("MAIL_FROM"),
                "to": [email],
                "subject": subject,
                "text": text,
            },
            timeout=8,
        )
    except requests.RequestException:
        return False
    return response.status_code < 400


def send_welcome(email: str) -> bool:
    return send_followup(
        email,
        "Your MacroPulse account is ready",
        (
            "Your MacroPulse account is ready.\n\n"
            "Log in with this email and the password you chose. "
            "Checkout comes after that, if you want the full site.\n\n"
            "This note is only about the account. It is not a bill."
        ),
    )


def send_purchase(email: str, charge: str) -> bool:
    return send_followup(
        email,
        "Your MacroPulse plan is active",
        (
            "Your MacroPulse plan is active.\n\n"
            f"{charge}\n\n"
            "Log in with this email to open the full site. "
            "Nothing on the site is a recommendation or a prediction."
        ),
    )
