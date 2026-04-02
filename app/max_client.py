"""
MAX messenger API client.
"""
from __future__ import annotations

from typing import Any

import httpx
import logging

logger = logging.getLogger(__name__)


class MaxClient:
    def __init__(self, token: str, *, base_url: str = "https://platform-api.max.ru") -> None:
        self.token = token
        self.base_url = base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": self.token,
            "Content-Type": "application/json",
        }

    async def get_me(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{self.base_url}/me", headers=self._headers())
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, dict) else {}

    async def get_updates(
        self,
        *,
        marker: str | None = None,
        limit: int = 100,
        timeout_sec: int = 30,
        types: list[str] | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit, "timeout": timeout_sec}
        if marker:
            params["marker"] = marker
        if types:
            params["types"] = ",".join(types)
        async with httpx.AsyncClient(timeout=timeout_sec + 15) as client:
            r = await client.get(f"{self.base_url}/updates", params=params, headers=self._headers())
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, dict) else {}

    async def send_message(
        self,
        *,
        chat_id: str | None = None,
        user_id: str | None = None,
        text: str,
        buttons: list[list[dict[str, str]]] | None = None,
    ) -> None:
        params: dict[str, Any] = {}
        if chat_id:
            params["chat_id"] = chat_id
        elif user_id:
            params["user_id"] = user_id
        else:
            raise ValueError("Either chat_id or user_id is required")
        body: dict[str, Any] = {
            "text": text,
        }
        if buttons:
            kb_rows: list[list[dict[str, str]]] = []
            for row in buttons:
                kb_row: list[dict[str, str]] = []
                for b in row:
                    payload = str(b.get("callback") or b.get("callback_data") or "").strip()
                    text_btn = str(b.get("text") or "").strip()
                    if not payload or not text_btn:
                        continue
                    kb_row.append(
                        {
                            "type": "callback",
                            "text": text_btn,
                            "payload": payload,
                        }
                    )
                if kb_row:
                    kb_rows.append(kb_row)
            if kb_rows:
                body["attachments"] = [
                    {
                        "type": "inline_keyboard",
                        "payload": {"buttons": kb_rows},
                    }
                ]
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{self.base_url}/messages",
                params=params,
                json=body,
                headers=self._headers(),
            )
            try:
                r.raise_for_status()
            except Exception:
                logger.exception("MAX send_message failed: status=%s body=%s", r.status_code, r.text)
                raise

    async def answer_callback(self, *, callback_id: str, text: str = "") -> None:
        body: dict[str, Any] = {}
        if text:
            body["notification"] = text
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{self.base_url}/answers",
                params={"callback_id": callback_id},
                json=body,
                headers=self._headers(),
            )
            try:
                r.raise_for_status()
            except Exception:
                logger.exception("MAX answer_callback failed: status=%s body=%s", r.status_code, r.text)
                raise
