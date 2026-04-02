"""
Unit tests for app.parsers: doctors filter/group, patient line, schedule/clinic_uid.
"""
import pytest
from app.parsers import (
    filter_doctors,
    group_doctors_by_employee,
    parse_enlargement_to_doctors,
    parse_patient_line,
    get_grafik_from_schedule_response,
    find_clinic_uid_for_slot,
    build_date_buttons_by_clinics,
    build_time_slots_buttons,
)


def test_filter_doctors_empty():
    assert filter_doctors([]) == []


def test_filter_doctors_requires_specialization():
    grafik = [
        {"СотрудникФИО": "Иванов Иван", "СотрудникID": "1", "Специализация": "", "Клиника": "c1"},
    ]
    assert filter_doctors(grafik) == []


def test_filter_doctors_excludes_non_doctor_keywords():
    grafik = [
        {"СотрудникФИО": "Рентген Кабинет", "СотрудникID": "1", "Специализация": "Врач", "Клиника": "c1"},
    ]
    assert filter_doctors(grafik) == []


def test_filter_doctors_requires_two_words_fio():
    grafik = [
        {"СотрудникФИО": "Иванов", "СотрудникID": "1", "Специализация": "Терапевт", "Клиника": "c1"},
    ]
    assert filter_doctors(grafik) == []


def test_filter_doctors_keeps_valid():
    grafik = [
        {"СотрудникФИО": "Иванов Иван Иванович", "СотрудникID": "1", "Специализация": "Терапевт", "Клиника": "c1"},
    ]
    out = filter_doctors(grafik)
    assert len(out) == 1
    assert out[0]["СотрудникФИО"] == "Иванов Иван Иванович"


def test_group_doctors_by_employee():
    grafik = [
        {"СотрудникФИО": "Иванов И.И.", "СотрудникID": "1", "Специализация": "Терапевт", "Клиника": "c1"},
        {"СотрудникФИО": "Иванов И.И.", "СотрудникID": "1", "Специализация": "Терапевт", "Клиника": "c2"},
    ]
    out = group_doctors_by_employee(grafik)
    assert len(out) == 1
    assert out[0]["СотрудникID"] == "1"
    assert set(out[0]["Клиника"]) == {"c1", "c2"}


def test_parse_enlargement_to_doctors():
    raw = {
        "Ответ": {
            "ГрафикДляСайта": [
                {"СотрудникФИО": "Петров П.П.", "СотрудникID": "2", "Специализация": "Хирург", "Клиника": "c1"},
            ],
        },
    }
    out = parse_enlargement_to_doctors(raw)
    assert len(out) == 1
    assert out[0]["СотрудникID"] == "2"
    assert out[0]["Специализация"] == "Хирург"


def test_parse_patient_line():
    text = "Иванов Иван Иванович, 06.06.2006, 89997776655"
    p = parse_patient_line(text, "20251222", "20251222T16:30:00")
    assert p is not None
    assert p.patient_surname == "Иванов"
    assert p.patient_name == "Иван"
    assert p.patient_father_name == "Иванович"
    assert p.birthday == "2006-06-06"
    assert p.phone == "89997776655"
    assert p.visit_date_human == "22.12.2025"
    assert p.visit_time_human == "16:30"


def test_parse_patient_line_invalid():
    assert parse_patient_line("short", "20251222", "16:30") is None
    assert parse_patient_line("Only, 01.01.1990, 8", "20251222", "16:30") is None  # single word FIO


def test_get_grafik_from_schedule_response():
    raw = {"Ответ": {"ГрафикДляСайта": [{"Клиника": "c1"}]}}
    out = get_grafik_from_schedule_response(raw)
    assert len(out) == 1
    assert out[0]["Клиника"] == "c1"


def test_get_grafik_from_schedule_response_data_string():
    import json
    raw = {"data": json.dumps({"Ответ": {"ГрафикДляСайта": [{"Клиника": "c2"}]}})}
    out = get_grafik_from_schedule_response(raw)
    assert len(out) == 1
    assert out[0]["Клиника"] == "c2"


def test_find_clinic_uid_for_slot():
    schedules = [
        {
            "Клиника": "clinic-1",
            "ПериодыГрафика": {
                "СвободноеВремя": [
                    {
                        "Дата": "2025-12-04T00:00:00",
                        "ВремяНачала": "2025-12-04T10:00:00",
                        "ВремяОкончания": "2025-12-04T11:00:00",
                    },
                ],
                "ЗанятоеВремя": [],
            },
        },
    ]
    uid = find_clinic_uid_for_slot(schedules, "20251204", "20251204T10:30:00")
    assert uid == "clinic-1"


def test_find_clinic_uid_for_slot_not_found():
    schedules = [
        {
            "Клиника": "c1",
            "ПериодыГрафика": {
                "СвободноеВремя": [{"Дата": "2025-12-05T00:00:00", "ВремяНачала": "2025-12-05T10:00:00", "ВремяОкончания": "2025-12-05T11:00:00"}],
                "ЗанятоеВремя": [],
            },
        },
    ]
    assert find_clinic_uid_for_slot(schedules, "20251204", "20251204T10:30:00") is None


def test_build_date_buttons_by_clinics():
    grafik = [
        {
            "Клиника": "c1",
            "ПериодыГрафика": {
                "СвободноеВремя": [{"Дата": "2025-12-04T00:00:00"}],
                "ЗанятоеВремя": [{"Дата": "2025-12-05T00:00:00"}],
            },
        },
    ]
    clinics = {"c1": "Филиал 1"}
    rows = build_date_buttons_by_clinics(grafik, clinics)
    assert any(any(b.get("callback_data") == "free_20251204" for b in r) for r in rows)
    assert any(any(b.get("text") == "⬅ Назад" for b in r) for r in rows)


def test_build_time_slots_buttons():
    grafik_first = {
        "ПериодыГрафика": {
            "СвободноеВремя": [
                {"Дата": "20251204T00:00:00", "ВремяНачала": "2025-12-04T10:00:00", "ВремяОкончания": "2025-12-04T11:00:00"},
            ],
            "ЗанятоеВремя": [],
        },
        "ДлительностьПриема": "PT30M",
    }
    rows = build_time_slots_buttons(grafik_first, "20251204")
    assert len(rows) >= 1
    assert any(any("freeTime_" in (b.get("callback_data") or "") for b in r) for r in rows)
