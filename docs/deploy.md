# Деплой Creator Hero Bot на VPS

Целевая конфигурация: любой Ubuntu 22.04/24.04 VPS (хватит 1 vCPU / 1 GB RAM), Docker + Docker Compose plugin. Боту не нужен домен, SSL и открытые порты — исходящий long polling к api.telegram.org.

## Текущий сервер

- SSH-алиас на рабочем компьютере: `creator-hero-vps` (пользователь `ubuntu`, порт `2222`).
- Каталог проекта: `/home/ubuntu/coding-projects/creator-hero-bot`, рядом с `crossposting-service` и `doctor_council_app`.
- Контейнер: `creator-bot`; Docker volume с БД: `creator-bot-data`.
- Ежедневный серверный бэкап: 01:00 UTC / 04:00 МСК в `backups/`.
- Команда обновления кода без запуска: `./scripts/deploy.sh creator-hero-vps coding-projects/creator-hero-bot --stage-only`.
- После обновления кода: `ssh creator-hero-vps 'cd ~/coding-projects/creator-hero-bot && docker compose up -d --build'`.
- Забрать проверяемую копию БД на рабочий компьютер: `./scripts/backup.sh creator-hero-vps`.

SSH-ключ и токен бота в Git не хранятся. Серверный `.env` находится только в каталоге проекта с правами `600`.

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
./scripts/deploy.sh creator-hero-vps coding-projects/creator-hero-bot --stage-only
```

## 3. Настройка

```bash
cd creator-hero-bot
cp .env.example .env
nano .env   # BOT_TOKEN от @BotFather, OWNER_TG_ID — ваш id (@userinfobot)
```

Адрес VPS, SSH-пользователь и путь к приватному ключу не относятся к `.env`
бота. Их удобно сохранить локально в `~/.ssh/config` под алиасом, например
`creator-hero-vps`. На сервере `.env` содержит только настройки бота. При переносе
существующей локальной конфигурации можно отправить её по SSH:

```bash
scp .env creator-hero-vps:coding-projects/creator-hero-bot/.env
ssh creator-hero-vps 'chmod 600 ~/coding-projects/creator-hero-bot/.env'
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

## Перенос действующего локального бота на VPS

Если локальная группа уже привязана, перенесите и SQLite-базу. Для одного
Telegram-бота одновременно должен работать только один polling-процесс.

1. Подготовьте сервер: установите Docker с Compose и проверьте SSH-доступ.
2. Передайте код без запуска: `./scripts/deploy.sh creator-hero-vps coding-projects/creator-hero-bot --stage-only`.
   Скрипт сохраняет серверные `.env` и `backups/` при следующих обновлениях.
3. Передайте `.env` через `scp`, как показано выше, и соберите образ на сервере:
   `ssh creator-hero-vps 'cd ~/coding-projects/creator-hero-bot && docker compose build'`.
4. Начните короткое переключение: остановите локального бота командой
   `docker compose stop bot`, затем сделайте финальную копию `./scripts/backup.sh`.
5. Скопируйте полученный `.db` в `~/creator-hero-bot/backups/` на VPS. На сервере
   создайте volume `creator-bot-data` и восстановите файл командой
   `python -m bot.backup restore` внутри образа `creator-bot`.
6. Запустите `docker compose up -d` на VPS. Проверьте `docker compose ps`,
   `/stats` у бота и существующий поток. Если запуск не удался, остановите
   контейнер на VPS и снова запустите локальный `docker compose up -d bot`.

Сообщения, отправленные во время переключения, Telegram хранит до запуска
polling на VPS; бот обрабатывает их перед восстановлением расписания.
