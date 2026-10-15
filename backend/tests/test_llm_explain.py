"""Tests for AI Explain feature (/analyze/explain)."""
import os
import pytest
from fastapi.testclient import TestClient

import app.main as m
import app.auth as auth_mod
import app.vault as vault_mod
from app.explain.llm_client import LLMClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    test_users_file = tmp_path / "test_users.json"
    test_vaults_dir = tmp_path / "vaults"
    test_vaults_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(auth_mod, "USERS_FILE", test_users_file)
    monkeypatch.setattr(auth_mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(vault_mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(vault_mod, "VAULTS_DIR", test_vaults_dir)

    vault_mod._vault_unlock_state.clear()
    m.EXPLAIN_CACHE.clear()

    with TestClient(m.app) as c:
        yield c


@pytest.fixture
def auth_header(client):
    res = client.post("/auth/demo")
    assert res.status_code == 200
    token = res.json()["token"]
    return {"Authorization": f"Bearer {token}"}


# 1. unknown severity: LLM never called, template text returned.
def test_unknown_severity_template_text(client, auth_header, monkeypatch):
    called = []

    def mock_call(self, facts: str) -> str:
        called.append(facts)
        return "Should not be called"

    monkeypatch.setattr(LLMClient, "__call__", mock_call)
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")

    payload = {
        "category": "dose",
        "severity": "unknown",
        "ingredients": ["paracetamol"],
        "evidence_source": "dose_rule seed",
        "kb_version": "v1.4",
        "data": {"total_daily_mg": 2000, "unknown_lines": 1},
        "age": 45,
    }

    res = client.post("/analyze/explain", json=payload, headers=auth_header)
    assert res.status_code == 200
    data = res.json()
    assert len(called) == 0, "LLM was called for unknown severity!"
    assert data["ai_explanation"]["source"] == "template"
    assert data["ai_explanation"]["text"] == "The knowledge base lists this pair with no severity rating. No further information is available."
    assert data["ai_explanation"]["model"] is None
    assert data["ai_explanation"]["verified"] is False
    assert data["ai_explanation"]["label"] == "No AI used."


# 2. known severity: LLM called once; the facts string contains only the allowed fields.
def test_known_severity_facts_format(client, auth_header, monkeypatch):
    called = []

    def mock_call(self, facts: str) -> str:
        called.append(facts)
        return "Warfarin and aspirin combined increase bleeding risk. Monitor INR and stool color."

    monkeypatch.setattr(LLMClient, "__call__", mock_call)
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "model-abc")

    payload = {
        "category": "ddi",
        "severity": "major",
        "ingredients": ["warfarin", "aspirin"],
        "evidence_source": "DDInter 2.0",
        "kb_version": "v1.4",
        "data": {
            "severity": "major",
            "total_daily_mg": 5.0,
            "disallowed_key": "bad_value",
            "too_long": "x" * 70,
        },
        "age": 68,
    }

    res = client.post("/analyze/explain", json=payload, headers=auth_header)
    assert res.status_code == 200
    assert len(called) == 1

    facts = called[0]
    # Check format
    assert "Finding type: ddi" in facts
    assert "Drugs: warfarin, aspirin" in facts
    assert "Severity per DDInter 2.0: major" in facts
    assert "Knowledge base: v1.4" in facts
    assert "Details: severity=major, total_daily_mg=5.0" in facts
    assert "disallowed_key" not in facts
    assert "too_long" not in facts
    assert "Patient age: 68" in facts
    # Never include names/allergies/OCR/images
    assert "OCR" not in facts
    assert "image" not in facts

    data = res.json()
    assert data["ai_explanation"]["source"] == "llm"
    assert data["ai_explanation"]["text"] == "Warfarin and aspirin combined increase bleeding risk. Monitor INR and stool color."
    assert data["ai_explanation"]["model"] == "model-abc"
    assert data["ai_explanation"]["verified"] is False
    assert "Unverified" in data["ai_explanation"]["label"]


# 3. LLM raises: response is 503 and nothing is cached.
def test_llm_raises_503_and_nothing_cached(client, auth_header, monkeypatch):
    def mock_call(self, facts: str) -> str:
        raise RuntimeError("LLM call failed")

    monkeypatch.setattr(LLMClient, "__call__", mock_call)
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "model-abc")

    payload = {
        "category": "ddi",
        "severity": "major",
        "ingredients": ["warfarin", "aspirin"],
        "evidence_source": "DDInter 2.0",
        "kb_version": "v1.4",
    }

    res = client.post("/analyze/explain", json=payload, headers=auth_header)
    assert res.status_code == 503
    assert res.json()["detail"] == "AI explanation unavailable"
    assert len(m.EXPLAIN_CACHE) == 0


