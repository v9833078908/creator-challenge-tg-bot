# Working in Creator Hero Bot

This is a single-process Telegram bot for a small friends challenge. Python 3.12,
aiogram, APScheduler, SQLAlchemy, Alembic, and SQLite run in one Docker container.
Carry out authorized, reversible work without asking for a second permission;
ask when required connection details are missing or a destructive choice is unclear.

## Before changing behavior

- Read `docs/architecture.md` for the challenge rules and state transitions.
- Preserve the friendly entry policy: a shared invite link remains valid; an unknown
  joiner stays in the group and the owner receives a notification. The bot does not
  count that guest as a participant.
- Bind an approved participant to a Telegram user ID only once. A later username
  change must not transfer the participant record to another account.
- Keep reports, penalties, and scheduled jobs idempotent. Store schema changes in
  Alembic migrations and cover changed behavior with tests.

## Before deploying or touching data

- Read `docs/deploy.md` for the current VPS path, cutover, backup, and restore steps.
- Run only one polling instance for this token. The VPS instance is the live one;
  keep the local Docker container stopped during normal operation.
- Preserve the Docker volume `creator-bot-data` and the server's `.env` and
  `backups/` directory. Make and verify a SQLite backup before data migration.
- Keep bot tokens and SSH keys out of Git and tool output. Use the local SSH alias
  `creator-hero-vps` for server work.

## Verification

- Run `pytest -q` and `ruff check .` for code changes; build the Docker image for
  packaging or dependency changes.
- For a deployed change, check container health, restart count, and logs on the
  VPS. Check the database revision and `PRAGMA integrity_check` after migration.
- Report separately what was checked locally, what was checked on the VPS, and
  what still needs a human Telegram interaction.
