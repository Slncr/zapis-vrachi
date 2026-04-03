"""Точка входа ASGI для uvicorn: gunicorn / Docker / локально."""
from __future__ import annotations

from app.factory import create_app

app = create_app()
