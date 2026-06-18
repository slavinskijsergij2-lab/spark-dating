from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey, Enum, Float, UniqueConstraint
from sqlalchemy.orm import relationship
import enum

from app.database import Base


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
    created_at = Column(DateTime, default=datetime.utcnow)

    # Function 3: Politeness rating
    politeness_score = Column(Float, nullable=True, default=5.0)
    politeness_votes = Column(Integer, nullable=True, default=0)

    # Function 7: Photo verification
    is_verified = Column(Boolean, nullable=True, default=False)
    verify_gesture = Column(String(50), nullable=True)

    # Online status
    last_seen = Column(DateTime, nullable=True)

    # Email verification
    email_verified = Column(Boolean, default=False, nullable=False)
    email_verify_token = Column(String(100), nullable=True, index=True)

    profile = relationship("Profile", back_populates="user", uselist=False, cascade="all, delete-orphan")
    likes_given = relationship("Like", foreign_keys="Like.liker_id", back_populates="liker", cascade="all, delete-orphan")
    likes_received = relationship("Like", foreign_keys="Like.liked_id", back_populates="liked", cascade="all, delete-orphan")
    messages_sent = relationship("Message", foreign_keys="Message.sender_id", back_populates="sender")


class Profile(Base):
    __tablename__ = "profiles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    name = Column(String(100), nullable=False)
    age = Column(Integer, nullable=False)
    gender = Column(Enum(GenderEnum), nullable=False)
    looking_for = Column(Enum(GenderEnum), nullable=True)
    city = Column(String(100), nullable=True)
    bio = Column(Text, nullable=True)
    photo = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Function 5: Intention
    intention = Column(String(20), nullable=True)

    user = relationship("User", back_populates="profile")
    photos = relationship("ProfilePhoto", back_populates="profile", cascade="all, delete-orphan", order_by="ProfilePhoto.position")


class Like(Base):
    __tablename__ = "likes"

    id = Column(Integer, primary_key=True, index=True)
    liker_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    liked_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    is_like = Column(Boolean, nullable=False)  # True = like, False = dislike
    created_at = Column(DateTime, default=datetime.utcnow)

    liker = relationship("User", foreign_keys=[liker_id], back_populates="likes_given")
    liked = relationship("User", foreign_keys=[liked_id], back_populates="likes_received")


class Match(Base):
    __tablename__ = "matches"

    id = Column(Integer, primary_key=True, index=True)
    user1_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    user2_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Notification: who has seen this match
    seen_by_user1 = Column(Boolean, default=True, nullable=False)
    seen_by_user2 = Column(Boolean, default=False, nullable=False)

    user1 = relationship("User", foreign_keys=[user1_id])
    user2 = relationship("User", foreign_keys=[user2_id])
    messages = relationship("Message", back_populates="match", cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=False)
    sender_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    is_read = Column(Boolean, default=False)

    match = relationship("Match", back_populates="messages")
    sender = relationship("User", foreign_keys=[sender_id], back_populates="messages_sent")


# Function 3: Politeness vote tracking
class PolitenessVote(Base):
    __tablename__ = "politeness_votes"

    id = Column(Integer, primary_key=True, index=True)
    voter_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    target_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    stars = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("voter_id", "target_id", name="uq_politeness_vote"),)


# Photo gallery
class ProfilePhoto(Base):
    __tablename__ = "profile_photos"

    id = Column(Integer, primary_key=True, index=True)
    profile_id = Column(Integer, ForeignKey("profiles.id"), nullable=False)
    url = Column(Text, nullable=False)
    position = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    profile = relationship("Profile", back_populates="photos")


# Function 6: Quiz answers
class QuizAnswer(Base):
    __tablename__ = "quiz_answers"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    question_id = Column(Integer, nullable=False)
    answer_index = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("user_id", "question_id", name="uq_quiz_answer"),)
