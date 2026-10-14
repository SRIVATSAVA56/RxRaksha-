"""RxRaksha Authentication Module.

Supports:
- Google OAuth ID token verification (with graceful 503 if not configured)
- Demo clinician login fallback
- JWT (HS256) session tokens with 12h expiry
- User profile persistence in backend/app/data/users.json
- get_current_user dependency for route protection
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

# ----------------- Configuration & .env -----------------

def _load_env() -> None:
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.exists():
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip()
                    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                        v = v[1:-1]
                    os.environ.setdefault(k, v)

_load_env()

def get_google_client_id() -> str | None:
    val = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    return val if val else None

def get_auth_secret() -> str:
    return os.environ.get("AUTH_SECRET", "dev-secret-change-me")


# ----------------- User Persistence -----------------

DATA_DIR = Path(__file__).resolve().parent / "data"
USERS_FILE = DATA_DIR / "users.json"

def _ensure_users_file() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not USERS_FILE.exists():
        demo_user = {
            "demo:demo-user": {
                "user_id": "demo:demo-user",
                "email": "demo@rxraksha.demo",
                "name": "Demo Clinician",
                "picture": None,
                "provider": "demo",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "pin_hash": None,
                "pin_salt": None,
            }
        }
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(demo_user, f, indent=2)

def load_users() -> dict[str, dict[str, Any]]:
    _ensure_users_file()
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_users(users: dict[str, dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = USERS_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=2)
    tmp.replace(USERS_FILE)


# ----------------- JWT Utilities -----------------

def create_token(user_id: str, expires_delta: timedelta | None = None) -> str:
    now = datetime.now(timezone.utc)
    exp = now + (expires_delta or timedelta(hours=12))
    payload = {
        "sub": user_id,
        "exp": int(exp.timestamp()),
        "iat": int(now.timestamp()),
    }
    return jwt.encode(payload, get_auth_secret(), algorithm="HS256")

def decode_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, get_auth_secret(), algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid authentication token")


# ----------------- Dependency -----------------

def get_current_user(request: Request) -> dict[str, Any]:
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = auth_header[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing authentication token")

    payload = decode_token(token)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token: missing subject")

    users = load_users()
    user = users.get(user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


# ----------------- Router & Endpoints -----------------

router = APIRouter(tags=["auth"])

class GoogleAuthRequest(BaseModel):
    credential: str

@router.get("/config")
def get_config():
    """Returns dynamic frontend configuration for login providers."""
    return {
        "google_client_id": get_google_client_id(),
        "demo_enabled": True
    }

@router.post("/auth/google")
def auth_google(req: GoogleAuthRequest):
    """Verifies a Google OAuth ID token and returns session JWT."""
    client_id = get_google_client_id()
    if not client_id:
        raise HTTPException(status_code=503, detail="Google login not configured")

    try:
        from google.oauth2 import id_token
        from google.auth.transport import requests as google_requests
        idinfo = id_token.verify_oauth2_token(req.credential, google_requests.Request(), client_id)
    except Exception:
        raise HTTPException(status_code=401, detail="Google sign-in verification failed")

    sub = idinfo.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="Google sign-in verification failed")

    email = idinfo.get("email", "")
    name = idinfo.get("name", email.split("@")[0] if email else "Google User")
    picture = idinfo.get("picture", None)
    user_id = f"google:{sub}"

    users = load_users()
    now_iso = datetime.now(timezone.utc).isoformat()
    if user_id not in users:
        users[user_id] = {
            "user_id": user_id,
            "email": email,
            "name": name,
            "picture": picture,
            "provider": "google",
            "created_at": now_iso,
            "pin_hash": None,
            "pin_salt": None,
        }
    else:
        users[user_id]["email"] = email
        users[user_id]["name"] = name
        users[user_id]["picture"] = picture
    save_users(users)

    token = create_token(user_id)
    u = users[user_id]
    return {
        "token": token,
        "user": {
            "user_id": u["user_id"],
            "name": u["name"],
            "email": u["email"],
            "picture": u["picture"],
            "provider": u["provider"]
        }
    }

@router.post("/auth/demo")
def auth_demo():
    """One-click demo login returning a test clinician session."""
    user_id = "demo:demo-user"
    users = load_users()
    now_iso = datetime.now(timezone.utc).isoformat()
    if user_id not in users:
        users[user_id] = {
            "user_id": user_id,
            "email": "demo@rxraksha.demo",
            "name": "Demo Clinician",
            "picture": None,
            "provider": "demo",
            "created_at": now_iso,
            "pin_hash": None,
            "pin_salt": None,
        }
    else:
        users[user_id]["email"] = "demo@rxraksha.demo"
        users[user_id]["name"] = "Demo Clinician"
        users[user_id]["provider"] = "demo"
    save_users(users)

    token = create_token(user_id)
    u = users[user_id]
    return {
        "token": token,
        "user": {
            "user_id": u["user_id"],
            "name": u["name"],
            "email": u["email"],
            "picture": u["picture"],
            "provider": u["provider"]
        }
    }

@router.get("/me")
def get_me(current_user: dict[str, Any] = Depends(get_current_user)):
    """Returns the authenticated user's profile."""
    return {
        "user_id": current_user["user_id"],
        "name": current_user["name"],
        "email": current_user["email"],
        "picture": current_user.get("picture"),
        "provider": current_user.get("provider", "demo"),
        "created_at": current_user.get("created_at"),
    }
