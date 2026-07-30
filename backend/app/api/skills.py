"""Read-only metadata API for the active advisory skill catalog."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.skills.models import SkillDescriptor
from app.skills.service import get_skill_service

router = APIRouter()


def _serialize_skill(skill: SkillDescriptor) -> dict:
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
        "availability": skill.availability.value,
        "blocked_reason": skill.blocked_reason or None,
    }


@router.get("/status")
async def get_skill_status():
    service = get_skill_service()
    return {
        **service.status(),
        "diagnostics": list(service.diagnostics()),
    }


@router.get("")
async def list_skills():
    service = get_skill_service()
    return {
        **service.status(),
        "skills": [_serialize_skill(skill) for skill in service.descriptors()],
    }


@router.get("/{name}")
async def get_skill(name: str):
    skill = get_skill_service().descriptor(name)
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    return _serialize_skill(skill)
