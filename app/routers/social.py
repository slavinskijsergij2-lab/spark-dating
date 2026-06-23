from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.auth import get_current_user
from app.csrf import validate_csrf_header
from app.database import get_db
from app.i18n import get_lang, get_translations, is_rtl
from app.models.models import Block, Report, User
from app.rate_limit import rate_limit
from app.templates import templates

router = APIRouter()

REPORT_REASONS = ["spam", "fake", "harassment", "inappropriate", "other"]


@router.post("/user/{target_id}/block", dependencies=[Depends(rate_limit(20, 60)), Depends(validate_csrf_header)])
async def block_user(target_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if target_id == user.id:
        return JSONResponse({"error": "Cannot block yourself"}, status_code=400)
    result = await db.execute(select(User).where(User.id == target_id))
    target = result.scalar_one_or_none()
    if not target:
        return JSONResponse({"error": "User not found"}, status_code=404)
    result = await db.execute(
        select(Block).where(Block.blocker_id == user.id, Block.blocked_id == target_id)
    )
    if result.scalar_one_or_none():
        return JSONResponse({"success": True, "already": True})
    db.add(Block(blocker_id=user.id, blocked_id=target_id))
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
    return JSONResponse({"success": True})


@router.post("/user/{target_id}/unblock", dependencies=[Depends(rate_limit(20, 60)), Depends(validate_csrf_header)])
async def unblock_user(target_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Block).where(Block.blocker_id == user.id, Block.blocked_id == target_id)
    )
    block = result.scalar_one_or_none()
    if block:
        await db.delete(block)
        await db.commit()
    return JSONResponse({"success": True})


@router.post("/user/{target_id}/report", dependencies=[Depends(rate_limit(5, 300)), Depends(validate_csrf_header)])
async def report_user(
    target_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if target_id == user.id:
        return JSONResponse({"error": "Cannot report yourself"}, status_code=400)
    result = await db.execute(
        select(User).where(User.id == target_id, User.is_active == True)
    )
    target = result.scalar_one_or_none()
    if not target:
        return JSONResponse({"error": "User not found"}, status_code=404)
    data = await request.json()
    reason = data.get("reason", "other")
    comment = (data.get("comment") or "")[:500]
    if reason not in REPORT_REASONS:
        reason = "other"

    result = await db.execute(
        select(Report).where(Report.reporter_id == user.id, Report.reported_id == target_id)
    )
    if result.scalar_one_or_none():
        return JSONResponse({"success": True, "already": True})

    db.add(Report(reporter_id=user.id, reported_id=target_id, reason=reason, comment=comment))
    result = await db.execute(
        select(Block).where(Block.blocker_id == user.id, Block.blocked_id == target_id)
    )
    if not result.scalar_one_or_none():
        db.add(Block(blocker_id=user.id, blocked_id=target_id))
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return JSONResponse({"error": "Already reported"}, status_code=409)
    return JSONResponse({"success": True})


@router.get("/settings/blocks", response_class=HTMLResponse)
async def blocked_list(request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    lang = get_lang(request, user)
    result = await db.execute(
        select(Block).where(Block.blocker_id == user.id).order_by(Block.created_at.desc())
    )
    block_records = result.scalars().all()
    blocked_ids = [b.blocked_id for b in block_records]
    if blocked_ids:
        result = await db.execute(
            select(User).options(joinedload(User.profile)).where(User.id.in_(blocked_ids))
        )
        blocked_users = {u.id: u for u in result.scalars().unique().all()}
    else:
        blocked_users = {}
    items = [(b, blocked_users.get(b.blocked_id)) for b in block_records if blocked_users.get(b.blocked_id)]
    return templates.TemplateResponse(request, "settings_blocks.html", {
        "user": user,
        "items": items,
        "t": get_translations(lang),
        "rtl": is_rtl(lang),
        "lang": lang,
    })
