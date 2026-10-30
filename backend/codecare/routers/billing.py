from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from codecare.config import get_settings
from codecare.db import get_db
from codecare.deps import AuthContext, current_auth, require_roles
from codecare.models import RazorpayWebhookEvent, Subscription, new_id, utcnow
from codecare.schemas import ChangePlanRequest, CheckoutRequest, RazorpayVerifyRequest
from codecare.security import verify_razorpay_payment, verify_razorpay_webhook
from codecare.services import razorpay
from codecare.services.audit import audit
from codecare.services.entitlements import current_plan
from codecare.services.razorpay import PLAN_RANK

router = APIRouter(tags=["billing"])

PLANS = [
    {"id": "starter", "name": "Starter", "monthly": 29, "currency": "USD", "repos": 1, "blurb": "One app, maintained."},
    {"id": "indie", "name": "Indie", "monthly": 79, "currency": "USD", "repos": 5, "blurb": "A bundle of five apps."},
    {"id": "studio", "name": "Studio", "monthly": 199, "currency": "USD", "repos": None, "blurb": "Unlimited apps, one fee."},
]

HANDLED_EVENTS = {
    "subscription.activated",
    "subscription.charged",
    "subscription.pending",
    "subscription.halted",
    "subscription.cancelled",
    "subscription.completed",
    "subscription.updated",
    "payment.failed",
}

EVENT_STATUS = {
    "subscription.activated": "active",
    "subscription.charged": "active",
    "subscription.pending": "pending",
    "subscription.halted": "halted",
    "subscription.cancelled": "canceled",
    "subscription.completed": "completed",
    "payment.failed": "past_due",
}


def _require_razorpay():
    settings = get_settings()
    if not settings.razorpay_configured:
        raise HTTPException(status_code=503, detail="Billing is disabled until Razorpay credentials are configured.")
    return settings


def _subscription(db: Session, organization_id: str) -> Subscription:
    sub = db.scalar(select(Subscription).where(Subscription.organization_id == organization_id))
    if not sub:
        sub = Subscription(id=new_id(), organization_id=organization_id)
        db.add(sub)
    return sub


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
        "detail": "Demo plan recorded. Razorpay was not charged.",
    }


@router.get("/api/v1/billing/plans")
def plans():
    settings = get_settings()
    return {"checkout_enabled": settings.razorpay_configured, "currency": "USD", "plans": PLANS}


