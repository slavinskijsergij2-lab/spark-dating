import os
import secrets

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.models import ErrorLog, Match, Message, Profile, ProfilePhoto, Report, User
from app.rate_limit import rate_limit
from app.templates import templates
from app.auth import invalidate_user_cache
from app.utils.time import utcnow as _utcnow

router = APIRouter(prefix="/admin")

_ADMIN_KEY = os.getenv("ADMIN_KEY", "")

# Pagination constant for admin panel
_ADMIN_PAGE_SIZE = 100


def _check_admin(request: Request) -> None:
    key = request.cookies.get("admin_key") or request.query_params.get("key", "")
    # FIX High #4: constant-time comparison to prevent admin key timing attack.
    if not _ADMIN_KEY or not secrets.compare_digest(key, _ADMIN_KEY):
        raise HTTPException(403, "Forbidden")


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def admin_login_page(request: Request):
    return HTMLResponse("""
<html><head><title>Admin Login</title>
<style>body{font-family:sans-serif;display:flex;justify-content:center;align-items:center;min-height:100vh;background:#fdf2f8;margin:0}
.box{background:#fff;padding:2rem;border-radius:16px;box-shadow:0 4px 20px #ec489933;width:320px}
h2{color:#ec4899;margin-bottom:1.5rem;text-align:center}
input{width:100%;padding:.75rem 1rem;border:1px solid #e5e7eb;border-radius:8px;font-size:1rem;margin-bottom:1rem;box-sizing:border-box}
button{width:100%;background:#ec4899;color:#fff;border:none;padding:.75rem;border-radius:8px;font-size:1rem;cursor:pointer}
button:hover{opacity:.9}</style></head>
<body><div class="box"><h2>Spark Admin</h2>
<form method="POST" action="/admin/login">
<input type="password" name="key" placeholder="Admin key" required>
<button type="submit">Войти</button>
</form></div></body></html>
""")


@router.post("/login", include_in_schema=False, dependencies=[Depends(rate_limit(5, 60))])
async def admin_login(key: str = Form(...)):
    # FIX High #4: constant-time comparison on login too.
    if not _ADMIN_KEY or not secrets.compare_digest(key, _ADMIN_KEY):
        return RedirectResponse("/admin/login", status_code=302)
    resp = RedirectResponse("/admin", status_code=302)
    resp.set_cookie("admin_key", key, httponly=True, samesite="strict", max_age=86400 * 7)
    return resp


@router.get("", response_class=HTMLResponse, include_in_schema=False)
async def admin_panel(
    request: Request,
    page: int = Query(default=1, ge=1),
    db: AsyncSession = Depends(get_db),
):
    _check_admin(request)

    # FIX Medium #19: add LIMIT/OFFSET pagination to avoid loading all users into memory.
    offset = (page - 1) * _ADMIN_PAGE_SIZE
    result = await db.execute(
        select(User)
        .options(selectinload(User.profile).selectinload(Profile.photos))
        .where(User.is_active == True)
        .order_by(User.created_at.desc())
        .limit(_ADMIN_PAGE_SIZE)
        .offset(offset)
    )
    users = result.scalars().all()

    total_active = (await db.execute(
        select(func.count(User.id)).where(User.is_active == True)
    )).scalar() or 0

    result2 = await db.execute(
        select(User)
        .options(selectinload(User.profile))
        .where(User.is_active == False)
        .order_by(User.created_at.desc())
        .limit(_ADMIN_PAGE_SIZE)
    )
    banned = result2.scalars().all()

    total_matches = (await db.execute(select(func.count(Match.id)))).scalar() or 0
    total_messages = (await db.execute(select(func.count(Message.id)))).scalar() or 0

    err_result = await db.execute(
        select(ErrorLog).order_by(desc(ErrorLog.ts)).limit(20)
    )
    error_logs = err_result.scalars().all()

    # Pending reports (with reporter and reported user info)
    reports_result = await db.execute(
        select(Report)
        .where(Report.status == "pending")
        .order_by(Report.created_at.desc())
        .limit(50)
    )
    pending_reports = reports_result.scalars().all()

    reporter_ids = {r.reporter_id for r in pending_reports}
    reported_ids = {r.reported_id for r in pending_reports}
    all_report_user_ids = reporter_ids | reported_ids
    report_users: dict[int, User] = {}
    if all_report_user_ids:
        ru_result = await db.execute(
            select(User).options(selectinload(User.profile)).where(User.id.in_(all_report_user_ids))
        )
        report_users = {u.id: u for u in ru_result.scalars().all()}

    return templates.TemplateResponse(request, "admin.html", {
        "users": users,
        "banned": banned,
        "total_matches": total_matches,
        "total_messages": total_messages,
        "error_logs": error_logs,
        "pending_reports": pending_reports,
        "report_users": report_users,
        "page": page,
        "total_active": total_active,
        "page_size": _ADMIN_PAGE_SIZE,
        "has_next": total_active > page * _ADMIN_PAGE_SIZE,
    })


@router.post("/photo/delete/{photo_id}", include_in_schema=False)
async def admin_delete_gallery_photo(
    photo_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    # FIX High #3: admin POST endpoints now verify the admin cookie via _check_admin.
    # The cookie is httponly+samesite=strict so CSRF via a third-party page is blocked.
    _check_admin(request)
    result = await db.execute(select(ProfilePhoto).where(ProfilePhoto.id == photo_id))
    photo = result.scalar_one_or_none()
    if photo:
        from app.utils.photos import remove_photo_file
        remove_photo_file(photo.url)
        await db.delete(photo)
        await db.commit()
    return RedirectResponse("/admin", status_code=302)


@router.post("/photo/clear/{user_id}", include_in_schema=False)
async def admin_clear_main_photo(
    user_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _check_admin(request)
    result = await db.execute(select(Profile).where(Profile.user_id == user_id))
    profile = result.scalar_one_or_none()
    if profile and profile.photo:
        from app.utils.photos import remove_photo_file
        remove_photo_file(profile.photo)
        profile.photo = None
        await db.commit()
    return RedirectResponse("/admin", status_code=302)


@router.post("/ban/{user_id}", include_in_schema=False)
async def admin_ban_user(
    user_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _check_admin(request)
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user:
        user.is_active = False
        await db.commit()
        invalidate_user_cache(user_id)
    return RedirectResponse("/admin", status_code=302)


@router.post("/unban/{user_id}", include_in_schema=False)
async def admin_unban_user(
    user_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _check_admin(request)
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user:
        user.is_active = True
        await db.commit()
    return RedirectResponse("/admin", status_code=302)


@router.post("/report/review/{report_id}", include_in_schema=False)
async def admin_review_report(
    report_id: int,
    request: Request,
    action: str = Form(...),  # "reviewed" | "dismissed" | "ban"
    note: str = Form(default=""),
    db: AsyncSession = Depends(get_db),
):
    _check_admin(request)
    if action not in ("reviewed", "dismissed", "ban"):
        return RedirectResponse("/admin#reports", status_code=302)

    result = await db.execute(select(Report).where(Report.id == report_id))
    report = result.scalar_one_or_none()
    if report:
        report.status = "reviewed" if action in ("reviewed", "ban") else "dismissed"
        report.admin_note = note.strip()[:300] if note.strip() else None
        report.reviewed_at = _utcnow()
        await db.commit()

        if action == "ban":
            result2 = await db.execute(select(User).where(User.id == report.reported_id))
            reported_user = result2.scalar_one_or_none()
            if reported_user:
                reported_user.is_active = False
                await db.commit()
                invalidate_user_cache(report.reported_id)

    return RedirectResponse("/admin#reports", status_code=302)
