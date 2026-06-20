from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

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
def block_user(target_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if target_id == user.id:
        return JSONResponse({"error": "Cannot block yourself"}, status_code=400)
    target = db.query(User).filter(User.id == target_id).first()
    if not target:
        return JSONResponse({"error": "User not found"}, status_code=404)
    if db.query(Block).filter(Block.blocker_id == user.id, Block.blocked_id == target_id).first():
        return JSONResponse({"success": True, "already": True})
    db.add(Block(blocker_id=user.id, blocked_id=target_id))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
    return JSONResponse({"success": True})


@router.post("/user/{target_id}/unblock", dependencies=[Depends(validate_csrf_header)])
def unblock_user(target_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    block = db.query(Block).filter(Block.blocker_id == user.id, Block.blocked_id == target_id).first()
    if block:
        db.delete(block)
        db.commit()
    return JSONResponse({"success": True})


@router.post("/user/{target_id}/report", dependencies=[Depends(rate_limit(5, 300)), Depends(validate_csrf_header)])
async def report_user(
    target_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if target_id == user.id:
        return JSONResponse({"error": "Cannot report yourself"}, status_code=400)
    # H3: verify target exists before inserting FK-constrained rows
    target = db.query(User).filter(User.id == target_id, User.is_active == True).first()
    if not target:
        return JSONResponse({"error": "User not found"}, status_code=404)
    data = await request.json()
    reason = data.get("reason", "other")
    comment = (data.get("comment") or "")[:500]
    if reason not in REPORT_REASONS:
        reason = "other"

    existing = db.query(Report).filter(
        Report.reporter_id == user.id, Report.reported_id == target_id
    ).first()
    if existing:
        return JSONResponse({"success": True, "already": True})

    db.add(Report(reporter_id=user.id, reported_id=target_id, reason=reason, comment=comment))
    # Auto-block after report
    if not db.query(Block).filter(Block.blocker_id == user.id, Block.blocked_id == target_id).first():
        db.add(Block(blocker_id=user.id, blocked_id=target_id))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return JSONResponse({"error": "Already reported"}, status_code=409)
    return JSONResponse({"success": True})


@router.get("/settings/blocks", response_class=HTMLResponse)
def blocked_list(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    lang = get_lang(request, user)
    block_records = db.query(Block).filter(Block.blocker_id == user.id).order_by(Block.created_at.desc()).all()
    blocked_ids = [b.blocked_id for b in block_records]
    blocked_users = {u.id: u for u in db.query(User).options(joinedload(User.profile)).filter(User.id.in_(blocked_ids)).all()} if blocked_ids else {}
    items = [(b, blocked_users.get(b.blocked_id)) for b in block_records if blocked_users.get(b.blocked_id)]
    return templates.TemplateResponse("settings_blocks.html", {
        "request": request,
        "user": user,
        "items": items,
        "t": get_translations(lang),
        "rtl": is_rtl(lang),
        "lang": lang,
    })
