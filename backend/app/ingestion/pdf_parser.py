"""
PDF parsing using PyMuPDF with optional Nougat fallback for complex layouts.
Confidence is scored by checking text coverage and structure quality.
"""
from __future__ import annotations
import re
import structlog
import fitz  # PyMuPDF

logger = structlog.get_logger()

# Sections that typically appear in academic papers
SECTION_PATTERNS = [
    r"^abstract$",
    r"^introduction$",
    r"^related work",
    r"^background",
    r"^method",
    r"^approach",
    r"^experiment",
    r"^result",
    r"^discussion",
    r"^conclusion",
    r"^reference",
    r"^appendix",
    r"^\d+\.?\s+\w+",  # numbered sections
]
_SECTION_RE = re.compile("|".join(SECTION_PATTERNS), re.IGNORECASE)


def parse_pdf(pdf_path: str) -> dict:
    """
    Parse a PDF and extract structured content.
    Returns a dict with: title, abstract, sections, chunks_raw, page_count, confidence.
    """
    doc = fitz.open(pdf_path)
    page_count = len(doc)

    blocks_by_page: list[list[dict]] = []
    total_text_len = 0

    for page_num, page in enumerate(doc):
        blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
        page_blocks = []
        for block in blocks:
            if block["type"] == 0:  # text block
                text = " ".join(
                    span["text"]
                    for line in block.get("lines", [])
                    for span in line.get("spans", [])
                ).strip()
                if not text:
                    continue
                bbox = block["bbox"]
                page_blocks.append({
                    "text": text,
                    "page": page_num + 1,
                    "bbox": {"page": page_num + 1, "x0": bbox[0], "y0": bbox[1], "x1": bbox[2], "y1": bbox[3]},
                    "font_size": _dominant_font_size(block),
                })
                total_text_len += len(text)
            elif block["type"] == 1:  # image block
                bbox = block["bbox"]
                page_blocks.append({
                    "text": "[FIGURE]",
                    "page": page_num + 1,
                    "bbox": {"page": page_num + 1, "x0": bbox[0], "y0": bbox[1], "x1": bbox[2], "y1": bbox[3]},
                    "content_type": "figure",
                    "font_size": 0,
                })
        blocks_by_page.append(page_blocks)

    # Confidence: based on character density and presence of section headers
    avg_chars_per_page = total_text_len / max(page_count, 1)
    sections_found = _detect_sections(blocks_by_page)
    confidence = _compute_confidence(avg_chars_per_page, sections_found)

    # Extract structured content
    title = _extract_title(blocks_by_page)
    abstract = _extract_abstract(blocks_by_page)
    raw_chunks = _build_raw_chunks(blocks_by_page, sections_found)

    doc.close()
    return {
        "title": title,
        "abstract": abstract,
        "section_headers": [s["title"] for s in sections_found],
        "page_count": page_count,
        "confidence": confidence,
        "raw_chunks": raw_chunks,
    }


def _dominant_font_size(block: dict) -> float:
    sizes = [
        span["size"]
        for line in block.get("lines", [])
        for span in line.get("spans", [])
    ]
    return max(sizes) if sizes else 0


def _detect_sections(blocks_by_page: list[list[dict]]) -> list[dict]:
    sections = []
    for page_blocks in blocks_by_page:
        for block in page_blocks:
            text = block["text"].strip()
            if _SECTION_RE.match(text) and len(text) < 120:
                sections.append({
                    "title": text,
                    "page": block["page"],
                    "bbox": block.get("bbox"),
                })
    return sections


def _compute_confidence(avg_chars: float, sections: list[dict]) -> float:
    score = 0.0
    # Dense text is a good sign
    if avg_chars > 500:
        score += 0.4
    elif avg_chars > 200:
        score += 0.2
    # Having recognizable section headers is a good sign
    if len(sections) >= 3:
        score += 0.4
    elif len(sections) >= 1:
        score += 0.2
    # Base quality
    score += 0.2
    return round(min(score, 1.0), 2)


def _extract_title(blocks_by_page: list[list[dict]]) -> str | None:
    if not blocks_by_page:
        return None
    first_page = blocks_by_page[0]
    if not first_page:
        return None
    # Heuristic: largest font-size text on first page, not too short/long
    candidates = sorted(first_page, key=lambda b: b.get("font_size", 0), reverse=True)
    for c in candidates:
        text = c["text"].strip()
        if 10 < len(text) < 300:
            return text
    return None


def _extract_abstract(blocks_by_page: list[list[dict]]) -> str | None:
    """Find the abstract block by looking for 'Abstract' header or long first-page paragraph."""
    full_text = []
    for page_blocks in blocks_by_page[:3]:  # abstract is usually in first 3 pages
        for block in page_blocks:
            text = block["text"].strip()
            if re.match(r"^abstract", text, re.IGNORECASE) and len(text) > 50:
                # The abstract might be in the same block
                return text
            full_text.append(text)

    # Fallback: look for next long paragraph after an 'Abstract' label
    joined = "\n".join(full_text)
    m = re.search(r"abstract[\s\n]+(.{100,1500}?)(?:\n\n|\Z)", joined, re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1).strip()
    return None


