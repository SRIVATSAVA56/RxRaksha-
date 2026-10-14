"""Tests for PIN-locked vault (Prompt 11)."""
import io
import json
import pytest
from fastapi.testclient import TestClient

import app.main as m
import app.auth as auth_mod
import app.vault as vault_mod


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Isolate users file and vaults directory in tmp_path
    test_users_file = tmp_path / "test_users.json"
    test_vaults_dir = tmp_path / "vaults"
    test_vaults_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(auth_mod, "USERS_FILE", test_users_file)
    monkeypatch.setattr(auth_mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(vault_mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(vault_mod, "VAULTS_DIR", test_vaults_dir)

    # Reset in-memory unlock state
    vault_mod._vault_unlock_state.clear()

    with TestClient(m.app) as c:
        yield c


@pytest.fixture
def auth_header(client):
    res = client.post("/auth/demo")
    assert res.status_code == 200
    token = res.json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_vault_status_and_unlock_before_pin(client, auth_header):
    # status before PIN -> has_pin false, locked true
    res = client.get("/vault/status", headers=auth_header)
    assert res.status_code == 200
    assert res.json() == {"has_pin": False, "locked": True}

    # unlock before PIN set -> 400
    res_unlock = client.post("/vault/unlock", json={"pin": "1234"}, headers=auth_header)
    assert res_unlock.status_code == 400
    assert "PIN" in res_unlock.json()["detail"]


def test_set_pin_and_unlock(client, auth_header):
    # invalid pin format -> 422
    assert client.post("/vault/pin", json={"pin": "abc"}, headers=auth_header).status_code == 422
    assert client.post("/vault/pin", json={"pin": "123"}, headers=auth_header).status_code == 422
    assert client.post("/vault/pin", json={"pin": "1234567"}, headers=auth_header).status_code == 422

    # set valid pin (4-6 digits)
    res_set = client.post("/vault/pin", json={"pin": "1234"}, headers=auth_header)
    assert res_set.status_code == 200

    # status after PIN -> has_pin true, locked true
    res_status = client.get("/vault/status", headers=auth_header)
    assert res_status.json()["has_pin"] is True
    assert res_status.json()["locked"] is True

    # wrong pin -> 401
    res_wrong = client.post("/vault/unlock", json={"pin": "9999"}, headers=auth_header)
    assert res_wrong.status_code == 401
    assert "Incorrect PIN" in res_wrong.json()["detail"]

    # correct pin -> 200 with unlocked_until timestamp
    res_ok = client.post("/vault/unlock", json={"pin": "1234"}, headers=auth_header)
    assert res_ok.status_code == 200
    assert "unlocked_until" in res_ok.json()

    # status now shows unlocked
    res_status2 = client.get("/vault/status", headers=auth_header)
    assert res_status2.json()["locked"] is False

    # lock immediately -> locked again
    res_lock = client.post("/vault/lock", headers=auth_header)
    assert res_lock.status_code == 200
    assert res_lock.json()["locked"] is True
    assert client.get("/vault/status", headers=auth_header).json()["locked"] is True


def test_locked_access_rejected(client, auth_header):
    # Set PIN and lock
    client.post("/vault/pin", json={"pin": "1234"}, headers=auth_header)
    client.post("/vault/lock", headers=auth_header)

    # locked access to /vault/files -> 423
    res = client.get("/vault/files", headers=auth_header)
    assert res.status_code == 423
    assert "Vault is locked" in res.json()["detail"]

    # locked access to upload -> 423
    res_up = client.post(
        "/vault/upload",
        files={"file": ("test.pdf", b"%PDF-fake", "application/pdf")},
        headers=auth_header,
    )
    assert res_up.status_code == 423


def test_upload_pdf_download_and_delete(client, auth_header):
    # Set PIN and unlock
    client.post("/vault/pin", json={"pin": "1234"}, headers=auth_header)
    client.post("/vault/unlock", json={"pin": "1234"}, headers=auth_header)

    fake_pdf = b"%PDF-1.4 fake pdf content here for test"
    res_upload = client.post(
        "/vault/upload",
        files={"file": ("prescription.pdf", fake_pdf, "application/pdf")},
        headers=auth_header,
    )
    assert res_upload.status_code == 200
    meta = res_upload.json()
    assert meta["name"] == "prescription.pdf"
    assert meta["kind"] == "pdf"
    assert meta["size_bytes"] == len(fake_pdf)
    file_id = meta["file_id"]

    # listed in /vault/files
    res_list = client.get("/vault/files", headers=auth_header)
    assert res_list.status_code == 200
    files = res_list.json()
    assert len(files) == 1
    assert files[0]["file_id"] == file_id

    # download returns same bytes and inline content-disposition
    res_down = client.get(f"/vault/files/{file_id}", headers=auth_header)
    assert res_down.status_code == 200
    assert res_down.content == fake_pdf
    assert "inline" in res_down.headers.get("Content-Disposition", "")
    assert res_down.headers["Content-Type"] == "application/pdf"

    # delete file -> returns success
    res_del = client.delete(f"/vault/files/{file_id}", headers=auth_header)
    assert res_del.status_code == 200

    # after delete -> 404
    assert client.get(f"/vault/files/{file_id}", headers=auth_header).status_code == 404
    assert len(client.get("/vault/files", headers=auth_header).json()) == 0


def test_upload_invalid_type_and_oversized(client, auth_header):
    client.post("/vault/pin", json={"pin": "1234"}, headers=auth_header)
    client.post("/vault/unlock", json={"pin": "1234"}, headers=auth_header)

    # upload .txt -> 422
    res_txt = client.post(
        "/vault/upload",
        files={"file": ("notes.txt", b"plain text", "text/plain")},
        headers=auth_header,
    )
    assert res_txt.status_code == 422

    # oversized (>10MB) -> 413
    big_data = b"0" * (10 * 1024 * 1024 + 10)
    res_big = client.post(
        "/vault/upload",
        files={"file": ("huge.png", big_data, "image/png")},
        headers=auth_header,
    )
    assert res_big.status_code == 413


def test_save_report(client, auth_header):
    client.post("/vault/pin", json={"pin": "1234"}, headers=auth_header)
    client.post("/vault/unlock", json={"pin": "1234"}, headers=auth_header)

    fake_report = {
        "score": 95,
        "band": "Critical",
        "findings": [{"rule_id": "DDI-001", "severity": "contraindicated"}],
        "summary": "Severe drug interaction detected"
    }

    res = client.post("/vault/save-report", json=fake_report, headers=auth_header)
    assert res.status_code == 200
    entry = res.json()
    assert entry["kind"] == "report"
    assert entry["content_type"] == "application/json"
    assert entry["name"].startswith("report-") and entry["name"].endswith(".json")

    # verify downloadable report
    file_id = entry["file_id"]
    res_down = client.get(f"/vault/files/{file_id}", headers=auth_header)
    assert res_down.status_code == 200
    saved_json = json.loads(res_down.content.decode("utf-8"))
    assert saved_json["score"] == 95


def test_path_traversal_prevention(client, auth_header):
    client.post("/vault/pin", json={"pin": "1234"}, headers=auth_header)
    client.post("/vault/unlock", json={"pin": "1234"}, headers=auth_header)

    # Path traversal attempts (must never escape vault dir -> 400 or 404)
    traversal_ids = ["..%2F..", "%2e%2e%2f%2e%2e", "..%2Fetc", "etc%2Fpasswd"]
    for bad_id in traversal_ids:
        res = client.get(f"/vault/files/{bad_id}", headers=auth_header)
        assert res.status_code in (400, 404)

        res_del = client.delete(f"/vault/files/{bad_id}", headers=auth_header)
        assert res_del.status_code in (400, 404)
