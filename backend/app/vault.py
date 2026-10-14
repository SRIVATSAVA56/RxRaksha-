"""RxRaksha PIN-Locked Vault Module.

Provides:
- In-memory vault unlock tracking (relocks upon server restart)
- PBKDF2-HMAC-SHA256 salted PIN storage in users.json
- Endpoints for PIN setting, unlocking, manual locking, and lock-state checks
- File storage and index metadata management (PDF, images, reports)
- Path traversal protection and 10MB size limits
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.auth import DATA_DIR, get_current_user, load_users, save_users

VAULTS_DIR = DATA_DIR / "vaults"

# In-memory unlock state: {user_id: unlocked_until_unix_timestamp}
# Never persisted — restarting the server safely locks all vaults.
_vault_unlock_state: dict[str, int] = {}

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

ALLOWED_TYPES: dict[str, str] = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}


# ----------------- Helper Functions -----------------

def get_user_vault_dir(user_id: str) -> Path:
    # Deterministic and safe directory name on Windows and POSIX
    safe_user_id = user_id.replace(":", "_").replace("/", "_").replace("\\", "_")
    d = VAULTS_DIR / safe_user_id
    d.mkdir(parents=True, exist_ok=True)
    return d

def get_vault_index(user_id: str) -> list[dict[str, Any]]:
    d = get_user_vault_dir(user_id)
    idx_file = d / "index.json"
    if not idx_file.exists():
        return []
    try:
        with open(idx_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_vault_index(user_id: str, entries: list[dict[str, Any]]) -> None:
    d = get_user_vault_dir(user_id)
    idx_file = d / "index.json"
    tmp = idx_file.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)
    tmp.replace(idx_file)

def is_vault_unlocked(user_id: str) -> bool:
    unlocked_until = _vault_unlock_state.get(user_id, 0)
    return time.time() < unlocked_until


# ----------------- Dependencies -----------------

def require_unlocked(current_user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    user_id = current_user["user_id"]
    if not is_vault_unlocked(user_id):
        raise HTTPException(status_code=423, detail="Vault is locked")
    return current_user


# ----------------- Router & Schemas -----------------

router = APIRouter(prefix="/vault", tags=["vault"])

class SetPinRequest(BaseModel):
    pin: str

class UnlockRequest(BaseModel):
    pin: str


# ----------------- PIN Endpoints -----------------

@router.post("/pin")
def set_pin(req: SetPinRequest, current_user: dict[str, Any] = Depends(get_current_user)):
    """Sets or changes the user's 4-6 digit vault PIN."""
    pin = req.pin
    if not isinstance(pin, str) or not re.fullmatch(r"^\d{4,6}$", pin):
        raise HTTPException(status_code=422, detail="PIN must be 4 to 6 digits")

    salt = secrets.token_hex(16)
    pin_hash = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt.encode("utf-8"), 100_000).hex()

    users = load_users()
    user_id = current_user["user_id"]
    if user_id not in users:
        users[user_id] = current_user
    users[user_id]["pin_hash"] = pin_hash
    users[user_id]["pin_salt"] = salt
    save_users(users)

    return {"ok": True, "detail": "PIN set successfully"}

@router.get("/status")
def vault_status(current_user: dict[str, Any] = Depends(get_current_user)):
    """Returns current lock state and whether a PIN has been configured."""
    users = load_users()
    user = users.get(current_user["user_id"], current_user)
    has_pin = bool(user.get("pin_hash"))
    locked = not is_vault_unlocked(current_user["user_id"])
    return {
        "has_pin": has_pin,
        "locked": locked
    }

@router.post("/unlock")
def unlock_vault(req: UnlockRequest, current_user: dict[str, Any] = Depends(get_current_user)):
    """Unlocks the user's vault for 5 minutes with the correct PIN."""
    users = load_users()
    user = users.get(current_user["user_id"], current_user)

    pin_hash = user.get("pin_hash")
    pin_salt = user.get("pin_salt")
    if not pin_hash or not pin_salt:
        raise HTTPException(status_code=400, detail="Set a PIN first")

    calc_hash = hashlib.pbkdf2_hmac("sha256", req.pin.encode("utf-8"), pin_salt.encode("utf-8"), 100_000).hex()
    if not hmac.compare_digest(calc_hash, pin_hash):
        raise HTTPException(status_code=401, detail="Incorrect PIN")

    unlocked_until = int(time.time()) + 300  # 5 minutes
    _vault_unlock_state[current_user["user_id"]] = unlocked_until
    return {"unlocked_until": unlocked_until}

@router.post("/lock")
def lock_vault(current_user: dict[str, Any] = Depends(get_current_user)):
    """Immediately relocks the user's vault."""
    _vault_unlock_state[current_user["user_id"]] = 0
    return {"locked": True}


