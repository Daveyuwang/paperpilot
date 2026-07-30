"""PostgreSQL lifecycle coverage for the Research Director API.

This test deliberately uses a caller-provided database and an isolated schema.
It is skipped in ordinary unit-test runs so a developer's local database is
never selected implicitly.
"""

from __future__ import annotations

import asyncio
import json
import os
import unittest
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

from fastapi import HTTPException
from slowapi.errors import RateLimitExceeded
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool
from starlette.requests import Request

from app.api import research_director as api
from app.db.postgres import Base
from app.models.orm import (
    ResearchArtifactStatus,
    ResearchHandoffBundle,
    ResearchIdempotencyReceipt,
    ResearchPlanReview,
    ResearchPlanVersion,
    ResearchProject,
    Workspace,
)
from app.research_director.models import (
    DEFAULT_REVIEWER_PERSPECTIVES,
    EvidenceItem,
    GeneratePlanRequest,
    PlanLifecycleStatus,
    ReviewIssue,
    ReviewIssueStatus,
    ReviewPlanRequest,
    ReviewReport,
    ReviewSeverity,
    ReviewVerdict,
    RevisePlanRequest,
)
from app.research_director.service import (
    generate_plan as generate_plan_without_model,
)
from app.research_director.service import (
    revise_plan as revise_plan_without_model,
)

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


def _async_postgres_url(raw_url: str):
    url = make_url(raw_url)
    if url.drivername in {"postgres", "postgresql", "postgresql+psycopg2"}:
        url = url.set(drivername="postgresql+asyncpg")
    if url.drivername != "postgresql+asyncpg":
        raise unittest.SkipTest(
            "TEST_DATABASE_URL must identify a PostgreSQL database"
        )
    return url


def _post_request(path: str, guest_id: str) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [(b"x-guest-id", guest_id.encode())],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        }
    )


def _replayed_payload(response) -> dict:
    return json.loads(response.body.decode("utf-8"))


def _review_for(plan_version: int, plan_id: str) -> ReviewReport:
    if plan_version == 1:
        issues = [
            ReviewIssue(
                id="issue-method-baseline",
                perspective=DEFAULT_REVIEWER_PERSPECTIVES[2],
                severity=ReviewSeverity.MAJOR,
                artifact_path="experiments[0].baselines",
                problem="The baseline selection is not yet frozen.",
                evidence="The initial draft records the baseline as unresolved.",
                impact="External results would not support a stable comparison.",
                required_fix="Freeze the baseline before approval.",
                status=ReviewIssueStatus.OPEN,
            )
        ]
        summary = "Revision is required before handoff."
        next_step = "Revise the plan and repeat independent review."
    else:
        issues = []
        summary = "All required perspectives completed without blocking issues."
        next_step = "Record human approval before external handoff."

    return ReviewReport(
        review_id=f"controlled-review-v{plan_version}",
        reviewed_plan_id=plan_id,
        reviewed_plan_version=plan_version,
        verdict=(
            ReviewVerdict.REVISION_REQUIRED
            if issues
            else ReviewVerdict.APPROVABLE_FOR_HANDOFF
        ),
        perspectives_completed=list(DEFAULT_REVIEWER_PERSPECTIVES),
        issues=issues,
        strengths=["The external-execution boundary is explicit."],
        summary=summary,
        required_next_step=next_step,
    )


