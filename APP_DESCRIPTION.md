# Zapisvrachi — описание приложения

## Что это

**Zapisvrachi** — Telegram-бот для записи пациентов на приём к врачам. Пользователь общается с ботом в чате: выбирает врача по ФИО, смотрит расписание по филиалам, указывает дату и время, вводит данные пациента и подтверждает запись. Запись создаётся в медицинской информационной системе (1C МИС).

---

## Основные возможности

- **Регистрация пользователя** — ввод ФИО врача, поиск по справочнику
- **Расписание** — выбор филиала, даты и времени приёма из свободных слотов
- **Создание записи** — ввод данных пациента (ФИО, дата рождения, телефон), подтверждение, отправка в 1C МИС
- **Подтверждение** — уведомление об успешной записи в Telegram
- **Отмена записи** — кнопка отмены в течение 30 минут (вызов API МИС)

---

## Технологический стек

| Компонент | Технология |
|-----------|------------|
| Язык | Python 3.11+ |
| Бот | python-telegram-bot 21.7 (асинхронный) |
| База данных | PostgreSQL |
| Доступ к БД | asyncpg (асинхронный драйвер) |
| HTTP-клиент | httpx (для API 1C) |
| Конфигурация | pydantic-settings, python-dotenv |
| Планировщик задач | APScheduler (Job Queue в python-telegram-bot) |
| Тесты | pytest, pytest-asyncio |

---

## Архитектура

### Структура проекта

```
Zapisvrachi/
├── main.py              # Точка входа, инициализация, polling
├── config.py            # Загрузка переменных окружения
├── app/
│   ├── db.py            # Пул подключений PostgreSQL, init schema
│   ├── mis_client.py    # HTTP-клиент 1C МИС (Schedule, AppointmentCreate, AppointmentCancel)
│   ├── parsers.py       # Парсинг ответов МИС, расписания, данных пациента
│   ├── repositories.py  # Слой данных (sessions, doctors, clinics, appointments)
│   └── bot.py           # Обработчики команд и callback, FSM
├── scripts/
│   ├── load_doctors.py  # Загрузка врачей и филиалов из МИС в БД
│   ├── export_clinics.py # Выгрузка UID филиалов в файл
│   └── apply_filials_names.py  # Применение названий филиалов из файла в БД
├── deploy/
│   ├── zapisvrachi-bot.service  # systemd unit
│   └── install-service.sh      # Установка автозапуска
├── schema.sql           # Схема БД
├── filials_uid.txt      # UID филиалов и их названия (для отображения в боте)
├── .env                 # Переменные окружения (не в git)
└── requirements.txt     # Зависимости Python
```

### Поток данных

1. **Telegram** → бот получает сообщения и callback (long polling)
2. **Бот** → читает/пишет сессию в PostgreSQL (`telegram_sessions`)
3. **Справочники** → врачи и филиалы из БД (`doctors`, `clinics`)
4. **Расписание** → запрос к 1C МИС `GetShedule20`
5. **Создание записи** → запрос к 1C МИС `BookAnAppointmentWithParams`
6. **История** → записи сохраняются в `appointments`

### Состояния (FSM)

- `start` — начало, кнопка «Регистрация»
- `reg` — ввод ФИО врача
- `schedule_ready` — выбор филиала, даты, времени
- `zapis` — ввод данных пациента, подтверждение

---

## Интеграции

### 1. Telegram Bot API

- Long polling (getUpdates)
- Команды: `/start`
- Callback-кнопки: Регистрация, Записать на приём, выбор даты/времени, Подтвердить, Отменить запись
- Текстовые сообщения: ФИО врача, данные пациента

### 2. 1C МИС (HTTP API)

- **GetEnlargementSchedule** — список врачей (для справочника)
- **GetShedule20** — расписание врача по датам, филиалам и слотам
- **BookAnAppointmentWithParams** — создание записи
- **CancelBookAnAppointment** — отмена записи

Аутентификация: HTTP Basic Auth. Формат запросов — JSON, соответствует BitMedic API.

---

## База данных (PostgreSQL)

| Таблица | Назначение |
|---------|------------|
| `telegram_sessions` | Сессии чатов: state, doc_data (врач), draft (черновик записи), zapis_slot_uid |
| `doctors` | Справочник врачей: employee_uid, fio, specialization, clinic_uids |
| `clinics` | Справочник филиалов: clinic_uid, clinic_name |
| `appointments` | История записей: пациент, врач, дата/время, mis_uid, отмена |

---

## Фоновые задачи

- **Обновление врачей** — периодически (по умолчанию раз в 60 минут) загрузка из 1C `GetEnlargementSchedule` и обновление таблиц `doctors`, `clinics`
- **Очистка сессий** — ежедневно удаление старых записей из `telegram_sessions` (старше SESSION_TTL_HOURS)

---

## Переменные окружения

| Переменная | Описание |
|------------|----------|
| `TELEGRAM_BOT_TOKEN` | Токен бота из @BotFather |
| `DATABASE_URL` | Подключение PostgreSQL |
| `MIS_BASE_URL` | Базовый URL API 1C (например `http://host/g8_mis`) |
| `MIS_API_KEY` | Ключ API 1C |
| `MIS_USER`, `MIS_PASSWORD` | HTTP Basic Auth для 1C |
| `SESSION_TTL_HOURS` | Время жизни неактивных сессий (по умолчанию 24) |
| `DOCTORS_REFRESH_INTERVAL_MINUTES` | Интервал обновления врачей (по умолчанию 60) |

---

## Деплой и автозапуск

- **Сервис:** systemd `zapisvrachi-bot.service`
- **Установка:** `sudo bash deploy/install-service.sh`
- **Автозапуск:** при загрузке системы (`WantedBy=multi-user.target`)
- **Перезапуск при падении:** `Restart=always`, `RestartSec=5`
- **Логи:** `bot.log` (append) или `journalctl -u zapisvrachi-bot -f`

---

## История

Проект — миграция с N8N (low-code) на Python + PostgreSQL. Логика обработки сообщений, интеграция с 1C МИС и структура данных воспроизводят исходный N8N-сценарий.
