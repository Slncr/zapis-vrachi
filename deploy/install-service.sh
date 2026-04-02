#!/bin/bash
# Установка автозапуска бота Zapisvrachi через systemd.
# Запуск: sudo bash deploy/install-service.sh
# Путь к проекту по умолчанию: /root/Zapisvrachi (измените PROJECT_DIR при необходимости).

set -e
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE_NAME="zapisvrachi-bot"

echo "Проект: $PROJECT_DIR"

# Файл .env обязателен
if [ ! -f "$PROJECT_DIR/.env" ]; then
  if [ -f "$PROJECT_DIR/.env.example" ]; then
    echo "Создайте .env и заполните переменные: cp $PROJECT_DIR/.env.example $PROJECT_DIR/.env"
    echo "Затем отредактируйте: nano $PROJECT_DIR/.env"
    exit 1
  else
    echo "Файл .env не найден. Создайте его с переменными TELEGRAM_BOT_TOKEN, DATABASE_URL, MIS_*"
    exit 1
  fi
fi

# Подставляем путь проекта в unit-файл
SERVICE_FILE="$PROJECT_DIR/deploy/zapisvrachi-bot.service"
TMP_SERVICE="/tmp/zapisvrachi-bot.service"
sed "s|/root/Zapisvrachi|$PROJECT_DIR|g" "$SERVICE_FILE" > "$TMP_SERVICE"

# Копируем в systemd
cp "$TMP_SERVICE" /etc/systemd/system/zapisvrachi-bot.service
rm -f "$TMP_SERVICE"

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"

# Останавливаем старый процесс, если бот был запущен вручную (nohup)
pkill -f "python main.py" 2>/dev/null || true
sleep 1

systemctl start "$SERVICE_NAME"

echo "Сервис установлен и запущен. Автозапуск при загрузке системы включён."
echo "Команды:"
echo "  sudo systemctl status $SERVICE_NAME   — статус"
echo "  sudo systemctl restart $SERVICE_NAME  — перезапуск"
echo "  sudo systemctl stop $SERVICE_NAME     — остановка"
echo "  journalctl -u $SERVICE_NAME -f        — лог (или tail -f $PROJECT_DIR/bot.log)"
