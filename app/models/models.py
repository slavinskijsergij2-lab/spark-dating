from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey, Enum, Float, Index, UniqueConstraint, CheckConstraint
from sqlalchemy.orm import relationship
import enum

from app.database import Base
from app.utils.time import utcnow as _utcnow


class GenderEnum(str, enum.Enum):
    male = "male"
    female = "female"
    other = "other"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    language = Column(String(10), nullable=True, default="ru")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=_utcnow)

    # Function 3: Politeness rating
    politeness_score = Column(Float, nullable=True, default=5.0)
    politeness_votes = Column(Integer, nullable=True, default=0)

    # Function 7: Photo verification
    is_verified = Column(Boolean, nullable=True, default=False)
    verify_gesture = Column(String(50), nullable=True)

    # Online status
    last_seen = Column(DateTime, nullable=True, index=True)

    # Email verification
    email_verified = Column(Boolean, default=False, nullable=False)
    email_verify_token = Column(String(100), nullable=True, index=True)
    email_verify_created_at = Column(DateTime, nullable=True)

    # Premium & boost
    is_premium = Column(Boolean, default=False, nullable=False)
    boost_until = Column(DateTime, nullable=True)
    premium_until = Column(DateTime, nullable=True)  # time-limited bonus from referrals

    # Referral system
    referral_code = Column(String(20), unique=True, nullable=True, index=True)
    referred_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Zodiac & phone
    birth_date = Column(DateTime, nullable=True)
    phone = Column(String(20), nullable=True)
    phone_verified = Column(Boolean, default=False, nullable=False)

    # Token version — increment on password change to invalidate old JWTs
    token_version = Column(Integer, default=0, nullable=False)

    @property
    def is_premium_active(self) -> bool:
        """True if user has permanent premium OR active timed referral bonus."""
        if self.is_premium:
            return True
        if self.premium_until:
            return self.premium_until > _utcnow()
        return False

    profile = relationship("Profile", back_populates="user", uselist=False, cascade="all, delete-orphan")
    likes_given = relationship("Like", foreign_keys="Like.liker_id", back_populates="liker", cascade="all, delete-orphan")
    likes_received = relationship("Like", foreign_keys="Like.liked_id", back_populates="liked", cascade="all, delete-orphan")
    messages_sent = relationship("Message", foreign_keys="Message.sender_id", back_populates="sender")


class Profile(Base):
    __tablename__ = "profiles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    name = Column(String(100), nullable=False)
    age = Column(Integer, nullable=False)
    gender = Column(Enum(GenderEnum), nullable=False)
    looking_for = Column(Enum(GenderEnum), nullable=True)
    city = Column(String(100), nullable=True)
    bio = Column(Text, nullable=True)
    photo = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    # Function 5: Intention
    intention = Column(String(20), nullable=True)

    # Interests & anonymous mode
    interests = Column(String(500), nullable=True)   # comma-separated tags
    is_anonymous = Column(Boolean, default=False, nullable=False)

    user = relationship("User", back_populates="profile")
    photos = relationship("ProfilePhoto", back_populates="profile", cascade="all, delete-orphan", order_by="ProfilePhoto.position")

    __table_args__ = (
        # Composite index covering swipe candidate filter: gender + age + intention
        Index("ix_profile_swipe", "gender", "age", "intention"),
    )


class Like(Base):
    __tablename__ = "likes"

    id = Column(Integer, primary_key=True, index=True)
    liker_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    liked_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    is_like = Column(Boolean, nullable=False)  # True = like, False = dislike
    is_super = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=_utcnow)

    liker = relationship("User", foreign_keys=[liker_id], back_populates="likes_given")
    liked = relationship("User", foreign_keys=[liked_id], back_populates="likes_received")

    __table_args__ = (
        UniqueConstraint("liker_id", "liked_id", name="uq_like_pair"),
        Index("ix_like_liker_created", "liker_id", "created_at"),
        Index("ix_like_liked_is_like", "liked_id", "is_like"),  # "who liked me" query
    )


