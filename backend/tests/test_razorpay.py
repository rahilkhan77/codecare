import hashlib
import hmac
import json

from codecare.security import verify_razorpay_payment, verify_razorpay_webhook
from tests.conftest import register


def _sign_webhook(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _sign_payment(secret: str, payment_id: str, subscription_id: str) -> str:
    message = f"{payment_id}|{subscription_id}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def _enable(monkeypatch):
    from codecare.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "razorpay_key_id", "rzp_test_key")
    monkeypatch.setattr(settings, "razorpay_key_secret", "rzp_test_secret")
    monkeypatch.setattr(settings, "razorpay_webhook_secret", "whsec_test")
    monkeypatch.setattr(settings, "razorpay_plan_starter", "plan_starter")
    monkeypatch.setattr(settings, "razorpay_plan_indie", "plan_indie")
    monkeypatch.setattr(settings, "razorpay_plan_studio", "plan_studio")
    monkeypatch.setattr(settings, "demo_mode", False)
    return settings


def test_payment_signature_round_trip():
    signature = _sign_payment("rzp_test_secret", "pay_1", "sub_1")
    assert verify_razorpay_payment("rzp_test_secret", "pay_1", "sub_1", signature)
    assert not verify_razorpay_payment("rzp_test_secret", "pay_1", "sub_1", "nope")


def test_verify_rejects_bad_signature(client, monkeypatch):
    _enable(monkeypatch)
    register(client)
    monkeypatch.setattr("codecare.routers.billing.razorpay.create_subscription", lambda *args, **kwargs: {"id": "sub_1"})
    client.post("/api/v1/billing/checkout", json={"plan": "starter"})
    response = client.post(
        "/api/v1/billing/verify",
        json={"razorpay_payment_id": "pay_1", "razorpay_subscription_id": "sub_1", "razorpay_signature": "not-a-real-signature"},
    )
    assert response.status_code == 400


def test_verify_activates_matching_subscription(client, monkeypatch):
    _enable(monkeypatch)
    register(client)
    monkeypatch.setattr("codecare.routers.billing.razorpay.create_subscription", lambda *args, **kwargs: {"id": "sub_1"})
    client.post("/api/v1/billing/checkout", json={"plan": "starter"})
    signature = _sign_payment("rzp_test_secret", "pay_1", "sub_1")
    response = client.post(
        "/api/v1/billing/verify",
        json={"razorpay_payment_id": "pay_1", "razorpay_subscription_id": "sub_1", "razorpay_signature": signature},
    )
    assert response.status_code == 200, response.text
    assert client.get("/api/v1/billing/status").json()["status"] == "active"


def test_webhook_signature_and_idempotency(client, monkeypatch):
    _enable(monkeypatch)
    register(client)
    monkeypatch.setattr("codecare.routers.billing.razorpay.create_subscription", lambda *args, **kwargs: {"id": "sub_1"})
    created = client.post("/api/v1/billing/checkout", json={"plan": "starter"})
    org_id = client.get("/api/v1/auth/me").json()["organization"]["id"]
    body = json.dumps(
        {
            "event": "subscription.activated",
            "payload": {
                "subscription": {
                    "entity": {
                        "id": "sub_1",
                        "plan_id": "plan_starter",
                        "status": "active",
                        "customer_id": "cust_1",
                        "current_end": 1893456000,
                        "notes": {"organization_id": org_id, "plan": "starter"},
                    }
                }
            },
        }
    ).encode()
    headers = {"X-Razorpay-Signature": "bad", "x-razorpay-event-id": "evt_1", "Content-Type": "application/json"}
    assert client.post("/api/v1/webhooks/razorpay", content=body, headers=headers).status_code == 401
    headers["X-Razorpay-Signature"] = _sign_webhook("whsec_test", body)
    first = client.post("/api/v1/webhooks/razorpay", content=body, headers=headers)
    second = client.post("/api/v1/webhooks/razorpay", content=body, headers=headers)
    assert first.status_code == 200, first.text
    assert second.json()["duplicate"] is True
    assert client.get("/api/v1/billing/status").json()["plan"] == "starter"
    assert created.status_code == 200


def _sign_webhook(secret: str, body: bytes) -> str:
    return hmac_hex(secret, body)


