"""
Runtime configuration from environment.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def _required(name: str, default: str = "") -> str:
    value = (os.getenv(name) or "").strip()
    return value or default


def _optional(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip()


def _optional_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _optional_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value.strip())
    except Exception:
        return default


TELEGRAM_BOT_TOKEN: str = _required("TELEGRAM_BOT_TOKEN")
DATABASE_URL: str = _required("DATABASE_URL")

MIS_BASE_URL: str = _required("MIS_BASE_URL")
MIS_API_KEY: str = _optional("MIS_API_KEY")
MIS_USER: str = _optional("MIS_USER")
MIS_PASSWORD: str = _optional("MIS_PASSWORD")
MIS_VERIFY_TLS: bool = _optional_bool("MIS_VERIFY_TLS", True)

MIS_SOAP_URL: str = _optional("MIS_SOAP_URL", f"{MIS_BASE_URL.rstrip('/')}/ws/ws1.1cws")
MIS_SOAP_USER: str = _optional("MIS_SOAP_USER")
MIS_SOAP_PASSWORD: str = _optional("MIS_SOAP_PASSWORD")
MIS_VIT_SOAP_URL: str = _optional("MIS_VIT_SOAP_URL", f"{MIS_BASE_URL.rstrip('/')}/ws/VIT_Integration")
MIS_CLINICDATA_URL: str = _optional("MIS_CLINICDATA_URL", f"{MIS_BASE_URL.rstrip('/')}/hs/bwi/DictionaryData")
MIS_PATIENT_TICKETS_URL: str = _optional(
    "MIS_PATIENT_TICKETS_URL",
    f"{MIS_BASE_URL.rstrip('/')}/hs/bwi/PatientTickets",
)
MIS_MAIN_ONLY: bool = _optional_bool("MIS_MAIN_ONLY", True)

ENABLE_MAX_BOT: bool = _optional_bool("ENABLE_MAX_BOT", False)
MAX_BOT_TOKEN: str = _optional("MAX_BOT_TOKEN")
MAX_API_BASE_URL: str = _optional("MAX_API_BASE_URL", "https://platform-api.max.ru")
MAX_POLL_TIMEOUT_SEC: int = _optional_int("MAX_POLL_TIMEOUT_SEC", 30)
MAX_POLL_LIMIT: int = _optional_int("MAX_POLL_LIMIT", 100)

SESSION_TTL_HOURS: int = _optional_int("SESSION_TTL_HOURS", 24)
DOCTORS_REFRESH_INTERVAL_MINUTES: int = _optional_int("DOCTORS_REFRESH_INTERVAL_MINUTES", 60)