class Match(Base):
    __tablename__ = "matches"

    id = Column(Integer, primary_key=True, index=True)
    user1_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    user2_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime, default=_utcnow)

    # Notification: who has seen this match
    seen_by_user1 = Column(Boolean, default=False, nullable=False)
    seen_by_user2 = Column(Boolean, default=False, nullable=False)

    # Streak
    streak_days = Column(Integer, default=0, nullable=False)
    last_streak_date = Column(DateTime, nullable=True)

    # Anonymous mode: True = revealed (default), False = hidden
    user1_revealed = Column(Boolean, default=True, nullable=False)
    user2_revealed = Column(Boolean, default=True, nullable=False)

    user1 = relationship("User", foreign_keys=[user1_id])
    user2 = relationship("User", foreign_keys=[user2_id])
    messages = relationship("Message", back_populates="match", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("user1_id", "user2_id", name="uq_match_pair"),
        # Enforce that user1_id < user2_id so (A,B) and (B,A) can't both exist
        CheckConstraint("user1_id < user2_id", name="ck_match_user_order"),
    )


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    match_id = Column(Integer, ForeignKey("matches.id", ondelete="CASCADE"), nullable=False, index=True)
    sender_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=_utcnow)
    is_read = Column(Boolean, default=False)
    is_voice = Column(Boolean, default=False, nullable=False)

    match = relationship("Match", back_populates="messages")
    sender = relationship("User", foreign_keys=[sender_id], back_populates="messages_sent")

    __table_args__ = (
        # Covers: WHERE match_id=X AND id>Y ORDER BY created_at  (SSE + polling)
        Index("ix_message_match_id", "match_id", "id", "created_at"),
        # Covers: unread count query — WHERE match_id=X AND sender_id!=Y AND is_read=False
        Index("ix_message_unread", "match_id", "is_read", "sender_id"),
    )


# Function 3: Politeness vote tracking
class PolitenessVote(Base):
    __tablename__ = "politeness_votes"

    id = Column(Integer, primary_key=True, index=True)
    voter_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    target_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    stars = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (UniqueConstraint("voter_id", "target_id", name="uq_politeness_vote"),)


# Photo gallery
class ProfilePhoto(Base):
    __tablename__ = "profile_photos"

    id = Column(Integer, primary_key=True, index=True)
    profile_id = Column(Integer, ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    url = Column(Text, nullable=False)
    position = Column(Integer, default=0)
    created_at = Column(DateTime, default=_utcnow)

    profile = relationship("Profile", back_populates="photos")


# Block / Report
class Block(Base):
    __tablename__ = "blocks"

    id = Column(Integer, primary_key=True, index=True)
    blocker_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    blocked_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (UniqueConstraint("blocker_id", "blocked_id", name="uq_block"),)


class Report(Base):
    __tablename__ = "reports"

    id = Column(Integer, primary_key=True, index=True)
    reporter_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    reported_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    reason = Column(String(50), nullable=False)
    comment = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (UniqueConstraint("reporter_id", "reported_id", name="uq_report"),)


# Stories (disappear after 24h)
class Story(Base):
    __tablename__ = "stories"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    content = Column(Text, nullable=False)       # text or "/photos/story_xxx.jpg"
    media_type = Column(String(10), default="text")  # "text" | "image"
    expires_at = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", name="uq_story_per_user"),  # one story per user at a time
    )


# Who viewed my profile
class ProfileView(Base):
    __tablename__ = "profile_views"

    id = Column(Integer, primary_key=True, index=True)
    viewer_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    viewed_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (
        UniqueConstraint("viewer_id", "viewed_id", name="uq_profile_view"),
        Index("ix_profile_view_viewed", "viewed_id", "viewer_id", "created_at"),
    )


# Message reactions (emoji)
class MessageReaction(Base):
    __tablename__ = "message_reactions"

    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(Integer, ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    emoji = Column(String(10), nullable=False)
    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (UniqueConstraint("message_id", "user_id", name="uq_msg_reaction"),)


# Function 6: Quiz answers
class QuizAnswer(Base):
    __tablename__ = "quiz_answers"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    question_id = Column(Integer, nullable=False)
    answer_index = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (UniqueConstraint("user_id", "question_id", name="uq_quiz_answer"),)


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    endpoint = Column(Text, nullable=False, unique=True)
    p256dh = Column(Text, nullable=False)
    auth = Column(Text, nullable=False)
    created_at = Column(DateTime, default=_utcnow)
