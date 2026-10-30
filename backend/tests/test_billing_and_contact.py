from tests.conftest import register


def test_billing_disabled_without_stripe(client):
    register(client)
    plans = client.get("/api/v1/billing/plans")
    assert plans.json()["checkout_enabled"] is False
    checkout = client.post("/api/v1/billing/checkout", json={"plan": "pro"})
    assert checkout.status_code == 503


def test_contact_persists(client):
    response = client.post(
        "/api/v1/contact",
        json={
            "name": "Jamie",
            "email": "jamie@example.com",
            "topic": "product",
            "message": "I would like to learn about CodeCare scans.",
        },
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_static_does_not_serve_backend(client):
    leaked = client.get("/backend/codecare/config.py")
    assert leaked.status_code == 404