# 4. feature disabled (no key/model): 404, and /health shows ai_explain false.
def test_feature_disabled_404_and_health_false(client, auth_header, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)

    # Health check shows ai_explain: False
    h_res = client.get("/health")
    assert h_res.status_code == 200
    h_data = h_res.json()
    assert h_data["ai_explain"] is False
    assert "drugs" in h_data
    assert "interactions" in h_data

    payload = {
        "category": "ddi",
        "severity": "major",
        "ingredients": ["warfarin", "aspirin"],
        "evidence_source": "DDInter 2.0",
        "kb_version": "v1.4",
    }

    res = client.post("/analyze/explain", json=payload, headers=auth_header)
    assert res.status_code == 404
    assert res.json()["detail"] == "AI explanation is not enabled"


# 5. second identical request: client not called again (cache hit).
def test_second_request_cache_hit(client, auth_header, monkeypatch):
    call_count = [0]

    def mock_call(self, facts: str) -> str:
        call_count[0] += 1
        return "Cached explanation text."

    monkeypatch.setattr(LLMClient, "__call__", mock_call)
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "model-cached")

    payload = {
        "category": "ddi",
        "severity": "moderate",
        "ingredients": ["ibuprofen", "aspirin"],
        "evidence_source": "DDInter 2.0",
        "kb_version": "v1.4",
    }

    # First request
    res1 = client.post("/analyze/explain", json=payload, headers=auth_header)
    assert res1.status_code == 200
    assert call_count[0] == 1

    # Second identical request
    res2 = client.post("/analyze/explain", json=payload, headers=auth_header)
    assert res2.status_code == 200
    assert call_count[0] == 1  # not called again!
    assert res1.json() == res2.json()


# 6. no token: same rejection status the existing /analyze test expects.
def test_no_token_rejected_401(client):
    payload = {
        "category": "ddi",
        "severity": "major",
        "ingredients": ["warfarin", "aspirin"],
        "evidence_source": "DDInter 2.0",
        "kb_version": "v1.4",
    }
    res = client.post("/analyze/explain", json=payload)
    assert res.status_code == 401


