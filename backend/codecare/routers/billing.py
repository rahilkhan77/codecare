from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from codecare.config import get_settings
from codecare.db import get_db
from codecare.deps import AuthContext, current_auth, require_roles
from codecare.models import DodoWebhookEvent, Organization, Subscription, new_id, utcnow
from codecare.schemas import ChangePlanRequest, CheckoutRequest
from codecare.security import verify_standard_webhook
from codecare.services import dodo
from codecare.services.audit import audit
from codecare.services.dodo import PLAN_RANK
from codecare.services.entitlements import current_plan

router = APIRouter(tags=["billing"])

PLANS = [
    {"id": "starter", "name": "Starter", "monthly": 29, "currency": "USD", "repos": 1, "blurb": "One app, maintained."},
    {"id": "indie", "name": "Indie", "monthly": 79, "currency": "USD", "repos": 5, "blurb": "A bundle of five apps."},
    {"id": "studio", "name": "Studio", "monthly": 199, "currency": "USD", "repos": None, "blurb": "Unlimited apps, one fee."},
]

HANDLED_EVENTS = {
    "subscription.active",
    "subscription.renewed",
    "subscription.on_hold",
    "subscription.cancelled",
    "subscription.failed",
    "subscription.expired",
    "subscription.plan_changed",
    "payment.succeeded",
    "payment.failed",
}

EVENT_STATUS = {
    "subscription.active": "active",
    "subscription.renewed": "active",
    "subscription.plan_changed": "active",
    "subscription.on_hold": "on_hold",
    "subscription.cancelled": "canceled",
    "subscription.failed": "failed",
    "subscription.expired": "expired",
    "payment.succeeded": "active",
    "payment.failed": "past_due",
}

TERMINAL_STATUSES = {"canceled", "expired", "inactive", "failed", "completed"}
PLAN_EVENTS = {"subscription.active", "subscription.renewed", "subscription.plan_changed"}


def _require_dodo():
    settings = get_settings()
    if not settings.dodo_configured:
        raise HTTPException(status_code=503, detail="Billing is coming soon.")
    return settings


def _return_url() -> str:
    return get_settings().public_base_url.rstrip("/") + "/workspace"


def _subscription(db: Session, organization_id: str) -> Subscription:
    sub = db.scalar(select(Subscription).where(Subscription.organization_id == organization_id))
    if not sub:
        sub = Subscription(id=new_id(), organization_id=organization_id)
        db.add(sub)
    return sub


def _raise_dodo(exc: Exception) -> None:
    if isinstance(exc, dodo.DodoNotConfigured):
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if isinstance(exc, dodo.DodoError):
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    raise exc


def _apply_demo_plan(plan: str, auth: AuthContext, db: Session) -> dict:
    if plan not in PLAN_RANK:
        raise HTTPException(status_code=400, detail="Unknown plan")
    sub = _subscription(db, auth.organization_id)
    sub.plan = plan
    sub.status = "active"
    sub.cancel_at_period_end = False
    audit(db, action="billing.demo_plan", organization_id=auth.organization_id, profile_id=auth.profile.id, metadata={"plan": plan})
    db.commit()
    return {
        "demo": True,
        "plan": plan,
        "detail": "Demo plan recorded. Dodo Payments was not charged.",
    }


@router.get("/api/v1/billing/plans")
def plans():
    settings = get_settings()
    return {
        "checkout_enabled": settings.dodo_configured,
        "availability": settings.billing_state,
        "currency": "USD",
        "plans": PLANS,
    }


