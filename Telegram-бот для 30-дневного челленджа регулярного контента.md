# ТЗ: Telegram-бот для 30-дневного челленджа регулярного контента

## 1. Цель продукта

Создать Telegram-бота для проведения групповых челленджей, которые помогают пользователям сформировать привычку регулярно публиковать контент.

Основной сценарий:

> Пользователь вступает в 30-дневный челлендж и обязуется каждый день публиковать минимум один пост в любой социальной сети. До конца дня пользователь должен прислать боту ссылку на опубликованный пост. Если публикации нет — бот фиксирует пропуск и назначает штраф. Невыполненный штраф приводит к исключению из челленджа.

Главная ценность продукта:

**не помогать создавать контент, а создавать внешний forcing function, который заставляет пользователя регулярно нажимать Publish.**

---

# 2. MVP

Первая версия предназначена для закрытой группы друзей.

Количество участников:

- 5–30 человек;
- один одновременно активный челлендж;
- длительность — 30 дней;
- управление через Telegram;
- отдельное веб-приложение не требуется.

MVP должен включать:

1. Telegram-бота.
2. Telegram-группу участников.
3. Регистрацию пользователей.
4. Ежедневные check-in.
5. Проверку дедлайна.
6. Streak.
7. Пропуски.
8. Штрафы.
9. Подтверждение выполнения штрафа.
10. Исключение пользователя.
11. Общий leaderboard.
12. Ежедневные напоминания.
13. Админские команды.
14. Историю активности пользователей.

---

# 3. Основная механика челленджа

## 3.1. Правило

Каждый участник должен:

**каждый календарный день опубликовать минимум один оригинальный публичный пост.**

Допустимые площадки:

- Telegram;
- LinkedIn;
- X / Twitter;
- Instagram;
- Threads;
- Facebook;
- VK;
- YouTube;
- TikTok;
- другие публичные площадки.

На MVP бот не обязан иметь API-интеграции с социальными сетями.

Доказательством выполнения является URL публикации.

Пример:

`https://t.me/channel/123`

или

`https://www.linkedin.com/posts/...`

---

# 4. День челленджа

Часовой пояс задаётся на уровне челленджа.

Для первого MVP:

`Europe/Moscow`

или configurable через ENV / admin setting.

Каждый challenge day длится:

`00:00–23:59`

До 23:59 пользователь должен зарегистрировать публикацию.

После наступления 00:00 предыдущий день автоматически закрывается.

---

# 5. Структура Telegram

Необходимо использовать:

### 5.1. Bot

Личные сообщения пользователю:

- onboarding;
- reminders;
- подтверждение check-in;
- информация о streak;
- уведомление о штрафе;
- подтверждение выполнения штрафа;
- статистика.

### 5.2. Общая Telegram-группа

Используется для:

- общения;
- social accountability;
- ежедневных итогов;
- leaderboard;
- announcements;
- поддержки участников.

Бот должен быть администратором группы.

Желательно иметь permission:

- читать сообщения;
- публиковать сообщения;
- удалять пользователя из группы.

---

# 6. Onboarding

## 6.1. Старт

Пользователь открывает бота:

`/start`

Бот показывает:

> Добро пожаловать в 30 Days of Showing Up.
>
> Следующие 30 дней твоя задача — каждый день публиковать хотя бы один пост.
>
> Публикация считается выполненной, если до 23:59 ты прислал ссылку на неё боту.
>
> Пропустил день → получаешь штраф.
>
> Не выполнил штраф → покидаешь челлендж.

Кнопка:

`Вступить в челлендж`

---

## 6.2. Подтверждение правил

Пользователь должен подтвердить:

- понимаю, что нужно публиковаться каждый день;
- понимаю deadline;
- понимаю штрафную механику;
- согласен, что результаты могут показываться группе.

Кнопка:

`Принимаю правила`

---

## 6.3. Имя

По умолчанию:

Telegram first_name.

Пользователь может изменить отображаемое имя.

---

## 6.4. Выбор штрафа

На MVP пользователь выбирает один основной вариант.

### Charity

При пропуске необходимо пожертвовать:

`500 ₽ / €5 / configurable amount`

в любую благотворительную организацию.

Proof:

скриншот оплаты.

### Exercise

Например:

`30 burpees`

