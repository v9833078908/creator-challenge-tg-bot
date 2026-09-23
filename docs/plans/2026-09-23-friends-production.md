# Friends Production Implementation Plan

**Goal:** Make the existing friends challenge reliable without tightening group entry.

**Architecture:** Keep a single polling bot and SQLite. Bind participants to Telegram ID once, preserve pending updates, make day closure durable and recoverable, and add operational checks and backups.

**Tech Stack:** Python 3.12, aiogram 3, SQLAlchemy 2, Alembic, APScheduler 3, SQLite, Docker.

---

1. Add failing tests for username reassignment, unknown member notification, date conversion, configured deadline, and unban failure; make minimal fixes.
2. Add failing tests for durable day closure and missed-run handling; introduce migration and recovery code; verify after restart scenarios.
3. Replace live file tar with SQLite backup API; add restore script and test a round trip.
4. Fix healthcheck, validate settings, escape user-controlled HTML, and pin dependencies.
5. Add CI and deployment runbook; initialize local Git if absent, excluding secrets.
6. Run the full suite, lint, Docker build, migration smoke, backup restore drill, and inspect the final diff.
