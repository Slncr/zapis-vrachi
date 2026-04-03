"""
Unit tests for MisClient with mocked httpx (no real 1C calls).
"""
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.mis_client import MisClient


@pytest.fixture
def mock_config():
    with patch.dict("os.environ", {
        "DATABASE_URL": "postgresql://localhost/test",
        "MIS_BASE_URL": "http://test.local/mis",
        "MIS_API_KEY": "test-key",
        "MIS_USER": "u",
        "MIS_PASSWORD": "p",
    }, clear=False):
        yield


def _make_response(json_data):
    r = Mock()
    r.raise_for_status = Mock()
    r.json = Mock(return_value=json_data)
    return r


@pytest.mark.asyncio
async def test_create_appointment_parses_uid(mock_config):
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_post = AsyncMock(return_value=_make_response({"Ответ": {"УИД": "created-guid-123"}}))
        mock_client_cls.return_value.__aenter__.return_value.post = mock_post
        client = MisClient()
        resp = await client.create_appointment(
            employee_id="emp1",
            patient_surname="Иванов",
            patient_name="Иван",
            patient_father_name="Иванович",
            birthday="1990-01-01",
            date_ymd="20251222",
            time_begin="20251222T10:00:00",
            phone="89991234567",
            clinic="clinic-1",
        )
        assert resp.success is True
        assert resp.uid == "created-guid-123"


@pytest.mark.asyncio
async def test_cancel_appointment_parses_result(mock_config):
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_post = AsyncMock(return_value=_make_response({"Ответ": {"Результат": True}}))
        mock_client_cls.return_value.__aenter__.return_value.post = mock_post
        client = MisClient()
        resp = await client.cancel_appointment("some-guid")
        assert resp.success is True
        call_args = mock_post.call_args
        url = str(call_args[0][0] if call_args[0] else "")
        assert "AppointmentCancel" in url
        body = call_args.kwargs.get("json") or {}
        assert body.get("GUID") == "some-guid"
        assert body.get("Method") == "CancelBookAnAppointment"
        assert body.get("Reason") == "Пациент отказался"
        assert body.get("AdditionalInformation") == ""
