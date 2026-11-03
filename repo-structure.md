# Структура репозитория

Монорепозиторий: backend (FastAPI), frontend (React + Vite), общие артефакты (БД, ИИ, документация). Если команда выбрала Node.js, меняется только папка `backend/`, а остальное остаётся тем же.

## Дерево

```text
upk-helper/
├── README.md                    # запуск, архитектура, демо-аккаунты, ограничения (владелец: роль 5)
├── .env.example                 # все переменные без значений-секретов
├── .gitignore                   # .env, node_modules, __pycache__, uploads/, *.db
├── docker-compose.yml           # db + backend + frontend одной командой
├── Makefile                     # make dev | make db | make seed | make test | make qr
│
├── docs/
│   ├── api-contract.md          # контракт API (единый источник правды)
│   ├── repo-structure.md        # этот файл
│   ├── architecture.md          # схема компонентов, потоки данных, решения
│   ├── acceptance-checklist.md  # AT-01…AT-12 с отметками «проверено кем и когда»
│   ├── known-limitations.md     # что не реализовано (обязательно по ТЗ)
│   └── presentation/            # слайды и сценарий защиты
│
├── db/
│   ├── schema.sql               # таблицы, индексы, триггеры
│   ├── seed.sql                 # категории, локации, база знаний
│   └── migrations/              # если используете Alembic/Prisma, иначе пусто
│
├── ai/
│   ├── system_prompt.md         # промпт и правила интеграции
│   ├── knowledge_base.json      # статьи базы знаний (источник для seed)
│   ├── test_cases.json          # 10+ тестовых обращений
│   └── run_tests.py             # прогон тестов через /ai/analyze
│
├── backend/
│   ├── pyproject.toml | requirements.txt
│   ├── Dockerfile
│   ├── app/
│   │   ├── main.py              # создание приложения, CORS, подключение роутеров
│   │   ├── config.py            # чтение .env (pydantic-settings)
│   │   ├── db.py                # подключение, сессии
│   │   ├── models.py            # ORM-модели (по db/schema.sql)
│   │   ├── schemas.py           # pydantic-схемы запросов/ответов (по api-contract.md)
│   │   ├── security.py          # хеш паролей, JWT, токены отслеживания, HMAC
│   │   ├── deps.py              # зависимости: current_user, require_role("staff"/"admin")
│   │   ├── errors.py            # единый формат ошибок
│   │   ├── routers/
│   │   │   ├── public.py        # /locations, /categories, /tickets, /tracking
│   │   │   ├── ai.py            # /ai/analyze
│   │   │   ├── auth.py          # /auth/*
│   │   │   ├── staff.py         # /staff/*
│   │   │   └── admin.py         # /admin/*
│   │   ├── services/
│   │   │   ├── tickets.py       # создание, переходы статусов, история
│   │   │   ├── attachments.py   # проверка типа/размера, сохранение вне публичной папки
│   │   │   ├── stats.py         # запросы статистики
│   │   │   ├── qr.py            # генерация PNG QR
│   │   │   └── ai_service.py    # предфильтр, вызов LLM, валидация, fallback
│   │   └── storage/             # загруженные файлы (в .gitignore, не раздаётся статикой)
│   ├── scripts/
│   │   ├── seed_users.py        # демо-пользователи (пароли из .env или консоли)
│   │   └── make_qr_pack.py      # PNG всех QR + printable.html для печати
│   └── tests/
│       ├── test_tickets.py      # создание, дубликаты, идемпотентность
│       ├── test_transitions.py  # допустимые и недопустимые переходы
│       ├── test_access.py       # без токена 401, staff в /admin 403, чужой токен 404
│       └── test_ai.py           # предфильтр + схема ответа
│
├── frontend/
│   ├── package.json | vite.config.ts
│   ├── Dockerfile
│   └── src/
│       ├── main.tsx, App.tsx    # маршруты (см. раздел 6 контракта)
│       ├── api/
│       │   ├── client.ts        # fetch-обёртка: Bearer, разбор ошибок {error:{…}}
│       │   ├── mocks/           # JSON-моки по контракту для работы до готовности бэкенда
│       │   └── *.ts             # функции по группам: tickets, tracking, staff, ai, auth
│       ├── pages/
│       │   ├── NewTicketPage.tsx        # форма, QR-место, блок ИИ
│       │   ├── TicketCreatedPage.tsx    # номер, ссылка, копирование
│       │   ├── TrackingPage.tsx         # статус, история, ответ на уточнение
│       │   ├── staff/LoginPage.tsx
│       │   ├── staff/TicketsListPage.tsx    # таблица, фильтры, поиск, пустое состояние
│       │   ├── staff/TicketDetailPage.tsx   # карточка, статус, исполнитель, комментарии
│       │   ├── staff/StatsPage.tsx
│       │   └── admin/LocationsPage.tsx      # QR-привязки, скачивание QR
│       ├── components/
│       │   ├── AiAssistantBox.tsx   # ответ ИИ, предупреждение об опасности, уточняющий вопрос
│       │   ├── StatusBadge.tsx, PhotoUploader.tsx, EmptyState.tsx, ErrorNotice.tsx
│       └── styles/              # mobile-first, токены цветов
│
└── qr/                          # сгенерированные демо-коды (выход make_qr_pack.py)
    ├── k214.png, k305.png, khall.png, ...
    └── printable.html           # страница для печати всех кодов с подписями
```