@router.get("/api/v1/billing/status")
def status(auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    settings = get_settings()
    sub = db.scalar(select(Subscription).where(Subscription.organization_id == auth.organization_id))
    return {
        "checkout_enabled": settings.razorpay_configured,
        "demo_billing": settings.demo_mode,
        "currency": "USD",
        "plan": current_plan(db, auth.organization_id),
        "status": sub.status if sub else "inactive",
        "current_period_end": sub.current_period_end.isoformat() if sub and sub.current_period_end else None,
        "cancel_at_period_end": bool(sub and sub.cancel_at_period_end),
        "razorpay_subscription_id": sub.razorpay_subscription_id if sub else "",
    }


@router.post("/api/v1/billing/checkout")
def checkout(payload: CheckoutRequest, auth: AuthContext = Depends(require_roles("owner", "admin")), db: Session = Depends(get_db)):
    if get_settings().demo_mode:
        return _apply_demo_plan(payload.plan, auth, db)
    settings = _require_razorpay()
    if payload.plan not in PLAN_RANK:
        raise HTTPException(status_code=400, detail="Unknown plan")
    try:
        created = razorpay.create_subscription(payload.plan, auth.organization_id, settings)
    except razorpay.RazorpayError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    sub = _subscription(db, auth.organization_id)
    sub.razorpay_subscription_id = str(created.get("id") or "")
    sub.plan = payload.plan
    sub.status = "pending"
    db.commit()
    return {
        "key_id": settings.razorpay_key_id,
        "subscription_id": sub.razorpay_subscription_id,
        "plan": payload.plan,
        "name": "CodeCare",
        "description": f"{payload.plan.title()} · USD",
        "currency": "USD",
    }


@router.post("/api/v1/billing/verify")
def verify_payment(payload: RazorpayVerifyRequest, auth: AuthContext = Depends(require_roles("owner", "admin")), db: Session = Depends(get_db)):
    settings = _require_razorpay()
    if not verify_razorpay_payment(
        settings.razorpay_key_secret,
        payload.razorpay_payment_id,
        payload.razorpay_subscription_id,
        payload.razorpay_signature,
    ):
        raise HTTPException(status_code=400, detail="Invalid Razorpay payment signature")
    sub = db.scalar(select(Subscription).where(Subscription.organization_id == auth.organization_id))
    if not sub or sub.razorpay_subscription_id != payload.razorpay_subscription_id:
        raise HTTPException(status_code=400, detail="This payment does not match the workspace subscription")
    sub.status = "active"
    audit(db, action="billing.verified", organization_id=auth.organization_id, profile_id=auth.profile.id)
    db.commit()
    return {"ok": True, "plan": sub.plan, "status": sub.status}


@router.post("/api/v1/billing/cancel")
def cancel(auth: AuthContext = Depends(require_roles("owner", "admin")), db: Session = Depends(get_db)):
    if get_settings().demo_mode:
        sub = db.scalar(select(Subscription).where(Subscription.organization_id == auth.organization_id))
        if not sub or sub.status not in {"active", "trialing"}:
            raise HTTPException(status_code=400, detail="No active demo subscription to cancel.")
        sub.cancel_at_period_end = True
        audit(db, action="billing.demo_cancel", organization_id=auth.organization_id, profile_id=auth.profile.id)
        db.commit()
        return {"ok": True, "demo": True, "detail": "Demo subscription will end when the period ends. Razorpay was not charged."}
    settings = _require_razorpay()
    sub = db.scalar(select(Subscription).where(Subscription.organization_id == auth.organization_id))
    if not sub or not sub.razorpay_subscription_id:
        raise HTTPException(status_code=400, detail="No active Razorpay subscription to cancel.")
    try:
        razorpay.cancel_subscription(sub.razorpay_subscription_id, settings)
    except razorpay.RazorpayError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    sub.cancel_at_period_end = True
    audit(db, action="billing.cancel", organization_id=auth.organization_id, profile_id=auth.profile.id)
    db.commit()
    return {"ok": True, "detail": "Cancellation requested at the end of the Razorpay billing cycle."}


@router.post("/api/v1/billing/change-plan")
def change_plan(payload: ChangePlanRequest, auth: AuthContext = Depends(require_roles("owner", "admin")), db: Session = Depends(get_db)):
    if get_settings().demo_mode:
        result = _apply_demo_plan(payload.plan, auth, db)
        result["detail"] = "Demo plan changed. Razorpay was not charged."
        return result
    settings = _require_razorpay()
    if payload.plan not in PLAN_RANK:
        raise HTTPException(status_code=400, detail="Unknown plan or missing Razorpay plan ID")
    sub = db.scalar(select(Subscription).where(Subscription.organization_id == auth.organization_id))
    if not sub or not sub.razorpay_subscription_id or sub.status not in {"active", "pending"}:
        raise HTTPException(status_code=400, detail="Start a subscription with checkout first.")
    if sub.plan == payload.plan:
        raise HTTPException(status_code=400, detail="That plan is already selected")
    immediate = PLAN_RANK[payload.plan] > PLAN_RANK.get(sub.plan, 0)
    try:
        razorpay.update_subscription(sub.razorpay_subscription_id, payload.plan, immediate=immediate, settings=settings)
    except razorpay.RazorpayError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    when = "now" if immediate else "cycle_end"
    audit(db, action="billing.change_plan", organization_id=auth.organization_id, profile_id=auth.profile.id, metadata={"plan": payload.plan, "when": when})
    db.commit()
    return {
        "ok": True,
        "schedule_change_at": when,
        "detail": "Plan change sent to Razorpay. The workspace updates when the subscription.updated webhook arrives.",
    }


def _apply_event(db: Session, event_name: str, entity: dict) -> None:
    settings = get_settings()
    notes = entity.get("notes") or {}
    org_id = notes.get("organization_id")
    if event_name == "payment.failed":
        subscription_id = str(entity.get("subscription_id") or "")
    else:
        subscription_id = str(entity.get("id") or "")
    sub = None
    if org_id:
        sub = db.scalar(select(Subscription).where(Subscription.organization_id == org_id))
    if sub is None and subscription_id:
        sub = db.scalar(select(Subscription).where(Subscription.razorpay_subscription_id == subscription_id))
    if sub is None:
        return
    if subscription_id and event_name.startswith("subscription."):
        sub.razorpay_subscription_id = subscription_id
    customer_id = entity.get("customer_id")
    if customer_id:
        sub.razorpay_customer_id = str(customer_id)
    mapped = razorpay.plan_from_id(entity.get("plan_id"), settings) or notes.get("plan")
    if mapped in PLAN_RANK and event_name != "payment.failed":
        sub.plan = mapped
    if event_name == "subscription.updated":
        raw = str(entity.get("status") or "")
        status_map = {
            "active": "active",
            "authenticated": "pending",
            "pending": "pending",
            "halted": "halted",
            "cancelled": "canceled",
            "completed": "completed",
            "created": "pending",
            "expired": "canceled",
        }
        sub.status = status_map.get(raw, sub.status)
    elif event_name in EVENT_STATUS:
        sub.status = EVENT_STATUS[event_name]
        if event_name == "subscription.cancelled":
            sub.cancel_at_period_end = False
            sub.plan = "none"
        if event_name == "subscription.completed":
            sub.plan = "none"
    period_end = entity.get("current_end")
    if period_end:
        sub.current_period_end = datetime.fromtimestamp(int(period_end), tz=timezone.utc)
    sub.updated_at = utcnow()
    audit(db, action="billing.webhook", organization_id=sub.organization_id, metadata={"type": event_name})


@router.post("/api/v1/webhooks/razorpay")
async def razorpay_webhook(request: Request, db: Session = Depends(get_db)):
    settings = get_settings()
    if not settings.razorpay_webhook_secret:
        raise HTTPException(status_code=503, detail="Razorpay webhook secret is not configured")
    body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")
    if not verify_razorpay_webhook(settings.razorpay_webhook_secret, body, signature):
        raise HTTPException(status_code=401, detail="Invalid Razorpay signature")
    event_id = request.headers.get("x-razorpay-event-id") or hashlib.sha256(body).hexdigest()
    if db.scalar(select(RazorpayWebhookEvent).where(RazorpayWebhookEvent.event_id == event_id)):
        return {"ok": True, "duplicate": True}
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid Razorpay payload") from exc
    event_name = str(payload.get("event") or "")
    if event_name in HANDLED_EVENTS:
        container = payload.get("payload") or {}
        if event_name == "payment.failed":
            entity = ((container.get("payment") or {}).get("entity")) or {}
        else:
            entity = ((container.get("subscription") or {}).get("entity")) or {}
        _apply_event(db, event_name, entity)
    db.add(RazorpayWebhookEvent(id=new_id(), event_id=event_id, event=event_name or "unknown"))
    db.commit()
    return {"ok": True}
