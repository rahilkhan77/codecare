from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from codecare.config import get_settings
from codecare.db import get_db
from codecare.deps import AuthContext, current_auth
from codecare.models import Subscription, new_id, utcnow
from codecare.schemas import ChangePlanRequest, CheckoutRequest
from codecare.services.audit import audit
from codecare.services.entitlements import current_plan

router = APIRouter(tags=["billing"])

PLANS = [
    {"id": "starter", "name": "Starter", "monthly": 19, "repos": 1},
    {"id": "pro", "name": "Pro", "monthly": 49, "repos": 5},
    {"id": "team", "name": "Team", "monthly": 149, "repos": 15},
    {"id": "enterprise", "name": "Enterprise", "monthly": None, "repos": None},
]


def _price_map(settings) -> dict[str, str]:
    return {
        "starter": settings.stripe_price_starter,
        "pro": settings.stripe_price_pro,
        "team": settings.stripe_price_team,
    }


def _plan_from_price(settings, price_id: str | None) -> str | None:
    if not price_id:
        return None
    inverted = {value: key for key, value in _price_map(settings).items() if value}
    return inverted.get(price_id)


def _stripe():
    settings = get_settings()
    if not settings.stripe_configured:
        raise HTTPException(status_code=503, detail="Billing is disabled until Stripe credentials are configured.")
    import stripe

    stripe.api_key = settings.stripe_secret_key
    return stripe, settings


@router.get("/api/v1/billing/plans")
def plans():
    settings = get_settings()
    return {"checkout_enabled": settings.stripe_configured, "plans": PLANS}


