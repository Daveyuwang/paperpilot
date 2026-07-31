"""HTTP contract tests for the read-only skill metadata API."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import skills as skills_api
from app.config import Settings
from app.skills.registry import load_skill_catalog
from app.skills.service import SkillService
from app.skills.source import SourceSnapshot, SourceStatus


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
            return {
                "enabled": True,
                "state": "ready",
                "source_url": "https://example.test/skills.git",
                "revision": "a" * 40,
                "catalog_revision": catalog.revision,
            }

        def status_view(self):
            return self.status(), ()

        def catalog_view(self):
            return self.status(), tuple((skill, False) for skill in catalog.skills)

        def descriptor_view(self, name: str):
            descriptor = catalog.get(name, include_blocked=True)
            return descriptor, "a" * 40, catalog.revision, False

        def diagnostics(self):
            return ()

        def descriptors(self):
            return catalog.skills

        def descriptor(self, name: str):
            return catalog.get(name, include_blocked=True)

        def is_loaded(self, _name: str):
            return False

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
    assert detail.json()["byte_size"] > 0
    assert detail.json()["body_chars"] == len(secret) + 1
    assert detail.json()["reference_count"] == 0
    assert detail.json()["loaded"] is False
    assert detail.json()["source_revision"] == "a" * 40
    assert detail.json()["catalog_revision"] == catalog.revision
    assert detail.json()["source_url"] == "https://example.test/skills.git"
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


def test_preview_is_metadata_only_and_does_not_change_cache_counters(
    tmp_path: Path,
    write_skill,
    monkeypatch,
) -> None:
    write_skill(
        tmp_path,
        "review",
        name="preview-review",
        description="Systematic literature papers review",
        tags=("papers", "review"),
        body="BODY-MUST-NOT-BE-READ-BY-PREVIEW\n",
    )

    class OfflineSource:
        def load_current(self):
            raise AssertionError("preview attempted source I/O")

        def get_snapshot(self, **_kwargs):
            raise AssertionError("preview attempted a network refresh")

    service = SkillService(
        Settings(
            agent_skills_enabled=True,
            agent_skills_cache_dir=str(tmp_path / "cache"),
            agent_skills_min_score=0.0,
            agent_skills_max_selected=3,
        ),
        source=OfflineSource(),  # type: ignore[arg-type]
    )
    service._activate(
        SourceSnapshot(
            root=tmp_path,
            revision="8" * 40,
            status=SourceStatus.CACHED,
            refreshed_at=1.0,
            source_url="fixture://local",
            ref="fixture",
        )
    )
    before = service.status()

    from app.skills import documents

    monkeypatch.setattr(
        documents,
        "_read_relative_regular_file",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("preview attempted a document read")
        ),
    )
    monkeypatch.setattr(skills_api, "get_skill_service", lambda: service)
    app = FastAPI()
    app.include_router(skills_api.router, prefix="/api/skills")
    client = TestClient(app)

    response = client.post(
        "/api/skills/preview",
        json={
            "query": "review literature papers",
            "flow": "deep_research",
            "max_results": 3,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["selected_count"] == 1
    assert payload["selected"][0]["name"] == "preview-review"
    assert payload["selected"][0]["loaded"] is False
    assert payload["cache"]["loaded_count"] == 0
    assert "BODY-MUST-NOT-BE-READ-BY-PREVIEW" not in response.text
    after = service.status()
    for key in (
        "loaded_count",
        "loaded_bytes",
        "cache_hits",
        "cache_misses",
        "cache_evictions",
    ):
        assert after[key] == before[key]

    assert (
        client.post(
            "/api/skills/preview",
            json={"query": "   ", "flow": "deep_research"},
        ).status_code
        == 422
    )
