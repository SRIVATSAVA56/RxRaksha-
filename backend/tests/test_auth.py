"""Tests for authentication and route protection (Prompt 10)."""
from datetime import timedelta
import pytest
from fastapi.testclient import TestClient

import app.main as m
from app.auth import create_token, load_users


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Isolated users store for auth tests
    test_users_file = tmp_path / "test_users.json"
    import app.auth as auth_mod
    monkeypatch.setattr(auth_mod, "USERS_FILE", test_users_file)
    monkeypatch.setattr(auth_mod, "DATA_DIR", tmp_path)
    with TestClient(m.app) as c:
        yield c


def test_demo_login_and_me(client):
    # Demo login returns token + user
    res = client.post("/auth/demo")
    assert res.status_code == 200
    data = res.json()
    assert "token" in data
    assert data["user"]["user_id"] == "demo:demo-user"
    assert data["user"]["email"] == "demo@rxraksha.demo"
    assert data["user"]["name"] == "Demo Clinician"
    assert data["user"]["provider"] == "demo"
    token = data["token"]

    # /me works with valid token
    me_res = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert me_res.status_code == 200
    me_data = me_res.json()
    assert me_data["user_id"] == "demo:demo-user"
    assert me_data["email"] == "demo@rxraksha.demo"

    # /me without token -> 401
    assert client.get("/me").status_code == 401


def test_expired_and_garbage_token(client):
    # Garbage token -> 401
    res = client.get("/me", headers={"Authorization": "Bearer not-a-valid-jwt"})
    assert res.status_code == 401
    assert "Invalid" in res.json()["detail"] or "invalid" in res.json()["detail"].lower()

    # Expired token -> 401
    expired_token = create_token("demo:demo-user", expires_delta=timedelta(seconds=-10))
    res_exp = client.get("/me", headers={"Authorization": f"Bearer {expired_token}"})
    assert res_exp.status_code == 401
    assert "expired" in res_exp.json()["detail"].lower()


def test_analyze_requires_token(client):
    payload = {"lines": ["Warf 5mg OD"], "patient": {}}

    # /analyze without token -> 401
    res_no_auth = client.post("/analyze", json=payload)
    assert res_no_auth.status_code == 401

    # with demo token -> works
    demo_res = client.post("/auth/demo")
    token = demo_res.json()["token"]

    res_auth = client.post("/analyze", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert res_auth.status_code == 200
    assert "score" in res_auth.json()


def test_google_login_success_and_failure(client, monkeypatch):
    import app.auth as auth_mod
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "fake-google-client-id.apps.googleusercontent.com")

    # Path to google.oauth2.id_token.verify_oauth2_token
    import google.oauth2.id_token

    # Success path: mock verify_oauth2_token returning valid user info
    fake_payload = {
        "sub": "1234567890",
        "email": "doctor@example.com",
        "name": "Dr. Sarah",
        "picture": "https://example.com/sarah.jpg"
    }
    monkeypatch.setattr(google.oauth2.id_token, "verify_oauth2_token", lambda *args, **kwargs: fake_payload)

    res = client.post("/auth/google", json={"credential": "valid-fake-id-token"})
    assert res.status_code == 200
    data = res.json()
    assert "token" in data
    assert data["user"]["user_id"] == "google:1234567890"
    assert data["user"]["email"] == "doctor@example.com"
    assert data["user"]["name"] == "Dr. Sarah"
    assert data["user"]["provider"] == "google"

    # Verify /me with google token
    me_res = client.get("/me", headers={"Authorization": f"Bearer {data['token']}"})
    assert me_res.status_code == 200
    assert me_res.json()["email"] == "doctor@example.com"

    # Failure path: mock verify_oauth2_token raising an exception
    def raise_err(*args, **kwargs):
        raise ValueError("Invalid Google token signature")

    monkeypatch.setattr(google.oauth2.id_token, "verify_oauth2_token", raise_err)

    res_fail = client.post("/auth/google", json={"credential": "corrupt-id-token"})
    assert res_fail.status_code == 401
    assert "Google sign-in verification failed" in res_fail.json()["detail"]

    # Google login unconfigured -> 503
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    res_unconfigured = client.post("/auth/google", json={"credential": "some-token"})
    assert res_unconfigured.status_code == 503
    assert "Google login not configured" in res_unconfigured.json()["detail"]


def test_config_reflects_env(client, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id-123")
    res = client.get("/config")
    assert res.status_code == 200
    assert res.json()["google_client_id"] == "test-client-id-123"
    assert res.json()["demo_enabled"] is True

    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    res2 = client.get("/config")
    assert res2.status_code == 200
    assert res2.json()["google_client_id"] is None
