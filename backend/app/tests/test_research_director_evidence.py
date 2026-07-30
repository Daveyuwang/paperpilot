from __future__ import annotations

import unittest

from pydantic import ValidationError

from app.research_director.models import (
    EvidenceItem,
    EvidenceRelation,
    EvidenceStatus,
    GeneratePlanRequest,
    GenerationMode,
    ResearchPlanBundle,
    ReviewPlanRequest,
    RevisePlanRequest,
)
from app.research_director.service import (
    generate_plan,
    review_plan,
    revise_plan,
)


class _StaticLLM:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls = 0

    async def create_json(self, **_kwargs):
        self.calls += 1
        return self.payload


def _mark_first_claim_supported(
    plan: ResearchPlanBundle,
    *,
    evidence_item_ids: list[str] | None = None,
) -> dict:
    payload = plan.model_dump(mode="json")
    claim = payload["evidence_claims"][0]
    claim["status"] = EvidenceStatus.SUPPORTED.value
    claim["relation"] = EvidenceRelation.SUPPORTS.value
    if evidence_item_ids is not None:
        claim["evidence_item_ids"] = evidence_item_ids
    return payload


class SupportedClaimEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def _plan(self, evidence: list[EvidenceItem]) -> ResearchPlanBundle:
        return await generate_plan(
            None,
            GeneratePlanRequest(
                research_brief="Evaluate a research-agent planning method.",
                evidence=evidence,
            ),
        )

    async def test_supported_claim_rejects_metadata_or_unlocated_text(self):
        insufficient_items = [
            EvidenceItem(
                id="url-only",
                title="URL only",
                source_uri="https://example.test/paper",
            ),
            EvidenceItem(
                id="summary-only",
                title="Summary only",
                summary="A generated or catalog summary is not a frozen passage.",
            ),
            EvidenceItem(
                id="excerpt-without-locator",
                title="Unlocated excerpt",
                excerpt="The source reports a measurable improvement.",
            ),
            EvidenceItem(
                id="locator-without-excerpt",
                title="Locator without passage",
                source_uri="https://example.test/paper#results",
                locator="Results section",
            ),
            EvidenceItem(
                id="placeholder-locator",
                title="Placeholder locator",
                excerpt="The source reports a measurable improvement.",
                locator="unknown",
            ),
            EvidenceItem(
                id="whitespace-fields",
                title="Whitespace fields",
                source_uri="https://example.test/paper",
                excerpt="   ",
                locator="   ",
            ),
        ]

        for item in insufficient_items:
            with self.subTest(evidence_item=item.id):
                plan = await self._plan([item])
                payload = _mark_first_claim_supported(plan)
                with self.assertRaisesRegex(
                    ValidationError,
                    "non-empty excerpt and concrete locator",
                ):
                    ResearchPlanBundle.model_validate(payload)

    async def test_supported_claim_accepts_one_decisive_located_passage(self):
        metadata = EvidenceItem(
            id="metadata",
            title="Catalog metadata",
            source_uri="https://example.test/paper",
            summary="Useful discovery metadata, but not decisive support.",
        )
        located_passage = EvidenceItem(
            id="located-passage",
            title="Frozen passage",
            source_uri="https://example.test/paper#page=7",
            excerpt="The method improved the primary metric under the stated protocol.",
            locator="p. 7, Results, paragraph 2",
        )
        plan = await self._plan([metadata, located_passage])
        payload = _mark_first_claim_supported(
            plan,
            evidence_item_ids=[metadata.id, located_passage.id],
        )

        validated = ResearchPlanBundle.model_validate(payload)

        self.assertEqual(validated.evidence_claims[0].status, EvidenceStatus.SUPPORTED)
        self.assertEqual(
            validated.evidence_claims[0].evidence_item_ids,
            [metadata.id, located_passage.id],
        )

    async def test_contested_and_unknown_claims_keep_existing_semantics(self):
        metadata = EvidenceItem(
            id="metadata",
            title="Catalog summary",
            summary="The retained summaries disagree about the reported outcome.",
        )
        plan = await self._plan([metadata])

        unknown = ResearchPlanBundle.model_validate(plan.model_dump(mode="json"))
        self.assertEqual(unknown.evidence_claims[0].status, EvidenceStatus.UNKNOWN)

        contested_payload = plan.model_dump(mode="json")
        contested_payload["evidence_claims"][0].update(
            {
                "status": EvidenceStatus.CONTESTED.value,
                "relation": EvidenceRelation.CONFLICTS.value,
            }
        )
        contested = ResearchPlanBundle.model_validate(contested_payload)
        self.assertEqual(
            contested.evidence_claims[0].status,
            EvidenceStatus.CONTESTED,
        )

    async def test_invalid_model_support_claim_falls_back_closed(self):
        evidence = EvidenceItem(
            id="metadata-only",
            title="Metadata-only source",
            source_uri="https://example.test/paper",
            summary="No frozen located passage was supplied.",
        )
        request = GeneratePlanRequest(
            research_brief="Evaluate a research-agent planning method.",
            evidence=[evidence],
        )
        fallback = await generate_plan(None, request)
        invalid_model_payload = _mark_first_claim_supported(fallback)
        llm = _StaticLLM(invalid_model_payload)

        generated = await generate_plan(llm, request)

        self.assertEqual(llm.calls, 2)
        self.assertEqual(
            generated.generation_mode,
            GenerationMode.DETERMINISTIC_FALLBACK,
        )
        self.assertEqual(generated.evidence_claims[0].status, EvidenceStatus.UNKNOWN)
        self.assertEqual(
            generated.experiments[0].execution_status,
            "awaiting_external_execution",
        )

    async def test_invalid_revision_support_claim_falls_back_closed(self):
        evidence = EvidenceItem(
            id="metadata-only",
            title="Metadata-only source",
            source_uri="https://example.test/paper",
            summary="No frozen located passage was supplied.",
        )
        plan = await self._plan([evidence])
        review = await review_plan(None, ReviewPlanRequest(plan=plan))
        request = RevisePlanRequest(plan=plan, review=review)
        fallback_revision = await revise_plan(None, request)
        invalid_model_payload = _mark_first_claim_supported(fallback_revision)
        llm = _StaticLLM(invalid_model_payload)

        revised = await revise_plan(llm, request)

        self.assertEqual(llm.calls, 2)
        self.assertEqual(
            revised.generation_mode,
            GenerationMode.DETERMINISTIC_FALLBACK,
        )
        self.assertEqual(revised.evidence_claims[0].status, EvidenceStatus.UNKNOWN)
        self.assertEqual(revised.supersedes_plan_id, plan.plan_id)
        self.assertIsNotNone(revised.revision_record)


if __name__ == "__main__":
    unittest.main()