Proof:

видео.

### Custom

Пользователь пишет собственный штраф.

Например:

> 50 приседаний.

Для первой версии можно оставить только:

- Charity;
- 30 burpees.

---

# 7. Старт челленджа

Challenge имеет:

- id;
- title;
- start_date;
- end_date;
- timezone;
- status;
- Telegram group ID.

Статусы:

`draft`

`registration`

`active`

`completed`

После старта новые пользователи по умолчанию не принимаются.

Администратор может добавить человека вручную.

---

# 8. Daily check-in

## 8.1. Основной UX

Пользователь просто отправляет боту ссылку.

Например:

> https://t.me/mychannel/123

Бот определяет URL.

Если сегодня check-in ещё отсутствует:

создаёт completion record.

Ответ:

> ✅ День 7 выполнен.
>
> 🔥 Streak: 7 дней
>
> Ты опубликовался 7/7 дней.
>
> Сегодня выполнили: 9 из 12 участников.

---

## 8.2. Несколько ссылок

Если пользователь уже выполнил день и отправляет вторую ссылку:

> ✅ Сегодняшний день уже закрыт.
>
> Дополнительный пост сохранён, но streak не изменяется.

Дополнительные URL можно сохранять в базе.

---

## 8.3. Неверный URL

Если сообщение не содержит валидную ссылку:

> Чтобы закрыть сегодняшний день, пришли ссылку на опубликованный пост.

---

# 9. Валидация публикации

MVP:

валидировать только URL.

Проверять:

- URL syntactically valid;
- protocol HTTP/HTTPS;
- ссылка ещё не использована этим пользователем как check-in другого дня.

Не требуется:

- проверять время публикации;
- заходить в социальную сеть;
- проверять содержание;
- использовать API Instagram / LinkedIn / Telegram;
- определять автора публикации.

Это сознательная MVP-оптимизация.

---

# 10. Streak

У каждого пользователя есть:

`current_streak`

`best_streak`

`completed_days`

`missed_days`

Streak увеличивается после успешного check-in.

Пример:

Day 1 ✅ → streak 1

Day 2 ✅ → streak 2

Day 3 ✅ → streak 3

Day 4 ❌ → streak 0

Day 5 ✅ → streak 1

Best streak при этом остаётся 3.

---

# 11. Reminder engine

Бот проверяет всех участников активного челленджа.

Напоминания отправлять только тем, кто сегодня ещё не сделал check-in.

## 11.1. Morning

09:00:

> ☀️ День 8/30.
>
> 🔥 Твой streak: 7.
>
> Сегодня нужно опубликовать один пост и прислать сюда ссылку до 23:59.

---

## 11.2. Evening

20:00:

> ⏰ Сегодняшний пост ещё не отмечен.
>
> До дедлайна 4 часа.
>
> Уже выполнили: 8 из 12 участников.

---

## 11.3. Final reminder

22:30:

> 🚨 Осталось 90 минут.
>
> Сегодня ты пока не закрыл challenge.
>
> Опубликуй пост и пришли ссылку сюда.

---

## 11.4. Optional social-pressure message

Если осталось мало людей:

> Сегодня уже 10 из 12 участников опубликовались.
>
> Осталось двое 👀

Не использовать конкретные имена в личном сообщении без необходимости.

---

# 12. Закрытие дня

Scheduler запускается после 00:00.

Для каждого активного участника проверяется предыдущий день.

Если check-in существует:

`status = completed`

Если отсутствует:

`status = missed`

После чего:

- streak = 0;
- missed_days += 1;
- создаётся penalty;
- отправляется уведомление пользователю.

---

# 13. Penalty

После пропуска:

> ⚠️ Ты пропустил День 8.
>
> Streak сброшен.
>
> Теперь нужно выполнить штраф.
>
> Твой штраф:
>
> 💸 Пожертвовать €5 в благотворительную организацию.
>
> Выполни его в течение 24 часов и пришли подтверждение сюда.
>
> Deadline: 21 сентября, 00:00.

Кнопка:

`Я выполнил штраф`

---

# 14. Proof штрафа

После кнопки:

`Я выполнил штраф`

бот пишет:

> Пришли подтверждение выполнения.
>
> Для charity — screenshot оплаты.
>
> Для burpees — видео.

