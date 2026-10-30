from tests.conftest import register


def test_register_and_me(client):
    register(client)
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == "jamie@example.com"
    assert body["organization"]["id"]


def test_login_rejects_bad_password(client):
    register(client)
    client.post("/api/v1/auth/logout")
    failed = client.post("/api/v1/auth/login", json={"email": "jamie@example.com", "password": "wrong-password"})
    assert failed.status_code == 401


def test_duplicate_register(client):
    register(client)
    again = client.post(
        "/api/v1/auth/register",
        json={"email": "jamie@example.com", "password": "correct-horse-battery", "full_name": "Jamie"},
    )
    assert again.status_code == 409


def test_supabase_disabled(client):
    response = client.post("/api/v1/auth/supabase", json={"access_token": "nope"})
    assert response.status_code == 503
