import json
import time

from sqlalchemy import BigInteger, Boolean, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.config import STEP_KEYS


def now_ms() -> int:
    return int(time.time() * 1000)


class Mentor(Base):
    __tablename__ = "mentors"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    handle: Mapped[str] = mapped_column(String(64), index=True)


class Participant(Base):
    """Новичок в пилоте удержания (наставничество или контроль)."""
    __tablename__ = "participants"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    handle: Mapped[str] = mapped_column(String(64))
    group: Mapped[str] = mapped_column(String(16))          # mentored | control
    mentor_id: Mapped[int | None] = mapped_column(Integer)
    city: Mapped[str] = mapped_column(String(64), default="Москва")
    start_ms: Mapped[int] = mapped_column(BigInteger, default=now_ms)
    week2: Mapped[bool] = mapped_column(Boolean, default=False)
    d30: Mapped[bool] = mapped_column(Boolean, default=False)
    checklist_json: Mapped[str] = mapped_column(Text, default="{}")

    @property
    def checklist(self) -> dict:
        try:
            data = json.loads(self.checklist_json or "{}")
        except ValueError:
            data = {}
        return {k: bool(data.get(k, False)) for k in STEP_KEYS}

    def set_step(self, key: str, value: bool):
        cl = self.checklist
        if key in cl:
            cl[key] = value
            self.checklist_json = json.dumps(cl)

    @property
    def retained(self) -> bool:
        return self.week2 and self.d30


class DensityReport(Base):
    __tablename__ = "density_reports"
    __table_args__ = (Index("ix_density_city_created", "city", "created_ms"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    point: Mapped[str] = mapped_column(String(128))
    couriers: Mapped[int] = mapped_column(Integer, default=0)
    wait: Mapped[int] = mapped_column(Integer, default=0)
    city: Mapped[str] = mapped_column(String(64), default="", index=True)
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    author: Mapped[str] = mapped_column(String(64), default="")
    author_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    created_ms: Mapped[int] = mapped_column(BigInteger, default=now_ms)


class PartnerRequest(Base):
    __tablename__ = "partner_requests"
    __table_args__ = (Index("ix_partner_city_created", "city", "created_ms"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(32))
    area: Mapped[str] = mapped_column(String(128), default="")
    city: Mapped[str] = mapped_column(String(64), default="", index=True)
    author: Mapped[str] = mapped_column(String(64), default="")
    created_ms: Mapped[int] = mapped_column(BigInteger, default=now_ms)


class Feedback(Base):
    __tablename__ = "feedback"
    id: Mapped[int] = mapped_column(primary_key=True)
    rating: Mapped[int] = mapped_column(Integer, default=0)   # 0..5
    text: Mapped[str] = mapped_column(Text, default="")
    user_name: Mapped[str] = mapped_column(String(64), default="")
    created_ms: Mapped[int] = mapped_column(BigInteger, default=now_ms)


class Event(Base):
    """Событие реального использования — основа метрик пилота."""
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(48), index=True)   # open, screen, checklist_done, feedback, density, mentee, request
    user_name: Mapped[str] = mapped_column(String(64), default="", index=True)
    meta: Mapped[str] = mapped_column(Text, default="")
    created_ms: Mapped[int] = mapped_column(BigInteger, default=now_ms, index=True)


class User(Base):
    """Реальный пользователь (по Telegram). Роль, город, профиль, статистика."""
    __tablename__ = "users"
    __table_args__ = (Index("ix_user_role_mentor", "role", "mentor_id"), Index("ix_user_role_city", "role", "city"))
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    handle: Mapped[str] = mapped_column(String(64), default="")
    role: Mapped[str] = mapped_column(String(16), default="none")
    city: Mapped[str | None] = mapped_column(String(64), index=True)
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    start_ms: Mapped[int | None] = mapped_column(BigInteger)
    mentor_id: Mapped[int | None] = mapped_column(Integer, index=True)
    week2: Mapped[bool] = mapped_column(Boolean, default=False)
    d30: Mapped[bool] = mapped_column(Boolean, default=False)
    checklist_json: Mapped[str] = mapped_column(Text, default="{}")
    created_ms: Mapped[int] = mapped_column(BigInteger, default=now_ms)

    @property
    def checklist(self) -> dict:
        try:
            data = json.loads(self.checklist_json or "{}")
        except ValueError:
            data = {}
        return {k: bool(data.get(k, False)) for k in STEP_KEYS}

    def set_step(self, key: str, value: bool):
        cl = self.checklist
        if key in cl:
            cl[key] = value
            self.checklist_json = json.dumps(cl)

    @property
    def retained(self) -> bool:
        return self.week2 and self.d30
