CREATE TABLE IF NOT EXISTS sessions (
    chat_id TEXT PRIMARY KEY,
    state TEXT NOT NULL DEFAULT 'start',
    data JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS doctors (
    employee_uid TEXT PRIMARY KEY,
    fio TEXT NOT NULL,
    specialization TEXT,
    clinic_uids JSONB NOT NULL DEFAULT '[]'::jsonb,
    employee_phone TEXT,
    main_services JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE TABLE IF NOT EXISTS clinics (
    clinic_uid TEXT PRIMARY KEY,
    clinic_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS appointments (
    id BIGSERIAL PRIMARY KEY,
    mis_uid TEXT,
    chat_id TEXT NOT NULL,
    doctor_uid TEXT NOT NULL,
    patient_surname TEXT NOT NULL,
    patient_name TEXT NOT NULL,
    patient_father_name TEXT,
    birthday DATE,
    phone TEXT,
    visit_date DATE NOT NULL,
    visit_time TIME NOT NULL,
    clinic_uid TEXT,
    service_uid TEXT,
    service_name TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    cancelled_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_appointments_doctor_date_time
    ON appointments (doctor_uid, visit_date, visit_time);

CREATE INDEX IF NOT EXISTS idx_appointments_chat_id
    ON appointments (chat_id);

-- Normalized services (filled by sync from MIS tickets / future sources).
CREATE TABLE IF NOT EXISTS doctor_services (
    employee_uid TEXT NOT NULL REFERENCES doctors (employee_uid) ON DELETE CASCADE,
    service_uid TEXT NOT NULL,
    service_name TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (employee_uid, service_uid)
);

CREATE INDEX IF NOT EXISTS idx_doctor_services_employee ON doctor_services (employee_uid);

-- Cached schedule from MIS: free bookable slots and busy blocks (for display).
CREATE TABLE IF NOT EXISTS schedule_slots (
    employee_uid TEXT NOT NULL REFERENCES doctors (employee_uid) ON DELETE CASCADE,
    clinic_uid TEXT NOT NULL,
    slot_date DATE NOT NULL,
    time_hhmm TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('free', 'busy')),
    meta JSONB NOT NULL DEFAULT '{}'::jsonb,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (employee_uid, clinic_uid, slot_date, time_hhmm, kind)
);

CREATE INDEX IF NOT EXISTS idx_schedule_slots_lookup
    ON schedule_slots (employee_uid, clinic_uid, slot_date);

-- Sync bookkeeping (optional diagnostics).
CREATE TABLE IF NOT EXISTS sync_state (
    resource TEXT PRIMARY KEY,
    last_success_at TIMESTAMPTZ,
    detail JSONB NOT NULL DEFAULT '{}'::jsonb
);