Следующее фото / видео сохраняется как proof.

Penalty получает статус:

`proof_submitted`

---

# 15. Проверка proof

Для MVP есть два возможных режима.

Рекомендуется первый.

## Режим A — automatic trust

После получения файла:

`penalty = completed`

Бот:

> ✅ Штраф закрыт.
>
> Ты остаёшься в challenge.
>
> Сегодня начинаем новый streak.

Пользователи видят друг друга, поэтому social accountability снижает мотивацию обманывать.

---

## Режим B — admin approval

Администратор получает сообщение:

> Penalty proof
>
> @username
>
> Day 8
>
> Charity €5

Кнопки:

`Approve`

`Reject`

До approval статус:

`proof_submitted`.

Архитектуру желательно сразу сделать так, чтобы оба режима поддерживались.

---

# 16. Невыполненный штраф

Penalty deadline:

24 часа после создания penalty.

Если penalty не закрыт:

`penalty.status = failed`

Пользователь:

`challenge_member.status = eliminated`

Бот пишет:

> ❌ Штраф не был выполнен вовремя.
>
> Поэтому ты покидаешь текущий challenge.
>
> Результат:
>
> ✅ выполнено: 8 дней  
> ❌ пропущено: 2 дня  
> 🔥 лучший streak: 7 дней

После этого бот удаляет пользователя из Telegram-группы.

---

# 17. Grace day

На MVP желательно предусмотреть один:

`grace_day`

на 30-дневный challenge.

Пользователь может использовать его вместо penalty.

Команда:

`/skip`

или кнопка:

`Использовать grace day`

Условия:

- только один раз;
- решение нужно принять до penalty deadline;
- день считается missed;
- streak сбрасывается;
- штраф отменяется;
- пользователь остаётся в challenge.

Это защищает систему от болезни, перелёта и форс-мажора.

Настройка должна позволять администратору:

`grace_days = 0/1/N`.

---

# 18. Главное меню

Команда:

`/menu`

Кнопки:

### ✅ Отметить пост

Инструкция отправить URL.

### 🔥 Мой streak

Показывает:

> День: 12/30  
> 🔥 streak: 7  
> 🏆 best: 11  
> ✅ completed: 11  
> ❌ missed: 1  
> 🎟 grace days: 1

### 🏆 Leaderboard

### 📊 Моя статистика

### 📜 Правила

### 🆘 Помощь

---

# 19. Daily leaderboard

Каждый день после закрытия дня бот публикует в группу summary.

Пример:

> 🏆 DAY 7 COMPLETE
>
> ✅ 10/12 участников опубликовались
>
> 🔥 Current streaks
>
> 1. Anna — 7  
> 2. Lisa — 7  
> 3. Max — 7  
> 4. Dima — 6
>
> ⚠️ 2 участника получили штраф.
>
> 📊 Group consistency: 83%

Важно:

не сообщать размер конкретного денежного штрафа без необходимости.

---

# 20. Общий leaderboard

Сортировка:

1. `completed_days DESC`
2. `current_streak DESC`
3. `best_streak DESC`

Пример:

> 🏆 LEADERBOARD
>
> 1. Anna — 18/18 🔥18
> 2. Max — 18/18 🔥18
> 3. Lisa — 17/18 🔥5
> 4. Alex — 16/18 🔥3

Views, likes, followers и engagement социальных сетей не учитывать.

Задача challenge — consistency, а не популярность.

---

# 21. Group statistics

Хранить:

- total posts;
- total successful days;
- total missed days;
- penalties created;
- penalties completed;
- penalties failed;
- active members;
- eliminated members;
- challenge completion rate;
- group consistency %.

Формула:

`total completed check-ins / total expected check-ins`

---

# 22. Weekly report

Каждые 7 дней публиковать:

> 📊 WEEK 1
>
> 👥 12 участников
>
> 📝 79 постов опубликовано
>
> ✅ Consistency: 94%
>
> 🔥 8 участников сохранили perfect streak
>
> ⚠️ 5 penalties
>
> ❌ 1 участник выбыл
>
> Продолжаем.

---

# 23. Финал challenge

После Day 30:

пользователь получает:

