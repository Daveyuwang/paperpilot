"""
Named entity and relation extraction using spaCy.
Tries to load a SciERC fine-tuned model from HuggingFace; falls back to
the base en_core_web_sm model if the fine-tuned one is unavailable.
"""
from __future__ import annotations
import re
import structlog
import networkx as nx

logger = structlog.get_logger()

_NLP = None


def _get_nlp():
    global _NLP
    if _NLP is not None:
        return _NLP

    import spacy
    # Attempt to load a SciERC-fine-tuned model (optional, may not be installed)
    try:
        _NLP = spacy.load("en_core_sci_sm")
        logger.info("spacy_scispacy_loaded")
    except OSError:
        try:
            _NLP = spacy.load("en_core_web_sm")
            logger.warning("spacy_fallback_to_web_sm")
        except OSError:
            logger.error("spacy_model_not_found")
            return None
    return _NLP


def extract_entities_and_relations(chunks: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    Run NER over all chunks and build entity nodes + co-occurrence edges.
    Returns (nodes, edges) suitable for storing in Postgres and rendering
    as a NetworkX / D3.js concept map.
    """
    nlp = _get_nlp()
    if nlp is None:
        return [], []

    # Map entity label -> list of (chunk_id, page) appearances
    entity_occurrences: dict[str, dict] = {}
    # Co-occurrence pairs in same chunk
    cooccurrences: dict[tuple[str, str], int] = {}

    for chunk in chunks:
        content = chunk["content"]
        chunk_id = chunk.get("id", "")
        page = chunk.get("page_number")

        doc = nlp(content[:5000])  # cap per chunk to avoid memory issues
        ents = [ent.text.strip() for ent in doc.ents if len(ent.text.strip()) > 2]
        # Deduplicate within chunk
        chunk_ents = list(dict.fromkeys(ents))

        for ent_text in chunk_ents:
            ent_key = ent_text.lower()
            if ent_key not in entity_occurrences:
                entity_occurrences[ent_key] = {
                    "label": ent_text,
                    "entity_type": _get_ent_type(ent_text, doc),
                    "chunk_ids": [],
                    "page_numbers": [],
                }
            occ = entity_occurrences[ent_key]
            if chunk_id and chunk_id not in occ["chunk_ids"]:
                occ["chunk_ids"].append(chunk_id)
            if page is not None and page not in occ["page_numbers"]:
                occ["page_numbers"].append(page)

        # Register co-occurrences
        for i, e1 in enumerate(chunk_ents):
            for e2 in chunk_ents[i + 1 :]:
                pair = tuple(sorted([e1.lower(), e2.lower()]))
                cooccurrences[pair] = cooccurrences.get(pair, 0) + 1

    # Filter: keep entities appearing in >= 2 chunks or with high co-occurrence
    min_occurrences = 1
    nodes = [
        occ for occ in entity_occurrences.values()
        if len(occ["chunk_ids"]) >= min_occurrences
    ]

    # Keep top-N most common entities to avoid graph overload
    nodes = sorted(nodes, key=lambda n: len(n["chunk_ids"]), reverse=True)[:150]
    node_labels = {n["label"].lower() for n in nodes}

    edges = []
    for (e1, e2), weight in cooccurrences.items():
        if e1 in node_labels and e2 in node_labels and weight >= 2:
            edges.append({
                "source_label": e1,
                "target_label": e2,
                "relation": "co-occurs-with",
                "weight": weight,
            })

    return nodes, edges


def _get_ent_type(text: str, doc) -> str | None:
    """Return the entity type label for a given entity text."""
    for ent in doc.ents:
        if ent.text.strip().lower() == text.lower():
            return ent.label_
    return None


def build_networkx_graph(nodes: list[dict], edges: list[dict]) -> nx.Graph:
    G = nx.Graph()
    for node in nodes:
        G.add_node(node["label"], entity_type=node.get("entity_type"))
    for edge in edges:
        G.add_edge(
            edge["source_label"],
            edge["target_label"],
            relation=edge.get("relation"),
            weight=edge.get("weight", 1),
        )
    return G