@unittest.skipUnless(
    TEST_DATABASE_URL,
    "set TEST_DATABASE_URL to run the PostgreSQL persistence lifecycle test",
)
class ResearchDirectorPersistenceIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_durable_research_director_lifecycle(self):
        """Persist the full plan-review-revise-approve-handoff state machine."""

        database_url = _async_postgres_url(TEST_DATABASE_URL or "")
        schema_name = f"research_director_test_{uuid.uuid4().hex}"
        owner_id = "integration-owner"
        other_guest_id = "integration-other-guest"
        workspace_id = str(uuid.uuid4())

        admin_engine = create_async_engine(database_url, poolclass=NullPool)
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))

        test_engine = create_async_engine(
            database_url,
            connect_args={"server_settings": {"search_path": schema_name}},
            poolclass=NullPool,
        )

        async def clean_test_schema():
            await test_engine.dispose()
            async with admin_engine.begin() as connection:
                await connection.execute(
                    text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
                )
            await admin_engine.dispose()

        self.addAsyncCleanup(clean_test_schema)
        async with test_engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        sessions = async_sessionmaker(
            test_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

        initial_evidence = EvidenceItem(
            id="evidence-initial",
            title="Controlled source",
            source_uri="https://example.test/controlled-source",
            summary="A retained source used to exercise durable provenance.",
        )
        appended_evidence = EvidenceItem(
            id="evidence-appended",
            title="Controlled revision source",
            source_uri="https://example.test/revision-source",
            summary=(
                "A new source appended during revision without rewriting provenance."
            ),
        )
        initial_request = GeneratePlanRequest(
            research_brief="Design an evidence-gated research-agent evaluation.",
            evidence=[initial_evidence],
        )
        controlled_plan = await generate_plan_without_model(None, initial_request)
        active_endpoint_db: dict[str, AsyncSession | None] = {"value": None}
        create_started = asyncio.Event()
        release_create = asyncio.Event()
        create_model_calls = 0

        def assert_remote_call_has_no_db_transaction() -> None:
            endpoint_db = active_endpoint_db["value"]
            assert endpoint_db is not None
            assert not endpoint_db.in_transaction()

        async def fake_resolve_llm(_guest_id: str):
            assert_remote_call_has_no_db_transaction()
            return None

        async def fake_generate_plan(_llm, request: GeneratePlanRequest):
            nonlocal create_model_calls
            assert_remote_call_has_no_db_transaction()
            assert request == initial_request
            create_model_calls += 1
            create_started.set()
            await release_create.wait()
            return controlled_plan.model_copy(deep=True)

        async def fake_review_plan(_llm, request: ReviewPlanRequest):
            assert_remote_call_has_no_db_transaction()
            return _review_for(request.plan.version, request.plan.plan_id)

        revision_race = {
            "insert_new_review": False,
            "project_id": None,
            "plan_version_id": None,
        }

        async def fake_revise_plan(_llm, request: RevisePlanRequest):
            assert_remote_call_has_no_db_transaction()
            if revision_race["insert_new_review"]:
                revision_race["insert_new_review"] = False
                async with sessions() as competing_db:
                    result = await competing_db.execute(
                        select(ResearchPlanReview)
                        .where(
                            ResearchPlanReview.research_project_id
                            == str(revision_race["project_id"]),
                            ResearchPlanReview.research_plan_version_id
                            == revision_race["plan_version_id"],
                        )
                        .order_by(ResearchPlanReview.review_round.desc())
                        .limit(1)
                    )
                    current_review = result.scalar_one()
                    competing_db.add(
                        ResearchPlanReview(
                            workspace_id=current_review.workspace_id,
                            guest_id=current_review.guest_id,
                            research_project_id=current_review.research_project_id,
                            research_plan_version_id=(
                                current_review.research_plan_version_id
                            ),
                            review_round=current_review.review_round + 1,
                            status=ResearchArtifactStatus.reviewed,
                            review=dict(current_review.review),
                        )
                    )
                    await competing_db.commit()

            class StaticLLM:
                async def create_json(self, **_kwargs):
                    payload = request.plan.model_dump(mode="json")
                    pending_ids = {item.id for item in request.review.issues}
                    if request.plan.revision_record is not None:
                        pending_ids.update(
                            request.plan.revision_record.unresolved_issue_ids
                        )
                    payload["revision_record"] = {
                        "review_id": request.review.review_id,
                        "addressed_issue_ids": sorted(pending_ids),
                        "unresolved_issue_ids": [],
                        "changes": ["Resolved every classified review issue."],
                    }
                    return payload

            return await revise_plan_without_model(StaticLLM(), request)

        for attribute, replacement in (
            ("_resolve_llm", fake_resolve_llm),
            ("generate_plan", fake_generate_plan),
            ("review_plan", fake_review_plan),
            ("revise_plan", fake_revise_plan),
        ):
            patcher = patch.object(api, attribute, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

        async with sessions() as db:
            db.add(
                Workspace(
                    id=workspace_id,
                    guest_id=owner_id,
                    title="Research Director integration workspace",
                )
            )
            await db.commit()

        brief_snapshot = api.ResearchBriefSnapshot(
            title="Durable research lifecycle",
            research_question="Can review gates preserve research lineage?",
            objective="Produce an implementation-ready external handoff.",
            problem_statement="Open research workflows can erase blockers.",
            intended_contribution="A durable review-gated plan.",
            scope="Planning, independent review, and external handoff.",
            success_criteria=["No unresolved issue can be approved."],
            constraints=["Do not execute code or experiments."],
            desired_deliverables=["Reviewed implementation plan"],
            source_policy=api.ResearchSourcePolicySnapshot(
                use_workspace_sources=True,
                discover_external_sources=False,
                prefer_primary_sources=True,
                time_horizon="broad",
                must_include=[],
                must_exclude=[],
            ),
            notes="Persist this structured brief.",
        )
        create_body = api.CreateResearchProjectRequest(
            workspace_id=workspace_id,
            title="Durable research lifecycle",
            brief_snapshot=brief_snapshot,
            plan_request=initial_request,
        )
        create_path = "/api/research-director/projects"
        async with sessions() as first_db, sessions() as duplicate_db:
            active_endpoint_db["value"] = first_db
            first_create = asyncio.create_task(
                api.create_research_project(
                    body=create_body,
                    request=_post_request(create_path, owner_id),
                    idempotency_key="create-main",
                    guest_id=owner_id,
                    db=first_db,
                )
            )
            await asyncio.wait_for(create_started.wait(), timeout=5)
            active_endpoint_db["value"] = duplicate_db
            try:
                with self.assertRaises(HTTPException) as concurrent_duplicate:
                    await api.create_research_project(
                        body=create_body,
                        request=_post_request(create_path, owner_id),
                        idempotency_key="create-main",
                        guest_id=owner_id,
                        db=duplicate_db,
                    )
            finally:
                release_create.set()
            created = await first_create
        assert concurrent_duplicate.exception.status_code == 425
        assert concurrent_duplicate.exception.headers == {"Retry-After": "5"}
        assert create_model_calls == 1
        project_id = created.project.id
        revision_race["project_id"] = project_id
        revision_race["plan_version_id"] = created.plan_versions[0].id
        assert created.project.status == ResearchArtifactStatus.draft
        assert created.project.brief_snapshot == brief_snapshot
        assert [item.version_number for item in created.plan_versions] == [1]

        # A new session proves ownership is enforced from persisted records.
        async with sessions() as db:
            with self.assertRaises(HTTPException) as ownership_error:
                await api.get_research_project(
                    project_id,
                    guest_id=other_guest_id,
                    db=db,
                )
        assert ownership_error.exception.status_code == 404

        async with sessions() as db:
            active_endpoint_db["value"] = db
            reviewed_v1 = await api.review_research_plan(
                project_id=project_id,
                version_number=1,
                body=api.ReviewResearchPlanRequest(),
                request=_post_request(
                    f"/api/research-director/projects/{project_id}/versions/1/review",
                    owner_id,
                ),
                idempotency_key="review-v1",
                guest_id=owner_id,
                db=db,
            )
        assert reviewed_v1.project.status == ResearchArtifactStatus.reviewed
        assert reviewed_v1.reviews[-1].review.verdict == ReviewVerdict.REVISION_REQUIRED
        first_review_id = reviewed_v1.reviews[-1].id

        # Simulate a new review committing while the revision model is running.
        # The endpoint must reselect under the plan-version lock and reject the
        # now-stale implicit "latest" selection before creating version 2.
        revision_race["insert_new_review"] = True
        async with sessions() as db:
            active_endpoint_db["value"] = db
            with self.assertRaises(HTTPException) as review_race:
                await api.revise_research_plan(
                    project_id=project_id,
                    version_number=1,
                    body=api.ReviseResearchPlanRequest(
                        evidence=[appended_evidence]
                    ),
                    request=_post_request(
                        f"/api/research-director/projects/{project_id}/versions/1/revise",
                        owner_id,
                    ),
                    idempotency_key="revise-race",
                    guest_id=owner_id,
                    db=db,
                )
        assert review_race.exception.status_code == 409
        assert "newer independent review" in review_race.exception.detail

        async with sessions() as db:
            refreshed = await api.get_research_project(
                project_id,
                guest_id=owner_id,
                db=db,
            )
        version_one_id = reviewed_v1.plan_versions[0].id
        version_one_reviews = [
            item
            for item in refreshed.reviews
            if item.plan_version_id == version_one_id
        ]
        latest_review_id = max(
            version_one_reviews,
            key=lambda item: item.review_round,
        ).id
        assert latest_review_id != first_review_id

        # A historical ID is rejected even when it belongs to the same owner and
        # plan version; review_id is an expected-latest optimistic token.
        async with sessions() as db:
            active_endpoint_db["value"] = db
            with self.assertRaises(HTTPException) as historical_review:
                await api.revise_research_plan(
                    project_id=project_id,
                    version_number=1,
                    body=api.ReviseResearchPlanRequest(
                        review_id=first_review_id,
                        evidence=[appended_evidence],
                    ),
                    request=_post_request(
                        f"/api/research-director/projects/{project_id}/versions/1/revise",
                        owner_id,
                    ),
                    idempotency_key="revise-historical",
                    guest_id=owner_id,
                    db=db,
                )
        assert historical_review.exception.status_code == 409
        assert "no longer latest" in historical_review.exception.detail

        async with sessions() as db:
            active_endpoint_db["value"] = db
            revised = await api.revise_research_plan(
                project_id=project_id,
                version_number=1,
                body=api.ReviseResearchPlanRequest(
                    review_id=latest_review_id,
                    evidence=[appended_evidence],
                ),
                request=_post_request(
                    f"/api/research-director/projects/{project_id}/versions/1/revise",
                    owner_id,
                ),
                idempotency_key="revise-v1",
                guest_id=owner_id,
                db=db,
            )
        assert [item.version_number for item in revised.plan_versions] == [1, 2]
        assert revised.plan_versions[0].status == ResearchArtifactStatus.superseded
        assert revised.plan_versions[1].status == ResearchArtifactStatus.draft
        assert [
            item.id for item in revised.plan_versions[1].content.evidence_catalog
        ] == [initial_evidence.id, appended_evidence.id]

        async with sessions() as db:
            active_endpoint_db["value"] = db
            reviewed_v2 = await api.review_research_plan(
                project_id=project_id,
                version_number=2,
                body=api.ReviewResearchPlanRequest(),
                request=_post_request(
                    f"/api/research-director/projects/{project_id}/versions/2/review",
                    owner_id,
                ),
                idempotency_key="review-v2",
                guest_id=owner_id,
                db=db,
            )
        assert (
            reviewed_v2.reviews[-1].review.verdict
            == ReviewVerdict.APPROVABLE_FOR_HANDOFF
        )

        async with sessions() as db:
            approved = await api.approve_research_plan(
                project_id,
                2,
                idempotency_key="approve-v2",
                guest_id=owner_id,
                db=db,
            )
        assert approved.project.status == ResearchArtifactStatus.approved
        assert approved.plan_versions[-1].content.lifecycle_status == (
            PlanLifecycleStatus.APPROVED_FOR_HANDOFF
        )

        async with sessions() as db:
            prepared = await api.prepare_research_handoff(
                project_id,
                2,
                idempotency_key="prepare-v2",
                guest_id=owner_id,
                db=db,
            )
        assert prepared.project.status == ResearchArtifactStatus.approved
        assert len(prepared.handoff_bundles) == 1
        assert prepared.handoff_bundles[0].status == ResearchArtifactStatus.approved
        assert prepared.handoff_bundles[0].content.status == "ready_for_handoff"
        assert prepared.handoff_bundles[0].content.execution_status == (
            "awaiting_external_execution"
        )

        async with sessions() as db:
            handed_off = await api.handoff_research_plan(
                project_id,
                2,
                api.ConfirmResearchHandoffRequest(confirm_transfer=True),
                idempotency_key="handoff-v2",
                guest_id=owner_id,
                db=db,
            )
        assert handed_off.project.status == ResearchArtifactStatus.handed_off
        assert len(handed_off.handoff_bundles) == 1
        handoff_content = handed_off.handoff_bundles[0].content
        assert handoff_content.execution_status == "awaiting_external_execution"
        assert (
            "did not execute code, builds, tests, or experiments"
            in handoff_content.boundary
        )
        assert (
            handoff_content.plan_snapshot.lifecycle_status
            == PlanLifecycleStatus.HANDED_OFF
        )
        assert handoff_content.implementation_plan.execution_status == (
            "awaiting_external_execution"
        )

        # Every completed mutation replays its exact frozen response even after
        # the project has advanced to handed_off. The successful revision was
        # the third fresh revise attempt, so this also proves replay bypasses
        # fresh-operation rate charging.
        async with sessions() as db:
            replayed_create = await api.create_research_project(
                body=create_body,
                request=_post_request(create_path, owner_id),
                idempotency_key="create-main",
                guest_id=owner_id,
                db=db,
            )
        assert replayed_create.status_code == 201
        assert _replayed_payload(replayed_create) == created.model_dump(mode="json")

        async with sessions() as db:
            replayed_review = await api.review_research_plan(
                project_id=project_id,
                version_number=1,
                body=api.ReviewResearchPlanRequest(),
                request=_post_request(
                    f"/api/research-director/projects/{project_id}/versions/1/review",
                    owner_id,
                ),
                idempotency_key="review-v1",
                guest_id=owner_id,
                db=db,
            )
        assert replayed_review.status_code == 200
        assert _replayed_payload(replayed_review) == reviewed_v1.model_dump(mode="json")

        async with sessions() as db:
            replayed_revision = await api.revise_research_plan(
                project_id=project_id,
                version_number=1,
                body=api.ReviseResearchPlanRequest(
                    review_id=latest_review_id,
                    evidence=[appended_evidence],
                ),
                request=_post_request(
                    f"/api/research-director/projects/{project_id}/versions/1/revise",
                    owner_id,
                ),
                idempotency_key="revise-v1",
                guest_id=owner_id,
                db=db,
            )
        assert replayed_revision.status_code == 200
        assert _replayed_payload(replayed_revision) == revised.model_dump(mode="json")

        async with sessions() as db:
            replayed_approval = await api.approve_research_plan(
                project_id,
                2,
                idempotency_key="approve-v2",
                guest_id=owner_id,
                db=db,
            )
        assert replayed_approval.status_code == 200
        assert _replayed_payload(replayed_approval) == approved.model_dump(mode="json")

        async with sessions() as db:
            replayed_prepare = await api.prepare_research_handoff(
                project_id,
                2,
                idempotency_key="prepare-v2",
                guest_id=owner_id,
                db=db,
            )
        assert replayed_prepare.status_code == 200
        assert _replayed_payload(replayed_prepare) == prepared.model_dump(mode="json")

        async with sessions() as db:
            replayed_handoff = await api.handoff_research_plan(
                project_id,
                2,
                api.ConfirmResearchHandoffRequest(confirm_transfer=True),
                idempotency_key="handoff-v2",
                guest_id=owner_id,
                db=db,
            )
        assert replayed_handoff.status_code == 200
        assert _replayed_payload(replayed_handoff) == handed_off.model_dump(mode="json")

        async with sessions() as db:
            with self.assertRaises(HTTPException) as key_mismatch:
                await api.create_research_project(
                    body=create_body.model_copy(update={"title": "Changed title"}),
                    request=_post_request(create_path, owner_id),
                    idempotency_key="create-main",
                    guest_id=owner_id,
                    db=db,
                )
        assert key_mismatch.exception.status_code == 409
        assert "different operation or request" in key_mismatch.exception.detail

        async with sessions() as db:
            with self.assertRaises(RateLimitExceeded):
                await api.revise_research_plan(
                    project_id=project_id,
                    version_number=1,
                    body=api.ReviseResearchPlanRequest(
                        review_id=latest_review_id,
                        evidence=[appended_evidence],
                    ),
                    request=_post_request(
                        f"/api/research-director/projects/{project_id}/versions/1/revise",
                        owner_id,
                    ),
                    idempotency_key="revise-fourth-fresh",
                    guest_id=owner_id,
                    db=db,
                )

        # Repeating handoff is rejected and cannot create a second durable bundle.
        async with sessions() as db:
            with self.assertRaises(HTTPException) as duplicate_error:
                await api.handoff_research_plan(
                    project_id,
                    2,
                    api.ConfirmResearchHandoffRequest(confirm_transfer=True),
                    idempotency_key="handoff-v2-again",
                    guest_id=owner_id,
                    db=db,
                )
        assert duplicate_error.exception.status_code == 409

        # Re-open once more and validate the complete durable state independently
        # of endpoint response objects.
        async with sessions() as db:
            project = await db.get(ResearchProject, project_id)
            version_result = await db.execute(
                select(ResearchPlanVersion)
                .where(ResearchPlanVersion.research_project_id == project_id)
                .order_by(ResearchPlanVersion.version_number)
            )
            versions = list(version_result.scalars())
            review_count = await db.scalar(
                select(func.count(ResearchPlanReview.id)).where(
                    ResearchPlanReview.research_project_id == project_id
                )
            )
            handoff_result = await db.execute(
                select(ResearchHandoffBundle).where(
                    ResearchHandoffBundle.research_project_id == project_id
                )
            )
            durable_handoffs = list(handoff_result.scalars())
            receipt_result = await db.execute(
                select(ResearchIdempotencyReceipt).where(
                    ResearchIdempotencyReceipt.workspace_id == workspace_id
                )
            )
            receipts = list(receipt_result.scalars())

        assert project is not None
        assert project.guest_id == owner_id
        assert project.content["brief_snapshot"] == brief_snapshot.model_dump(
            mode="json"
        )
        assert project.status == ResearchArtifactStatus.handed_off
        assert [item.status for item in versions] == [
            ResearchArtifactStatus.superseded,
            ResearchArtifactStatus.handed_off,
        ]
        assert review_count == 3
        assert len(durable_handoffs) == 1
        # Failed/rate-limited claims are deleted: only completed receipts have
        # durable replay value, so untrusted fresh keys cannot grow the table.
        assert len(receipts) == 7
        assert {
            item.idempotency_key
            for item in receipts
            if item.status == "completed"
        } == {
            "create-main",
            "review-v1",
            "revise-v1",
            "review-v2",
            "approve-v2",
            "prepare-v2",
            "handoff-v2",
        }
        assert all(item.status == "completed" for item in receipts)
        assert durable_handoffs[0].content["execution_status"] == (
            "awaiting_external_execution"
        )

        recovery_payload = {"project_id": project_id, "probe": "lease-recovery"}
        old_owner = str(uuid.uuid4())
        async with sessions() as db:
            db.add(
                ResearchIdempotencyReceipt(
                    workspace_id=workspace_id,
                    guest_id=owner_id,
                    idempotency_key="expired-recovery",
                    operation="research_probe.recover",
                    request_fingerprint=api._canonical_request_fingerprint(
                        "research_probe.recover",
                        recovery_payload,
                    ),
                    status="in_progress",
                    owner_token=old_owner,
                    lease_expires_at=datetime.utcnow() - timedelta(seconds=1),
                )
            )
            await db.commit()
        async with sessions() as db:
            recovered = await api._claim_idempotency(
                db,
                workspace_id=workspace_id,
                guest_id=owner_id,
                idempotency_key="expired-recovery",
                operation="research_probe.recover",
                request_payload=recovery_payload,
            )
            assert recovered.claim is not None
            assert recovered.claim.owner_token != old_owner
            await api._abandon_idempotency_claim(db, recovered.claim)

        async with sessions() as db:
            assert await db.get(
                ResearchIdempotencyReceipt, recovered.claim.receipt_id
            ) is None

        # A real PostgreSQL heartbeat renews a live worker's short lease from a
        # separate session while preserving the owner fence.
        heartbeat_payload = {"project_id": project_id, "probe": "heartbeat"}
        async with sessions() as db:
            heartbeat_resolution = await api._claim_idempotency(
                db,
                workspace_id=workspace_id,
                guest_id=owner_id,
                idempotency_key="heartbeat-probe",
                operation="research_probe.heartbeat",
                request_payload=heartbeat_payload,
            )
        assert heartbeat_resolution.claim is not None
        heartbeat_claim = heartbeat_resolution.claim
        async with sessions() as db:
            before_heartbeat = await db.get(
                ResearchIdempotencyReceipt, heartbeat_claim.receipt_id
            )
            assert before_heartbeat is not None
            initial_expiry = before_heartbeat.lease_expires_at
        assert initial_expiry is not None

        with (
            patch.object(api, "AsyncSessionLocal", sessions),
            patch.object(api, "IDEMPOTENCY_HEARTBEAT_SECONDS", 0.01),
            patch.object(api, "IDEMPOTENCY_LEASE", timedelta(minutes=3)),
        ):
            heartbeat_task = asyncio.create_task(
                api._renew_idempotency_lease(heartbeat_claim)
            )
            await asyncio.sleep(0.05)
            await api._stop_idempotency_heartbeat(heartbeat_task)

        async with sessions() as db:
            after_heartbeat = await db.get(
                ResearchIdempotencyReceipt, heartbeat_claim.receipt_id
            )
            assert after_heartbeat is not None
            assert after_heartbeat.owner_token == heartbeat_claim.owner_token
            assert after_heartbeat.lease_expires_at is not None
            assert after_heartbeat.lease_expires_at > initial_expiry
            await api._abandon_idempotency_claim(db, heartbeat_claim)

        # A worker that outlives its lease keeps the originally claimed receipt
        # in its identity map because production sessions use
        # expire_on_commit=False. A takeover must fence that stale worker both
        # from committing domain state and from clearing the new owner's lease.
        stale_payload = {"project_id": project_id, "probe": "stale-owner-fence"}
        original_title = handed_off.project.title
        async with sessions() as stale_db, sessions() as takeover_db:
            stale = await api._claim_idempotency(
                stale_db,
                workspace_id=workspace_id,
                guest_id=owner_id,
                idempotency_key="stale-owner-fence",
                operation="research_probe.fence",
                request_payload=stale_payload,
            )
            assert stale.claim is not None

            takeover_result = await takeover_db.execute(
                select(ResearchIdempotencyReceipt)
                .where(
                    ResearchIdempotencyReceipt.id == stale.claim.receipt_id
                )
                .with_for_update()
            )
            takeover_receipt = takeover_result.scalar_one()
            takeover_receipt.lease_expires_at = datetime.utcnow() - timedelta(
                seconds=1
            )
            await takeover_db.commit()

            replacement = await api._claim_idempotency(
                takeover_db,
                workspace_id=workspace_id,
                guest_id=owner_id,
                idempotency_key="stale-owner-fence",
                operation="research_probe.fence",
                request_payload=stale_payload,
            )
            assert replacement.claim is not None
            assert replacement.claim.owner_token != stale.claim.owner_token

            stale_project = await stale_db.get(ResearchProject, project_id)
            assert stale_project is not None
            stale_project.title = "stale worker must not persist"
            with self.assertRaises(HTTPException) as stale_finalize:
                await api._freeze_detail_and_commit(
                    stale_db,
                    project_id,
                    owner_id,
                    idempotency_claim=stale.claim,
                )
            assert stale_finalize.exception.status_code == 409
            await api._abandon_idempotency_claim(stale_db, stale.claim)

            async with sessions() as verification_db:
                durable_project = await verification_db.get(
                    ResearchProject, project_id
                )
                durable_receipt = await verification_db.get(
                    ResearchIdempotencyReceipt,
                    stale.claim.receipt_id,
                )
            assert durable_project is not None
            assert durable_project.title == original_title
            assert durable_receipt is not None
            assert durable_receipt.status == "in_progress"
            assert durable_receipt.owner_token == replacement.claim.owner_token
            assert durable_receipt.lease_expires_at is not None
            assert durable_receipt.lease_expires_at > datetime.utcnow()

            await api._abandon_idempotency_claim(
                takeover_db, replacement.claim
            )

        # The database constraint is the final guard against concurrent duplicate
        # handoff writes, independent of the endpoint's lifecycle check.
        async with sessions() as db:
            original = durable_handoffs[0]
            db.add(
                ResearchHandoffBundle(
                    workspace_id=workspace_id,
                    guest_id=owner_id,
                    research_project_id=project_id,
                    research_plan_version_id=versions[1].id,
                    version_number=2,
                    status=ResearchArtifactStatus.handed_off,
                    content=original.content,
                )
            )
            with self.assertRaises(IntegrityError):
                await db.commit()
            await db.rollback()

        # Match the production workspace-delete UOW: load only Workspace, then
        # rely on ORM project/version cascades plus database ON DELETE CASCADE.
        async with sessions() as db:
            workspace = await db.get(Workspace, workspace_id)
            assert workspace is not None
            await db.delete(workspace)
            await db.commit()

        async with sessions() as db:
            for model in (
                ResearchProject,
                ResearchPlanVersion,
                ResearchPlanReview,
                ResearchHandoffBundle,
                ResearchIdempotencyReceipt,
            ):
                remaining = await db.scalar(select(func.count()).select_from(model))
                assert remaining == 0, model.__name__