> 🏁 Challenge complete.
>
> Ты прошёл 30 дней.
>
> ✅ Опубликовано: 28/30
>
> 🔥 Best streak: 17
>
> ❌ Missed: 2
>
> 📝 Posts submitted: 31
>
> Ты опубликовал 28 постов, которые без challenge могли бы так и остаться идеями.

Общий чат:

> 🏆 30 DAYS COMPLETE
>
> Started: 15
>
> Finished: 12
>
> Perfect 30/30: 7
>
> Total posts: 391
>
> Group consistency: 87%

---

# 24. Retrospective

После окончания challenge бот задаёт три вопроса.

### Question 1

> Сколько постов из этих 30 ты, скорее всего, НЕ опубликовал бы без challenge?

Ответ:

0–30.

### Question 2

> Что сильнее всего заставляло тебя публиковаться?

Кнопки:

- streak;
- штраф;
- группа;
- reminders;
- публичное обещание;
- другое.

### Question 3

> Хочешь пройти ещё один challenge?

- Да
- Нет
- Возможно

Ответы сохранить.

Это критически важно для проверки product hypothesis.

---

# 25. Admin

Admin определяется через Telegram user ID.

ENV:

`ADMIN_TELEGRAM_IDS`

Admin commands:

`/admin`

---

## 25.1. Создание challenge

Admin задаёт:

- title;
- duration;
- start date;
- timezone;
- Telegram group;
- penalty rules;
- grace days;
- max participants.

---

## 25.2. Admin dashboard внутри Telegram

Показывает:

- participants;
- active users;
- сегодняшние check-ins;
- missing users;
- open penalties;
- eliminated users.

---

## 25.3. Admin commands

Минимум:

`/participants`

`/today`

`/penalties`

`/kick USER`

`/restore USER`

`/complete USER`

`/miss USER`

`/start_challenge`

`/stop_challenge`

---

# 26. Ручное исправление ошибок

Admin должен иметь возможность:

- вручную закрыть день;
- отменить ошибочный check-in;
- отменить penalty;
- восстановить streak;
- вернуть пользователя;
- изменить deadline;
- отключить reminder.

Это важно, потому что в MVP будут edge cases.

---

# 27. Состояния пользователя

ChallengeMember:

```text
registered
active
eliminated
completed
left
```

---

# 28. Состояния дня

DailyCheckin:

```text
pending
completed
missed
excused
```

---

# 29. Состояния penalty

```text
pending
proof_submitted
completed
failed
cancelled
```

---

# 30. Data model

Рекомендуемая база:

PostgreSQL.

Для локального MVP допустим SQLite.

## users

```text
id
telegram_user_id
telegram_username
first_name
display_name
created_at
updated_at
```

## challenges

```text
id
title
description
start_date
end_date
timezone
duration_days
telegram_group_id
status
grace_days_allowed
created_at
updated_at
```

## challenge_members

```text
id
challenge_id
user_id
status
joined_at

current_streak
best_streak
completed_days
missed_days
grace_days_used

penalty_type
penalty_amount
penalty_description

eliminated_at
completed_at
```

## daily_checkins

```text
id
challenge_member_id
challenge_day
date

status

primary_post_url

created_at
completed_at
```

Unique:

```text
challenge_member_id + challenge_day
```

## post_submissions

```text
id
daily_checkin_id
url
created_at
```

## penalties

```text
id
challenge_member_id
daily_checkin_id

type
amount
description

status

created_at
deadline_at
proof_submitted_at
completed_at
```

## penalty_proofs

```text
id
penalty_id

telegram_file_id
file_type
message_id

created_at
```

## reminders

```text
id
challenge_member_id
challenge_day
reminder_type
sent_at
```

## audit_log

```text
id
actor_user_id
action
entity_type
entity_id
metadata_json
created_at
```

---

# 31. Scheduler

Нужен background scheduler.

Можно использовать:

- APScheduler;
- Celery;
- cron + application commands;
- отдельный worker.

Для MVP предпочтительно:

**APScheduler или cron-based worker.**

Jobs:

### 09:00

morning reminder

### 20:00

evening reminder

### 22:30

final reminder

### 00:01

close challenge day

### 00:05

publish daily leaderboard

### каждый час

check expired penalties

Все операции должны быть idempotent.

Повторный запуск job не должен:

- дважды создать penalty;
- дважды отправить одинаковый reminder;
- дважды закрыть день.

---

# 32. Telegram framework

Рекомендуемый стек:

Python 3.12+

Telegram:

**aiogram 3.x**

Backend:

можно вообще без FastAPI для MVP.

Если API планируется дальше:

**FastAPI**

Database:

**PostgreSQL + asyncpg**

Migrations:

Alembic либо простые SQL migrations.

Scheduler:

APScheduler.

Deployment:

Docker Compose.

---

# 33. Архитектура

```text
Telegram
   │
   ▼
Bot Handlers
   │
   ▼
Application Services
   │
   ├── ChallengeService
   ├── CheckinService
   ├── StreakService
   ├── PenaltyService
   ├── ReminderService
   ├── LeaderboardService
   └── AdminService
   │
   ▼
Repository Layer
   │
   ▼
PostgreSQL
```

Отдельно:

```text
Scheduler
   │
   ├── reminders
   ├── daily close
   ├── penalty expiration
   └── reports
```

Business logic не помещать непосредственно в Telegram handlers.

---

# 34. Структура проекта

```text
app/
    bot/
        handlers/
            start.py
            onboarding.py
            checkin.py
            stats.py
            penalties.py
            admin.py

        keyboards/
        middlewares/

    services/
        challenges.py
        checkins.py
        streaks.py
        penalties.py
        reminders.py
        leaderboard.py

    repositories/
        users.py
        challenges.py
        checkins.py
        penalties.py

    models/
    schemas/

    scheduler/
        jobs.py
        setup.py

    db/
        connection.py
        migrations/

    config.py
    main.py

tests/
```

---

# 35. Основные сервисные методы

## CheckinService

```text
submit_post(user_id, url)
get_today_status(user_id)
complete_day(...)
```

## StreakService

```text
increment_streak(member_id)
reset_streak(member_id)
recalculate_streak(member_id)
```

## PenaltyService

```text
create_penalty(...)
submit_proof(...)
approve_penalty(...)
expire_penalty(...)
cancel_penalty(...)
```

## ChallengeService

```text
join(...)
start(...)
finish(...)
eliminate_member(...)
restore_member(...)
```

## LeaderboardService

```text
get_daily_leaderboard(...)
get_challenge_leaderboard(...)
get_group_stats(...)
```

---

# 36. Critical business rules

### Rule 1

Один пользователь может закрыть challenge day только один раз.

### Rule 2

Повторная ссылка не создаёт второй completed day.

### Rule 3

Check-in после deadline относится к текущему дню, а не автоматически к предыдущему.

Admin может исправить это вручную.

### Rule 4

Miss автоматически создаёт penalty.

### Rule 5

Создание penalty происходит ровно один раз.

### Rule 6

Неисполненный penalty → elimination.

### Rule 7

Completed penalty не восстанавливает streak.

### Rule 8

Grace day отменяет penalty, но не сохраняет streak.

### Rule 9

Eliminated user больше не получает reminders.

### Rule 10

Scheduler operations должны быть idempotent.

---

# 37. Edge cases

Обязательно протестировать:

- пользователь прислал ссылку в 23:59;
- сообщение пришло Telegram API после 00:00;
- scheduler перезапустился;
- сервер был недоступен в момент 00:01;
- пользователь отправил одну ссылку дважды;
- пользователь удалил Telegram username;
- user blocked bot;
- admin случайно удалил check-in;
- Telegram group недоступна;
- penalty proof пришёл за секунду до deadline;
- сервер работает в UTC, challenge — в другом timezone.

Внутри системы все timestamps хранить в UTC.

Business date рассчитывать через timezone challenge.

---

# 38. Logging

Логировать:

- joins;
- check-ins;
- missed days;
- streak changes;
- penalties;
- proof submissions;
- eliminations;
- manual admin operations;
- scheduler runs;
- Telegram API errors.

Использовать structured logging.

---

# 39. Monitoring

Минимально:

- healthcheck;
- error logs;
- notification admin при critical error.

Например:

> 🚨 Scheduler failed: close_daily_challenge

---

# 40. Privacy

Не хранить лишние данные.

Хранить только:

- Telegram ID;
- username/name;
- ссылки на публикации;
- penalty proof;
- challenge statistics.

