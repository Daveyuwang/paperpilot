import re
import structlog
import httpx
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.api.guest import require_guest_id

logger = structlog.get_logger()
router = APIRouter()

OPENALEX_API = "https://api.openalex.org/works"
ARXIV_API = "https://export.arxiv.org/api/query"


class DiscoveredSource(BaseModel):
    external_id: str
    provider: str
    title: str
    authors: list[str]
    year: int | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    abstract: str | None = None
    url: str | None = None
    citation_count: int | None = None


class DiscoverResponse(BaseModel):
    results: list[DiscoveredSource]
    query: str


def _normalize_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]", "", t.lower())


def _parse_openalex(works: list[dict]) -> list[DiscoveredSource]:
    out: list[DiscoveredSource] = []
    for w in works:
        title = (w.get("title") or "").strip()
        if not title:
            continue
        doi = w.get("doi") or None
        if doi and doi.startswith("https://doi.org/"):
            doi = doi[len("https://doi.org/"):]
        authors = [
            a.get("author", {}).get("display_name", "")
            for a in (w.get("authorships") or [])[:5]
        ]
        out.append(DiscoveredSource(
            external_id=w.get("id", ""),
            provider="openalex",
            title=title,
            authors=[a for a in authors if a],
            year=w.get("publication_year"),
            doi=doi,
            abstract=_reconstruct_abstract(w.get("abstract_inverted_index")),
            url=w.get("primary_location", {}).get("landing_page_url") if w.get("primary_location") else None,
            citation_count=w.get("cited_by_count"),
        ))
    return out


def _reconstruct_abstract(inverted_index: dict | None) -> str | None:
    if not inverted_index:
        return None
    word_positions: list[tuple[int, str]] = []
    for word, positions in inverted_index.items():
        for pos in positions:
            word_positions.append((pos, word))
    word_positions.sort()
    text = " ".join(w for _, w in word_positions)
    return text[:500] if text else None


def _parse_arxiv(xml_text: str) -> list[DiscoveredSource]:
    import xml.etree.ElementTree as ET
    out: list[DiscoveredSource] = []
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out
    for entry in root.findall("atom:entry", ns):
        title_el = entry.find("atom:title", ns)
        title = (title_el.text or "").strip().replace("\n", " ") if title_el is not None else ""
        if not title:
            continue
        authors = []
        for author_el in entry.findall("atom:author", ns):
            name_el = author_el.find("atom:name", ns)
            if name_el is not None and name_el.text:
                authors.append(name_el.text.strip())
        summary_el = entry.find("atom:summary", ns)
        abstract = (summary_el.text or "").strip()[:500] if summary_el is not None else None
        id_el = entry.find("atom:id", ns)
        arxiv_url = id_el.text.strip() if id_el is not None and id_el.text else ""
        arxiv_id = arxiv_url.split("/abs/")[-1] if "/abs/" in arxiv_url else ""
        published_el = entry.find("atom:published", ns)
        year = None
        if published_el is not None and published_el.text:
            year = int(published_el.text[:4])
        out.append(DiscoveredSource(
            external_id=arxiv_url,
            provider="arxiv",
            title=title,
            authors=authors[:5],
            year=year,
            arxiv_id=arxiv_id or None,
            abstract=abstract,
            url=arxiv_url,
        ))
    return out


def _dedupe(sources: list[DiscoveredSource]) -> list[DiscoveredSource]:
    seen_doi: set[str] = set()
    seen_arxiv: set[str] = set()
    seen_title: set[str] = set()
    out: list[DiscoveredSource] = []
    for s in sources:
        if s.doi:
            key = s.doi.lower()
            if key in seen_doi:
                continue
            seen_doi.add(key)
        if s.arxiv_id:
            key = s.arxiv_id.lower()
            if key in seen_arxiv:
                continue
            seen_arxiv.add(key)
        norm = _normalize_title(s.title)
        if norm in seen_title:
            continue
        seen_title.add(norm)
        out.append(s)
    return out


@router.get("/discover", response_model=DiscoverResponse)
async def discover_sources(
    q: str = Query(..., min_length=2, max_length=300),
    guest_id: str = Depends(require_guest_id),
):
    all_results: list[DiscoveredSource] = []

    async with httpx.AsyncClient(timeout=10.0) as client:
        # OpenAlex
        try:
            resp = await client.get(OPENALEX_API, params={
                "search": q,
                "per_page": 15,
                "sort": "relevance_score:desc",
                "select": "id,title,authorships,publication_year,doi,abstract_inverted_index,cited_by_count,primary_location",
            })
            if resp.status_code == 200:
                all_results.extend(_parse_openalex(resp.json().get("results", [])))
        except Exception as exc:
            logger.warning("openalex_search_failed", error=str(exc))

        # arXiv
        try:
            resp = await client.get(ARXIV_API, params={
                "search_query": f"all:{q}",
                "start": 0,
                "max_results": 10,
                "sortBy": "relevance",
                "sortOrder": "descending",
            })
            if resp.status_code == 200:
                all_results.extend(_parse_arxiv(resp.text))
        except Exception as exc:
            logger.warning("arxiv_search_failed", error=str(exc))

    deduped = _dedupe(all_results)
    return DiscoverResponse(results=deduped[:20], query=q)