## Переменные окружения (`.env.example`)

```dotenv
# База данных
DATABASE_URL=postgresql://upk:upk@db:5432/upk

# Безопасность (сгенерировать: python -c "import secrets;print(secrets.token_urlsafe(48))")
JWT_SECRET=
TRACKING_SECRET=
JWT_EXPIRES_MINUTES=480

# ИИ (только на сервере!)
LLM_API_KEY=
LLM_MODEL=
LLM_BASE_URL=
LLM_ENABLED=true            # false = режим fallback без модели

# Приложение
PUBLIC_BASE_URL=http://localhost:5173
UPLOAD_DIR=./app/storage
MAX_UPLOAD_MB=5

# Демо-учётки (используются только seed_users.py)
DEMO_STAFF_PASSWORD=
DEMO_ADMIN_PASSWORD=
```

## Запуск (шаблон для README)

```bash
cp .env.example .env            # заполнить секреты
docker compose up -d db
psql "$DATABASE_URL" -f db/schema.sql -f db/seed.sql
python backend/scripts/seed_users.py
make qr                         # PNG кодов → qr/
docker compose up backend frontend
# фронтенд: http://localhost:5173   API: http://localhost:8000/api/v1   Swagger: /docs
```

## Правила работы в git

| Правило | Как |
|---|---|
| Ветки | `main` (всегда запускается) ← `dev` ← `feat/<роль>-<что>` (например `feat/front-tracking-page`) |
| Коммиты | Короткие, по-русски или по-английски, но единообразно: `feat: страница отслеживания`, `fix: дубль заявки` |
| Слияние | Минимум раз в день в `dev`; в `main` только после прогона сквозного сценария |
| Секреты | Никогда в коммитах. Если ключ утёк, сразу сменить, а не просто удалить из истории |
| Контракт | Меняется только вместе с кодом в одном PR и с уведомлением команды |

## Кто за что отвечает

| Папка | Основной владелец | Проверяет |
|---|---|---|
| `backend/app/routers`, `services/tickets.py`, `db/` | Роль 1 | Роль 2 |
| `backend/app/security.py`, `deps.py`, `attachments.py`, `qr.py`, `stats.py`, `routers/auth.py`, `admin.py` | Роль 2 | Роль 1 |
| `frontend/src/pages/NewTicket*`, `TrackingPage`, `AiAssistantBox`, `PhotoUploader` | Роль 3 | Роль 4 |
| `frontend/src/pages/staff/*`, `admin/*`, `StatusBadge`, `EmptyState` | Роль 4 | Роль 3 |
| `ai/`, `services/ai_service.py`, `docs/`, `README.md`, презентация | Роль 5 | Роль 1 |

## Минимальный набор тестов перед защитой

Запускается командой `make test`:
- создание заявки возвращает уникальный номер; повторный запрос с тем же `Idempotency-Key` не создаёт вторую запись;
- недопустимый переход статуса даёт `409`; `resolved` без `note` даёт `422`;
- `/staff/*` без токена даёт `401`; `staff` в `/admin/*` даёт `403`; неверный токен отслеживания даёт `404`;
- в ответе `/tracking/{token}` нет внутренних комментариев и имени исполнителя;
- файл неверного типа или больше 5 МБ отклоняется;
- предфильтр ловит «искрит розетка» и не срабатывает на «лампа не горит».
