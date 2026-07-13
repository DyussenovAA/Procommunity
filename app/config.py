import os

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ENV = os.getenv("ENV", "dev")
FEE_SAVED = int(os.getenv("FEE_SAVED", "22000"))
DB_PATH = os.getenv("DB_PATH", "procommunity.db")

def _database_url() -> str:
    raw = os.getenv("DATABASE_URL", "").strip()
    if not raw:
        return f"sqlite+aiosqlite:///{DB_PATH}"
    # normalize to async driver for SQLAlchemy
    if raw.startswith("postgres://"):
        raw = raw.replace("postgres://", "postgresql+asyncpg://", 1)
    elif raw.startswith("postgresql://"):
        raw = raw.replace("postgresql://", "postgresql+asyncpg://", 1)
    return raw

DATABASE_URL = _database_url()

# Ключ для доступа к дашборду метрик /admin (задай длинную случайную строку)
ADMIN_KEY = os.getenv("ADMIN_KEY", "changeme")

# демо-режим: если Telegram не передал валидный initData, показываем демо-пользователя
DEMO = ENV == "dev" or not BOT_TOKEN
DEMO_NEWBIE_HANDLE = "timur_go"
DEMO_MENTOR_HANDLE = "petr_snr"
STEP_KEYS = ["standards", "slot", "order", "demand", "payout"]
DAY_MS = 86_400_000

# Куда отправлять отзывы (id закрытого канала/группы, напр. -1001234567890)
FEEDBACK_CHAT_ID = os.getenv("FEEDBACK_CHAT_ID", "")

# true = демо-режим (переключатель ролей + сид-данные, для жюри). false = реальный режим.
SHOWCASE = os.getenv("SHOWCASE", "true").lower() in ("1", "true", "yes")

ZONE_CELL_DEG = 0.006   # ~600 м: размер ячейки сетки для агрегации плотности
DENSITY_TTL_MIN = 60    # сигналы старше этого не показываются на живой карте

MENTOR_MAX_MENTEES = int(os.getenv("MENTOR_MAX_MENTEES", "8"))   # лимит активных подопечных на наставника
CLEANUP_EVERY_MIN = int(os.getenv("CLEANUP_EVERY_MIN", "60"))       # период фоновой очистки старья