# 7. bad severity, 7 ingredients, or an ingredient containing "<script>": 422.
def test_validation_errors_422(client, auth_header, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")

    base = {
        "category": "ddi",
        "severity": "major",
        "ingredients": ["warfarin"],
        "evidence_source": "DDInter 2.0",
        "kb_version": "v1.4",
    }

    # Bad severity
    p = {**base, "severity": "super_critical"}
    assert client.post("/analyze/explain", json=p, headers=auth_header).status_code == 422

    # 7 ingredients (limit is 6)
    p = {**base, "ingredients": ["a", "b", "c", "d", "e", "f", "g"]}
    assert client.post("/analyze/explain", json=p, headers=auth_header).status_code == 422

    # 0 ingredients
    p = {**base, "ingredients": []}
    assert client.post("/analyze/explain", json=p, headers=auth_header).status_code == 422

    # Ingredient containing <script>
    p = {**base, "ingredients": ["warfarin", "<script>alert(1)</script>"]}
    assert client.post("/analyze/explain", json=p, headers=auth_header).status_code == 422

    # Evidence source containing <script>
    p = {**base, "evidence_source": "<script>alert(1)</script>"}
    assert client.post("/analyze/explain", json=p, headers=auth_header).status_code == 422

    # Bad age
    p = {**base, "age": 150}
    assert client.post("/analyze/explain", json=p, headers=auth_header).status_code == 422

    p = {**base, "age": -5}
    assert client.post("/analyze/explain", json=p, headers=auth_header).status_code == 422


# 8. /analyze output still has ai_summary == None and contains no AI text.
def test_analyze_output_unaffected(client, auth_header, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")

    payload = {
        "lines": ["Warfarin 5mg OD", "Aspirin 75mg OD"],
        "patient": {"age": 60, "weight_kg": 70},
    }
    res = client.post("/analyze", json=payload, headers=auth_header)
    assert res.status_code == 200
    data = res.json()
    assert data["ai_summary"] is None
    # Findings must not contain AI fields
    for f in data["findings"]:
        assert "ai_explanation" not in f


# 9. audience="patient" invokes patient explanation and applies safety cleanups.
def test_patient_voice_and_safety_net(client, auth_header, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")

    def mock_call(self, facts: str) -> str:
        assert self.audience == "patient"
        assert "Patient: age 72" in facts
        # Simulate LLM returning jargon and extra sentences
        return (
            "Warfarin and aspirin are taken together. "
            "Aspirin increases the anticoagulant effect. "
            "This requires regular INR monitoring. "
            "Because of your age, bleeding risks are elevated. "
            "This fifth sentence should be trimmed off."
        )

    monkeypatch.setattr(LLMClient, "__call__", mock_call)

    payload = {
        "category": "ddi",
        "severity": "major",
        "ingredients": ["warfarin", "aspirin"],
        "evidence_source": "ddinter",
        "kb_version": "2.0",
        "age": 72,
        "gender": "female",
        "audience": "patient",
    }
    res = client.post("/analyze/explain", json=payload, headers=auth_header)
    assert res.status_code == 200
    data = res.json()["ai_explanation"]
    assert data["audience"] == "patient"
    assert data["verified"] is False
    # Max 4 sentences
    sentences = [s for s in data["text"].split(".") if s.strip()]
    assert len(sentences) <= 5  # 4 sentences + warning
    # Jargon replaced
    assert "anticoagulant" not in data["text"].lower()
    assert "blood thinner" in data["text"].lower()
    assert "inr" not in data["text"].lower()
    # Mandatory warning included
    assert "do not stop any medicine on your own" in data["text"].lower()


# 10. audience validation rejects invalid audience.
def test_audience_validation(client, auth_header, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")

    payload = {
        "category": "ddi",
        "severity": "major",
        "ingredients": ["warfarin", "aspirin"],
        "evidence_source": "ddinter",
        "kb_version": "2.0",
        "audience": "hacker",
    }
    res = client.post("/analyze/explain", json=payload, headers=auth_header)
    assert res.status_code == 422


# 11. separate caching per audience for the same finding.
def test_separate_caching_per_audience(client, auth_header, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")

    call_count = {"clinician": 0, "patient": 0}

    def mock_call(self, facts: str) -> str:
        call_count[self.audience] += 1
        return f"Response for {self.audience}"

    monkeypatch.setattr(LLMClient, "__call__", mock_call)

    base = {
        "category": "ddi",
        "severity": "major",
        "ingredients": ["warfarin", "aspirin"],
        "evidence_source": "ddinter",
        "kb_version": "2.0",
        "age": 65,
    }

    # First clinician call
    r1 = client.post("/analyze/explain", json={**base, "audience": "clinician"}, headers=auth_header)
    assert r1.status_code == 200
    assert call_count["clinician"] == 1

    # First patient call - must call LLM again, not return cached clinician response
    r2 = client.post("/analyze/explain", json={**base, "audience": "patient"}, headers=auth_header)
    assert r2.status_code == 200
    assert call_count["patient"] == 1
    assert "patient" in r2.json()["ai_explanation"]["text"].lower()

    # Second patient call - served from cache
    r3 = client.post("/analyze/explain", json={**base, "audience": "patient"}, headers=auth_header)
    assert r3.status_code == 200
    assert call_count["patient"] == 1

