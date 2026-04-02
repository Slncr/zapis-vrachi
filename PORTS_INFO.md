# Порты подключений

## Используемые порты в приложении

### 1. 1C МИС API
- **URL:** `http://185.154.75.166/g8_mis`
- **Порт:** **80** (HTTP) - по умолчанию
- **После редиректа:** **443** (HTTPS)
- **Endpoints:**
  - `/hs/bwi/Schedule` - получение расписания
  - `/hs/bwi/AppointmentCreate` - создание записи
  - `/hs/bwi/AppointmentCancel` - отмена записи

**Примечание:** Сервер делает редирект с HTTP (порт 80) на HTTPS (порт 443).

### 2. PostgreSQL
- **URL:** `postgresql://zapisvrachi_user:zapisvrachi_pass@localhost:5432/zapisvrachi`
- **Порт:** **5432** (стандартный порт PostgreSQL)
- **Хост:** localhost (локальный сервер)

### 3. Telegram API
- **URL:** `https://api.telegram.org`
- **Порт:** **443** (HTTPS)
- **Endpoints:**
  - `/bot{token}/getUpdates` - получение обновлений
  - `/bot{token}/sendMessage` - отправка сообщений

## Проверка доступности портов

### Проверка порта 1C МИС:
```bash
# HTTP (порт 80)
curl -v http://185.154.75.166/g8_mis/hs/bwi/Schedule

# HTTPS (порт 443)
curl -k -v https://185.154.75.166/g8_mis/hs/bwi/Schedule
```

### Проверка порта PostgreSQL:
```bash
# Локальный порт 5432
sudo netstat -tuln | grep 5432
# или
sudo ss -tuln | grep 5432

# Проверка подключения
psql -h localhost -p 5432 -U zapisvrachi_user -d zapisvrachi
```

### Проверка порта Telegram API:
```bash
curl https://api.telegram.org/bot{token}/getMe
```

## Если нужно указать порт явно

Если сервер 1C МИС работает на нестандартном порту, укажите его в `MIS_BASE_URL`:

```bash
# Например, если порт 8080:
export MIS_BASE_URL="http://185.154.75.166:8080/g8_mis"

# Или если порт 8443 для HTTPS:
export MIS_BASE_URL="https://185.154.75.166:8443/g8_mis"
```

## Текущая конфигурация

- **MIS_BASE_URL:** `http://185.154.75.166/g8_mis` (порт 80, редирект на 443)
- **DATABASE_URL:** `postgresql://zapisvrachi_user:zapisvrachi_pass@localhost:5432/zapisvrachi` (порт 5432)
- **Telegram:** `https://api.telegram.org` (порт 443)
