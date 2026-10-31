import base64
import hashlib
import hmac
import json
import time

from codecare.security import verify_standard_webhook
from tests.conftest import register

SECRET_RAW = b"dodo-test-secret-key"
SECRET = "whsec_" + base64.b64encode(SECRET_RAW).decode()


def _enable(monkeypatch):
    from codecare.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "dodo_payments_api_key", "dodo_test_key")
    monkeypatch.setattr(settings, "dodo_payments_webhook_secret", SECRET)
    monkeypatch.setattr(settings, "dodo_payments_environment", "test_mode")
    monkeypatch.setattr(settings, "dodo_product_starter", "pdt_starter")
    monkeypatch.setattr(settings, "dodo_product_indie", "pdt_indie")
    monkeypatch.setattr(settings, "dodo_product_studio", "pdt_studio")
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "public_base_url", "https://codecare.si")
    return settings


def _sign(body: bytes, webhook_id: str = "msg_1", timestamp: int | None = None) -> dict[str, str]:
    issued = int(time.time()) if timestamp is None else timestamp
    signed = f"{webhook_id}.{issued}.".encode() + body
    digest = base64.b64encode(hmac.new(SECRET_RAW, signed, hashlib.sha256).digest()).decode()
    return {
        "webhook-id": webhook_id,
        "webhook-timestamp": str(issued),
        "webhook-signature": f"v1,{digest}",
        "Content-Type": "application/json",
    }


def _event(event_type: str, data: dict) -> bytes:
    return json.dumps({"business_id": "bus_1", "type": event_type, "timestamp": "2026-10-31T00:00:00Z", "data": data}).encode()


def _post_event(client, event_type: str, data: dict, webhook_id: str, timestamp: int | None = None):
    body = _event(event_type, data)
    response = client.post("/api/v1/webhooks/dodo", content=body, headers=_sign(body, webhook_id, timestamp))
    assert response.status_code == 200, response.text
    return response


def test_standard_webhook_signature_round_trip():
    body = b'{"type":"subscription.active"}'
    headers = _sign(body, "msg_round")
    assert verify_standard_webhook(SECRET, body, headers["webhook-id"], headers["webhook-timestamp"], headers["webhook-signature"])
    assert not verify_standard_webhook(SECRET, body, headers["webhook-id"], headers["webhook-timestamp"], "v1,bm90LWEtcmVhbC1zaWduYXR1cmU=")
    stale = _sign(body, "msg_stale", timestamp=int(time.time()) - 3600)
    assert not verify_standard_webhook(SECRET, body, stale["webhook-id"], stale["webhook-timestamp"], stale["webhook-signature"])


