import asyncio
import json

from sqlalchemy import select

from app.config import STEP_KEYS
from app.db import SessionLocal
from app.models import DensityReport, Mentor, Participant, PartnerRequest, now_ms

DAY = 86_400_000


def cl(n):
    return json.dumps({k: (i < n) for i, k in enumerate(STEP_KEYS)})


async def run():
    async with SessionLocal() as db:
        if (await db.execute(select(Participant))).first():
            print("seed: already populated")
            return
        m1 = Mentor(name="Пётр С.", handle="petr_snr")
        m2 = Mentor(name="Аня К.", handle="anna_pro")
        db.add_all([m1, m2]); await db.flush()
        now = now_ms()
        P = lambda name, h, g, mid, days, steps, w2, d30: Participant(
            name=name, handle=h, group=g, mentor_id=mid, start_ms=now - days * DAY,
            week2=w2, d30=d30, checklist_json=cl(steps))
        db.add_all([
            P("Тимур", "timur_go", "mentored", m1.id, 12, 3, True, False),
            P("Света", "sveta_k", "mentored", m1.id, 26, 5, True, True),
            P("Дамир", "damir_r", "mentored", m1.id, 21, 4, True, True),
            P("Лена", "lena_m", "mentored", m2.id, 30, 5, True, True),
            P("Марат", "marat_e", "mentored", m2.id, 18, 3, True, False),
            P("Ольга", "olga_v", "mentored", m1.id, 30, 5, True, True),
            P("Костя", "kostya", "mentored", m2.id, 30, 4, True, True),
            P("Вика", "vika_d", "mentored", m1.id, 9, 2, True, False),
            P("Игорь", "igor_m", "mentored", m1.id, 24, 5, True, True),
            P("Настя", "nastya_p", "mentored", m2.id, 28, 5, True, True),
            P("Рома", "roma_k", "mentored", m1.id, 16, 4, True, True),
            P("Юля", "yulia_s", "mentored", m2.id, 22, 5, True, True),
            *[P("—", f"c{i}", "control", None, 30, 0, w2, d30)
              for i, (w2, d30) in enumerate(
                  [(1,1),(1,1),(1,1),(1,1),(1,1),(1,1),(0,0),(1,0),(0,0),(1,0),(0,0),(1,0)], 1)],
        ])
        db.add_all([
            DensityReport(point="ТЦ Авиапарк", couriers=6, wait=15, lat=55.7909, lng=37.5308, author="Дамир"),
            DensityReport(point="ТЦ Европейский", couriers=5, wait=12, lat=55.7447, lng=37.5670, author="Лена"),
            DensityReport(point="Киевский вокзал", couriers=3, wait=8, lat=55.7430, lng=37.5665, author="Марат"),
            DensityReport(point="м. Сокол", couriers=2, wait=5, lat=55.8050, lng=37.5150, author="Костя"),
            DensityReport(point="ТЦ Метрополис", couriers=1, wait=3, lat=55.8210, lng=37.4960, author="Ольга"),
        ])
        db.add_all([
            PartnerRequest(type="Велосипед", area="САО, вечерние смены", author="Марат"),
            PartnerRequest(type="Смены", area="Центр, будни", author="Ольга"),
        ])
        await db.commit()
        print("seed: done")


if __name__ == "__main__":
    asyncio.run(run())
