#!/usr/bin/env bash
set -e
pip install -r requirements.txt
alembic upgrade head
exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
