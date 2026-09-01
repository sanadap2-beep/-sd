from fastapi.testclient import TestClient

from api.app import InMemoryRateLimitMiddleware, app
from config import settings


def test_api_rate_limit_blocks_excess_requests(monkeypatch):
    InMemoryRateLimitMiddleware._hits.clear()
    monkeypatch.setattr(settings, "API_RATE_LIMIT_PER_MINUTE", 1)
    monkeypatch.setattr(settings, "API_RATE_LIMIT_BURST", 2)
    client = TestClient(app)

    assert client.get("/api/v1/catalog").status_code == 200
    assert client.get("/api/v1/catalog").status_code == 200
    response = client.get("/api/v1/catalog")
    assert response.status_code == 429
    assert response.json()["detail"] == "rate limit exceeded"


def test_api_rate_limit_skips_health(monkeypatch):
    InMemoryRateLimitMiddleware._hits.clear()
    monkeypatch.setattr(settings, "API_RATE_LIMIT_PER_MINUTE", 1)
    monkeypatch.setattr(settings, "API_RATE_LIMIT_BURST", 1)
    client = TestClient(app)

    for _ in range(5):
        assert client.get("/health/live").status_code == 200
