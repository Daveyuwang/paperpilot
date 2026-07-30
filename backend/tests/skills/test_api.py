"""HTTP contract tests for the read-only skill metadata API."""

from __future__ import annotations

from pathlib import Path

from app.api import skills as skills_api
from app.skills.registry import load_skill_catalog
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_skill_api_exposes_status_and_metadata_without_bodies(
    tmp_path: Path,
    write_skill,
    monkeypatch,
) -> None:
    secret = "BODY-MUST-STAY-PRIVATE"
    write_skill(
        tmp_path,
        "methods/literature-review",
        name="literature-review",
        description="Review research evidence",
        body=f"{secret}\n",
    )
    catalog = load_skill_catalog(tmp_path)

    class FakeService:
        def status(self):
            return {"enabled": True, "state": "ready", "revision": "a" * 40}

        def diagnostics(self):
            return ()

        def descriptors(self):
            return catalog.skills

        def descriptor(self, name: str):
            return catalog.get(name, include_blocked=True)

    monkeypatch.setattr(skills_api, "get_skill_service", lambda: FakeService())
    app = FastAPI()
    app.include_router(skills_api.router, prefix="/api/skills")
    client = TestClient(app)

    status = client.get("/api/skills/status")
    listing = client.get("/api/skills")
    detail = client.get("/api/skills/literature-review")

    assert status.status_code == 200
    assert status.json()["state"] == "ready"
    assert listing.status_code == 200
    assert listing.json()["skills"][0]["name"] == "literature-review"
    assert detail.status_code == 200
    assert detail.json()["description"] == "Review research evidence"
    assert secret not in listing.text
    assert secret not in detail.text
    assert client.get("/api/skills/missing").status_code == 404


def test_skill_api_has_no_mutating_refresh_route() -> None:
    app = FastAPI()
    app.include_router(skills_api.router, prefix="/api/skills")

    assert not any(
        route.path == "/api/skills/refresh" and "POST" in (route.methods or set())
        for route in app.routes
    )
