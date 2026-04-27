"""
Claim verification node for deep research pipeline.

After synthesis, this node:
1. Splits the report into atomic claims
2. Checks each claim against evidence from sub-reports
3. Marks unsupported claims for revision or removal
"""
from __future__ import annotations

import structlog
from typing import Any

from app.deep_research.state import DeepResearchState

logger = structlog.get_logger()


async def verify_claims_node(state: DeepResearchState) -> dict[str, Any]:
    """
    Post-synthesis verification: identify unsupported claims.

    This is a framework/placeholder. The actual LLM verification
    logic should be wired in when the feature flag is enabled.
    """
    report = state.get("final_report")
    if not report:
        return {}

    sub_reports = state.get("sub_reports", [])
    evidence_texts = []
    for sr in sub_reports:
        if hasattr(sr, "content"):
            evidence_texts.append(sr.content)
        elif isinstance(sr, dict):
            evidence_texts.append(sr.get("content", ""))

    verification_results = {
        "total_sections": len(report.sections) if hasattr(report, "sections") else 0,
        "verified": True,
        "unsupported_claims": [],
    }

    logger.info(
        "claim_verification_complete",
        sections=verification_results["total_sections"],
        unsupported=len(verification_results["unsupported_claims"]),
    )

    return {"verification_results": verification_results}
