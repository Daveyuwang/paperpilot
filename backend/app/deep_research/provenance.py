from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.deep_research.models import (
    EvidenceSource,
    EvidenceUnit,
    PostSynthesisEvaluationRun,
    ReportSegment,
    ResearchReport,
    SubReport,
)

MAX_SOURCE_EXCERPT_CHARS = 2000
MAX_SOURCE_TITLE_CHARS = 500
MAX_SOURCE_METADATA_CHARS = 100

_SENSITIVE_QUERY_KEYS = {
    "access_token",
    "apikey",
    "api_key",
    "auth",
    "authorization",
    "code",
    "credential",
    "jwt",
    "key",
    "password",
    "passwd",
    "secret",
    "session",
    "session_id",
    "sessionid",
    "sig",
    "signature",
    "token",
}
_CREDENTIAL_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|authorization|password|passwd|secret)"
    r"\b\s*[:=]\s*['\"]?[^\s,;\"']+"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_JWT_RE = re.compile(
    r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\b"
)
_URL_RE = re.compile(r"https?://[^\s<>\"']+")


def _stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def report_digest(report: ResearchReport) -> str:
    """Return a deterministic digest of the canonical report payload."""
    canonical_report = ResearchReport.model_validate(report)
    payload = json.dumps(
        canonical_report.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def corpus_digest(reports: list[SubReport] | tuple[SubReport, ...]) -> str:
    """Return a deterministic digest of the current report snapshot."""

    canonical = sorted(
        (
            SubReport.model_validate(report).model_dump(mode="json")
            for report in reports
        ),
        key=lambda item: item["sub_question_id"],
    )
    payload = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def evaluation_digest(run: PostSynthesisEvaluationRun) -> str:
    """Return a deterministic digest of one exact post-evaluation run artifact."""
    canonical_run = PostSynthesisEvaluationRun.model_validate(run)
    payload = json.dumps(
        canonical_run.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def normalize_source_url(url: str) -> str:
    """Return a stable public URL with credentials and sensitive query values removed."""
    if not isinstance(url, str):
        return ""
    cleaned = url.strip()
    try:
        parts = urlsplit(cleaned)
        port = parts.port
    except ValueError:
        return ""
    scheme = parts.scheme.lower()
    hostname = parts.hostname
    if scheme not in {"http", "https"} or not hostname:
        return ""
    normalized_host = hostname.lower()
    if ":" in normalized_host and not normalized_host.startswith("["):
        normalized_host = f"[{normalized_host}]"
    if port is not None and not (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        normalized_host = f"{normalized_host}:{port}"
    safe_query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not _is_sensitive_query_key(key)
    ]
    normalized_query = urlencode(sorted(safe_query))
    normalized_path = parts.path or "/"
    return urlunsplit(
        (
            scheme,
            normalized_host,
            normalized_path,
            normalized_query,
            "",
        )
    )


def _is_sensitive_query_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")
    return (
        normalized in _SENSITIVE_QUERY_KEYS
        or normalized.endswith(
            (
                "_auth",
                "_credential",
                "_key",
                "_password",
                "_secret",
                "_session",
                "_signature",
                "_token",
            )
        )
        or normalized.startswith(
            ("auth_", "credential_", "password_", "secret_", "session_", "token_")
        )
    )


def sanitize_source_metadata(value: object, *, max_chars: int) -> str:
    """Collapse control/whitespace and cap untrusted provider metadata."""
    if not isinstance(value, str) or max_chars <= 0:
        return ""
    printable = "".join(
        char if char.isprintable() or char.isspace() else " "
        for char in value
    )
    return re.sub(r"\s+", " ", printable).strip()[:max_chars].rstrip()


def sanitize_source_excerpt(
    value: object,
    *,
    max_chars: int = MAX_SOURCE_EXCERPT_CHARS,
) -> str:
    """Retain a bounded excerpt while redacting common credential representations."""
    text = sanitize_source_metadata(value, max_chars=max(max_chars * 2, max_chars))
    if not text:
        return ""
    text = _BEARER_RE.sub("Bearer [REDACTED]", text)
    text = _CREDENTIAL_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}=[REDACTED]",
        text,
    )
    text = _JWT_RE.sub("[REDACTED]", text)
    text = _URL_RE.sub(
        lambda match: normalize_source_url(match.group(0)) or "[REDACTED_URL]",
        text,
    )
    return text[:max_chars].rstrip()