def _build_raw_chunks(
    blocks_by_page: list[list[dict]],
    sections: list[dict],
) -> list[dict]:
    """
    Build raw text chunks with metadata.
    Each chunk is bounded by section boundaries.
    """
    # Build a map: page -> section title (most recent section up to this page)
    section_map: dict[int, str] = {}
    current_section = "Preamble"
    for page_blocks in blocks_by_page:
        for block in page_blocks:
            text = block["text"].strip()
            if _SECTION_RE.match(text) and len(text) < 120:
                current_section = text
            section_map[block["page"]] = current_section

    chunks: list[dict] = []
    current_section_title = "Preamble"
    current_texts: list[str] = []
    current_page = 1
    current_bbox: dict | None = None
    content_type = "text"

    for page_blocks in blocks_by_page:
        for block in page_blocks:
            text = block["text"].strip()
            page = block["page"]
            bbox = block.get("bbox")
            btype = block.get("content_type", "text")

            # Detect section boundary
            if _SECTION_RE.match(text) and len(text) < 120:
                if current_texts:
                    chunks.append({
                        "content": " ".join(current_texts),
                        "section_title": current_section_title,
                        "page_number": current_page,
                        "content_type": content_type,
                        "bbox": current_bbox,
                    })
                    current_texts = []
                current_section_title = text
                current_page = page
                current_bbox = bbox
                content_type = "text"
                continue

            if btype == "figure":
                # Emit figures as their own chunk
                chunks.append({
                    "content": text,
                    "section_title": current_section_title,
                    "page_number": page,
                    "content_type": "figure",
                    "bbox": bbox,
                })
                continue

            current_texts.append(text)
            if current_bbox is None:
                current_bbox = bbox

    if current_texts:
        chunks.append({
            "content": " ".join(current_texts),
            "section_title": current_section_title,
            "page_number": current_page,
            "content_type": content_type,
            "bbox": current_bbox,
        })

    return chunks


# ── Nougat fallback ────────────────────────────────────────────────────────

def parse_pdf_nougat(pdf_path: str) -> dict:
    """
    Fallback parser using Nougat for complex/multi-column layouts.
    Nougat is imported lazily to avoid loading the heavy model unless needed.
    """
    try:
        # nougat-ocr package: https://github.com/facebookresearch/nougat
        from nougat import NougatModel
        from nougat.utils.dataset import LazyDataset
        import torch
        from pathlib import Path

        logger.info("nougat_fallback_start", pdf_path=pdf_path)
        model = NougatModel.from_pretrained("facebook/nougat-base")
        model.eval()

        dataset = LazyDataset(pdf_path, partial(model.encoder.prepare_input, random_padding=False))
        dataloader = torch.utils.data.DataLoader(dataset, batch_size=1, shuffle=False)

        pages_text = []
        for sample, _ in dataloader:
            output = model.inference(image_tensors=sample)
            pages_text.append(output["predictions"][0])

        full_text = "\n\n".join(pages_text)
        return _nougat_to_chunks(full_text, pdf_path)
    except ImportError:
        logger.warning("nougat_not_installed", pdf_path=pdf_path)
        return parse_pdf(pdf_path)
    except Exception as exc:
        logger.error("nougat_failed", pdf_path=pdf_path, error=str(exc))
        return parse_pdf(pdf_path)


def _nougat_to_chunks(full_text: str, pdf_path: str) -> dict:
    """Convert Nougat markdown output to the same chunk format as parse_pdf."""
    # Use PyMuPDF just for page count
    doc = fitz.open(pdf_path)
    page_count = len(doc)
    doc.close()

    # Split by markdown headers
    sections = re.split(r"\n(#{1,4} .+)\n", full_text)
    chunks = []
    current_section = "Preamble"

    for part in sections:
        if re.match(r"#{1,4} ", part):
            current_section = part.lstrip("# ").strip()
        elif part.strip():
            chunks.append({
                "content": part.strip(),
                "section_title": current_section,
                "page_number": None,
                "content_type": "text",
                "bbox": None,
            })

    title = _extract_title_from_text(full_text)
    abstract = _extract_abstract_from_text(full_text)
    section_headers = list({c["section_title"] for c in chunks if c["section_title"]})

    return {
        "title": title,
        "abstract": abstract,
        "section_headers": section_headers,
        "page_count": page_count,
        "confidence": 0.9,  # Nougat output is assumed high quality
        "raw_chunks": chunks,
    }


def _extract_title_from_text(text: str) -> str | None:
    m = re.search(r"^# (.+)$", text, re.MULTILINE)
    return m.group(1).strip() if m else None


def _extract_abstract_from_text(text: str) -> str | None:
    m = re.search(r"abstract[\s\n]+(.{100,1500}?)(?:\n\n|\Z)", text, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else None
