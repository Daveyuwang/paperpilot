"""
Paper full-text fetch tool — downloads and extracts text from arXiv PDFs.
"""
from __future__ import annotations

import io
import structlog
import httpx
from langchain_core.tools import tool

logger = structlog.get_logger()


@tool
async def fetch_paper_fulltext(arxiv_id: str = "", pdf_url: str = "") -> dict:
    """Download a paper PDF and extract a text preview (first ~3000 chars).

    Use this when the user wants to preview a paper's content before adding it
    to their workspace, or to check if a discovered source is relevant.

    Args:
        arxiv_id: arXiv paper ID (e.g., '2301.07041'). Will construct the PDF URL automatically.
        pdf_url: Direct PDF URL. Used if arxiv_id is not provided.
    """
    if not arxiv_id and not pdf_url:
        return {"error": "Provide either arxiv_id or pdf_url."}

    url = f"https://arxiv.org/pdf/{arxiv_id}.pdf" if arxiv_id else pdf_url

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return {"error": f"Failed to download PDF (status {resp.status_code})."}

            content_type = resp.headers.get("content-type", "")
            if "pdf" not in content_type and not resp.content[:5] == b"%PDF-":
                return {"error": "Response is not a PDF."}

            # Extract text with PyMuPDF
            import fitz
            pdf_bytes = resp.content
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")

            text_parts = []
            char_count = 0
            for page in doc:
                page_text = page.get_text()
                text_parts.append(page_text)
                char_count += len(page_text)
                if char_count > 4000:
                    break
            doc.close()

            full_text = "\n".join(text_parts)[:3000]
            return {
                "source": url,
                "pages_extracted": min(len(text_parts), doc.page_count if 'doc' in dir() else len(text_parts)),
                "text_preview": full_text,
            }

    except Exception as exc:
        logger.warning("paper_fetch_failed", url=url, error=str(exc))
        return {"error": f"Failed to fetch paper: {str(exc)}"}
