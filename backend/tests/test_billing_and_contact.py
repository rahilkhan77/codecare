from tests.conftest import register


def test_billing_disabled_without_razorpay(client):
    register(client)
    plans = client.get("/api/v1/billing/plans")
    assert plans.json()["checkout_enabled"] is False
    assert plans.json()["currency"] == "USD"
    checkout = client.post("/api/v1/billing/checkout", json={"plan": "starter"})
    assert checkout.status_code == 503
    assert "Razorpay" in checkout.json()["detail"]


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
