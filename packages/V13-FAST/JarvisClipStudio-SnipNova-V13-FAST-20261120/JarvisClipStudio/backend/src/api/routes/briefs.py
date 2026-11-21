"""Paste-a-brief extraction endpoints (Content Requirements, Addendum A.4).

POST /briefs/extract turns raw campaign text into a DRAFT v2 brief for
the user to review and edit in the Create panel. POST
/briefs/extract-image does the same from an uploaded brief SCREENSHOT:
the image is OCR'd locally (tesseract, see
``auto_director.brief_image``), the recognised text is returned with
the draft so the user reviews exactly what the machine read, and the
text feeds the same extraction path. The endpoints persist NOTHING —
no task, no campaign, no brief row — and never apply the draft. Only a
brief the user confirms through the normal create-task payload is ever
stored. Extraction is labelled honestly: ``llm`` when the
(denylist-guarded) model produced a schema-valid draft,
``deterministic`` when the rule-based extractor ran or the model output
failed validation.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Tuple

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_headers import resolve_authenticated_user_id
from ...auto_director.brief_image import (
    BRIEF_IMAGE_EXTENSIONS,
    MAX_BRIEF_IMAGE_BYTES,
    ocr_image_bytes,
)
from ...auto_director.content_requirements import (
    extract_brief_deterministically,
    normalize_brief_v2,
)
from ...auto_director.content_requirements_llm import (
    extract_brief_with_llm,
    is_banned_model,
    resolved_focus_model,
)
from ...config import get_config
from ...database import get_db
from ...utils.async_helpers import run_in_thread

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/briefs", tags=["briefs"])

MAX_BRIEF_TEXT_CHARS = 20000


async def _build_draft(brief_text: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Shared extraction: (draft, extraction-metadata). Persists nothing."""
    warnings: list[str] = []
    model = resolved_focus_model()
    draft: Dict[str, Any] | None = None
    source = "deterministic"
    if model is not None and not is_banned_model(model):
        raw = await extract_brief_with_llm(brief_text)
        if raw is not None:
            try:
                draft = normalize_brief_v2(raw)
                source = "llm"
            except ValueError as exc:
                warnings.append(
                    f"Model extraction failed validation ({exc}); "
                    "basic extraction was used instead"
                )
                draft = None
        else:
            warnings.append("Model extraction was unavailable; basic extraction was used")
    else:
        warnings.append("No permitted model is configured; basic extraction was used")
    if draft is None:
        draft = extract_brief_deterministically(brief_text)
        source = "deterministic"
    return draft, {
        "source": source,
        "model": model if source == "llm" else None,
        "warnings": warnings,
    }


@router.post("/extract")
async def extract_brief(request: Request, db: AsyncSession = Depends(get_db)):
    """Extract a draft brief from pasted campaign text. Persists nothing."""
    await resolve_authenticated_user_id(request, db, get_config())
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Request body must be an object")
    brief_text = payload.get("brief_text")
    if not isinstance(brief_text, str) or not brief_text.strip():
        raise HTTPException(status_code=400, detail="brief_text is required")
    if len(brief_text) > MAX_BRIEF_TEXT_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"brief_text is limited to {MAX_BRIEF_TEXT_CHARS} characters",
        )

    draft, extraction = await _build_draft(brief_text)
    return {"content_brief": draft, "extraction": extraction}


@router.post("/extract-image")
async def extract_brief_image(
    request: Request,
    db: AsyncSession = Depends(get_db),
    file: UploadFile = File(...),
):
    """Extract a draft brief from an uploaded brief screenshot.

    OCR runs locally; the recognised text rides along in the response
    (``brief_text``) so the review shows what the machine actually read.
    Persists nothing, exactly like /extract.
    """
    await resolve_authenticated_user_id(request, db, get_config())
    extension = Path(file.filename or "").suffix.lower()
    if extension not in BRIEF_IMAGE_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported brief image type. Use one of: "
                + ", ".join(sorted(BRIEF_IMAGE_EXTENSIONS))
            ),
        )
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="The uploaded image is empty")
    if len(data) > MAX_BRIEF_IMAGE_BYTES:
        raise HTTPException(
            status_code=413, detail="Brief image exceeds the 10 MB limit"
        )
    text, available = await run_in_thread(ocr_image_bytes, data)
    if not available:
        raise HTTPException(
            status_code=503,
            detail="Text recognition (OCR) is unavailable on this server",
        )
    if text is None or len(text.strip()) < 20:
        raise HTTPException(
            status_code=422,
            detail="No readable text was found in that image",
        )
    if len(text) > MAX_BRIEF_TEXT_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"Recognised text is limited to {MAX_BRIEF_TEXT_CHARS} characters",
        )
    draft, extraction = await _build_draft(text)
    return {
        "content_brief": draft,
        "extraction": extraction,
        "brief_text": text,
        "ocr": {"characters": len(text)},
    }
