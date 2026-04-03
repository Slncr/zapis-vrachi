"""
HTTP client for 1C MIS.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
import time
from typing import Any

import httpx

import config

logger = logging.getLogger(__name__)


@dataclass
class CreateAppointmentResponse:
    success: bool
    uid: str | None = None
    raw: dict[str, Any] | None = None
    error: str | None = None


@dataclass
class CancelAppointmentResponse:
    success: bool
    raw: dict[str, Any] | None = None
    error: str | None = None


class MisClient:
    def __init__(self) -> None:
        self.base_url = config.MIS_BASE_URL.rstrip("/")
        self.api_key = config.MIS_API_KEY
        self.user = config.MIS_USER
        self.password = config.MIS_PASSWORD
        self.verify = config.MIS_VERIFY_TLS
        self.clinic_data_url = config.MIS_CLINICDATA_URL
        self.patient_tickets_url = config.MIS_PATIENT_TICKETS_URL
        self._schedule_cache_ttl_sec = 45
        self._services_cache_ttl_sec = 60
        self._schedule_cache: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}
        self._services_cache: dict[tuple[str, str, str], tuple[float, list[dict[str, str]]]] = {}

    @staticmethod
    def _cache_get(cache: dict, key: tuple, ttl_sec: int):
        item = cache.get(key)
        if not item:
            return None
        ts, value = item
        if (time.monotonic() - ts) > ttl_sec:
            cache.pop(key, None)
            return None
        return value

    @staticmethod
    def _cache_set(cache: dict, key: tuple, value: Any) -> None:
        cache[key] = (time.monotonic(), value)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        return headers

    def _auth(self) -> httpx.BasicAuth | None:
        if self.user and self.password:
            return httpx.BasicAuth(self.user, self.password)
        return None

    async def _post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=60, verify=self.verify) as client:
            resp = await client.post(
                url,
                json=payload,
                headers=self._headers(),
                auth=self._auth(),
            )
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, dict) else {"data": data}

    def _base_body(self) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if self.user:
            body["User"] = self.user
        if self.password:
            body["Password"] = self.password
        if self.api_key:
            body["Key"] = self.api_key
        return body

    async def get_enlargement_schedule(self, start_dt: str, finish_dt: str) -> dict[str, Any]:
        # Try both known endpoint names — first bwi/Schedule, fallback GetEnlargementSchedule
        body = {**self._base_body(), "StartDate": start_dt, "FinishDate": finish_dt}
        errors: list[str] = []
        for path in ("hs/bwi/Schedule", "hs/bwi/GetEnlargementSchedule"):
            url = f"{self.base_url}/{path}"
            try:
                raw = await self._post_json(url, body)
                return {"raw": raw}
            except Exception as e:
                errors.append(f"{path}: {type(e).__name__}: {e}")
                logger.warning("MIS get_enlargement_schedule %s failed: %s", url, e)
        detail = "; ".join(errors) if errors else "no attempts"
        raise RuntimeError(f"GetEnlargementSchedule: all endpoints failed ({detail})")

    async def get_schedule20(self, employee_uid: str, start_dt: str, finish_dt: str) -> dict[str, Any]:
        cache_key = (str(employee_uid or "").strip(), start_dt, finish_dt)
        cached = self._cache_get(self._schedule_cache, cache_key, self._schedule_cache_ttl_sec)
        if cached is not None:
            return {"raw": cached}
        body = {**self._base_body(), "Employee": employee_uid, "StartDate": start_dt, "FinishDate": finish_dt}
        errors: list[str] = []
        for path in ("hs/bwi/Schedule", "hs/bwi/GetShedule20"):
            url = f"{self.base_url}/{path}"
            try:
                raw = await self._post_json(url, body)
                # Some MIS contours return merged schedule for many employees even when Employee is passed.
                # Filter by employee UID locally to keep bot calendar accurate.
                ans = raw.get("Ответ")
                if isinstance(ans, dict):
                    grafik = ans.get("ГрафикДляСайта")
                    if isinstance(grafik, dict):
                        grafik = [grafik]
                    if isinstance(grafik, list):
                        target = str(employee_uid or "").strip().lower()
                        filtered = []
                        for item in grafik:
                            if not isinstance(item, dict):
                                continue
                            eid = str(item.get("СотрудникID") or item.get("Employee") or "").strip().lower()
                            if eid == target:
                                filtered.append(item)
                        if filtered:
                            ans["ГрафикДляСайта"] = filtered
                self._cache_set(self._schedule_cache, cache_key, raw)
                return {"raw": raw}
            except Exception as e:
                errors.append(f"{path}: {type(e).__name__}: {e}")
                logger.warning("MIS get_schedule20 %s failed: %s", url, e)
        detail = "; ".join(errors) if errors else "no attempts"
        raise RuntimeError(f"GetShedule20: all endpoints failed ({detail})")

    async def create_appointment(
        self,
        *,
        employee_id: str,
        patient_surname: str,
        patient_name: str,
        patient_father_name: str,
        birthday: str,
        date_ymd: str,
        time_begin: str,
        phone: str,
        clinic: str,
        service: str | None = None,
    ) -> CreateAppointmentResponse:
        base_body: dict[str, Any] = {
            **self._base_body(),
            "Method": "BookAnAppointmentWithParams",
            # MIS on current contour expects EmployeeID (not Employee).
            "EmployeeID": employee_id,
            "Employee": employee_id,  # compatibility with alternate publishes
            "PatientSurname": patient_surname,
            "PatientName": patient_name,
            "PatientFatherName": patient_father_name or "",
            "Birthday": birthday or "",
            "Date": date_ymd,
            "TimeBegin": time_begin,
            "Phone": phone,
            "Clinic": clinic,
        }
        if service:
            base_body["Service"] = service
        last_error: Exception | None = None
        for path in ("hs/bwi/AppointmentCreate", "hs/bwi/BookAnAppointmentWithParams"):
            try:
                raw = await self._post_json(f"{self.base_url}/{path}", base_body)
                ans = raw.get("Ответ") or raw.get("Ответы") or {}
                uid = (
                    ans.get("УИД")
                    or ans.get("UID")
                    or ans.get("GUID")
                    or raw.get("УИД")
                    or raw.get("UID")
                    or raw.get("GUID")
                )
                return CreateAppointmentResponse(success=True, uid=str(uid) if uid else None, raw=raw)
            except Exception as e:
                last_error = e
                continue
        return CreateAppointmentResponse(success=False, error=str(last_error) if last_error else "Create appointment failed")

    async def cancel_appointment(self, mis_uid: str) -> CancelAppointmentResponse:
        url = f"{self.base_url}/hs/bwi/CancelBookAnAppointment"
        body = {**self._base_body(), "UID": mis_uid}
        try:
            raw = await self._post_json(url, body)
            ans = raw.get("Ответ") or raw.get("Ответы") or {}
            ok = ans.get("Результат")
            if ok is None:
                ok = raw.get("Результат")
            return CancelAppointmentResponse(success=bool(ok), raw=raw)
        except Exception as e:
            return CancelAppointmentResponse(success=False, error=str(e))

    async def get_employee_contacts(self) -> dict[str, dict[str, str]]:
        body: dict[str, Any] = {
            **self._base_body(),
            "Method": "GetListEmployees",
            "MainOnly": config.MIS_MAIN_ONLY,
        }
        raw = await self._post_json(self.clinic_data_url, body)
        ans = raw.get("Ответ") or raw.get("Ответы") or raw.get("Result") or []
        if isinstance(ans, dict):
            employees = ans.get("Сотрудник") or ans.get("Employees") or []
        else:
            employees = ans
        if isinstance(employees, dict):
            employees = [employees]
        out: dict[str, dict[str, str]] = {}
        for it in employees if isinstance(employees, list) else []:
            if not isinstance(it, dict):
                continue
            uid = str(it.get("UID") or it.get("УИД") or it.get("СотрудникID") or "").strip()
            if not uid:
                continue
            phone = str(it.get("Phone") or it.get("Телефон") or it.get("СотовыйТелефон") or "").strip()
            fio = str(
                it.get("FIO")
                or it.get("СотрудникФИО")
                or it.get("Name")
                or it.get("Наименование")
                or ""
            ).strip()
            out[uid] = {"phone": phone, "fio": fio}
        return out

    async def get_employee_main_services(self, employee_uid: str) -> list[dict[str, str]]:
        # Conservative fallback: no direct SOAP parsing in this recovery build.
        _ = employee_uid
        return []

    async def get_patient_tickets_http(
        self,
        start_dt: str,
        finish_dt: str,
        employee_uid: str | None = None,
    ) -> list[dict[str, Any]]:
        # Primary method is PatientTickets (as used on current MIS contour).
        # Keep fallbacks for compatibility with alternate publishes.
        methods = ("PatientTickets", "GetPatientTickets", "GetPatientsTickets")
        last_exc: Exception | None = None
        for method_name in methods:
            body: dict[str, Any] = {
                **self._base_body(),
                "Method": method_name,
                "StartDate": start_dt,
                "FinishDate": finish_dt,
            }
            if employee_uid:
                body["Employee"] = employee_uid
            try:
                raw = await self._post_json(self.patient_tickets_url, body)
                items = raw.get("Ответ") or raw.get("Ответы") or raw.get("Result") or raw.get("data") or []
                if isinstance(items, dict):
                    items = [items]
                return items if isinstance(items, list) else []
            except Exception as e:
                last_exc = e
                continue
        if last_exc:
            raise last_exc
        return []

    async def get_doctor_services_from_tickets_http(
        self,
        employee_uid: str,
        start_dt: str,
        finish_dt: str,
    ) -> list[dict[str, str]]:
        cache_key = (str(employee_uid or "").strip(), start_dt, finish_dt)
        cached = self._cache_get(self._services_cache, cache_key, self._services_cache_ttl_sec)
        if cached is not None:
            return cached
        try:
            tickets = await self.get_patient_tickets_http(start_dt, finish_dt, employee_uid=employee_uid)
        except Exception:
            # Some MIS publishes fail on Employee filter; retry global and filter locally.
            tickets = await self.get_patient_tickets_http(start_dt, finish_dt, employee_uid=None)
        out: dict[str, str] = {}
        for t in tickets:
            if not isinstance(t, dict):
                continue
            emp = str(t.get("Сотрудник") or t.get("Employee") or "").strip()
            if emp and str(employee_uid or "").strip() and emp != str(employee_uid).strip():
                continue
            services = (
                t.get("СписокРабот")
                or t.get("services")
                or t.get("Services")
                or t.get("Услуги")
                or []
            )
            if isinstance(services, dict):
                services = [services]
            for s in services if isinstance(services, list) else []:
                if not isinstance(s, dict):
                    continue
                sid = str(s.get("uid") or s.get("UID") or s.get("УИД") or "").strip()
                name = str(s.get("name") or s.get("Name") or s.get("Наименование") or "").strip()
                if sid and name:
                    out[sid] = name
        result = [{"uid": k, "name": v} for k, v in out.items()]
        self._cache_set(self._services_cache, cache_key, result)
        return result
