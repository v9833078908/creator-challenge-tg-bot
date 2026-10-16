"""Все тексты бота. Свои формулировки, тон — игровой, на «ты» (как у оригинала, но не копипаст)."""

import html
import random

from bot.config import settings


def _name(full_name: str, username: str | None) -> str:
    raw = full_name or (f"@{username}" if username else "друг")
    return html.escape(raw)  # parse_mode=HTML: экранируем пользовательские имена


# --- Личка: /start ---------------------------------------------------------


def start_invited(full_name: str, username: str | None, stream_title: str, invite_link: str) -> str:
    return (
        f"👑 Привет, {_name(full_name, username)}!\n\n"
        f"Это Creator Hero — челлендж, где мы пишем по посту в день. Ты в списке, так что держи вход:\n\n"
        f"{invite_link}\n\n"
        f"Правила простые: каждый день до {settings.report_deadline} кидаешь в чат #пост — "
        f"ссылку, форвард или текст от {settings.min_post_chars} символов. "
        f"Пропустил — штраф, просрочил штраф — вылет. Всё честно 🤝\n\n"
        f"Залетай в «{html.escape(stream_title)}» — ждём!"
    )


def start_already_active(full_name: str, username: str | None, invite_link: str) -> str:
    return (
        f"Ты уже в игре, {_name(full_name, username)} 😎 "
        f"Если вдруг потерял ссылку — держи: {invite_link}"
    )


def start_refused() -> str:
    return (
        "Челлендж закрытый, и тебя пока нет в списке участников 🙈\n"
        "Попроси организатора челленджа добавить твой @username — и возвращайся."
    )


def start_kicked() -> str:
    return (
        "Сейчас доступ к челленджу приостановлен. Для возврата свяжись с организатором — "
        "он поможет вернуться в игру. Прогресс уроков сохранён."
    )

def start_no_stream() -> str:
    return "Челлендж ещё не настроен. Организатор, выполни /init_stream в группе — и я оживу."


# --- Группа: вход/выход ----------------------------------------------------


def welcome_joined(full_name: str, username: str | None, *, first_join: bool = False) -> str:
    name = html.escape(f"@{username}") if username else _name(full_name, username)
    if first_join:
        return (
            f"Да это же {name}, привет! "
            "Представься с хештегом #обомне и напиши свою #цель "
            "чтобы другие участники познакомились с тобой 👋"
        )
    return f"Оооо, да это же {name}! С возвращением!"


def welcome_back_with_penalties(full_name: str, username: str | None, count: int) -> str:
    return (
        f"{welcome_joined(full_name, username)}\n"
        "У тебя остался неотработанный штраф — у тебя "
        f"{settings.penalty_grace_hours} часа, чтобы закинуть #штраф, иначе вылет."
    )


def welcome_guest(full_name: str, username: str | None) -> str:
    return (
        f"👋 {_name(full_name, username)}, привет! Добро пожаловать в чат Creator Hero. "
        "Тебя пока нет в списке участников — для подключения к челленджу обратись к организатору. "
        "Пока я не учитываю твои отчёты и не начисляю штрафы 🫶"
    )


def unknown_joined(full_name: str, username: str | None, stream_title: str) -> str:
    return (
        f"В «{html.escape(stream_title)}» вошёл {_name(full_name, username)}, "
        "кого пока нет в списке. Я никого не удалял; реши сам, нужно ли добавить друга 👋"
    )


# --- Владелец: команды -----------------------------------------------------


def owner_only() -> str:
    return "Эта команда только для организатора челленджа."


def stream_created(title: str, invite_link: str) -> str:
    return (
        f"👑 Поток «{html.escape(title)}» создан, чат привязан!\n"
        f"Бессрочная ссылка: {invite_link}\n\n"
        f"Теперь добавь участников в личке: /approve @username"
    )


def stream_exists() -> str:
    return "Этот чат уже привязан к потоку. Второй раз /init_stream не нужен 🙂"


def stream_need_admin() -> str:
    return (
        "Не могу создать инвайт-ссылку — сделай меня админом чата "
        "(права: пригласительные ссылки, блокировка пользователей) и повтори /init_stream."
    )


def no_stream_admin() -> str:
    return "Потока пока нет. Сначала выполни /init_stream в группе челленджа."


def approve_usage() -> str:
    return "Формат: /approve @username или /approve TelegramID (уже привязанного участника)."


def _owner_target(value: str) -> str:
    return f"Telegram ID {value}" if value.isdecimal() else f"@{value}"


def approve_ok(username: str) -> str:
    return (
        f"✅ {_owner_target(username)} в списке. Пусть напишет мне в личку /start — выдам ссылку на чат."
    )


def approve_back(username: str) -> str:
    return (
        f"✅ {_owner_target(username)} возвращён в челлендж: бан снят, штрафы обнулены. "
        f"Пусть напишет мне /start — выдам ссылку."
    )


def unban_failed(username: str) -> str:
    return f"Не смог снять бан с {_owner_target(username)}. Проверь мои права в группе и повтори /approve."


