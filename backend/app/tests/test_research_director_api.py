import asyncio
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.api.research_director import (
    ConfirmResearchHandoffRequest,
    CreateResearchProjectRequest,
    ResearchBriefSnapshot,
    ResearchHandoffContent,
    ResearchHandoffOut,
    ResearchPlanReviewOut,
    ResearchPlanVersionOut,
    ResearchSourcePolicySnapshot,
    ReviewResearchPlanRequest,
    ReviseResearchPlanRequest,
    _build_prepared_handoff_content,
    _canonical_request_fingerprint,
    _commit_or_conflict,
    _completed_receipt_response,
    _confirm_handoff_content,
    _ensure_append_only_evidence,
    _ensure_approvable,
    _ensure_expected_latest_review,
    _ensure_review_evidence_matches_plan,
    _ensure_review_targets_plan_snapshot,
    _ensure_reviewable,
    _ensure_revisable,
    _ensure_revision_review_stable,
    _freeze_detail_and_commit,
    _load_persisted_handoff,
    _load_persisted_plan,
    _mark_plan_handed_off,
    _normalize_idempotency_key,
    _owned_version_query,
    _summary,
)
from app.models.orm import (
    ResearchArtifactStatus,
    ResearchIdempotencyReceipt,
    ResearchProject,
)
from app.research_director.models import (
    DEFAULT_REVIEWER_PERSPECTIVES,
    EvidenceClaim,
    EvidenceItem,
    EvidenceRelation,
    EvidenceStatus,
    GeneratePlanRequest,
    GenerationMode,
    HypothesisStatus,
    PlanLifecycleStatus,
    ResearchContractSeed,
    ResearchPlanBundle,
    ReviewerPerspective,
    ReviewIssue,
    ReviewIssueStatus,
    ReviewPlanRequest,
    ReviewReport,
    ReviewSeverity,
    ReviewVerdict,
    RevisePlanRequest,
    RevisionRecord,
    review_report_digest,
    scientific_plan_digest,
)
from app.research_director.prompts import (
    MAX_CANONICAL_INPUT_JSON_BYTES,
    CanonicalInputTooLargeError,
    build_generate_user_prompt,
)
from app.research_director.service import (
    _merge_evidence,
    generate_plan,
    review_plan,
    revise_plan,
)