@router.get("/api/v1/billing/status")
def status(auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    settings = get_settings()
    sub = db.scalar(select(Subscription).where(Subscription.organization_id == auth.organization_id))
    return {
        "checkout_enabled": settings.dodo_configured,
        "availability": settings.billing_state,
        "demo_billing": settings.demo_mode,
        "currency": "USD",
        "provider": "dodo",
        "plan": current_plan(db, auth.organization_id),
        "status": sub.status if sub else "inactive",
        "current_period_end": sub.current_period_end.isoformat() if sub and sub.current_period_end else None,
        "cancel_at_period_end": bool(sub and sub.cancel_at_period_end),
        "dodo_subscription_id": sub.dodo_subscription_id if sub else "",
        "portal_available": bool(sub and sub.dodo_customer_id),
    }


@router.post("/api/v1/billing/checkout")
def checkout(payload: CheckoutRequest, auth: AuthContext = Depends(require_roles("owner", "admin")), db: Session = Depends(get_db)):
    if get_settings().demo_mode:
        return _apply_demo_plan(payload.plan, auth, db)
    settings = _require_dodo()
    if payload.plan not in PLAN_RANK:
        raise HTTPException(status_code=400, detail="Unknown plan")
    existing = db.scalar(select(Subscription).where(Subscription.organization_id == auth.organization_id))
    if existing and existing.dodo_subscription_id and existing.status not in TERMINAL_STATUSES:
        raise HTTPException(status_code=400, detail="This workspace already has a Dodo subscription. Change the plan or open the customer portal.")
    try:
        created = dodo.create_checkout(
            plan=payload.plan,
            organization_id=auth.organization_id,
            email=auth.profile.email,
            name=auth.profile.full_name or auth.profile.email,
            return_url=_return_url(),
            settings=settings,
        )
    except (dodo.DodoNotConfigured, dodo.DodoError) as exc:
        _raise_dodo(exc)
    sub = _subscription(db, auth.organization_id)
    if sub.status in TERMINAL_STATUSES or not sub.dodo_subscription_id:
        sub.plan = payload.plan
        sub.status = "pending"
    db.commit()
    return {"url": created["checkout_url"], "session_id": created["session_id"], "plan": payload.plan}


@router.post("/api/v1/billing/portal")
def portal(auth: AuthContext = Depends(require_roles("owner", "admin")), db: Session = Depends(get_db)):
    if get_settings().demo_mode:
        raise HTTPException(status_code=400, detail="Customer portal is unavailable in demo mode. Dodo Payments was not charged.")
    settings = _require_dodo()
    sub = db.scalar(select(Subscription).where(Subscription.organization_id == auth.organization_id))
    if not sub or not sub.dodo_customer_id:
        raise HTTPException(status_code=400, detail="Start a subscription before opening the customer portal.")
    try:
        link = dodo.create_portal(customer_id=sub.dodo_customer_id, return_url=_return_url(), settings=settings)
    except (dodo.DodoNotConfigured, dodo.DodoError) as exc:
        _raise_dodo(exc)
    return {"url": link}


@router.post("/api/v1/billing/cancel")
def cancel(auth: AuthContext = Depends(require_roles("owner", "admin")), db: Session = Depends(get_db)):
    if get_settings().demo_mode:
        sub = db.scalar(select(Subscription).where(Subscription.organization_id == auth.organization_id))
        if not sub or sub.status not in {"active", "trialing"}:
            raise HTTPException(status_code=400, detail="No active demo subscription to cancel.")
        sub.cancel_at_period_end = True
        audit(db, action="billing.demo_cancel", organization_id=auth.organization_id, profile_id=auth.profile.id)
        db.commit()
        return {"ok": True, "demo": True, "detail": "Demo subscription will end when the period ends. Dodo Payments was not charged."}
    settings = _require_dodo()
    sub = db.scalar(select(Subscription).where(Subscription.organization_id == auth.organization_id))
    if not sub or not sub.dodo_subscription_id:
        raise HTTPException(status_code=400, detail="No active Dodo subscription to cancel.")
    try:
        dodo.cancel_at_next_billing_date(sub.dodo_subscription_id, settings)
    except (dodo.DodoNotConfigured, dodo.DodoError) as exc:
        _raise_dodo(exc)
    sub.cancel_at_period_end = True
    audit(db, action="billing.cancel", organization_id=auth.organization_id, profile_id=auth.profile.id)
    db.commit()
    return {"ok": True, "detail": "Cancellation is scheduled for the next Dodo billing date. Access stays on until then."}


@router.post("/api/v1/billing/change-plan")
def change_plan(payload: ChangePlanRequest, auth: AuthContext = Depends(require_roles("owner", "admin")), db: Session = Depends(get_db)):
    if get_settings().demo_mode:
        result = _apply_demo_plan(payload.plan, auth, db)
        result["detail"] = "Demo plan changed. Dodo Payments was not charged."
        return result
    settings = _require_dodo()
    if payload.plan not in PLAN_RANK:
        raise HTTPException(status_code=400, detail="Unknown plan")
    sub = db.scalar(select(Subscription).where(Subscription.organization_id == auth.organization_id))
    if not sub or not sub.dodo_subscription_id or sub.status not in {"active", "past_due", "on_hold"}:
        raise HTTPException(status_code=400, detail="Start a subscription with checkout first.")
    if sub.plan == payload.plan:
        raise HTTPException(status_code=400, detail="That plan is already selected")
    immediate = PLAN_RANK[payload.plan] > PLAN_RANK.get(sub.plan, 0)
    try:
        changed = dodo.change_plan(
            subscription_id=sub.dodo_subscription_id,
            plan=payload.plan,
            immediate=immediate,
            organization_id=auth.organization_id,
            settings=settings,
        )
    except (dodo.DodoNotConfigured, dodo.DodoError) as exc:
        _raise_dodo(exc)
    when = "now" if immediate else "next_billing_date"
    audit(db, action="billing.change_plan", organization_id=auth.organization_id, profile_id=auth.profile.id, metadata={"plan": payload.plan, "when": when})
    db.commit()
    return {
        "ok": True,
        "schedule_change_at": when,
        "url": changed.get("payment_link"),
        "detail": "Plan change sent to Dodo Payments. The workspace updates when the subscription webhook arrives.",
    }


def _metadata(data: dict) -> dict:
    meta = data.get("metadata") or {}
    return meta if isinstance(meta, dict) else {}


def _customer_id(data: dict) -> str:
    customer = data.get("customer") or {}
    if isinstance(customer, dict):
        return str(customer.get("customer_id") or "")
    return ""


def _subscription_id(event_name: str, data: dict) -> str:
    if event_name.startswith("payment."):
        direct = data.get("subscription_id")
        if direct:
            return str(direct)
        ids = data.get("subscription_ids") or []
        return str(ids[0]) if ids else ""
    return str(data.get("subscription_id") or "")


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _apply_event(db: Session, event_name: str, data: dict) -> None:
    settings = get_settings()
    notes = _metadata(data)
    org_id = str(notes.get("organization_id") or "")
    subscription_id = _subscription_id(event_name, data)
    if event_name.startswith("payment.") and not subscription_id:
        return
    sub = None
    if org_id and db.get(Organization, org_id):
        sub = _subscription(db, org_id)
    if sub is None and subscription_id:
        sub = db.scalar(select(Subscription).where(Subscription.dodo_subscription_id == subscription_id))
    if sub is None:
        return
    if subscription_id:
        sub.dodo_subscription_id = subscription_id
    customer_id = _customer_id(data)
    if customer_id:
        sub.dodo_customer_id = customer_id
    mapped = dodo.plan_from_product(data.get("product_id"), settings) or notes.get("plan")
    if mapped in PLAN_RANK and event_name in PLAN_EVENTS:
        sub.plan = mapped
    if event_name in EVENT_STATUS:
        sub.status = EVENT_STATUS[event_name]
    if event_name in {"subscription.cancelled", "subscription.expired"}:
        sub.cancel_at_period_end = False
        sub.plan = "none"
    elif "cancel_at_next_billing_date" in data:
        sub.cancel_at_period_end = bool(data.get("cancel_at_next_billing_date"))
    period_end = _parse_dt(data.get("next_billing_date"))
    if period_end:
        sub.current_period_end = period_end
    sub.updated_at = utcnow()
    audit(db, action="billing.webhook", organization_id=sub.organization_id, metadata={"type": event_name})


@router.post("/api/v1/webhooks/dodo")
async def dodo_webhook(request: Request, db: Session = Depends(get_db)):
    settings = get_settings()
    if not settings.dodo_payments_webhook_secret:
        raise HTTPException(status_code=503, detail="Dodo Payments webhook secret is not configured")
    body = await request.body()
    webhook_id = request.headers.get("webhook-id", "")
    webhook_timestamp = request.headers.get("webhook-timestamp", "")
    webhook_signature = request.headers.get("webhook-signature", "")
    if not verify_standard_webhook(
        settings.dodo_payments_webhook_secret,
        body,
        webhook_id,
        webhook_timestamp,
        webhook_signature,
        tolerance_seconds=settings.webhook_replay_seconds,
    ):
        raise HTTPException(status_code=401, detail="Invalid Dodo Payments signature")
    if db.scalar(select(DodoWebhookEvent).where(DodoWebhookEvent.event_id == webhook_id)):
        return {"ok": True, "duplicate": True}
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid Dodo Payments payload") from exc
    event_name = str(payload.get("type") or "")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    if event_name in HANDLED_EVENTS:
        _apply_event(db, event_name, data)
    db.add(DodoWebhookEvent(id=new_id(), event_id=webhook_id, event=event_name or "unknown"))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return {"ok": True, "duplicate": True}
    return {"ok": True}
