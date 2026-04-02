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
