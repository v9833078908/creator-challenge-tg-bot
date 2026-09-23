# Деплой Creator Hero Bot на VPS

Целевая конфигурация: любой Ubuntu 22.04/24.04 VPS (хватит 1 vCPU / 1 GB RAM), Docker + Docker Compose plugin. Боту не нужен домен, SSL и открытые порты — исходящий long polling к api.telegram.org.

## 1. Установка Docker на VPS (один раз)

```bash
ssh user@your-vps
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER   # перелогиниться после
docker version && docker compose version
```

## 2. Перенос кода на VPS

Вариант A — git (рекомендуется):
```bash
ssh user@your-vps
git clone <ваш-репозиторий> creator-hero-bot
cd creator-hero-bot
```

Вариант B — rsync с локальной машины (см. scripts/deploy.sh):
```bash
./scripts/deploy.sh user@your-vps
```

## 3. Настройка

```bash
cd creator-hero-bot
cp .env.example .env
nano .env   # BOT_TOKEN от @BotFather, OWNER_TG_ID — ваш id (@userinfobot)
```

## 4. Запуск

```bash
docker compose up -d --build
docker compose logs -f bot     # проверить: "migrations applied" → "bot started"
```

Проверка в Telegram: группа → бот админом → `/init_stream` → в личке боту `/approve @username` → друг пишет боту `/start`.

## 5. Обновление (после изменений кода)

```bash
cd creator-hero-bot
git pull                    # или rsync заново
docker compose up -d --build
```

Миграции БД накатываются автоматически при старте контейнера. Данные (SQLite) живут в named volume `creator-bot-data` — пересборка их не трогает.

## 6. Бэкапы

```bash
./scripts/backup.sh                 # локально
./scripts/backup.sh user@your-vps   # с VPS на локальную машину
```

Скрипт использует SQLite backup API и проверяет целостность копии. На выходе
получается файл `.db` в `./backups/`. Рекомендуется cron:
`0 4 * * * /path/to/scripts/backup.sh user@your-vps`.

Проверка восстановления без влияния на рабочий бот:

```bash
python -m bot.backup restore backups/creator-bot-YYYY-MM-DD_HH-MM-SS.db /tmp/creator-bot-check.db
python -c 'import sqlite3; print(sqlite3.connect("/tmp/creator-bot-check.db").execute("pragma integrity_check").fetchone())'
```

Восстановление локального Docker volume из проверенной копии:

```bash
./scripts/restore.sh backups/creator-bot-YYYY-MM-DD_HH-MM-SS.db
```

Скрипт останавливает бота перед заменой файла и снова запускает его. Для VPS
сначала скопируйте нужный `.db` на сервер, затем запустите `restore.sh` там.

## 7. Полезные команды

```bash
docker compose logs -f bot        # логи (ротация настроена: 3 файла по 10 МБ)
docker compose restart bot        # перезапуск
docker compose ps                 # статус (есть healthcheck)
docker exec -it creator-bot sh    # зайти внутрь контейнера
```

## Замечания

- `restart: unless-stopped` — бот сам поднимется после перезагрузки VPS.
- Часовые пояса джобов — по таймзоне потока (`CHALLENGE_TIMEZONE`, по умолчанию Europe/Moscow), не от TZ сервера.
- Если Telegram API заблокирован в регионе VPS — берите VPS вне РФ или настройте прокси (в коде не требуется, только сеть).

## Проверка перед первым запуском с друзьями

1. В тестовой группе проверить `/init_stream`, `/approve`, `/start`, вход друга и уведомление о неизвестном госте. Гость остаётся в группе.
2. Проверить `#пост` до дедлайна и после него, а также смену username участника.
3. Перезапустить контейнер с накопленным сообщением и убедиться, что отчёт обработан по времени отправки.
4. Проверить закрытие дня, штраф, `#штраф`, кик и повторное `/approve` на тестовых аккаунтах.
5. Сделать бэкап, восстановить его в отдельный файл и проверить `PRAGMA integrity_check`.
6. Убедиться, что `docker compose ps` показывает здоровый контейнер и что владелец получает сообщения об ошибках кика.

При первом обновлении со старой схемы миграция восстановит только последний
известный интервал членства. Если поток уже существовал вчера, автоматически
будет закрыт лишь этот день. Более ранние дни без истории бот не пересчитывает.
После обновления история
входов и выходов ведётся полностью.