class ResearchDirectorLifecycleTests(unittest.TestCase):
    def test_lifecycle_gates_reject_invalid_transitions(self):
        for artifact_status in (
            ResearchArtifactStatus.reviewed,
            ResearchArtifactStatus.approved,
            ResearchArtifactStatus.superseded,
            ResearchArtifactStatus.handed_off,
        ):
            with self.assertRaises(HTTPException) as context:
                _ensure_reviewable(artifact_status)
            self.assertEqual(context.exception.status_code, 409)

        _ensure_reviewable(ResearchArtifactStatus.draft)
        _ensure_revisable(ResearchArtifactStatus.reviewed)
        with self.assertRaises(HTTPException) as context:
            _ensure_revisable(ResearchArtifactStatus.draft)
        self.assertEqual(context.exception.status_code, 409)

        locked_query = _owned_version_query(
            project_id="project-1",
            version_number=1,
            guest_id="guest-1",
            for_update=True,
        )
        self.assertTrue(locked_query.get_execution_options()["populate_existing"])
        self.assertIsNotNone(locked_query._for_update_arg)

    def test_confirmation_requires_explicit_true(self):
        with self.assertRaises(ValueError):
            ConfirmResearchHandoffRequest(confirm_transfer=False)
        self.assertTrue(
            ConfirmResearchHandoffRequest(confirm_transfer=True).confirm_transfer
        )

    def test_idempotency_keys_use_canonical_request_fingerprints(self):
        left = _canonical_request_fingerprint(
            "research_plan.review",
            {"version": 1, "body": {"b": 2, "a": 1}},
        )
        reordered = _canonical_request_fingerprint(
            "research_plan.review",
            {"body": {"a": 1, "b": 2}, "version": 1},
        )
        changed = _canonical_request_fingerprint(
            "research_plan.review",
            {"version": 1, "body": {"a": 1, "b": 3}},
        )
        other_operation = _canonical_request_fingerprint(
            "research_plan.revise",
            {"version": 1, "body": {"a": 1, "b": 2}},
        )
        self.assertEqual(left, reordered)
        self.assertNotEqual(left, changed)
        self.assertNotEqual(left, other_operation)
        self.assertEqual(_normalize_idempotency_key("  stable-key  "), "stable-key")
        with self.assertRaises(HTTPException) as invalid:
            _normalize_idempotency_key("contains whitespace")
        self.assertEqual(invalid.exception.status_code, 422)

    def test_revision_review_selection_detects_latest_review_race(self):
        stable = {
            "selected_review_id": "review-row-1",
            "selected_review_round": 1,
            "selected_review_digest": "digest-1",
            "locked_review_id": "review-row-1",
            "locked_review_round": 1,
            "locked_review_digest": "digest-1",
        }
        _ensure_expected_latest_review(None, "review-row-1")
        _ensure_expected_latest_review("review-row-1", "review-row-1")
        with self.assertRaises(HTTPException) as historical:
            _ensure_expected_latest_review("review-row-0", "review-row-1")
        self.assertEqual(historical.exception.status_code, 409)
        self.assertIn("no longer latest", historical.exception.detail)

        _ensure_revision_review_stable(**stable)

        with self.assertRaises(HTTPException) as latest_race:
            _ensure_revision_review_stable(
                **{
                    **stable,
                    "locked_review_id": "review-row-2",
                    "locked_review_round": 2,
                    "locked_review_digest": "digest-2",
                },
            )
        self.assertEqual(latest_race.exception.status_code, 409)
        self.assertIn("newer independent review", latest_race.exception.detail)

        with self.assertRaises(HTTPException) as content_race:
            _ensure_revision_review_stable(
                **{**stable, "locked_review_digest": "changed"},
            )
        self.assertEqual(content_race.exception.status_code, 409)

    def test_typed_brief_snapshot_is_strict_and_legacy_safe(self):
        snapshot = ResearchBriefSnapshot(
            title="Reliable research agents",
            research_question="Which design improves evidence-grounded planning?",
            objective="Freeze an implementation-ready research contract.",
            problem_statement="Open research remains difficult to reproduce.",
            intended_contribution="A review-gated workflow.",
            scope="Planning, review, and external handoff.",
            success_criteria=["Every major claim has located evidence."],
            constraints=["Do not execute experiments in PaperPilot."],
            desired_deliverables=["Implementation plan"],
            source_policy=ResearchSourcePolicySnapshot(
                use_workspace_sources=True,
                discover_external_sources=False,
                prefer_primary_sources=True,
                time_horizon="broad",
                must_include=["Primary research"],
                must_exclude=["Unattributed summaries"],
            ),
            notes="Preserve the structured brief verbatim.",
        )
        request = CreateResearchProjectRequest(
            workspace_id="workspace-1",
            brief_snapshot=snapshot,
            plan_request=GeneratePlanRequest(research_brief="Typed brief test."),
        )
        self.assertEqual(request.brief_snapshot, snapshot)

        payload = snapshot.model_dump(mode="json")
        with self.assertRaises(ValueError):
            ResearchBriefSnapshot.model_validate({**payload, "unexpected": True})
        with self.assertRaises(ValueError):
            ResearchBriefSnapshot.model_validate({**payload, "title": "x" * 513})

        now = datetime.utcnow()
        legacy = ResearchProject(
            id="project-legacy",
            workspace_id="workspace-1",
            guest_id="guest-1",
            title="Legacy project",
            objective=None,
            status=ResearchArtifactStatus.draft,
            content={},
            created_at=now,
            updated_at=now,
        )
        self.assertIsNone(_summary(legacy, None).brief_snapshot)
        legacy.content = {"brief_snapshot": payload}
        self.assertEqual(_summary(legacy, None).brief_snapshot, snapshot)

    def test_optional_project_title_matches_persistence_contract(self):
        request = CreateResearchProjectRequest(
            workspace_id="workspace-1",
            title="  Canonical project title  ",
            plan_request=GeneratePlanRequest(research_brief="Title validation."),
        )
        self.assertEqual(request.title, "Canonical project title")
        for title in ("", "   ", "x" * 513):
            with self.assertRaises(ValueError):
                CreateResearchProjectRequest(
                    workspace_id="workspace-1",
                    title=title,
                    plan_request=GeneratePlanRequest(
                        research_brief="Title validation."
                    ),
                )

    def test_review_request_requires_complete_perspective_coverage(self):
        with self.assertRaises(ValueError):
            ReviewResearchPlanRequest(
                perspectives=[ReviewerPerspective.EVIDENCE]
            )

        request = ReviewResearchPlanRequest()
        self.assertEqual(
            set(request.perspectives), set(DEFAULT_REVIEWER_PERSPECTIVES)
        )

    def test_evidence_ids_are_unique_and_append_only(self):
        stored = EvidenceItem(
            id="evidence-1",
            title="Stored source",
            source_uri="https://example.test/source",
            summary="Stable provenance metadata.",
        )
        identical = stored.model_copy(deep=True)
        changed = stored.model_copy(update={"title": "Mutated source"})
        appended = EvidenceItem(
            id="evidence-2",
            title="New source",
            summary="A genuinely new source.",
        )

        with self.assertRaises(ValueError):
            ReviewResearchPlanRequest(evidence=[stored, identical])
        with self.assertRaises(ValueError):
            ReviseResearchPlanRequest(evidence=[stored, identical])

        _ensure_append_only_evidence([stored], [identical, appended])
        with self.assertRaises(HTTPException) as context:
            _ensure_append_only_evidence([stored], [changed])
        self.assertEqual(context.exception.status_code, 409)
        with self.assertRaises(HTTPException) as context:
            _ensure_review_evidence_matches_plan([stored], [appended])
        self.assertEqual(context.exception.status_code, 409)

        merged = _merge_evidence([stored], [identical, appended])
        self.assertEqual([item.id for item in merged], ["evidence-1", "evidence-2"])
        self.assertEqual(merged[0], stored)
        with self.assertRaises(ValueError):
            _merge_evidence([stored], [changed])

    def test_claim_status_requires_semantically_matching_evidence(self):
        with self.assertRaises(ValueError):
            EvidenceClaim(
                id="claim-supported",
                statement="A supported claim.",
                evidence_item_ids=["evidence-1"],
                relation=EvidenceRelation.UNKNOWN,
                status=EvidenceStatus.SUPPORTED,
            )
        with self.assertRaises(ValueError):
            EvidenceClaim(
                id="claim-contested",
                statement="A contested claim.",
                evidence_item_ids=["evidence-1"],
                relation=EvidenceRelation.SUPPORTS,
                status=EvidenceStatus.CONTESTED,
            )

        supported = EvidenceClaim(
            id="claim-supported",
            statement="A supported claim.",
            evidence_item_ids=["evidence-1"],
            relation=EvidenceRelation.SUPPORTS,
            status=EvidenceStatus.SUPPORTED,
        )
        contested = EvidenceClaim(
            id="claim-contested",
            statement="A contested claim.",
            evidence_item_ids=["evidence-1"],
            relation=EvidenceRelation.CONFLICTS,
            status=EvidenceStatus.CONTESTED,
        )
        self.assertEqual(supported.status, EvidenceStatus.SUPPORTED)
        self.assertEqual(contested.status, EvidenceStatus.CONTESTED)

    def test_approval_requires_complete_clean_independent_review(self):
        plan = asyncio.run(
            generate_plan(
                None,
                GeneratePlanRequest(research_brief="Approval gate test plan."),
            )
        )
        incomplete = ReviewReport(
            review_id="review-incomplete",
            reviewed_plan_id="plan-1",
            reviewed_plan_version=1,
            verdict=ReviewVerdict.APPROVABLE_FOR_HANDOFF,
            perspectives_completed=[ReviewerPerspective.EVIDENCE],
            summary="No blocking issues were reported.",
            required_next_step="Record human approval.",
        )
        with self.assertRaises(HTTPException) as context:
            _ensure_approvable(incomplete, plan)
        self.assertEqual(context.exception.status_code, 409)

        approvable = ReviewReport(
            review_id="review-clean",
            reviewed_plan_id="plan-1",
            reviewed_plan_version=1,
            verdict=ReviewVerdict.APPROVABLE_FOR_HANDOFF,
            perspectives_completed=list(DEFAULT_REVIEWER_PERSPECTIVES),
            summary="No blocking issues remain.",
            required_next_step="Record human approval.",
        )
        _ensure_approvable(approvable, plan)

        blocked = ReviewReport(
            review_id="review-blocked",
            reviewed_plan_id="plan-1",
            reviewed_plan_version=1,
            verdict=ReviewVerdict.BLOCKED,
            perspectives_completed=list(DEFAULT_REVIEWER_PERSPECTIVES),
            issues=[
                ReviewIssue(
                    id="issue-1",
                    perspective=ReviewerPerspective.EVIDENCE,
                    severity=ReviewSeverity.BLOCKER,
                    artifact_path="evidence_claims",
                    problem="A decisive claim is unsupported.",
                    evidence="No source is linked to the claim.",
                    impact="The proposed method lacks a defensible premise.",
                    required_fix="Add evidence or remove the claim.",
                    status=ReviewIssueStatus.OPEN,
                )
            ],
            summary="Evidence review found a blocker.",
            required_next_step="Revise and review again.",
        )
        with self.assertRaises(HTTPException) as context:
            _ensure_approvable(blocked, plan)
        self.assertEqual(context.exception.status_code, 409)

        unresolved_plan = plan.model_copy(
            update={
                "revision_record": RevisionRecord(
                    review_id="prior-review",
                    addressed_issue_ids=[],
                    unresolved_issue_ids=["prior-major"],
                    changes=["Carried a prior unresolved issue."],
                )
            }
        )
        with self.assertRaises(HTTPException) as unresolved:
            _ensure_approvable(approvable, unresolved_plan)
        self.assertEqual(unresolved.exception.status_code, 409)
        self.assertIn("revision record", unresolved.exception.detail)


class ResearchDirectorBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_mutation_response_is_frozen_before_commit(self):
        events: list[str] = []
        db = AsyncMock()

        async def flush():
            events.append("flush")

        async def commit():
            events.append("commit")

        async def project_detail(_db, project_id, guest_id):
            self.assertEqual(project_id, "project-1")
            self.assertEqual(guest_id, "guest-1")
            events.append("detail")
            return "frozen-detail"

        db.flush.side_effect = flush
        db.commit.side_effect = commit
        with patch(
            "app.api.research_director._project_detail",
            new=project_detail,
        ):
            result = await _freeze_detail_and_commit(
                db,
                "project-1",
                "guest-1",
            )

        self.assertEqual(result, "frozen-detail")
        self.assertEqual(events, ["flush", "detail", "commit"])

    async def test_unique_constraint_race_becomes_conflict(self):
        class UniqueViolation(Exception):
            sqlstate = "23505"

        db = AsyncMock()
        db.commit.side_effect = IntegrityError(
            "INSERT",
            {},
            UniqueViolation("duplicate key violates unique constraint"),
        )

        with self.assertRaises(HTTPException) as context:
            await _commit_or_conflict(db, "Concurrent transition conflict.")
        self.assertEqual(context.exception.status_code, 409)
        self.assertEqual(context.exception.detail, "Concurrent transition conflict.")
        db.rollback.assert_awaited_once()

    async def test_partial_contract_seed_and_warnings_are_preserved(self):
        warning = "Client corpus was truncated to the bounded evidence window."
        seed = ResearchContractSeed(
            title="Frozen contract title",
            research_question="Which retrieval design is most defensible?",
            scope_inclusions=["Retrieval architecture and evaluation"],
            constraints=["Use only the supplied corpus"],
            success_criteria=["Beat the frozen baseline with uncertainty reported"],
        )
        request = GeneratePlanRequest(
            research_brief="Evaluate research-agent retrieval designs.",
            contract=seed,
            evidence_warnings=[warning],
        )
        fallback = await generate_plan(None, request)

        self.assertEqual(fallback.contract.title, seed.title)
        self.assertEqual(fallback.contract.constraints, seed.constraints)
        self.assertTrue(fallback.contract.assumptions)
        self.assertTrue(fallback.contract.unknowns)
        self.assertTrue(fallback.contract.failure_criteria)
        self.assertIn(warning, fallback.generation_warnings)

        class StaticLLM:
            def __init__(self, payload):
                self.payload = payload

            async def create_json(self, **_kwargs):
                return self.payload

        raw = fallback.model_dump(mode="json")
        raw["contract"]["title"] = "Model attempted rewrite"
        raw["generation_warnings"] = ["Model-authored warning"]
        generated = await generate_plan(StaticLLM(raw), request)
        self.assertEqual(generated.contract.title, seed.title)
        self.assertEqual(generated.contract.constraints, seed.constraints)
        self.assertIn(warning, generated.generation_warnings)
        self.assertIn("Model-authored warning", generated.generation_warnings)

    async def test_schema_valid_execution_claim_fails_closed(self):
        request = GeneratePlanRequest(
            research_brief="Plan a bounded evaluation without executing it."
        )
        safe_fallback = await generate_plan(None, request)

        class StaticLLM:
            def __init__(self, payload):
                self.payload = payload

            async def create_json(self, **_kwargs):
                return self.payload

        claims = (
            "PaperPilot implemented and tested the method, ran the experiments, and published the paper.",
            "The proposed method achieved 95% accuracy.",
            "Evaluation yielded a significant gain.",
            "The model outperformed every baseline.",
            "Tests passed with 98% coverage.",
        )
        for claim in claims:
            with self.subTest(claim=claim):
                malicious_payload = safe_fallback.model_dump(mode="json")
                malicious_payload["methods"][0]["summary"] = claim
                guarded = await generate_plan(StaticLLM(malicious_payload), request)
                self.assertEqual(
                    guarded.generation_mode,
                    GenerationMode.DETERMINISTIC_FALLBACK,
                )
                self.assertNotEqual(guarded.methods[0].summary, claim)

        future_payload = safe_fallback.model_dump(mode="json")
        future_payload["methods"][0]["summary"] = (
            "Prior studies showed the need for this control. Our system will be "
            "tested externally; this plan describes work to be executed later."
        )
        future_plan = await generate_plan(StaticLLM(future_payload), request)
        self.assertEqual(future_plan.generation_mode, GenerationMode.MODEL)

    async def test_unresolved_review_issue_lineage_cannot_disappear(self):
        class StaticLLM:
            def __init__(self, payload):
                self.payload = payload

            async def create_json(self, **_kwargs):
                return self.payload

        initial = await generate_plan(
            None,
            GeneratePlanRequest(research_brief="Preserve unresolved issue lineage."),
        )
        first_review = await review_plan(None, ReviewPlanRequest(plan=initial))
        first_revision = await revise_plan(
            None,
            RevisePlanRequest(plan=initial, review=first_review),
        )
        carried_issue_ids = set(
            first_revision.revision_record.unresolved_issue_ids
        )
        self.assertTrue(carried_issue_ids)

        clean_report_payload = ReviewReport(
            review_id="attempted-clean-review",
            reviewed_plan_id=first_revision.plan_id,
            reviewed_plan_version=first_revision.version,
            reviewed_plan_digest=scientific_plan_digest(first_revision),
            verdict=ReviewVerdict.APPROVABLE_FOR_HANDOFF,
            perspectives_completed=list(DEFAULT_REVIEWER_PERSPECTIVES),
            issues=[],
            summary="The model attempted to omit prior unresolved issues.",
            required_next_step="Approve.",
        ).model_dump(mode="json")
        guarded_review = await review_plan(
            StaticLLM(clean_report_payload),
            ReviewPlanRequest(plan=first_revision),
        )
        guarded_issues = {item.id: item for item in guarded_review.issues}
        self.assertTrue(carried_issue_ids.issubset(guarded_issues))
        self.assertTrue(
            all(
                guarded_issues[issue_id].status == ReviewIssueStatus.OPEN
                for issue_id in carried_issue_ids
            )
        )
        self.assertEqual(guarded_review.verdict, ReviewVerdict.BLOCKED)

        next_issue = ReviewIssue(
            id="new-method-issue",
            perspective=ReviewerPerspective.METHOD,
            severity=ReviewSeverity.MAJOR,
            artifact_path="methods[0]",
            problem="The next review found a new method issue.",
            evidence="The method does not yet state the required control.",
            impact="The external experiment would be ambiguous.",
            required_fix="Add and freeze the missing control.",
            status=ReviewIssueStatus.OPEN,
        )
        next_review = ReviewReport(
            review_id="next-review",
            reviewed_plan_id=first_revision.plan_id,
            reviewed_plan_version=first_revision.version,
            reviewed_plan_digest=scientific_plan_digest(first_revision),
            verdict=ReviewVerdict.REVISION_REQUIRED,
            perspectives_completed=list(DEFAULT_REVIEWER_PERSPECTIVES),
            issues=[next_issue],
            summary="A new method issue was found.",
            required_next_step="Revise the plan.",
        )
        revision_request = RevisePlanRequest(
            plan=first_revision,
            review=next_review,
            evidence_warnings=["New revision evidence was truncated."],
        )

        invalid_payload = first_revision.model_dump(mode="json")
        invalid_payload["revision_record"] = {
            "review_id": next_review.review_id,
            "addressed_issue_ids": [next_issue.id],
            "unresolved_issue_ids": [],
            "changes": ["Addressed only the newly reported issue."],
        }
        guarded_revision = await revise_plan(
            StaticLLM(invalid_payload),
            revision_request,
        )
        self.assertEqual(
            guarded_revision.generation_mode,
            GenerationMode.DETERMINISTIC_FALLBACK,
        )
        self.assertEqual(
            set(guarded_revision.revision_record.unresolved_issue_ids),
            carried_issue_ids | {next_issue.id},
        )

        valid_payload = first_revision.model_dump(mode="json")
        valid_payload["revision_record"] = {
            "review_id": next_review.review_id,
            "addressed_issue_ids": sorted(carried_issue_ids),
            "unresolved_issue_ids": [next_issue.id],
            "changes": ["Classified both carried and newly reported issues."],
        }
        classified_revision = await revise_plan(
            StaticLLM(valid_payload),
            revision_request,
        )
        self.assertEqual(classified_revision.generation_mode, GenerationMode.MODEL)
        self.assertEqual(
            set(classified_revision.revision_record.addressed_issue_ids),
            carried_issue_ids,
        )
        self.assertEqual(
            classified_revision.revision_record.unresolved_issue_ids,
            [next_issue.id],
        )
        self.assertIn(
            "New revision evidence was truncated.",
            classified_revision.generation_warnings,
        )

    async def test_oversize_inputs_fail_closed_before_llm(self):
        oversized_text = "x" * (MAX_CANONICAL_INPUT_JSON_BYTES + 1_024)
        oversized_request = GeneratePlanRequest(
            research_brief="Bound an oversized planner input.",
            notes=oversized_text,
        )
        with self.assertRaises(CanonicalInputTooLargeError):
            build_generate_user_prompt(oversized_request)

        planner_llm = AsyncMock()
        fallback_plan = await generate_plan(planner_llm, oversized_request)
        planner_llm.create_json.assert_not_awaited()
        self.assertEqual(
            fallback_plan.generation_mode,
            GenerationMode.DETERMINISTIC_FALLBACK,
        )

        plan_payload = fallback_plan.model_dump(mode="json")
        plan_payload["limitations"] = [oversized_text]
        oversized_plan = ResearchPlanBundle.model_validate(plan_payload)
        reviewer_llm = AsyncMock()
        blocked_review = await review_plan(
            reviewer_llm,
            ReviewPlanRequest(plan=oversized_plan),
        )
        reviewer_llm.create_json.assert_not_awaited()
        self.assertEqual(blocked_review.verdict, ReviewVerdict.BLOCKED)
        self.assertTrue(blocked_review.issues)

        reviser_llm = AsyncMock()
        revised = await revise_plan(
            reviser_llm,
            RevisePlanRequest(plan=oversized_plan, review=blocked_review),
        )
        reviser_llm.create_json.assert_not_awaited()
        self.assertIsNotNone(revised.revision_record)
        self.assertEqual(revised.revision_record.addressed_issue_ids, [])
        self.assertEqual(
            revised.revision_record.unresolved_issue_ids,
            [blocked_review.issues[0].id],
        )
        self.assertEqual(
            revised.revision_record.source_plan_digest,
            scientific_plan_digest(oversized_plan),
        )
        self.assertEqual(
            revised.revision_record.source_review_digest,
            review_report_digest(blocked_review),
        )

    async def test_handoff_preserves_external_execution_boundary(self):
        evidence = EvidenceItem(
            id="evidence-1",
            title="Evaluation source",
            source_uri="https://example.test/evaluation",
            excerpt="The retained passage reports the evaluation setup and outcome.",
            summary="A source retained in the frozen handoff snapshot.",
            locator="p. 8, Evaluation, paragraph 1",
        )
        plan = await generate_plan(
            None,
            GeneratePlanRequest(
                research_brief="Evaluate a research-agent retrieval method.",
                evidence=[evidence],
            ),
        )
        review = await review_plan(None, ReviewPlanRequest(plan=plan))
        plan_digest = scientific_plan_digest(plan)
        self.assertEqual(review.reviewed_plan_digest, plan_digest)
        stale_review = review.model_copy(
            update={"reviewed_plan_digest": "0" * 64}
        )
        with self.assertRaises(HTTPException) as stale_context:
            _ensure_review_targets_plan_snapshot(stale_review, plan)
        self.assertEqual(stale_context.exception.status_code, 409)

        new_review_evidence = EvidenceItem(
            id="evidence-new",
            title="New evidence must enter through revision",
            summary="Not yet part of the frozen plan snapshot.",
        )
        with self.assertRaises(ValueError):
            ReviewPlanRequest(plan=plan, evidence=[new_review_evidence])

        unsupported_payload = plan.model_dump(mode="json")
        unsupported_payload["hypotheses"][0][
            "status"
        ] = HypothesisStatus.EVIDENCE_BACKED.value
        with self.assertRaises(ValueError):
            ResearchPlanBundle.model_validate(unsupported_payload)

        supported_payload = plan.model_dump(mode="json")
        supported_payload["evidence_claims"][0].update(
            {
                "status": EvidenceStatus.SUPPORTED.value,
                "relation": EvidenceRelation.SUPPORTS.value,
            }
        )
        supported_payload["hypotheses"][0][
            "status"
        ] = HypothesisStatus.EVIDENCE_BACKED.value
        ResearchPlanBundle.model_validate(supported_payload)

        self.assertEqual(plan.lifecycle_status, PlanLifecycleStatus.DRAFT)
        self.assertEqual(
            plan.implementation_plan.handoff.handoff_status, "not_handed_off"
        )
        invalid_payload = plan.model_dump(mode="json")
        invalid_payload["implementation_plan"]["handoff"][
            "handoff_status"
        ] = "handed_off"
        with self.assertRaises(ValueError):
            ResearchPlanBundle.model_validate(invalid_payload)
        with self.assertRaises(ValueError):
            _build_prepared_handoff_content(
                bundle_id="bundle-1",
                project_id="project-1",
                plan_version_id="version-1",
                plan=plan,
                review=review,
            )

        approved_payload = plan.model_dump(mode="json")
        approved_payload[
            "lifecycle_status"
        ] = PlanLifecycleStatus.APPROVED_FOR_HANDOFF.value
        approved = ResearchPlanBundle.model_validate(approved_payload)
        self.assertEqual(scientific_plan_digest(approved), plan_digest)
        prepared = _build_prepared_handoff_content(
            bundle_id="bundle-1",
            project_id="project-1",
            plan_version_id="version-1",
            plan=approved,
            review=review,
        )
        self.assertIsInstance(prepared, ResearchHandoffContent)
        self.assertEqual(prepared.status, "ready_for_handoff")
        self.assertEqual(
            prepared.plan_snapshot.lifecycle_status,
            PlanLifecycleStatus.APPROVED_FOR_HANDOFF,
        )
        self.assertEqual(
            prepared.implementation_plan.handoff.handoff_status,
            "not_handed_off",
        )
        handed_off = _mark_plan_handed_off(approved)
        self.assertEqual(scientific_plan_digest(handed_off), plan_digest)
        self.assertEqual(
            handed_off.lifecycle_status, PlanLifecycleStatus.HANDED_OFF
        )
        self.assertEqual(
            handed_off.implementation_plan.handoff.handoff_status, "handed_off"
        )

        changed_payload = plan.model_dump(mode="json")
        changed_payload["contract"]["objective"] = "Scientifically changed objective"
        changed_plan = ResearchPlanBundle.model_validate(changed_payload)
        self.assertNotEqual(scientific_plan_digest(changed_plan), plan_digest)

        content = _confirm_handoff_content(prepared, handed_off)

        self.assertIsInstance(content, ResearchHandoffContent)
        self.assertEqual(content.status, "handed_off")
        self.assertEqual(
            content.execution_status, "awaiting_external_execution"
        )
        self.assertIn(
            "did not execute code, builds, tests, or experiments",
            content.boundary,
        )
        self.assertEqual(
            content.implementation_plan.execution_status,
            "awaiting_external_execution",
        )
        self.assertEqual(
            content.implementation_plan.handoff.handoff_status,
            "handed_off",
        )
        self.assertEqual(
            content.plan_snapshot.lifecycle_status, PlanLifecycleStatus.HANDED_OFF
        )
        self.assertEqual(
            content.plan_snapshot.evidence_catalog[0].id,
            "evidence-1",
        )
        self.assertTrue(content.plan_snapshot.evidence_claims)
        self.assertTrue(content.plan_snapshot.gaps)
        self.assertIsNone(content.plan_snapshot.revision_record)
        self.assertTrue(
            all(
                experiment.execution_status == "awaiting_external_execution"
                for experiment in content.experiment_plans
            )
        )
        self.assertTrue(
            all(
                package.execution_status == "planned_for_external_execution"
                for package in content.implementation_plan.work_packages
            )
        )

        now = datetime.utcnow()
        typed_version = ResearchPlanVersionOut(
            id="version-1",
            version_number=1,
            status=ResearchArtifactStatus.draft,
            content=plan.model_dump(mode="json"),
            created_at=now,
            updated_at=now,
        )
        self.assertIsInstance(typed_version.content, ResearchPlanBundle)
        malformed_plan = plan.model_dump(mode="json")
        malformed_plan["version"] = 2
        with self.assertRaises(ValueError):
            ResearchPlanVersionOut(
                id="version-1",
                version_number=1,
                status=ResearchArtifactStatus.draft,
                content=malformed_plan,
                created_at=now,
                updated_at=now,
            )
        noncanonical_plan = plan.model_dump(mode="json")
        noncanonical_plan.pop("schema_version")
        with self.assertRaises(HTTPException) as malformed_persistence:
            _load_persisted_plan(noncanonical_plan)
        self.assertEqual(malformed_persistence.exception.status_code, 500)

        typed_review = ResearchPlanReviewOut(
            id="review-row-1",
            plan_version_id="version-1",
            review_round=1,
            status=ResearchArtifactStatus.reviewed,
            review=review.model_dump(mode="json"),
            created_at=now,
        )
        self.assertIsInstance(typed_review.review, ReviewReport)
        noncanonical_review = review.model_dump(mode="json")
        noncanonical_review.pop("schema_version")
        with self.assertRaises(ValueError):
            ResearchPlanReviewOut(
                id="review-row-1",
                plan_version_id="version-1",
                review_round=1,
                status=ResearchArtifactStatus.reviewed,
                review=noncanonical_review,
                created_at=now,
            )

        typed_handoff = ResearchHandoffOut(
            id="bundle-1",
            plan_version_id="version-1",
            version_number=1,
            status=ResearchArtifactStatus.approved,
            content=prepared.model_dump(mode="json"),
            created_at=now,
        )
        self.assertIsInstance(typed_handoff.content, ResearchHandoffContent)
        corrupted_handoff = prepared.model_dump(mode="json")
        corrupted_handoff["research_contract"]["objective"] = "Corrupted copy"
        with self.assertRaises(HTTPException) as corrupted_persistence:
            _load_persisted_handoff(corrupted_handoff)
        self.assertEqual(corrupted_persistence.exception.status_code, 500)
        with self.assertRaises(ValueError):
            ResearchHandoffOut(
                id="bundle-1",
                plan_version_id="version-1",
                version_number=1,
                status=ResearchArtifactStatus.approved,
                content=corrupted_handoff,
                created_at=now,
            )
        with self.assertRaises(ValueError):
            ResearchHandoffOut(
                id="different-bundle",
                plan_version_id="version-1",
                version_number=1,
                status=ResearchArtifactStatus.approved,
                content=prepared,
                created_at=now,
            )

        malformed_receipt = ResearchIdempotencyReceipt(
            workspace_id="workspace-1",
            guest_id="guest-1",
            idempotency_key="malformed-replay",
            operation="research_project.create",
            request_fingerprint="0" * 64,
            status="completed",
            response_status_code=201,
            response_payload={},
        )
        with self.assertRaises(HTTPException) as malformed_replay:
            _completed_receipt_response(malformed_receipt)
        self.assertEqual(malformed_replay.exception.status_code, 500)