@router.get("/api/v1/billing/status")
def status(auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    sub = db.scalar(select(Subscription).where(Subscription.organization_id == auth.organization_id))
    return {
        "checkout_enabled": get_settings().stripe_configured,
        "plan": current_plan(db, auth.organization_id),
        "status": sub.status if sub else "inactive",
        "current_period_end": sub.current_period_end.isoformat() if sub and sub.current_period_end else None,
        "cancel_at_period_end": bool(sub and sub.status == "active" and sub.stripe_subscription_id),
    }


@router.post("/api/v1/billing/checkout")
def checkout(payload: CheckoutRequest, auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    stripe, settings = _stripe()
    if payload.plan == "enterprise":
        raise HTTPException(status_code=400, detail="Enterprise is custom. Use the contact form.")
    price = _price_map(settings).get(payload.plan)
    if not price:
        raise HTTPException(status_code=400, detail="Unknown plan or missing Stripe price ID")
    existing = db.scalar(select(Subscription).where(Subscription.organization_id == auth.organization_id))
    kwargs = {
        "mode": "subscription",
        "line_items": [{"price": price, "quantity": 1}],
        "success_url": settings.public_base_url + "/workspace/?billing=success",
        "cancel_url": settings.public_base_url + "/workspace/?billing=cancelled",
        "client_reference_id": auth.organization_id,
        "metadata": {"organization_id": auth.organization_id, "plan": payload.plan},
        "subscription_data": {"metadata": {"organization_id": auth.organization_id, "plan": payload.plan}},
    }
    if existing and existing.stripe_customer_id:
        kwargs["customer"] = existing.stripe_customer_id
    session = stripe.checkout.Session.create(**kwargs)
    return {"url": session.url}


@router.post("/api/v1/billing/portal")
def portal(auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    stripe, settings = _stripe()
    sub = db.scalar(select(Subscription).where(Subscription.organization_id == auth.organization_id))
    if not sub or not sub.stripe_customer_id:
        raise HTTPException(status_code=400, detail="No Stripe customer exists for this workspace yet.")
    session = stripe.billing_portal.Session.create(
        customer=sub.stripe_customer_id,
        return_url=settings.public_base_url + "/workspace/",
    )
    return {"url": session.url}


@router.post("/api/v1/billing/cancel")
def cancel(auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    stripe, _settings = _stripe()
    sub = db.scalar(select(Subscription).where(Subscription.organization_id == auth.organization_id))
    if not sub or not sub.stripe_subscription_id:
        raise HTTPException(status_code=400, detail="No active Stripe subscription to cancel.")
    stripe.Subscription.modify(sub.stripe_subscription_id, cancel_at_period_end=True)
    audit(db, action="billing.cancel", organization_id=auth.organization_id, profile_id=auth.profile.id)
    db.commit()
    return {"ok": True, "detail": "Cancellation requested at period end. Entitlements follow the Stripe webhook."}


@router.post("/api/v1/billing/change-plan")
def change_plan(payload: ChangePlanRequest, auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    stripe, settings = _stripe()
    price = _price_map(settings).get(payload.plan)
    if not price:
        raise HTTPException(status_code=400, detail="Unknown plan or missing Stripe price ID")
    sub = db.scalar(select(Subscription).where(Subscription.organization_id == auth.organization_id))
    if not sub or not sub.stripe_subscription_id:
        raise HTTPException(status_code=400, detail="Start a subscription with checkout first.")
    current = stripe.Subscription.retrieve(sub.stripe_subscription_id)
    item_id = current["items"]["data"][0]["id"]
    stripe.Subscription.modify(
        sub.stripe_subscription_id,
        items=[{"id": item_id, "price": price}],
        metadata={"organization_id": auth.organization_id, "plan": payload.plan},
        proration_behavior="create_prorations",
    )
    audit(db, action="billing.change_plan", organization_id=auth.organization_id, profile_id=auth.profile.id, metadata={"plan": payload.plan})
    db.commit()
    return {"ok": True, "detail": "Plan change sent to Stripe. Status updates via webhook."}


def _apply_subscription(db: Session, org_id: str, data: dict, event_type: str) -> None:
    settings = get_settings()
    sub = db.scalar(select(Subscription).where(Subscription.organization_id == org_id))
    if not sub:
        sub = Subscription(id=new_id(), organization_id=org_id)
        db.add(sub)
    status = data.get("status") or sub.status
    if event_type == "checkout.session.completed":
        sub.status = "active"
        sub.stripe_customer_id = str(data.get("customer") or sub.stripe_customer_id)
        sub.stripe_subscription_id = str(data.get("subscription") or sub.stripe_subscription_id)
        plan = (data.get("metadata") or {}).get("plan")
        if plan:
            sub.plan = plan
    else:
        sub.status = status
        sub.stripe_customer_id = str(data.get("customer") or sub.stripe_customer_id)
        sub.stripe_subscription_id = str(data.get("id") or sub.stripe_subscription_id)
        items = ((data.get("items") or {}).get("data") or [{}])
        price_id = ((items[0].get("price") or {}).get("id")) if items else None
        mapped = _plan_from_price(settings, price_id) or (data.get("metadata") or {}).get("plan")
        if mapped:
            sub.plan = mapped
        period_end = data.get("current_period_end")
        if period_end:
            sub.current_period_end = datetime.fromtimestamp(int(period_end), tz=timezone.utc)
        if status in {"canceled", "unpaid", "incomplete_expired"}:
            sub.plan = "none"
            sub.status = "canceled" if status == "canceled" else status
        else:
            sub.status = status
    sub.updated_at = utcnow()
    audit(db, action="billing.webhook", organization_id=org_id, metadata={"type": event_type})
    db.commit()


@router.post("/api/v1/webhooks/stripe")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    settings = get_settings()
    if not settings.stripe_webhook_secret:
        raise HTTPException(status_code=503, detail="Stripe webhook secret is not configured")
    import stripe

    payload = await request.body()
    sig = request.headers.get("Stripe-Signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig, settings.stripe_webhook_secret)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid Stripe signature")
    data = event["data"]["object"]
    org_id = (data.get("metadata") or {}).get("organization_id") or data.get("client_reference_id")
    if event["type"] == "checkout.session.completed" and org_id:
        _apply_subscription(db, org_id, data, event["type"])
    elif event["type"] in {"customer.subscription.updated", "customer.subscription.created", "customer.subscription.deleted"}:
        if not org_id:
            org_id = (data.get("metadata") or {}).get("organization_id")
        if org_id:
            if event["type"] == "customer.subscription.deleted":
                data = {**data, "status": "canceled"}
            _apply_subscription(db, org_id, data, event["type"])
    return {"ok": True}
