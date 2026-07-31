"""Read-only metadata API for the active advisory skill catalog."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.skills.models import SkillDescriptor
from app.skills.service import get_skill_service

router = APIRouter()


class SkillPreviewRequest(BaseModel):
    query: str = Field(min_length=1, max_length=10_000)
    flow: str = Field(default="deep_research", min_length=1, max_length=64)
    max_results: int | None = Field(default=None, ge=1, le=8)

    @field_validator("query", "flow")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value cannot be blank")
        return normalized


def _serialize_skill(skill: SkillDescriptor, *, loaded: bool = False) -> dict:
    metadata = skill.metadata
    return {
        "name": metadata.name,
        "description": metadata.description,
        "version": metadata.version,
        "author": metadata.author,
        "license": metadata.license,
        "tags": list(metadata.tags),
        "category": metadata.category,
        "dependencies": list(metadata.dependencies),
        "source_path": skill.relative_path,
        "content_sha256": skill.content_sha256,
        "byte_size": skill.byte_size,
        "body_chars": skill.body_chars,
        "reference_count": len(skill.references),
        "reference_paths": list(skill.reference_paths),
        "availability": skill.availability.value,
        "blocked_reason": skill.blocked_reason or None,
        "loaded": loaded,
    }


@router.get("/status")
async def get_skill_status():
    service = get_skill_service()
    status, diagnostics = service.status_view()
    return {
        **status,
        "diagnostics": list(diagnostics),
    }


@router.get("")
async def list_skills():
    service = get_skill_service()
    status, skills = service.catalog_view()
    return {
        **status,
        "skills": [_serialize_skill(skill, loaded=loaded) for skill, loaded in skills],
    }


@router.post("/preview")
async def preview_skill_selection(request: SkillPreviewRequest):
    """Preview metadata routing without loading any skill or reference text."""

    service = get_skill_service()
    preview, status, loaded_flags = service.preview_view(
        request.query,
        flow=request.flow,
        limit=request.max_results,
    )
    selected = []
    for index, (item, loaded) in enumerate(
        zip(preview.selections, loaded_flags, strict=True),
        start=1,
    ):
        payload = _serialize_skill(
            item.skill,
            loaded=loaded,
        )
        payload.update(
            {
                "rank": index,
                "score": item.score,
                "matched_terms": list(item.matched_terms),
                "match_reason": (
                    f"Matched metadata terms: {', '.join(item.matched_terms)}"
                    if item.matched_terms
                    else "Matched skill metadata"
                ),
            }
        )
        selected.append(payload)
    cache = {
        "loaded_count": status["loaded_count"],
        "loaded_bytes": status["loaded_bytes"],
        "loaded_reference_count": status["loaded_reference_count"],
        "entry_count": status["cache_entry_count"],
        "total_bytes": status["cache_total_bytes"],
        "max_entries": status["cache_max_entries"],
        "max_bytes": status["cache_max_bytes"],
        "hits": status["cache_hits"],
        "misses": status["cache_misses"],
        "evictions": status["cache_evictions"],
    }
    return {
        "query": request.query,
        "flow": request.flow,
        "source_revision": preview.source_revision,
        "catalog_revision": preview.catalog_revision,
        "selected_count": len(selected),
        "available_count": status["available_count"],
        "loaded_count": status["loaded_count"],
        "selected": selected,
        "cache": cache,
    }


@router.get("/{name}")
async def get_skill(name: str):
    service = get_skill_service()
    skill, source_revision, catalog_revision, loaded = service.descriptor_view(name)
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    return {
        **_serialize_skill(skill, loaded=loaded),
        "source_url": service.status()["source_url"],
        "source_revision": source_revision,
        "catalog_revision": catalog_revision,
    }
