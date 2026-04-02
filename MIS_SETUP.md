# Инструкция по настройке endpoint 1C МИС

## Проблема
API endpoint `/g8_mis/hs/bwi/Schedule` возвращает 404 Not Found.

## Что нужно настроить на сервере 1C

### 1. Проверка HTTP-сервиса в 1C

В конфигурации 1C должен быть настроен HTTP-сервис с путем `/g8_mis/hs/bwi/`.

**Проверьте:**
- В конфигураторе 1C: `Администрирование` → `Публикация на веб-сервере`
- Должен быть опубликован HTTP-сервис с базовым URL: `/g8_mis/hs/bwi/`
- Методы: `Schedule`, `AppointmentCreate`, `AppointmentCancel`

### 2. Настройка веб-сервера (nginx/IIS)

Если используется nginx, проверьте конфигурацию:

```nginx
location /g8_mis/ {
    proxy_pass http://localhost:8080/g8_mis/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

### 3. Проверка доступности

После настройки проверьте:

```bash
curl -X POST "http://185.154.75.166/g8_mis/hs/bwi/Schedule" \
  -u "shindel:199779" \
  -H "Content-Type: application/json" \
  -d '{
    "Key": "bc1ff18a4ee04bb993df251cf0ebbfb4",
    "Method": "GetEnlargementSchedule",
    "StartDate": "30.1.2026 00:00:00",
    "FinishDate": "31.3.2026 00:00:00"
  }'
```

Должен вернуться JSON с данными расписания, а не `{"message":"Not Found"}`.

### 4. Альтернативные пути (если основной не работает)

Если путь `/g8_mis/hs/bwi/` не настроен, попробуйте:
- `/hs/bwi/` (без префикса g8_mis)
- `/g8_mis/hs/` (без bwi)
- Другой путь, указанный администратором 1C

## Текущие настройки в коде

- **Base URL:** `http://185.154.75.166/g8_mis`
- **Endpoints:**
  - `hs/bwi/Schedule` - для GetEnlargementSchedule и GetShedule20
  - `hs/bwi/AppointmentCreate` - для создания записи
  - `hs/bwi/AppointmentCancel` - для отмены записи

## После настройки

После того как endpoint будет настроен на сервере 1C, запустите загрузку врачей:

```bash
cd /root/Zapisvrachi
export TELEGRAM_BOT_TOKEN="..."
export DATABASE_URL="..."
export MIS_BASE_URL="http://185.154.75.166/g8_mis"
export MIS_API_KEY="bc1ff18a4ee04bb993df251cf0ebbfb4"
export MIS_USER="shindel"
export MIS_PASSWORD="199779"
. .venv/bin/activate
python -m scripts.load_doctors
```
