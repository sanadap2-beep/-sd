from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from fastapi.testclient import TestClient

from api.app import app
from config import settings


def signed_init_data(user_id: int) -> str:
    payload = {
        "auth_date": str(int(time.time())),
        "query_id": "pytest-query",
        "user": json.dumps(
            {"id": user_id, "first_name": "Test"},
            separators=(",", ":"),
        ),
    }
    check = "\n".join(f"{key}={value}" for key, value in sorted(payload.items()))
    secret = hmac.new(b"WebAppData", settings.BOT_TOKEN.encode(), hashlib.sha256).digest()
    payload["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(payload)


def test_public_health_and_catalog():
    with TestClient(app) as client:
        assert client.get("/health/live").json()["status"] == "ok"
        response = client.get("/api/v1/catalog")
        assert response.status_code == 200
        assert "categories" in response.json()


def test_webapp_auth_and_admin_guard():
    with TestClient(app) as client:
        assert client.get("/api/v1/me").status_code == 401
        headers = {"Authorization": f"tma {signed_init_data(1)}"}
        response = client.get("/api/v1/admin/overview", headers=headers)
        assert response.status_code == 200
        assert response.json()["users"] >= 1