def source_id_for_url(url: str) -> str:
    normalized = normalize_source_url(url)
    if not normalized:
        return ""
    return _stable_id("src", normalized)


def evidence_id_for_text(
    sub_question_id: str,
    kind: str,
    text: str,
) -> str:
    normalized_text = re.sub(r"\s+", " ", text).strip()
    return _stable_id(
        "ev",
        f"{sub_question_id.strip()}\x00{kind}\x00{normalized_text}",
    )


def build_evidence_inventory(
    sub_reports: list[SubReport],
) -> tuple[list[EvidenceSource], list[EvidenceUnit]]:
    """Create direct excerpt evidence plus explicitly unbound diagnostic summaries."""
    sources_by_id: dict[str, EvidenceSource] = {}
    evidence_by_id: dict[str, EvidenceUnit] = {}

    for report in sub_reports:
        for source in report.sources:
            normalized_url = normalize_source_url(source.url)
            source_id = source_id_for_url(normalized_url)
            if not normalized_url or not source_id:
                continue
            title = sanitize_source_excerpt(
                source.title,
                max_chars=MAX_SOURCE_TITLE_CHARS,
            )
            published_at = sanitize_source_excerpt(
                source.published_at,
                max_chars=MAX_SOURCE_METADATA_CHARS,
            ) or None
            source_type = sanitize_source_excerpt(
                source.source_type,
                max_chars=64,
            ) or None
            if source_id not in sources_by_id:
                sources_by_id[source_id] = EvidenceSource(
                    source_id=source_id,
                    url=normalized_url,
                    title=title,
                    published_at=published_at,
                    source_type=source_type,
                )
            else:
                existing = sources_by_id[source_id]
                sources_by_id[source_id] = existing.model_copy(
                    update={
                        "title": existing.title or title,
                        "published_at": existing.published_at or published_at,
                        "source_type": existing.source_type or source_type,
                    }
                )

            excerpt = sanitize_source_excerpt(source.excerpt)
            if excerpt:
                evidence_id = evidence_id_for_text(
                    report.sub_question_id,
                    f"source_excerpt:{source_id}",
                    excerpt,
                )
                if evidence_id not in evidence_by_id:
                    evidence_by_id[evidence_id] = EvidenceUnit(
                        evidence_id=evidence_id,
                        sub_question_id=report.sub_question_id,
                        provenance="source_excerpt",
                        kind="source_excerpt",
                        text=excerpt,
                        source_ids=[source_id],
                    )

        candidate_units: list[tuple[str, str]] = []
        if report.findings.strip():
            candidate_units.append(("finding", report.findings.strip()))
        candidate_units.extend(
            ("key_fact", fact.strip())
            for fact in report.key_facts
            if fact.strip()
        )
        for kind, text in candidate_units:
            evidence_id = evidence_id_for_text(report.sub_question_id, kind, text)
            if evidence_id not in evidence_by_id:
                evidence_by_id[evidence_id] = EvidenceUnit(
                    evidence_id=evidence_id,
                    sub_question_id=report.sub_question_id,
                    provenance="derived_summary",
                    kind=kind,
                    text=text,
                    source_ids=[],
                )

    return (
        list(sources_by_id.values()),
        list(evidence_by_id.values()),
    )


def build_report_segments(report: ResearchReport) -> list[ReportSegment]:
    """Split every publishable report surface into stable, location-based segments."""
    segments = [
        ReportSegment(
            id="seg-title",
            component="title",
            text=report.title.strip(),
        ),
        ReportSegment(
            id="seg-executive-summary",
            component="executive_summary",
            text=report.executive_summary.strip(),
        ),
    ]
    segments.extend(
        ReportSegment(
            id=f"seg-section-{index:03d}",
            component="section",
            section_index=index,
            heading=section.heading.strip(),
            text=section.content.strip(),
        )
        for index, section in enumerate(report.sections)
    )
    segments.extend(
        ReportSegment(
            id=f"seg-key-finding-{index:03d}",
            component="key_finding",
            item_index=index,
            text=finding.strip(),
        )
        for index, finding in enumerate(report.key_findings)
    )
    segments.append(
        ReportSegment(
            id="seg-limitations",
            component="limitations",
            text=report.limitations.strip(),
        )
    )
    return segments