# ----------------- File Storage Endpoints -----------------

@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    current_user: dict[str, Any] = Depends(require_unlocked)
):
    """Uploads a prescription document or image to the unlocked vault."""
    user_id = current_user["user_id"]
    content_type = file.content_type or ""

    if content_type not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported file type '{content_type}'. Allowed types: PDF, PNG, JPEG, WEBP."
        )

    raw = await file.read()
    if len(raw) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File too large: maximum allowed size is 10MB")

    file_id = str(uuid.uuid4())
    ext = ALLOWED_TYPES[content_type]
    stored_filename = f"{file_id}{ext}"
    vault_dir = get_user_vault_dir(user_id)
    stored_path = vault_dir / stored_filename
    stored_path.write_bytes(raw)

    kind = "pdf" if content_type == "application/pdf" else "image"
    entry = {
        "file_id": file_id,
        "name": file.filename or stored_filename,
        "content_type": content_type,
        "size_bytes": len(raw),
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "kind": kind
    }

    entries = get_vault_index(user_id)
    entries.insert(0, entry)
    save_vault_index(user_id, entries)

    return entry

@router.post("/save-report")
def save_report(
    report: dict[str, Any],
    current_user: dict[str, Any] = Depends(require_unlocked)
):
    """Saves a risk analysis report to the user's vault."""
    user_id = current_user["user_id"]
    file_id = str(uuid.uuid4())
    ts = int(time.time())
    filename = f"report-{ts}.json"

    raw = json.dumps(report, indent=2).encode("utf-8")
    vault_dir = get_user_vault_dir(user_id)
    stored_path = vault_dir / f"{file_id}.json"
    stored_path.write_bytes(raw)

    entry = {
        "file_id": file_id,
        "name": filename,
        "content_type": "application/json",
        "size_bytes": len(raw),
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "kind": "report"
    }

    entries = get_vault_index(user_id)
    entries.insert(0, entry)
    save_vault_index(user_id, entries)

    return entry

@router.get("/files")
def list_files(current_user: dict[str, Any] = Depends(require_unlocked)):
    """Lists vault metadata entries, sorted newest first."""
    entries = get_vault_index(current_user["user_id"])
    entries.sort(key=lambda x: x.get("uploaded_at", ""), reverse=True)
    return entries

@router.get("/files/{file_id}")
def get_file(file_id: str, current_user: dict[str, Any] = Depends(require_unlocked)):
    """Downloads or views a file securely from the vault."""
    if "/" in file_id or "\\" in file_id or ".." in file_id:
        raise HTTPException(status_code=400, detail="Invalid file ID")

    user_id = current_user["user_id"]
    vault_dir = get_user_vault_dir(user_id).resolve()
    entries = get_vault_index(user_id)

    entry = next((e for e in entries if e["file_id"] == file_id), None)
    if not entry:
        raise HTTPException(status_code=404, detail="File not found")

    matching = list(vault_dir.glob(f"{file_id}.*"))
    if not matching:
        raise HTTPException(status_code=404, detail="File content not found")

    target = matching[0].resolve()
    try:
        target.relative_to(vault_dir)
    except ValueError:
        raise HTTPException(status_code=400, detail="Path traversal detected")

    if not target.exists():
        raise HTTPException(status_code=404, detail="File not found")

    content_type = entry.get("content_type", "application/octet-stream")
    disp = "inline" if content_type == "application/pdf" else "attachment"
    filename = entry.get("name", "document")
    headers = {"Content-Disposition": f'{disp}; filename="{filename}"'}

    return FileResponse(target, media_type=content_type, headers=headers)

@router.delete("/files/{file_id}")
def delete_file(file_id: str, current_user: dict[str, Any] = Depends(require_unlocked)):
    """Removes a file and its index entry from the vault."""
    if "/" in file_id or "\\" in file_id or ".." in file_id:
        raise HTTPException(status_code=400, detail="Invalid file ID")

    user_id = current_user["user_id"]
    vault_dir = get_user_vault_dir(user_id).resolve()
    entries = get_vault_index(user_id)

    idx = next((i for i, e in enumerate(entries) if e["file_id"] == file_id), None)
    if idx is None:
        raise HTTPException(status_code=404, detail="File not found")

    matching = list(vault_dir.glob(f"{file_id}.*"))
    for f in matching:
        try:
            f.resolve().relative_to(vault_dir)
            f.unlink(missing_ok=True)
        except Exception:
            pass

    entries.pop(idx)
    save_vault_index(user_id, entries)

    return {"deleted": True, "file_id": file_id}
