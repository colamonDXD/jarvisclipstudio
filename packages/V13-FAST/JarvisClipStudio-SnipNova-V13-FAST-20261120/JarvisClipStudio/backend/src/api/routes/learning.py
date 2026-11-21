"""
Learning engine API routes (status, rollback).

The control surface for the per-user preference memory built by
``auto_director.learning``: what has been learned, from how much evidence,
and the append-only version history. Recording feedback itself lives on
the clip routes (POST /tasks/{task_id}/clips/{clip_id}/feedback).
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_headers import resolve_authenticated_user_id
from ...config import get_config
from ...database import get_db
from ...services.learning_service import (
    learning_status,
    rollback_learning as rollback_learning_document,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/learning", tags=["learning"])


async def _get_authenticated_user_id(request: Request, db: AsyncSession) -> str:
    config = get_config()
    return await resolve_authenticated_user_id(request, db, config)


@router.get("/status")
async def get_learning_status(
    request: Request, db: AsyncSession = Depends(get_db)
):
    """Current learning version, per-niche adjustments, evidence, history."""
    user_id = await _get_authenticated_user_id(request, db)
    try:
        return await learning_status(db, user_id)
    except Exception as e:
        logger.error(f"Error reading learning status: {e}")
        raise HTTPException(
            status_code=500, detail=f"Error reading learning status: {str(e)}"
        )


@router.post("/rollback")
async def rollback_learning(
    request: Request, db: AsyncSession = Depends(get_db)
):
    """Restore a prior version's adjustments as a NEW version.

    History is append-only: the rollback itself becomes the latest
    version, pointing back at the target's adjustments.
    """
    user_id = await _get_authenticated_user_id(request, db)
    try:
        payload = await request.json()
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Request body must be an object")
    version = payload.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise HTTPException(
            status_code=422, detail="version must be a positive integer"
        )
    try:
        document = await rollback_learning_document(db, user_id, version)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error rolling back learning: {e}")
        raise HTTPException(
            status_code=500, detail=f"Error rolling back learning: {str(e)}"
        )
    return {
        "version": document["version"],
        "rolled_back_to": version,
        "adjustments": document["niches"],
        "summary": document["summary"],
    }
