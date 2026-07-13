import asyncio
import math
import time
from collections import defaultdict
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_user, get_or_create_user
from app.config import (ADMIN_KEY, BOT_TOKEN, CLEANUP_EVERY_MIN, DAY_MS, DENSITY_TTL_MIN, FEE_SAVED,
                        MENTOR_MAX_MENTEES, SHOWCASE, STEP_KEYS, ZONE_CELL_DEG)
from app.db import SessionLocal, get_db
from app.models import DensityReport, Event, Feedback, Mentor, Participant, PartnerRequest, User, now_ms
from app.notify import send_feedback_to_telegram, send_user_message
from app import schemas, seed

app = FastAPI(title="ProCommunity")

STATIC = Path(__file__).resolve().parent.parent / "static"


@app.on_event("startup")
async def _startup():
    await seed.run()
    asyncio.create_task(cleanup_loop())


# ---------- helpers ----------
def day_of(p: Participant) -> int:
    return min(30, max(1, (now_ms() - p.start_ms) // DAY_MS + 1))


async def get_mentor(db, mid):
    if mid is None:
        return None
    return (await db.execute(select(Mentor).where(Mentor.id == mid))).scalar_one_or_none()


def p_dto(p: Participant, mentor: Mentor | None = None) -> dict:
    prog = sum(1 for k in STEP_KEYS if p.checklist[k]) / len(STEP_KEYS)
    d = {"id": p.id, "name": p.name, "handle": p.handle, "group": p.group,
         "day": day_of(p), "progress": round(prog, 3), "checklist": p.checklist,
         "week2": p.week2, "d30": p.d30, "retained": p.retained}
    if mentor:
        d["mentor"] = {"id": mentor.id, "name": mentor.name, "handle": mentor.handle}
    return d


async def compute_stats(db) -> dict:
    parts = (await db.execute(select(Participant))).scalars().all()
    m = [p for p in parts if p.group == "mentored"]
    c = [p for p in parts if p.group == "control"]
    mr = sum(1 for p in m if p.retained)
    cr = sum(1 for p in c if p.retained)
    mp = mr / len(m) if m else 0
    cp = cr / len(c) if c else 0
    extra = max(0, round(mr - cp * len(m)))
    return {"m": len(m), "c": len(c), "mr": mr, "cr": cr,
            "mp": round(mp, 3), "cp": round(cp, 3),
            "uplift": round((mp - cp) * 100, 1),
            "extra": extra, "saved": extra * FEE_SAVED, "fee": FEE_SAVED}


# ---------- API ----------
@app.get("/api/health")
async def health():
    return {"status": "ok"}


def zone_center(lat, lng):
    zlat = math.floor(lat / ZONE_CELL_DEG) * ZONE_CELL_DEG
    zlng = math.floor(lng / ZONE_CELL_DEG) * ZONE_CELL_DEG
    return (round(zlat + ZONE_CELL_DEG / 2, 5), round(zlng + ZONE_CELL_DEG / 2, 5))


def aggregate_zones(reports):
    """Группирует сигналы по ячейкам сетки. Возвращает агрегаты по зонам (не людей)."""
    zones = {}
    for r in reports:
        if r.lat is None or r.lng is None:
            continue
        key = zone_center(r.lat, r.lng)
        z = zones.get(key)
        if z is None:
            z = {"lat": key[0], "lng": key[1], "couriers": 0, "_wsum": 0, "reports": 0}
            zones[key] = z
        z["couriers"] = max(z["couriers"], r.couriers)
        z["_wsum"] += r.wait
        z["reports"] += 1
    out = []
    for z in zones.values():
        z["wait"] = round(z["_wsum"] / z["reports"]) if z["reports"] else 0
        z.pop("_wsum", None)
        out.append(z)
    return out


@app.get("/api/snapshot")
async def snapshot(user=Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Всё, что нужно фронту, одним запросом."""
    # "я" (новичок) — по handle из Telegram, иначе демо-новичок
    me = (await db.execute(select(Participant).where(Participant.handle == user["handle"]))).scalar_one_or_none()
    if me is None:
        me = (await db.execute(select(Participant).where(Participant.group == "mentored")
                               .order_by(Participant.id))).scalars().first()
    me_mentor = await get_mentor(db, me.mentor_id) if me else None

    mentored = (await db.execute(select(Participant).where(Participant.group == "mentored")
                                 .order_by(Participant.id))).scalars().all()
    mentors = {m.id: m for m in (await db.execute(select(Mentor))).scalars().all()}
    density = (await db.execute(select(DensityReport).order_by(DensityReport.created_ms.desc()))).scalars().all()
    reqs = (await db.execute(select(PartnerRequest).order_by(PartnerRequest.created_ms.desc()))).scalars().all()

    def ago(ms):
        mins = max(0, (now_ms() - ms) // 60000)
        if mins < 1: return "только что"
        if mins < 60: return f"{mins} мин назад"
        return f"{mins // 60} ч назад"

    return {
        "user": {"name": user["name"], "demo": user.get("demo", False)},
        "me": p_dto(me, me_mentor) if me else None,
        "mentees": [p_dto(p, mentors.get(p.mentor_id)) for p in mentored],
        "density": [{"id": d.id, "point": d.point, "couriers": d.couriers, "wait": d.wait,
                     "lat": d.lat, "lng": d.lng, "ago": ago(d.created_ms), "mine": (d.author_id is not None and d.author_id == user["tg_id"])} for d in density],
        "zones": aggregate_zones(density),
        "requests": [{"id": r.id, "type": r.type, "area": r.area, "author": r.author,
                      "ago": ago(r.created_ms)} for r in reqs],
        "feedback": [{"rating": f.rating, "text": f.text, "user": f.user_name, "ago": ago(f.created_ms)}
                     for f in (await db.execute(select(Feedback).order_by(Feedback.created_ms.desc()).limit(30))).scalars().all()],
        "stats": await compute_stats(db),
    }


async def _get_participant(db, pid):
    p = (await db.execute(select(Participant).where(Participant.id == pid))).scalar_one_or_none()
    if not p:
        raise HTTPException(404, "not found")
    return p


@app.post("/api/me/checklist")
async def my_step(body: schemas.StepIn, user=Depends(current_user), db: AsyncSession = Depends(get_db)):
    me = (await db.execute(select(Participant).where(Participant.handle == user["handle"]))).scalar_one_or_none()
    if me is None:
        me = (await db.execute(select(Participant).where(Participant.group == "mentored").order_by(Participant.id))).scalars().first()
    me.set_step(body.step, not me.checklist.get(body.step, False))
    if sum(1 for k in STEP_KEYS if me.checklist[k]) / len(STEP_KEYS) >= 0.6:
        me.week2 = True
    await db.commit()
    return {"ok": True}


@app.post("/api/mentee/{pid}/checklist")
async def mentee_step(pid: int, body: schemas.StepIn, db: AsyncSession = Depends(get_db)):
    p = await _get_participant(db, pid)
    p.set_step(body.step, not p.checklist.get(body.step, False))
    await db.commit()
    return {"ok": True}


@app.post("/api/mentee/{pid}/flag")
async def mentee_flag(pid: int, body: schemas.FlagIn, db: AsyncSession = Depends(get_db)):
    p = await _get_participant(db, pid)
    if body.flag in ("week2", "d30"):
        setattr(p, body.flag, body.value)
        await db.commit()
    return {"ok": True}


@app.post("/api/mentee")
async def add_mentee(body: schemas.MenteeIn, db: AsyncSession = Depends(get_db)):
    m1 = (await db.execute(select(Mentor).order_by(Mentor.id))).scalars().first()
    p = Participant(name=body.name, handle=body.handle, group="mentored",
                    mentor_id=m1.id if m1 else None, start_ms=now_ms(), checklist_json="{}")
    db.add(p); await db.commit()
    return {"ok": True, "id": p.id}


@app.post("/api/density")
async def add_density(body: schemas.DensityIn, user=Depends(current_user), db: AsyncSession = Depends(get_db)):
    rate_limit(user, "density", 15, 60)
    me = await get_or_create_user(db, user)
    db.add(DensityReport(point=body.point, couriers=body.couriers, wait=body.wait,
                         lat=body.lat, lng=body.lng, author=user["name"],
                         author_id=user["tg_id"], city=me.city or ""))
    await db.commit()
    return {"ok": True}


@app.post("/api/density/{sig_id}/delete")
async def delete_density(sig_id: int, user=Depends(current_user), db: AsyncSession = Depends(get_db)):
    rate_limit(user, "sigmod", 30, 60)
    r = (await db.execute(select(DensityReport).where(DensityReport.id == sig_id))).scalar_one_or_none()
    if r and r.author_id and r.author_id == user["tg_id"]:
        await db.delete(r)
        await db.commit()
        return {"ok": True}
    return {"ok": False, "reason": "not_owner"}


@app.post("/api/density/{sig_id}/refresh")
async def refresh_density(sig_id: int, user=Depends(current_user), db: AsyncSession = Depends(get_db)):
    rate_limit(user, "sigmod", 30, 60)
    r = (await db.execute(select(DensityReport).where(DensityReport.id == sig_id))).scalar_one_or_none()
    if r and r.author_id and r.author_id == user["tg_id"]:
        r.created_ms = now_ms()
        await db.commit()
        return {"ok": True}
    return {"ok": False, "reason": "not_owner"}


@app.post("/api/partners")
async def add_request(body: schemas.RequestIn, user=Depends(current_user), db: AsyncSession = Depends(get_db)):
    rate_limit(user, "partner", 6, 600)
    me = await get_or_create_user(db, user)
    db.add(PartnerRequest(type=body.type, area=body.area, author=user["name"], city=me.city or ""))
    await db.commit()
    return {"ok": True}


@app.post("/api/feedback")
async def add_feedback(body: schemas.FeedbackIn, user=Depends(current_user), db: AsyncSession = Depends(get_db)):
    rate_limit(user, "feedback", 5, 600)
    fb = Feedback(rating=body.rating, text=body.text.strip(), user_name=user["name"])
    db.add(fb)
    await db.commit()
    stars = "\u2605" * body.rating + "\u2606" * (5 - body.rating) if body.rating else "\u2014"
    msg = (f"\u041d\u043e\u0432\u044b\u0439 \u043e\u0442\u0437\u044b\u0432 \u00b7 \u041f\u0440\u043e\u041a\u043e\u043c\u044c\u044e\u043d\u0438\u0442\u0438\n"
           f"\u041e\u0446\u0435\u043d\u043a\u0430: {stars}\n\u041e\u0442: {user['name']}\n\n{body.text.strip() or '(\u0431\u0435\u0437 \u0442\u0435\u043a\u0441\u0442\u0430)'}")
    await send_feedback_to_telegram(msg)
    return {"ok": True}


@app.get("/api/feedback")
async def list_feedback(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Feedback).order_by(Feedback.created_ms.desc()).limit(50))).scalars().all()
    return [{"rating": f.rating, "text": f.text, "user": f.user_name} for f in rows]


async def log_event(db, name, user_name, meta=""):
    try:
        db.add(Event(name=name[:48], user_name=(user_name or "")[:64], meta=str(meta)[:400]))
        await db.commit()
    except Exception:
        pass


@app.post("/api/track")
async def track(body: dict, user=Depends(current_user), db: AsyncSession = Depends(get_db)):
    rate_limit(user, "track", 60, 60)
    await log_event(db, str(body.get("event", "event")), user["name"], body.get("meta") or "")
    return {"ok": True}


@app.get("/api/metrics")
async def metrics(key: str = "", db: AsyncSession = Depends(get_db)):
    if key != ADMIN_KEY:
        raise HTTPException(403, "bad key")
    import datetime
    from collections import Counter
    events = (await db.execute(select(Event))).scalars().all()
    by = Counter(e.name for e in events)
    users = {e.user_name for e in events if e.user_name}
    daily = Counter()
    for e in events:
        if e.name == "open":
            daily[datetime.datetime.utcfromtimestamp(e.created_ms / 1000).strftime("%d.%m")] += 1
    fb = (await db.execute(select(Feedback).order_by(Feedback.created_ms.desc()))).scalars().all()
    ratings = [f.rating for f in fb if f.rating]
    return {
        "unique_users": len(users),
        "opens": by.get("open", 0),
        "events_total": len(events),
        "checklist_actions": by.get("checklist", 0),
        "density_reports": by.get("density_sent", 0),
        "buddy_cards": by.get("buddy_sent", 0),
        "mentees_added": by.get("mentee_added", 0),
        "screen_views": by.get("screen", 0),
        "feedback_count": len(fb),
        "avg_rating": round(sum(ratings) / len(ratings), 2) if ratings else 0,
        "by_name": dict(by),
        "daily_opens": [{"day": d, "n": n} for d, n in sorted(daily.items())],
        "feedback": [{"rating": f.rating, "text": f.text, "user": f.user_name} for f in fb[:50]],
    }


_bcast = {"running": False, "total": 0, "sent": 0, "failed": 0}


async def _run_broadcast(ids, text):
    _bcast.update(running=True, total=len(ids), sent=0, failed=0)
    try:
        for i, tid in enumerate(ids):
            ok = await send_user_message(tid, text)
            _bcast["sent" if ok else "failed"] += 1
            if (i + 1) % 25 == 0:
                await asyncio.sleep(1.0)   # ~30 сообщений/сек — лимит Telegram
    finally:
        _bcast["running"] = False
    print(f"[broadcast] готово: доставлено={_bcast['sent']} не дошло={_bcast['failed']} всего={_bcast['total']}", flush=True)


@app.post("/api/broadcast")
async def broadcast(body: dict, key: str = "", db: AsyncSession = Depends(get_db)):
    if key != ADMIN_KEY:
        raise HTTPException(403, "bad key")
    if not BOT_TOKEN:
        return {"ok": False, "reason": "no_bot_token"}
    text = (body.get("text") or "").strip()
    if not text:
        return {"ok": False, "reason": "empty"}
    if _bcast["running"]:
        return {"ok": False, "reason": "already_running"}
    ids = [u.telegram_id for u in (await db.execute(select(User))).scalars().all() if u.telegram_id]
    asyncio.create_task(_run_broadcast(ids, text))
    return {"ok": True, "targets": len(ids)}


@app.get("/api/broadcast/status")
async def broadcast_status(key: str = ""):
    if key != ADMIN_KEY:
        raise HTTPException(403, "bad key")
    return _bcast


@app.get("/admin")
async def admin_page():
    return FileResponse(STATIC / "admin.html")


# ---------- rate limiting (in-memory; single worker) ----------
_RL = defaultdict(list)


def rate_limit(user, bucket, limit, window):
    key = f"{bucket}:{(user or {}).get('tg_id', 0)}"
    now = time.time()
    q = _RL[key]
    cut = now - window
    n = 0
    while n < len(q) and q[n] < cut:
        n += 1
    if n:
        del q[:n]
    if len(q) >= limit:
        raise HTTPException(429, "Слишком много запросов, попробуйте чуть позже")
    q.append(now)


# ---------- mentor <-> newbie matching by city ----------
async def active_mentee_count(db, mentor_id):
    return (await db.execute(select(func.count()).select_from(User)
            .where(User.role == "newbie", User.mentor_id == mentor_id))).scalar() or 0


async def auto_assign_mentor(db, newbie):
    """Наименее загруженный наставник города новичка (если есть со свободными местами)."""
    if newbie.mentor_id or newbie.role != "newbie" or not newbie.city:
        return None
    mentors = (await db.execute(select(User).where(User.role == "mentor", User.city == newbie.city))).scalars().all()
    best, best_load = None, None
    for m in mentors:
        load = await active_mentee_count(db, m.id)
        if load < MENTOR_MAX_MENTEES and (best_load is None or load < best_load):
            best, best_load = m, load
    if best:
        newbie.mentor_id = best.id
        await db.commit()
    return best


def notify_bg(tg_id, text):
    try:
        asyncio.create_task(send_user_message(tg_id, text))
    except Exception:
        pass


async def try_match(db, newbie):
    m = await auto_assign_mentor(db, newbie)
    if m:
        notify_bg(newbie.telegram_id, f"\U0001F389 Тебе назначен наставник {m.name}. Он на связи в приложении.")
        notify_bg(m.telegram_id, f"\U0001F44B Новый подопечный: {newbie.name}. Загляни во вкладку «Подопечные».")
        await log_event(db, "matched", newbie.name, str(m.id))
    return m


async def ensure_demo(db, me):
    """Демо-режим: досеивает наставника/подопечных/сигналы в город пользователя."""
    if not SHOWCASE or not me.city or me.lat is None:
        return
    import json as _json, random
    async def free_tg():
        mn = (await db.execute(select(func.min(User.telegram_id)))).scalar()
        return (mn - 1) if (mn is not None and mn < 0) else -1000
    dcnt = (await db.execute(select(func.count()).select_from(DensityReport).where(DensityReport.city == me.city))).scalar() or 0
    if dcnt < 3:
        for nm, cr, wt in [("ТЦ у метро", 6, 14), ("Точка выдачи", 3, 8), ("Кафе-дворик", 4, 11)]:
            db.add(DensityReport(point=nm, couriers=cr, wait=wt,
                                 lat=me.lat + (random.random() - 0.5) * 0.02,
                                 lng=me.lng + (random.random() - 0.5) * 0.02,
                                 author="Демо", author_id=-1, city=me.city))
        await db.commit()
    if me.role == "newbie" and not me.mentor_id:
        m = (await db.execute(select(User).where(User.role == "mentor", User.city == me.city))).scalars().first()
        if m is None:
            tg = await free_tg()
            m = User(telegram_id=tg, name="Наставник Алексей", handle="pro_courier",
                     role="mentor", city=me.city, lat=me.lat, lng=me.lng)
            db.add(m); await db.commit(); await db.refresh(m)
        me.mentor_id = m.id
        await db.commit()
    if me.role == "mentor" and await active_mentee_count(db, me.id) < 3:
        base = await free_tg()
        rows = [("Тимур", 0.8, True, False), ("Лена", 0.4, False, False), ("Марат", 1.0, True, True)]
        for i, (nm, prog, w2, d30) in enumerate(rows):
            steps = {k: (idx < round(prog * len(STEP_KEYS))) for idx, k in enumerate(STEP_KEYS)}
            db.add(User(telegram_id=base - i, name=nm, handle="demo" + str(i), role="newbie",
                        city=me.city, mentor_id=me.id, start_ms=now_ms() - (i + 1) * 86_400_000,
                        week2=w2, d30=d30, checklist_json=_json.dumps(steps)))
        await db.commit()


# ---------- background cleanup of stale rows ----------
async def cleanup_once():
    now = now_ms()
    async with SessionLocal() as db:
        await db.execute(delete(DensityReport).where(DensityReport.created_ms < now - DENSITY_TTL_MIN * 60_000 * 2))
        await db.execute(delete(Event).where(Event.created_ms < now - 90 * 86_400_000))
        await db.execute(delete(PartnerRequest).where(PartnerRequest.created_ms < now - 14 * 86_400_000))
        await db.commit()


async def cleanup_loop():
    while True:
        await asyncio.sleep(CLEANUP_EVERY_MIN * 60)
        try:
            await cleanup_once()
        except Exception as e:
            print("[cleanup]", repr(e), flush=True)


# ---------- real mode: profiles / roles / city ----------
async def get_user_by_id(db, uid):
    return (await db.execute(select(User).where(User.id == uid))).scalar_one_or_none()


def user_dto(u, mentor=None):
    prog = sum(1 for k in STEP_KEYS if u.checklist[k]) / len(STEP_KEYS)
    d = {"id": u.id, "name": u.name, "role": u.role, "city": u.city,
         "day": day_of(u) if u.start_ms else 0, "progress": round(prog, 3),
         "checklist": u.checklist, "week2": u.week2, "d30": u.d30, "retained": u.retained}
    if mentor:
        d["mentor"] = {"id": mentor.id, "name": mentor.name, "handle": mentor.handle}
    return d


@app.get("/api/config")
async def app_config():
    return {"showcase": SHOWCASE, "step_keys": STEP_KEYS, "build": "broadcast-2026-07-13"}


@app.post("/api/role")
async def set_role(body: dict, user=Depends(current_user), db: AsyncSession = Depends(get_db)):
    rate_limit(user, "role", 10, 60)
    me = await get_or_create_user(db, user)
    role = body.get("role")
    if role in ("newbie", "mentor"):
        me.role = role
        if role == "newbie" and not me.start_ms:
            me.start_ms = now_ms()
            me.checklist_json = "{}"
        await db.commit()
        await log_event(db, "role_set", me.name, role)
        if role == "newbie" and me.city and not me.mentor_id:
            await try_match(db, me)
    return {"ok": True, "role": me.role}


@app.post("/api/city")
async def set_city(body: dict, user=Depends(current_user), db: AsyncSession = Depends(get_db)):
    rate_limit(user, "city", 10, 60)
    me = await get_or_create_user(db, user)
    city = (body.get("city") or "").strip()[:64]
    if city:
        me.city = city
    if body.get("lat") is not None:
        me.lat = float(body["lat"]); me.lng = float(body["lng"])
    await db.commit()
    await log_event(db, "city_set", me.name, me.city or "")
    if me.role == "newbie" and not me.mentor_id and me.city:
        await try_match(db, me)
    return {"ok": True, "city": me.city}


@app.get("/api/app")
async def app_state(user=Depends(current_user), db: AsyncSession = Depends(get_db)):
    me = await get_or_create_user(db, user)
    if SHOWCASE:
        await ensure_demo(db, me)
        await db.refresh(me)
    mentor = await get_user_by_id(db, me.mentor_id) if me.mentor_id else None
    needs = "role" if me.role == "none" else ("city" if not me.city else None)
    out = {"me": user_dto(me, mentor), "needs": needs,
           "center": {"lat": me.lat, "lng": me.lng} if me.lat is not None else None,
           "density": [], "requests": [], "mentees": [], "queue": [], "stats": {}}
    if needs:
        return out

    def ago(ms):
        mins = max(0, (now_ms() - ms) // 60000)
        return "только что" if mins < 1 else (f"{mins} мин назад" if mins < 60 else f"{mins // 60} ч назад")

    _ttl = now_ms() - DENSITY_TTL_MIN * 60_000
    dens = (await db.execute(select(DensityReport).where(DensityReport.city == me.city,
            DensityReport.created_ms > _ttl).order_by(DensityReport.created_ms.desc()).limit(200))).scalars().all()
    reqs = (await db.execute(select(PartnerRequest).where(PartnerRequest.city == me.city)
            .order_by(PartnerRequest.created_ms.desc()).limit(100))).scalars().all()
    out["density"] = [{"id": d.id, "point": d.point, "couriers": d.couriers, "wait": d.wait,
                       "lat": d.lat, "lng": d.lng, "ago": ago(d.created_ms), "mine": (d.author_id is not None and d.author_id == me.telegram_id)} for d in dens]
    out["requests"] = [{"id": r.id, "type": r.type, "area": r.area, "author": r.author,
                        "ago": ago(r.created_ms)} for r in reqs]
    out["zones"] = aggregate_zones(dens)
    if me.role == "mentor":
        mentees = (await db.execute(select(User).where(User.role == "newbie", User.mentor_id == me.id))).scalars().all()
        queue = (await db.execute(select(User).where(User.role == "newbie", User.mentor_id.is_(None), User.city == me.city))).scalars().all()
        out["mentees"] = [user_dto(x) for x in mentees]
        out["queue"] = [user_dto(x) for x in queue]
        out["stats"] = {"role": "mentor", "mentees": len(mentees),
                        "retained": sum(1 for x in mentees if x.retained), "city": me.city}
    else:
        out["stats"] = {"role": "newbie", "day": day_of(me) if me.start_ms else 0,
                        "progress": round(sum(1 for k in STEP_KEYS if me.checklist[k]) / len(STEP_KEYS), 3),
                        "retained": me.retained, "city": me.city,
                        "mentor": mentor.name if mentor else None}
    return out


@app.post("/api/rt/checklist")
async def rt_checklist(body: dict, user=Depends(current_user), db: AsyncSession = Depends(get_db)):
    rate_limit(user, "rt", 40, 60)
    me = await get_or_create_user(db, user)
    if me.role == "newbie" and body.get("step"):
        me.set_step(body["step"], not me.checklist.get(body["step"], False))
        if sum(1 for k in STEP_KEYS if me.checklist[k]) / len(STEP_KEYS) >= 0.6:
            me.week2 = True
        await db.commit()
        await log_event(db, "checklist", me.name, body["step"])
    return {"ok": True}


@app.post("/api/rt/mentee/{uid}/take")
async def rt_take(uid: int, user=Depends(current_user), db: AsyncSession = Depends(get_db)):
    rate_limit(user, "take", 20, 60)
    me = await get_or_create_user(db, user)
    n = await get_user_by_id(db, uid)
    if me.role == "mentor" and n and n.role == "newbie" and n.mentor_id is None and n.city == me.city:
        if await active_mentee_count(db, me.id) >= MENTOR_MAX_MENTEES:
            return {"ok": False, "reason": "full"}
        n.mentor_id = me.id
        await db.commit()
        await log_event(db, "mentee_taken", me.name, str(uid))
        notify_bg(n.telegram_id, f"\U0001F389 Тебе назначен наставник {me.name}. Он на связи в приложении.")
    return {"ok": True}


@app.post("/api/rt/mentee/{uid}/release")
async def rt_release(uid: int, user=Depends(current_user), db: AsyncSession = Depends(get_db)):
    me = await get_or_create_user(db, user)
    n = await get_user_by_id(db, uid)
    if me.role == "mentor" and n and n.mentor_id == me.id:
        n.mentor_id = None
        await db.commit()
        await log_event(db, "mentee_released", me.name, str(uid))
    return {"ok": True}


@app.post("/api/rt/mentee/{uid}/flag")
async def rt_flag(uid: int, body: dict, user=Depends(current_user), db: AsyncSession = Depends(get_db)):
    me = await get_or_create_user(db, user)
    n = await get_user_by_id(db, uid)
    if me.role == "mentor" and n and n.mentor_id == me.id and body.get("flag") in ("week2", "d30"):
        setattr(n, body["flag"], bool(body.get("value")))
        await db.commit()
    return {"ok": True}


@app.post("/api/rt/mentee/{uid}/checklist")
async def rt_mentee_step(uid: int, body: dict, user=Depends(current_user), db: AsyncSession = Depends(get_db)):
    me = await get_or_create_user(db, user)
    n = await get_user_by_id(db, uid)
    if me.role == "mentor" and n and n.mentor_id == me.id and body.get("step"):
        n.set_step(body["step"], not n.checklist.get(body["step"], False))
        await db.commit()
    return {"ok": True}


# ---------- static (Mini App) ----------
@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


app.mount("/", StaticFiles(directory=str(STATIC), html=True), name="static")