def hmac_hex(secret: str, body: bytes) -> str:
    import hmac

    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_webhook_events_update_status(client, monkeypatch):
    _enable(monkeypatch)
    register(client)
    monkeypatch.setattr("codecare.routers.billing.razorpay.create_subscription", lambda *args, **kwargs: {"id": "sub_1"})
    client.post("/api/v1/billing/checkout", json={"plan": "starter"})
    org_id = client.get("/api/v1/auth/me").json()["organization"]["id"]

    def post(event, entity, event_id, kind="subscription"):
        body = json.dumps({"event": event, "payload": {kind: {"entity": entity}}}).encode()
        headers = {
            "X-Razorpay-Signature": hmac_hex("whsec_test", body),
            "x-razorpay-event-id": event_id,
            "Content-Type": "application/json",
        }
        response = client.post("/api/v1/webhooks/razorpay", content=body, headers=headers)
        assert response.status_code == 200, response.text

    base = {"id": "sub_1", "plan_id": "plan_indie", "notes": {"organization_id": org_id, "plan": "indie"}}
    post("subscription.charged", base, "evt_charged")
    assert client.get("/api/v1/billing/status").json()["status"] == "active"
    assert client.get("/api/v1/billing/status").json()["plan"] == "indie"
    post("subscription.pending", base, "evt_pending")
    assert client.get("/api/v1/billing/status").json()["status"] == "pending"
    post("subscription.halted", base, "evt_halted")
    assert client.get("/api/v1/billing/status").json()["status"] == "halted"
    post("payment.failed", {"id": "pay_x", "subscription_id": "sub_1"}, "evt_failed", kind="payment")
    assert client.get("/api/v1/billing/status").json()["status"] == "past_due"
    post("subscription.updated", {**base, "status": "active", "plan_id": "plan_studio"}, "evt_updated")
    assert client.get("/api/v1/billing/status").json()["plan"] == "studio"
    post("subscription.completed", base, "evt_completed")
    assert client.get("/api/v1/billing/status").json()["plan"] == "none"
    post("subscription.cancelled", base, "evt_cancelled")
    assert client.get("/api/v1/billing/status").json()["status"] == "canceled"


def test_change_plan_and_cancel_call_razorpay(client, monkeypatch):
    _enable(monkeypatch)
    register(client)
    monkeypatch.setattr("codecare.routers.billing.razorpay.create_subscription", lambda *args, **kwargs: {"id": "sub_1"})
    calls = []
    monkeypatch.setattr(
        "codecare.routers.billing.razorpay.update_subscription",
        lambda subscription_id, plan, *, immediate, settings=None: calls.append((subscription_id, plan, immediate)) or {"id": subscription_id},
    )
    monkeypatch.setattr(
        "codecare.routers.billing.razorpay.cancel_subscription",
        lambda subscription_id, settings=None: calls.append(("cancel", subscription_id)) or {"id": subscription_id},
    )
    client.post("/api/v1/billing/checkout", json={"plan": "starter"})
    signature = _sign_payment("rzp_test_secret", "pay_1", "sub_1")
    client.post(
        "/api/v1/billing/verify",
        json={"razorpay_payment_id": "pay_1", "razorpay_subscription_id": "sub_1", "razorpay_signature": signature},
    )
    upgraded = client.post("/api/v1/billing/change-plan", json={"plan": "studio"})
    assert upgraded.status_code == 200, upgraded.text
    assert calls[-1] == ("sub_1", "studio", True)
    org_id = client.get("/api/v1/auth/me").json()["organization"]["id"]
    body = json.dumps(
        {
            "event": "subscription.updated",
            "payload": {"subscription": {"entity": {"id": "sub_1", "plan_id": "plan_studio", "status": "active", "notes": {"organization_id": org_id, "plan": "studio"}}}},
        }
    ).encode()
    client.post(
        "/api/v1/webhooks/razorpay",
        content=body,
        headers={"X-Razorpay-Signature": hmac_hex("whsec_test", body), "x-razorpay-event-id": "evt_studio", "Content-Type": "application/json"},
    )
    downgraded = client.post("/api/v1/billing/change-plan", json={"plan": "indie"})
    assert downgraded.status_code == 200, downgraded.text
    assert calls[-1] == ("sub_1", "indie", False)
    cancelled = client.post("/api/v1/billing/cancel")
    assert cancelled.status_code == 200
    assert calls[-1] == ("cancel", "sub_1")
    assert client.get("/api/v1/billing/status").json()["cancel_at_period_end"] is True
