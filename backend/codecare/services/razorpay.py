"""Razorpay Subscriptions client. Amounts are USD plans created in the dashboard."""

from __future__ import annotations

import httpx

from codecare.config import Settings, get_settings

API = "https://api.razorpay.com/v1"
PLAN_RANK = {"starter": 1, "indie": 2, "studio": 3}


class RazorpayError(RuntimeError):
    pass


class RazorpayNotConfigured(RazorpayError):
    pass


def plan_id(plan: str, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    mapping = {
        "starter": settings.razorpay_plan_starter,
        "indie": settings.razorpay_plan_indie,
        "studio": settings.razorpay_plan_studio,
    }
    value = mapping.get(plan, "")
    if not value:
        raise RazorpayError(f"No Razorpay plan id is configured for {plan}")
    return value


def plan_from_id(plan_id_value: str | None, settings: Settings | None = None) -> str | None:
    if not plan_id_value:
        return None
    settings = settings or get_settings()
    mapping = {
        settings.razorpay_plan_starter: "starter",
        settings.razorpay_plan_indie: "indie",
        settings.razorpay_plan_studio: "studio",
    }
    return mapping.get(plan_id_value)


def _request(method: str, path: str, payload: dict | None, settings: Settings) -> dict:
    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        raise RazorpayNotConfigured("Razorpay keys are not configured")
    with httpx.Client(timeout=20) as client:
        response = client.request(
            method,
            f"{API}{path}",
            auth=(settings.razorpay_key_id, settings.razorpay_key_secret),
            json=payload,
        )
    if response.status_code >= 400:
        raise RazorpayError(f"Razorpay {response.status_code}: {response.text[:300]}")
    data = response.json()
    if not isinstance(data, dict):
        raise RazorpayError("Razorpay returned an unexpected response")
    return data


def create_subscription(plan: str, organization_id: str, settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    return _request(
        "POST",
        "/subscriptions",
        {
            "plan_id": plan_id(plan, settings),
            "total_count": 120,
            "customer_notify": 1,
            "notes": {"organization_id": organization_id, "plan": plan},
        },
        settings,
    )


def update_subscription(subscription_id: str, plan: str, *, immediate: bool, settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    return _request(
        "PATCH",
        f"/subscriptions/{subscription_id}",
        {
            "plan_id": plan_id(plan, settings),
            "schedule_change_at": "now" if immediate else "cycle_end",
            "customer_notify": 1,
        },
        settings,
    )


def cancel_subscription(subscription_id: str, settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    return _request(
        "POST",
        f"/subscriptions/{subscription_id}/cancel",
        {"cancel_at_cycle_end": 1},
        settings,
    )
