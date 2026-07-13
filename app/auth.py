"""Проверка Telegram initData (HMAC-SHA256) + демо-пользователь для браузера."""
import hashlib
import hmac
import json
from urllib.parse import parse_qsl

from fastapi import Header

from app.config import BOT_TOKEN, DEMO, DEMO_NEWBIE_HANDLE, ENV


def verify_init_data(init_data: str) -> dict | None:
    if not init_data or not BOT_TOKEN:
        return None
    try:
        parsed = dict(parse_qsl(init_data, strict_parsing=True))
    except ValueError:
        return None
    received = parsed.pop("hash", None)
    if not received:
        return None
    check = "\n".join(f"{k}={parsed[k]}" for k in sorted(parsed))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, received):
        return None
    try:
        parsed["user"] = json.loads(parsed.get("user", "{}"))
    except ValueError:
        parsed["user"] = {}
    return parsed


async def current_user(authorization: str | None = Header(default=None),
                       x_debug: str | None = Header(default=None)) -> dict:
    """Возвращает {handle,name,tg_id}. В демо-режиме — демо-новичок."""
    # dev-only: имперсонация для тестов, заголовок X-Debug: "<tg_id>:<name>"
    if ENV == "dev" and x_debug:
        parts = x_debug.split(":", 1)
        try:
            tid = int(parts[0])
        except ValueError:
            tid = 0
        return {"tg_id": tid, "name": parts[1] if len(parts) > 1 else "Dev",
                "handle": "dev" + str(tid)}
    init = ""
    if authorization and authorization.startswith("tma "):
        init = authorization[4:]
    data = verify_init_data(init)
    if data and data.get("user", {}).get("id"):
        u = data["user"]
        return {"tg_id": u["id"], "name": u.get("first_name", "Курьер"),
                "handle": u.get("username", "user")}
    # fallback (браузер / нет initData)
    return {"tg_id": 0, "name": "Демо", "handle": DEMO_NEWBIE_HANDLE, "demo": True}


async def get_or_create_user(db, u: dict):
    """Находит или создаёт реального пользователя по telegram_id."""
    from sqlalchemy import select
    from app.models import User
    res = await db.execute(select(User).where(User.telegram_id == u["tg_id"]))
    usr = res.scalar_one_or_none()
    if usr is None:
        usr = User(telegram_id=u["tg_id"], name=u.get("name", ""),
                   handle=u.get("handle", ""), role="none")
        db.add(usr)
        await db.commit()
        await db.refresh(usr)
    elif u.get("name") and usr.name != u["name"]:
        usr.name = u["name"]
        await db.commit()
    return usr