def test_checkout_returns_hosted_url(client, monkeypatch):
    _enable(monkeypatch)
    register(client)
    seen = {}

    def fake_create(**kwargs):
        seen.update(kwargs)
        return {"checkout_url": "https://checkout.dodopayments.com/session/chs_1", "session_id": "chs_1"}

    monkeypatch.setattr("codecare.routers.billing.dodo.create_checkout", fake_create)
    response = client.post("/api/v1/billing/checkout", json={"plan": "starter"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["url"] == "https://checkout.dodopayments.com/session/chs_1"
    assert body["session_id"] == "chs_1"
    assert "dodo_test_key" not in response.text
    assert seen["return_url"] == "https://codecare.si/workspace"
    assert seen["plan"] == "starter"
    assert client.get("/api/v1/billing/status").json()["status"] == "pending"


def test_webhook_signature_idempotency_and_events(client, monkeypatch):
    _enable(monkeypatch)
    register(client)
    org_id = client.get("/api/v1/auth/me").json()["organization"]["id"]
    body = _event(
        "subscription.active",
        {
            "subscription_id": "sub_1",
            "product_id": "pdt_starter",
            "status": "active",
            "customer": {"customer_id": "cus_1", "email": "owner@example.com", "name": "Owner"},
            "metadata": {"organization_id": org_id, "plan": "starter"},
            "next_billing_date": "2026-11-30T00:00:00Z",
            "cancel_at_next_billing_date": False,
        },
    )
    bad = _sign(body, "msg_active")
    bad["webhook-signature"] = "v1,bm90LWEtcmVhbC1zaWduYXR1cmU="
    assert client.post("/api/v1/webhooks/dodo", content=body, headers=bad).status_code == 401
    first = client.post("/api/v1/webhooks/dodo", content=body, headers=_sign(body, "msg_active"))
    second = client.post("/api/v1/webhooks/dodo", content=body, headers=_sign(body, "msg_active"))
    assert first.status_code == 200, first.text
    assert second.json()["duplicate"] is True
    status = client.get("/api/v1/billing/status").json()
    assert status["plan"] == "starter"
    assert status["status"] == "active"
    assert status["portal_available"] is True

    base = {
        "subscription_id": "sub_1",
        "product_id": "pdt_indie",
        "customer": {"customer_id": "cus_1", "email": "owner@example.com", "name": "Owner"},
        "metadata": {"organization_id": org_id, "plan": "indie"},
        "next_billing_date": "2026-12-30T00:00:00Z",
        "cancel_at_next_billing_date": False,
    }
    _post_event(client, "subscription.renewed", base, "msg_renewed")
    assert client.get("/api/v1/billing/status").json()["status"] == "active"
    assert client.get("/api/v1/billing/status").json()["plan"] == "indie"
    _post_event(client, "subscription.plan_changed", {**base, "product_id": "pdt_studio", "metadata": {"organization_id": org_id, "plan": "studio"}}, "msg_changed")
    assert client.get("/api/v1/billing/status").json()["plan"] == "studio"
    _post_event(client, "payment.succeeded", {"payment_id": "pay_1", "subscription_id": "sub_1", "metadata": {"organization_id": org_id}}, "msg_paid")
    assert client.get("/api/v1/billing/status").json()["status"] == "active"
    _post_event(client, "payment.failed", {"payment_id": "pay_2", "subscription_ids": ["sub_1"], "metadata": {"organization_id": org_id}}, "msg_pay_failed")
    assert client.get("/api/v1/billing/status").json()["status"] == "past_due"
    _post_event(client, "subscription.on_hold", base, "msg_hold")
    assert client.get("/api/v1/billing/status").json()["status"] == "on_hold"
    _post_event(client, "subscription.failed", base, "msg_failed")
    assert client.get("/api/v1/billing/status").json()["status"] == "failed"
    _post_event(client, "subscription.active", {**base, "product_id": "pdt_studio", "cancel_at_next_billing_date": True}, "msg_active_again")
    assert client.get("/api/v1/billing/status").json()["cancel_at_period_end"] is True
    _post_event(client, "subscription.cancelled", base, "msg_cancelled")
    cancelled = client.get("/api/v1/billing/status").json()
    assert cancelled["status"] == "canceled"
    assert cancelled["plan"] == "none"
    _post_event(client, "subscription.expired", base, "msg_expired")
    assert client.get("/api/v1/billing/status").json()["status"] == "expired"


def test_change_plan_cancel_and_portal(client, monkeypatch):
    _enable(monkeypatch)
    register(client)
    org_id = client.get("/api/v1/auth/me").json()["organization"]["id"]
    calls = []

    def fake_create(**kwargs):
        calls.append(("checkout", kwargs["return_url"]))
        return {"checkout_url": "https://checkout.dodopayments.com/session/chs_1", "session_id": "chs_1"}

    def fake_change(**kwargs):
        calls.append(("change", kwargs["subscription_id"], kwargs["plan"], kwargs["immediate"]))
        return {"payment_link": None}

    def fake_cancel(subscription_id, settings=None):
        calls.append(("cancel", subscription_id))

    def fake_portal(**kwargs):
        calls.append(("portal", kwargs["customer_id"], kwargs["return_url"]))
        return "https://customer.dodopayments.com/session/cps_1"

    monkeypatch.setattr("codecare.routers.billing.dodo.create_checkout", fake_create)
    monkeypatch.setattr("codecare.routers.billing.dodo.change_plan", fake_change)
    monkeypatch.setattr("codecare.routers.billing.dodo.cancel_at_next_billing_date", fake_cancel)
    monkeypatch.setattr("codecare.routers.billing.dodo.create_portal", fake_portal)
    assert client.post("/api/v1/billing/checkout", json={"plan": "starter"}).status_code == 200
    _post_event(
        client,
        "subscription.active",
        {
            "subscription_id": "sub_1",
            "product_id": "pdt_starter",
            "customer": {"customer_id": "cus_1", "email": "a@example.com", "name": "A"},
            "metadata": {"organization_id": org_id, "plan": "starter"},
            "next_billing_date": "2026-11-30T00:00:00Z",
            "cancel_at_next_billing_date": False,
        },
        "msg_live",
    )
    upgraded = client.post("/api/v1/billing/change-plan", json={"plan": "studio"})
    assert upgraded.status_code == 200, upgraded.text
    assert calls[-1] == ("change", "sub_1", "studio", True)
    _post_event(
        client,
        "subscription.plan_changed",
        {
            "subscription_id": "sub_1",
            "product_id": "pdt_studio",
            "customer": {"customer_id": "cus_1", "email": "a@example.com", "name": "A"},
            "metadata": {"organization_id": org_id, "plan": "studio"},
            "cancel_at_next_billing_date": False,
        },
        "msg_studio",
    )
    downgraded = client.post("/api/v1/billing/change-plan", json={"plan": "indie"})
    assert downgraded.status_code == 200, downgraded.text
    assert downgraded.json()["schedule_change_at"] == "next_billing_date"
    assert calls[-1] == ("change", "sub_1", "indie", False)
    cancelled = client.post("/api/v1/billing/cancel")
    assert cancelled.status_code == 200
    assert calls[-1] == ("cancel", "sub_1")
    assert client.get("/api/v1/billing/status").json()["cancel_at_period_end"] is True
    portal = client.post("/api/v1/billing/portal")
    assert portal.status_code == 200, portal.text
    assert portal.json()["url"] == "https://customer.dodopayments.com/session/cps_1"
    assert calls[-1] == ("portal", "cus_1", "https://codecare.si/workspace")


def test_sdk_methods_match_dodo_subscription_api(monkeypatch):
    from codecare.config import get_settings
    from codecare.services.dodo import cancel_at_next_billing_date, change_plan, create_checkout, create_portal

    settings = get_settings()
    monkeypatch.setattr(settings, "dodo_payments_api_key", "dodo_test_key")
    monkeypatch.setattr(settings, "dodo_payments_environment", "test_mode")
    monkeypatch.setattr(settings, "dodo_product_starter", "pdt_starter")
    monkeypatch.setattr(settings, "dodo_product_studio", "pdt_studio")
    calls = {}

    class Subscriptions:
        def change_plan(self, subscription_id, **kwargs):
            calls["change"] = (subscription_id, kwargs)
            return type("Change", (), {"payment_link": None})()

        def update(self, subscription_id, **kwargs):
            calls["update"] = (subscription_id, kwargs)

    class Portal:
        def create(self, customer_id, **kwargs):
            calls["portal"] = (customer_id, kwargs)
            return type("Portal", (), {"link": "https://customer.dodopayments.com/session/cps_1"})()

    class Customers:
        customer_portal = Portal()

    class Sessions:
        def create(self, **kwargs):
            calls["checkout"] = kwargs
            return type("Session", (), {"checkout_url": "https://checkout.dodopayments.com/session/chs_1", "session_id": "chs_1"})()

    class FakeClient:
        subscriptions = Subscriptions()
        customers = Customers()
        checkout_sessions = Sessions()

        def close(self):
            calls["closed"] = True

    monkeypatch.setattr("codecare.services.dodo.DodoPayments", lambda **kwargs: FakeClient())
    created = create_checkout(
        plan="starter",
        organization_id="org_1",
        email="owner@example.com",
        name="Owner",
        return_url="https://codecare.si/workspace",
        settings=settings,
    )
    assert created["checkout_url"].startswith("https://checkout.dodopayments.com/")
    assert calls["checkout"]["return_url"] == "https://codecare.si/workspace"
    assert calls["checkout"]["product_cart"] == [{"product_id": "pdt_starter", "quantity": 1}]
    change_plan(subscription_id="sub_1", plan="studio", immediate=True, organization_id="org_1", settings=settings)
    assert calls["change"][1]["effective_at"] == "immediately"
    assert calls["change"][1]["proration_billing_mode"] == "difference_immediately"
    assert calls["change"][1]["product_id"] == "pdt_studio"
    change_plan(subscription_id="sub_1", plan="starter", immediate=False, organization_id="org_1", settings=settings)
    assert calls["change"][1]["effective_at"] == "next_billing_date"
    assert calls["change"][1]["proration_billing_mode"] == "full_immediately"
    cancel_at_next_billing_date("sub_1", settings)
    assert calls["update"] == ("sub_1", {"cancel_at_next_billing_date": True})
    assert create_portal(customer_id="cus_1", return_url="https://codecare.si/workspace", settings=settings).startswith("https://customer.dodopayments.com/")
    assert calls["portal"][1]["return_url"] == "https://codecare.si/workspace"
    assert calls["closed"] is True


def test_webhook_without_secret_is_unavailable(client, monkeypatch):
    from codecare.config import get_settings

    monkeypatch.setattr(get_settings(), "dodo_payments_webhook_secret", "")
    response = client.post("/api/v1/webhooks/dodo", content=b"{}", headers={"webhook-id": "msg_x", "webhook-timestamp": "1", "webhook-signature": "v1,aa"})
    assert response.status_code == 503
    assert "Dodo" in response.json()["detail"]
