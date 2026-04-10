"""
LLM-based concept map extraction for academic papers.

Uses Claude to produce 8–15 grounded, typed concept nodes with semantic edges.
Runs synchronously inside the Celery worker (same pattern as scaffold_pass.py).
"""
from __future__ import annotations
import json
import re
import structlog
from anthropic import Anthropic

from app.config import get_settings

logger = structlog.get_logger()
settings = get_settings()

# ── Constants ──────────────────────────────────────────────────────────────

NODE_TYPES = frozenset({
    "Problem", "Method", "Component", "Baseline",
    "Dataset", "Metric", "Finding", "Limitation",
})

EDGE_RELATIONS = frozenset({
    "addresses", "consists_of", "compared_with",
    "evaluated_on", "measured_by", "leads_to", "limited_by",
})

MAX_CONTENT_CHARS = 38_000   # ~10K tokens, leaves room for prompt + response
MAX_CHUNKS = 60

EXTRACTION_PROMPT = """\
You are analyzing the academic paper "{title}".

Abstract:
{abstract}

Paper content (sections in document order):
{content}

---

Extract a concept map that helps readers understand this paper's core ideas and their relationships.

Return ONLY valid JSON with no markdown fences or explanation outside the JSON object.

Schema:
{{
  "nodes": [
    {{
      "id": "lowercase_underscore_id",
      "label": "Short Concept Name",
      "type": "Problem|Method|Component|Baseline|Dataset|Metric|Finding|Limitation",
      "short_description": "1–2 sentences describing this concept's specific role in THIS paper.",
      "evidence": ["A verbatim or near-verbatim quote (10–80 words) from the paper text above."],
      "section": "Section name where this primarily appears, or null",
      "page": page_number_integer_or_null
    }}
  ],
  "edges": [
    {{
      "source": "source_node_id",
      "target": "target_node_id",
      "relation": "addresses|consists_of|compared_with|evaluated_on|measured_by|leads_to|limited_by",
      "evidence": ["A quote from the paper justifying this relationship."]
    }}
  ]
}}

Node type guidance:
- Problem: The research challenge or gap this paper addresses
- Method: The primary approach, algorithm, or system proposed
- Component: A sub-module or mechanism that is part of the Method
- Baseline: An existing method this work is compared against (must be named)
- Dataset: A benchmark or dataset used in experiments (must be named)
- Metric: An evaluation metric (e.g., BLEU, F1, perplexity)
- Finding: A key empirical result, claim, or theoretical conclusion
- Limitation: An explicitly stated constraint, failure case, or scope limitation

Edge relation guidance (direction matters):
- addresses: [Method] addresses [Problem]
- consists_of: [Method] consists_of [Component]
- compared_with: [Method] compared_with [Baseline]
- evaluated_on: [Method or Finding] evaluated_on [Dataset]
- measured_by: [Finding] measured_by [Metric]
- leads_to: [Method or Component] leads_to [Finding]
- limited_by: [Method or Finding] limited_by [Limitation]

Hard rules:
1. Include 8–15 nodes total. Fewer is better than padding with weak concepts.
2. Only include concepts central to THIS paper's contribution.
3. Every node's evidence[] must contain a quote drawn from the paper text provided above.
4. short_description must describe the concept's specific role in THIS paper, not a generic definition.
5. IDs must be lowercase with underscores and globally unique (e.g., "attention_mechanism", "wmt14_en_de").
6. Prefer specific named entities: "AdaGrad" over "optimizer"; "WMT14" over "machine translation dataset".
7. Every edge's source and target must be node IDs that appear in the nodes list.
8. Do not invent node types or edge relations outside the lists above.
"""


# ── Content building ────────────────────────────────────────────────────────

