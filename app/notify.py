"""Отправка отзыва в Telegram-канал через бота — надёжный persist без БД."""
import sys

import httpx

from app.config import BOT_TOKEN, FEEDBACK_CHAT_ID


async def send_feedback_to_telegram(text: str) -> bool:
    if not BOT_TOKEN or not FEEDBACK_CHAT_ID:
        print(f"[feedback] пропуск: BOT_TOKEN задан={bool(BOT_TOKEN)}, "
              f"FEEDBACK_CHAT_ID задан={bool(FEEDBACK_CHAT_ID)}", file=sys.stderr, flush=True)
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(url, json={"chat_id": FEEDBACK_CHAT_ID, "text": text})
        if r.status_code != 200:
            print(f"[feedback] Telegram вернул {r.status_code}: {r.text}", file=sys.stderr, flush=True)
        return r.status_code == 200
    except Exception as e:
        print(f"[feedback] исключение при отправке: {e!r}", file=sys.stderr, flush=True)
        return False


async def send_user_message(tg_id, text: str) -> bool:
    """Личное сообщение пользователю через бота (работает, если он запускал бота)."""
    if not BOT_TOKEN or not tg_id:
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.post(url, json={"chat_id": tg_id, "text": text})
        return r.status_code == 200
    except Exception:
        return False
