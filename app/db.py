from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import DATABASE_URL

import re as _re
_url = DATABASE_URL
_connect_args = {}
if "asyncpg" in _url:
    # asyncpg doesn't accept libpq-style params; translate sslmode -> ssl
    if _re.search(r"sslmode=(require|verify)", _url):
        _connect_args["ssl"] = True
    _url = _re.sub(r"[?&](sslmode|channel_binding)=[^&]*", "", _url)
    _url = _url.replace("?&", "?").rstrip("?&")

engine = create_async_engine(_url, connect_args=_connect_args, future=True)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as s:
        yield s


async def init_db():
    from app import models  # noqa
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