def _build_content(chunks: list[dict]) -> str:
    """
    Condense paper chunks into a prompt-ready content string.
    Groups by section, adds section headers, respects MAX_CONTENT_CHARS limit.
    """
    seen_sections: set[str] = set()
    parts: list[str] = []
    total = 0

    for chunk in chunks[:MAX_CHUNKS]:
        content = (chunk.get("content") or "").strip()
        if not content:
            continue

        section = (chunk.get("section_title") or "Main Body").strip()
        page = chunk.get("page_number")
        page_str = f", p.{page}" if page else ""

        # Emit a section header when the section changes
        if section not in seen_sections:
            header = f"\n[{section}{page_str}]\n"
            seen_sections.add(section)
        else:
            header = f"[p.{page}] " if page else ""

        snippet = header + content
        remaining = MAX_CONTENT_CHARS - total
        if remaining <= 200:
            break
        if len(snippet) > remaining:
            parts.append(snippet[:remaining] + " …")
            break

        parts.append(snippet)
        total += len(snippet)

    return "\n".join(parts)


# ── Validation ─────────────────────────────────────────────────────────────

def _validate(raw: dict) -> dict:
    """
    Validate and clean LLM output. Removes invalid nodes/edges,
    normalises types/relations, deduplicates edges.
    """
    nodes = raw.get("nodes") or []
    edges = raw.get("edges") or []

    valid_ids: set[str] = set()
    clean_nodes: list[dict] = []

    for node in nodes:
        nid = str(node.get("id") or "").strip()
        label = str(node.get("label") or "").strip()
        if not nid or not label:
            continue

        ntype = node.get("type", "")
        if ntype not in NODE_TYPES:
            ntype = "Method"  # safe fallback

        page = node.get("page")
        if page is not None:
            try:
                page = int(page)
            except (TypeError, ValueError):
                page = None

        clean_nodes.append({
            "id": nid,
            "label": label,
            "type": ntype,
            "short_description": str(node.get("short_description") or label),
            "evidence": [str(e) for e in (node.get("evidence") or []) if e][:2],
            "section": node.get("section") or None,
            "page": page,
        })
        valid_ids.add(nid)

    clean_edges: list[dict] = []
    seen_pairs: set[tuple[str, str]] = set()

    for edge in edges:
        src = str(edge.get("source") or "").strip()
        tgt = str(edge.get("target") or "").strip()
        rel = str(edge.get("relation") or "").strip()

        if src not in valid_ids or tgt not in valid_ids:
            continue
        if rel not in EDGE_RELATIONS:
            continue
        if src == tgt:
            continue
        pair = (src, tgt)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)

        clean_edges.append({
            "source": src,
            "target": tgt,
            "relation": rel,
            "evidence": [str(e) for e in (edge.get("evidence") or []) if e][:1],
        })

    # Cap at 15 nodes
    return {"nodes": clean_nodes[:15], "edges": clean_edges}


# ── Public API ─────────────────────────────────────────────────────────────

def extract_concept_map(
    paper_title: str,
    paper_abstract: str,
    chunks: list[dict],
) -> dict:
    """
    Extract a grounded concept map using Claude (sync, for Celery worker).

    Returns a dict with keys "nodes" and "edges" matching ConceptMapOut schema.
    Falls back to {"nodes": [], "edges": []} on any error so ingestion never fails.
    """
    client = Anthropic(api_key=settings.anthropic_api_key)

    content_str = _build_content(chunks)
    prompt = EXTRACTION_PROMPT.format(
        title=paper_title or "Unknown Title",
        abstract=paper_abstract or "No abstract available.",
        content=content_str,
    )

    logger.info(
        "concept_extraction_start",
        paper_title=(paper_title or "")[:60],
        chunk_count=len(chunks),
        content_chars=len(content_str),
    )

    try:
        response = client.messages.create(
            model=settings.claude_model,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_text = response.content[0].text.strip()

        # Extract the JSON object even if the model wraps it in ``` fences
        json_match = re.search(r'\{[\s\S]*\}', raw_text)
        if not json_match:
            logger.warning("concept_extraction_no_json", snippet=raw_text[:300])
            return {"nodes": [], "edges": []}

        raw_data = json.loads(json_match.group())
        result = _validate(raw_data)

        logger.info(
            "concept_extraction_done",
            nodes=len(result["nodes"]),
            edges=len(result["edges"]),
        )
        return result

    except Exception as exc:
        logger.exception("concept_extraction_failed", error=str(exc))
        return {"nodes": [], "edges": []}