def approve_already(username: str) -> str:
    return f"{_owner_target(username)} уже в списке/в игре — ничего не менял."


def revoke_usage() -> str:
    return "Формат: /revoke @username или /revoke TelegramID (уже привязанного участника)."


def revoke_ok(username: str) -> str:
    return f"🗑 {_owner_target(username)} убран из списка ожидания."


def revoke_not_found(username: str) -> str:
    return f"{_owner_target(username)} не найден в списке ожидания (или уже вступил в чат — таких не трогаю)."


# --- Группа: отчёты #пост --------------------------------------------------


def display_name(p) -> str:
    """Имя участника для списков (дайджест, напоминания)."""
    raw = p.full_name or (f"@{p.username}" if p.username else "участник")
    return html.escape(raw)


def mention(p) -> str:
    """Кликабельное упоминание (HTML), чтобы человек точно увидел."""
    label = display_name(p)
    return f'<a href="tg://user?id={p.tg_user_id}">{label}</a>' if p.tg_user_id else label


def _join_names(people) -> str:
    return ", ".join(mention(p) for p in people)


def report_accepted(seq: int, total: int, emoji: str) -> str:
    if random.randrange(5) == 0:
        return f"вау, справилось {seq} из {total} героев! ⚡️"
    return f"№{seq} из {total} {emoji}"


def report_already_counted() -> str:
    return "Это уже засчитано сегодня — расслабься, всё чисто! ✨"


def report_not_participant() -> str:
    return (
        "Ты не в списке участников челленджа — напиши мне в личку /start, "
        "и если организатор тебя добавил, влетаешь 👑"
    )


def report_not_active() -> str:
    return (
        "Ты сейчас вне игры (вышел или вылетел). Хочешь обратно — "
        "заходи по ссылке, что я тебе присылал, или напиши организатору."
    )


def report_invalid(min_chars: int) -> str:
    return (
        "Хм, не понял отчёт 🙈 Нужен хештег #пост и одно из:\n"
        "— ссылка на пост (t.me/…)\n"
        "— форвард поста из своего канала\n"
        f"— текст от {min_chars} символов\n"
        "Попробуй ещё раз — редактирование уже отправленного я засчитать не умею, сорян!"
    )


def report_late(deadline: str) -> str:
    return f"Сегодняшний дедлайн {html.escape(deadline)} уже прошёл. Жду новый #пост завтра 👑"


# --- Джобы: напоминания, дайджест, кик -------------------------------------


def reminder_report(missing) -> str:
    return (
        f"⏰ Тик-так! Сегодня до {settings.report_deadline} жду #пост от {_join_names(missing)}.\n"
        "Если отправлял, а я не понял — кинь ещё раз с хештегом, чтобы точно засчитал!"
    )


def digest(for_date, total: int, reported, penalized) -> str:
    lines = [
        "#дайджест 👑 Creator Hero",
        f"📆 {for_date.strftime('%d/%m/%y')}: участников — {total}",
    ]
    if reported:
        lines.append(f"✅ Написали пост — {len(reported)}: {_join_names(reported)}")
    else:
        lines.append("✅ Написали пост — 0. Тихий день 🌫")
    if penalized:
        lines.append(f"💥 Новых штрафов — {len(penalized)}: {_join_names(penalized)}")
    else:
        lines.append("💥 Новых штрафов — 0.")
    return "\n".join(lines)


def penalty_reminder(holders) -> str:
    return (
        f"🧨 До кика меньше суток! Отработайте #штраф, пожалуйста: {_join_names(holders)}.\n"
        f"Напоминаю: донат {settings.penalty_amount_rub} ₽ или видео с 30 бёрпи. "
        "Не хочу никого удалять, но так уж я запрограммирован 😭"
    )


def kick_notice(kicked) -> str:
    return (
        f"😔 Удаляю за неотработанный штраф: {_join_names(kicked)}. "
        "Возвращение — через организатора."
    )


def kicked_dm(full_name: str) -> str:
    return (
        f"Увы, {full_name}: штраф просрочен, и я вынужден удалить тебя из челленджа 🥲\n"
        "Если захочешь вернуться — напиши организатору, он вернёт тебя в список, "
        "а я выдам ссылку заново."
    )


def penalty_paid(count: int) -> str:
    return "Долг закрыт, спасибо! ✨" if count == 1 else f"Долги закрыты ({count} шт.), спасибо! ✨"


def penalty_none() -> str:
    return "На тебе нет открытых штрафов — всё чисто! 💎"


# --- Владелец: статистика ---------------------------------------------------


def stats_text(s) -> str:
    return (
        "📊 Creator Hero — статистика\n"
        f"Активных участников: {s.total_active}\n"
        f"Сдали #пост сегодня: {s.reported_today}\n"
        f"Открытых штрафов: {s.open_penalties}"
        + (f" ({', '.join(s.penalized_names)})" if s.penalized_names else "")
        + f"\nВсего кикнуто: {s.total_kicked}"
    )
