"""Stripe Checkout for MacroPulse plans.

Card numbers stay on Stripe. This module only stores the email, the plan,
and the Stripe customer id after a payment is confirmed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent
MEMBERS_PATH = PROJECT_DIR / "data" / "members.json"
ACCOUNTS_PATH = PROJECT_DIR / "data" / "accounts.json"
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
FOUNDING_CAP = 100

load_dotenv(PROJECT_DIR / ".env")

# amount is US cents. interval is None for the one-time lifetime plan.
PLANS = (
    {
        "id": "year",
        "amount": 5000,
        "label": "$50",
        "period": "a year",
        "note": "Best value",
        "perk": "A full year of seeing it first.",
        "mode": "subscription",
        "interval": "year",
        "featured": True,
        "product": "MacroPulse full site, yearly",
        "charge": "Billed $50 today, then $50 each year until you cancel.",
    },
    {
        "id": "lifetime",
        "amount": 7000,
        "label": "$70",
        "period": "once",
        "note": "Lifetime access",
        "perk": "Pay once. Keep the head start.",
        "mode": "payment",
        "interval": None,
        "featured": False,
        "product": "MacroPulse full site, lifetime",
        "charge": "One charge of $70. The full site stays open. It does not renew.",
    },
    {
        "id": "founding",
        "amount": 4900,
        "label": "$49",
        "period": "a year",
        "note": "First 100 people",
        "perk": "The early price, kept each year you stay.",
        "mode": "subscription",
        "interval": "year",
        "featured": False,
        "product": "MacroPulse full site, founding yearly",
        "charge": "Billed $49 today, then $49 each year until you cancel. Limited to the first 100 people.",
    },
    {
        "id": "month",
        "amount": 800,
        "label": "$8",
        "period": "a month",
        "note": "Cancel anytime",
        "perk": "Start today. Stop whenever you want.",
        "mode": "subscription",
        "interval": "month",
        "featured": False,
        "product": "MacroPulse full site, monthly",
        "charge": "Billed $8 today, then $8 each month until you cancel.",
    },
)

_PLAN_BY_ID = {plan["id"]: plan for plan in PLANS}
_AMOUNT_TO_PLAN = {plan["amount"]: plan["id"] for plan in PLANS}
# Earlier checkout prices, so a subscription already on Stripe still maps to its plan.
_AMOUNT_TO_PLAN.update({7900: "year", 9000: "lifetime", 1200: "month"})
_ACTIVE_SUBSCRIPTION = {"active", "trialing", "past_due"}


def _secret(name: str) -> str:
    from runtime_secrets import load_secret

    return load_secret(name)


def stripe_secret_key() -> str:
    return _secret("STRIPE_SECRET_KEY")


def payments_configured() -> bool:
    key = stripe_secret_key()
    return key.startswith("sk_test_") or key.startswith("sk_live_")


def payments_test_mode() -> bool:
    return stripe_secret_key().startswith("sk_test_")


def normalize_email(raw: str) -> str:
    return (raw or "").strip().lower()


def valid_email(email: str) -> bool:
    if not email or len(email) > 254:
        return False
    return bool(EMAIL_RE.match(email))


def _password_hash(password: str, salt: str) -> str:
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        200_000,
    )
    return digest.hex()


def _load_accounts() -> dict:
    if not ACCOUNTS_PATH.exists():
        return {}
    try:
        payload = json.loads(ACCOUNTS_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_accounts(accounts: dict) -> None:
    ACCOUNTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = ACCOUNTS_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(accounts, indent=2))
    temporary.replace(ACCOUNTS_PATH)


def create_account(email: str, password: str) -> Optional[str]:
    """Create an account. Return an error message, or None on success."""
    email = normalize_email(email)
    if not valid_email(email):
        return "Enter a valid email address, like name@company.com."
    if len(password or "") < MIN_PASSWORD_LENGTH:
        return "Use at least 8 characters."
    if len(password) > MAX_PASSWORD_LENGTH:
        return "Use 128 characters or fewer."
    accounts = _load_accounts()
    if email in accounts:
        return "An account with this email already exists. Log in instead."
    salt = secrets.token_hex(16)
    accounts[email] = {
        "salt": salt,
        "password_hash": _password_hash(password, salt),
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    _save_accounts(accounts)
    try:
        from mailer import send_welcome

        send_welcome(email)
    except Exception:
        pass
    return None


def authenticate(email: str, password: str) -> Optional[str]:
    """Check a password. Return an error message, or None when it matches."""
    email = normalize_email(email)
    if not valid_email(email):
        return "Enter a valid email address, like name@company.com."
    if not password or len(password) > MAX_PASSWORD_LENGTH:
        return "That password does not match."
    account = _load_accounts().get(email)
    if not account:
        return "No account with that email. Sign up first."
    expected = str(account.get("password_hash") or "")
    salt = str(account.get("salt") or "")
    try:
        actual = _password_hash(password or "", salt)
    except ValueError:
        return "That password does not match."
    if len(actual) != len(expected) or not hmac.compare_digest(actual, expected):
        return "That password does not match."
    return None


def account_profile(email: str) -> Optional[dict]:
    """Public account details. Never includes the password hash."""
    email = normalize_email(email)
    account = _load_accounts().get(email)
    if not account:
        return None
    return {"email": email, "created_at": account.get("created_at") or ""}


def change_password(email: str, current: str, new_password: str) -> Optional[str]:
    """Replace a password. Return an error message, or None on success."""
    email = normalize_email(email)
    error = authenticate(email, current)
    if error:
        if error.startswith("That password"):
            return "The current password does not match."
        return error
    if len(new_password or "") < MIN_PASSWORD_LENGTH:
        return "Use at least 8 characters."
    if len(new_password) > MAX_PASSWORD_LENGTH:
        return "Use 128 characters or fewer."
    if new_password == current:
        return "Choose a different password."
    accounts = _load_accounts()
    account = accounts.get(email)
    if not account:
        return "No account with that email. Sign up first."
    salt = secrets.token_hex(16)
    account["salt"] = salt
    account["password_hash"] = _password_hash(new_password, salt)
    _save_accounts(accounts)
    return None


def plan_by_id(plan_id: str) -> Optional[dict]:
    plan = _PLAN_BY_ID.get(plan_id)
    return dict(plan) if plan else None


def _load_members() -> dict:
    if not MEMBERS_PATH.exists():
        return {}
    try:
        payload = json.loads(MEMBERS_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_members(members: dict) -> None:
    MEMBERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = MEMBERS_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(members, indent=2))
    temporary.replace(MEMBERS_PATH)


def _write_member(record: dict) -> dict:
    email = normalize_email(record.get("email", ""))
    record = dict(record)
    record["email"] = email
    record["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    members = _load_members()
    prior = members.get(email) or {}
    if prior.get("purchase_email_sent") and not record.get("purchase_email_sent"):
        record["purchase_email_sent"] = prior["purchase_email_sent"]
    members[email] = record
    _save_members(members)
    return record


def founding_spots_left() -> int:
    used = 0
    for record in _load_members().values():
        if record.get("plan") == "founding" and record.get("status") == "active":
            used += 1
    return max(0, FOUNDING_CAP - used)


def _stripe():
    import stripe

    key = stripe_secret_key()
    if not payments_configured():
        raise ValueError("Card checkout is not connected yet.")
    stripe.api_key = key
    return stripe


def _meta(value) -> dict:
    """Stripe objects are not dicts. Convert metadata before reading it."""
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        converted = to_dict()
        return converted if isinstance(converted, dict) else {}
    return {}


def _plan_from_amount(amount: Optional[int], fallback: str) -> str:
    if amount in _AMOUNT_TO_PLAN:
        return _AMOUNT_TO_PLAN[amount]
    return fallback if fallback in _PLAN_BY_ID else "year"


def _subscription_plan(subscription) -> str:
    metadata = _meta(getattr(subscription, "metadata", None))
    plan_id = metadata.get("plan") or ""
    amount = None
    items = getattr(subscription, "items", None)
    data = list(getattr(items, "data", []) or [])
    if data:
        price = getattr(data[0], "price", None)
        amount = getattr(price, "unit_amount", None)
    return _plan_from_amount(amount, plan_id)


def _record_from_subscription(email: str, customer_id: str, subscription) -> Optional[dict]:
    status = getattr(subscription, "status", "") or ""
    if status not in _ACTIVE_SUBSCRIPTION:
        return None
    return {
        "email": email,
        "plan": _subscription_plan(subscription),
        "status": "active",
        "customer_id": customer_id,
        "subscription_id": getattr(subscription, "id", "") or "",
        "checkout_session_id": "",
    }


def _lookup_stripe(email: str) -> Optional[dict]:
    stripe = _stripe()
    customers = stripe.Customer.list(email=email, limit=3)
    for customer in list(customers.data or []):
        customer_id = customer.id
        sessions = stripe.checkout.Session.list(customer=customer_id, limit=20)
        for session in list(sessions.data or []):
            metadata = _meta(session.metadata)
            if (
                session.mode == "payment"
                and session.payment_status == "paid"
                and metadata.get("plan") == "lifetime"
            ):
                return {
                    "email": email,
                    "plan": "lifetime",
                    "status": "active",
                    "customer_id": customer_id,
                    "subscription_id": "",
                    "checkout_session_id": session.id,
                }
        subscriptions = stripe.Subscription.list(customer=customer_id, status="all", limit=10)
        for subscription in list(subscriptions.data or []):
            record = _record_from_subscription(email, customer_id, subscription)
            if record:
                return record
    return None


def restore_member(email: str) -> Optional[dict]:
    """Return the paid plan for this email, or None when nothing is active."""
    email = normalize_email(email)
    if not valid_email(email):
        return None
    local = _load_members().get(email)
    if local and local.get("plan") == "lifetime" and local.get("status") == "active":
        return local
    if not payments_configured():
        if local and local.get("status") == "active":
            return local
        return None
    try:
        remote = _lookup_stripe(email)
    except Exception:
        if local and local.get("status") == "active":
            return local
        return None
    if remote:
        return _write_member(remote)
    if local and local.get("status") == "active":
        local = dict(local)
        local["status"] = "canceled"
        _write_member(local)
    return None


def create_checkout_session(plan_id: str, email: str, success_url: str, cancel_url: str) -> str:
    email = normalize_email(email)
    plan = plan_by_id(plan_id)
    if plan is None:
        raise ValueError("Choose a plan first.")
    if not valid_email(email):
        raise ValueError("Enter a valid email address, like name@company.com.")
    if not payments_configured():
        raise ValueError("Card checkout is not connected yet.")
    if plan_id == "founding" and founding_spots_left() <= 0:
        raise ValueError("The first 100 founding spots are taken.")

    stripe = _stripe()
    price_data = {
        "currency": "usd",
        "unit_amount": plan["amount"],
        "product_data": {"name": plan["product"]},
    }
    params = {
        "mode": plan["mode"],
        "customer_email": email,
        "client_reference_id": email,
        "success_url": success_url,
        "cancel_url": cancel_url,
        "metadata": {"plan": plan_id, "email": email},
        "line_items": [{"quantity": 1, "price_data": price_data}],
    }
    if plan["mode"] == "subscription":
        price_data["recurring"] = {"interval": plan["interval"]}
        params["subscription_data"] = {"metadata": {"plan": plan_id, "email": email}}
    else:
        params["customer_creation"] = "always"
        params["payment_intent_data"] = {"metadata": {"plan": plan_id, "email": email}}
    try:
        session = stripe.checkout.Session.create(**params)
    except Exception as exc:
        message = getattr(exc, "user_message", None) or "Stripe did not open checkout."
        raise ValueError(message) from exc
    url = getattr(session, "url", None)
    if not url:
        raise ValueError("Stripe did not open checkout.")
    return url


def fulfill_checkout(session_id: str) -> dict:
    session_id = (session_id or "").strip()
    if not session_id.startswith("cs_"):
        raise ValueError("This payment link is not valid.")
    stripe = _stripe()
    try:
        session = stripe.checkout.Session.retrieve(session_id, expand=["subscription"])
    except Exception as exc:
        raise ValueError("Stripe could not confirm this payment.") from exc
    if session.status != "complete" or session.payment_status != "paid":
        raise ValueError("The payment is not complete. If you were charged, log in with the same email in a minute.")

    details = session.customer_details
    metadata = _meta(session.metadata)
    email = normalize_email(
        getattr(details, "email", None) or session.customer_email or metadata.get("email", "")
    )
    if not valid_email(email):
        raise ValueError("Stripe confirmed the payment, but the receipt email was missing.")
    plan_id = metadata.get("plan") or ""
    if plan_id not in _PLAN_BY_ID:
        amount = None
        line_items = stripe.checkout.Session.list_line_items(session_id, limit=1)
        data = list(line_items.data or [])
        if data:
            price = getattr(data[0], "price", None)
            amount = getattr(price, "unit_amount", None)
        plan_id = _plan_from_amount(amount, "year")
    customer_id = session.customer if isinstance(session.customer, str) else getattr(session.customer, "id", "")
    subscription = session.subscription
    subscription_id = subscription if isinstance(subscription, str) else getattr(subscription, "id", "")
    record = _write_member(
        {
            "email": email,
            "plan": plan_id,
            "status": "active",
            "customer_id": customer_id or "",
            "subscription_id": subscription_id or "",
            "checkout_session_id": session.id,
        }
    )
    if not record.get("purchase_email_sent"):
        plan = plan_by_id(plan_id) or {}
        charge = plan.get("charge") or "The full site is open on this email."
        try:
            from mailer import send_purchase

            sent = send_purchase(email, charge)
        except Exception:
            sent = False
        if sent:
            record["purchase_email_sent"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            record = _write_member(record)
    return record


def billing_portal_url(customer_id: str, return_url: str) -> str:
    customer_id = (customer_id or "").strip()
    if not customer_id.startswith("cus_"):
        raise ValueError("No billing profile is on file for this email yet.")
    stripe = _stripe()
    try:
        session = stripe.billing_portal.Session.create(customer=customer_id, return_url=return_url)
    except Exception as exc:
        raise ValueError(
            "The billing portal is not open yet. Use the receipt Stripe emailed you to update the card or cancel."
        ) from exc
    url = getattr(session, "url", None)
    if not url:
        raise ValueError("The billing portal is not open yet.")
    return url