Если proof содержит screenshot платежа, желательно предусмотреть удаление файла после окончания challenge.

---

# 41. Что НЕ входит в MVP

Не реализовывать:

- AI generation;
- идеи постов через LLM;
- редактор контента;
- автопостинг;
- OAuth соцсетей;
- Instagram API;
- LinkedIn API;
- автоматическую проверку ownership публикации;
- analytics views/likes;
- payment processing;
- банковские карты;
- встроенную charity оплату;
- web dashboard;
- mobile app;
- referrals;
- subscriptions;
- несколько параллельных групп;
- автоматический content quality scoring.

---

# 42. Phase 2

После проверки MVP можно добавить:

### Flexible challenges

7 / 14 / 30 / 60 / 100 дней.

### Private groups

Пользователь создаёт собственный challenge.

### Stakes

Каждый задаёт размер штрафа.

### Accountability partner

Друг подтверждает выполнение.

### Automatic post verification

Telegram / X / LinkedIn API.

### AI coach

Предлагает тему, если до вечера ничего не опубликовано.

### Rescue prompt

22:30:

> Нет идеи?
>
> Ответь одним предложением:
> чему ты сегодня научился?
>
> Я превращу это в черновик поста.

Но AI здесь остаётся вспомогательной функцией.

---

# 43. Ключевые продуктовые метрики

Сохранять с первого дня:

### Activation

Процент зарегистрированных, сделавших Day 1.

### D7 consistency

Сколько пользователей продолжают публиковаться через неделю.

### D14 consistency

### Challenge completion

Сколько дошли до Day 30.

### Perfect completion

Сколько сделали 30/30.

### Average completed days

### Penalty rate

### Penalty completion rate

### Elimination rate

### Core value metric

После challenge:

> Сколько публикаций пользователь считает такими, которые не появились бы без challenge.

Это наиболее важная метрика продукта.

---

# 44. Acceptance criteria MVP

MVP считается готовым, если можно выполнить следующий сценарий без ручного доступа к базе:

1. Admin создаёт challenge.
2. Пользователь запускает `/start`.
3. Пользователь принимает правила.
4. Пользователь выбирает штраф.
5. Пользователь присоединяется.
6. Admin стартует challenge.
7. Пользователь получает утренний reminder.
8. Пользователь присылает URL.
9. День фиксируется как completed.
10. Streak увеличивается.
11. Пользователь появляется в leaderboard.
12. Другой пользователь ничего не отправляет.
13. После deadline его день становится missed.
14. Streak сбрасывается.
15. Создаётся penalty.
16. Пользователь получает уведомление.
17. Пользователь отправляет screenshot proof.
18. Penalty закрывается.
19. При невыполнении penalty другой пользователь автоматически исключается.
20. Бот публикует daily summary в общий чат.
21. Admin может вручную исправить любой ошибочный статус.
22. После 30 дней challenge автоматически завершается.
23. Каждый участник получает финальную статистику.

---

# 45. Приоритет разработки

## P0 — обязательно

- Telegram onboarding;
- challenge;
- URL check-in;
- daily deadline;
- streak;
- missed day;
- penalties;
- penalty proof;
- reminders;
- leaderboard;
- Telegram group integration;
- admin tools;
- scheduler;
- PostgreSQL;
- Docker.

## P1

- grace day;
- weekly stats;
- retrospective;
- admin approval penalty;
- multiple penalty types.

## P2

- AI;
- social APIs;
- automatic verification;
- custom challenge creation;
- payments;
- web UI.

---

# 46. Главное требование к реализации

Не превращать MVP в creator SaaS.

Главный цикл продукта должен оставаться максимально коротким:

```text
Нужно опубликоваться
        ↓
Пользователь публикует
        ↓
Кидает URL боту
        ↓
Получает ✅ + streak
        ↓
Видит прогресс группы
        ↓
Возвращается завтра
```

При отсутствии публикации:

```text
Нет URL до deadline
        ↓
Miss
        ↓
Streak = 0
        ↓
Penalty
        ↓
Proof
        ↓
Возвращение в challenge
```

или:

```text
Penalty не выполнен
        ↓
Elimination
```

Вся первая версия должна оптимизироваться вокруг одного поведения:

> **каждый день пользователь должен опубликовать что-нибудь публично.**