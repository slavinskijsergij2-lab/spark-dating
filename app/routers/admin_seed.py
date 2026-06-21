"""Temporary admin endpoints. Remove after use."""
import os
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.models import Like, User, Profile
from app.utils.time import utcnow as _utcnow

router = APIRouter()
_SEED_KEY = os.getenv("SEED_KEY", "spark-seed-2024")

BOT_EMAILS = [
    "anna_bot@spark.test", "masha_bot@spark.test", "kate_bot@spark.test",
    "sofia_bot@spark.test", "alina_bot@spark.test", "dmitry_bot@spark.test",
    "alex_bot@spark.test", "max_bot@spark.test",
]


@router.get("/admin/bot-likes")
def bot_likes(key: str = "", db: Session = Depends(get_db)):
    """Make all bots like all real users + clear swipe history between them."""
    if key != _SEED_KEY:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    bots = db.query(User).filter(User.email.in_(BOT_EMAILS)).all()
    bot_ids = {b.id for b in bots}

    real_users = (
        db.query(User)
        .join(Profile, Profile.user_id == User.id)
        .filter(User.is_active == True)
        .filter(~User.email.in_(BOT_EMAILS))
        .all()
    )

    deleted = 0
    created = 0

    for real in real_users:
        # Remove all existing swipes between real user <-> bots (fresh start)
        deleted += db.query(Like).filter(
            Like.liker_id == real.id,
            Like.liked_id.in_(bot_ids),
        ).delete(synchronize_session=False)
        deleted += db.query(Like).filter(
            Like.liker_id.in_(bot_ids),
            Like.liked_id == real.id,
        ).delete(synchronize_session=False)
        db.flush()

        # Each bot likes the real user
        for bot in bots:
            like = Like(
                liker_id=bot.id,
                liked_id=real.id,
                is_like=True,
                is_super=False,
            )
            db.add(like)
            created += 1

    db.commit()
    return JSONResponse({
        "bots": len(bots),
        "real_users": len(real_users),
        "likes_cleared": deleted,
        "likes_created": created,
        "msg": "Now swipe right on any bot to instantly get a match!",
    })
