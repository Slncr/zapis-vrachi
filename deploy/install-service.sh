#!/usr/bin/env bash
# Раньше ставился systemd-unit. Сейчас весь бекенд крутится в Docker.
set -euo pipefail
echo "Деплой через Docker (из корня репозитория):"
echo "  test -f .env || cp .env.example .env"
echo "  # отредактируйте .env: MIS_*, MAX_BOT_TOKEN"
echo "  docker compose up -d --build"
echo "  docker compose logs -f api"
exit 0
