# Контракт API — «УПК: Цифровой помощник колледжа»

Версия 1.0. Это единый источник правды для frontend и backend: фронтенд пишется по моковым ответам отсюда, пока бэкенд не готов. Любое изменение контракта обсуждается в общем чате и правится в этом файле в том же коммите.

## 0. Общие правила

| Параметр | Значение |
|---|---|
| Базовый URL | `/api/v1` |
| Формат | JSON, UTF-8 (кроме загрузки файлов: `multipart/form-data`) |
| Время | ISO 8601 в UTC: `2026-11-02T09:30:00Z` |
| Идентификаторы | В публичных ответах — `public_number` (`UPK-000042`); внутренний числовой `id` только в `/staff` и `/admin` |
| Авторизация сотрудников | `Authorization: Bearer <JWT>`; срок жизни 8 часов |
| Публичный доступ к заявке | только по токену отслеживания `{token}` (≥ 32 символов, base64url) |

### Формат ошибки (единый для всех эндпоинтов)

```json
{
  "error": {
    "code": "validation_error",
    "message": "Проверьте поля формы",
    "fields": { "description": "Опишите проблему хотя бы в 10 символах" }
  }
}
```

`fields` присутствует только для `validation_error`. Сообщения (`message`, `fields.*`) написаны для человека на русском. Клиент показывает их как есть.

| HTTP | `code` | Когда |
|---|---|---|
| 400 | `bad_request` | Битый JSON, неверный формат параметра |
| 401 | `unauthorized` | Нет или просрочен токен сотрудника |
| 403 | `forbidden` | Роль не подходит (например, `staff` вызывает `/admin`) |
| 404 | `not_found` | Нет такой заявки, токена или локации (для токена всегда 404, без подсказок) |
| 409 | `invalid_transition` | Недопустимый переход статуса |
| 413 | `file_too_large` | Файл > 5 МБ |
| 415 | `unsupported_file_type` | Не JPEG/PNG/WebP |
| 422 | `validation_error` | Поля не прошли проверку |
| 429 | `rate_limited` | Слишком много запросов (заголовок `Retry-After`) |
| 500 | `internal_error` | Ошибка сервера. **Клиент не показывает «успешно»**, а предлагает повторить |
| 503 | `ai_unavailable` | ИИ недоступен (форма при этом продолжает работать, см. 2.4) |

### Лимиты и защита

- `POST /ai/analyze`: 20 запросов в минуту с одного IP.
- `POST /tickets`: 10 заявок в час с одного IP.
- `POST /auth/login`: 10 попыток в 15 минут с одного IP и логина.
- Вложения: до 3 файлов в заявке, до 5 МБ каждый, JPEG/PNG/WebP. Тип проверяется по содержимому, а не по расширению.
- Секреты (ключ ИИ, `JWT_SECRET`, `TRACKING_SECRET`) — только в `.env` на сервере.

### Справочники

**Статусы** (`status`):

| Код | Название в UI | Кто ставит |
|---|---|---|
| `new` | Новая | система |
| `under_review` | На рассмотрении | staff |
| `assigned` | Назначен исполнитель | staff (или автоматически при назначении) |
| `in_progress` | В работе | staff |
| `needs_info` | Нужны уточнения | staff |
| `resolved` | Решена | staff (нужен `note`) |
| `rejected` | Отклонена | staff (нужен `note`) |

**Разрешённые переходы** (всё остальное → `409 invalid_transition`):

| Из | В |
|---|---|
| `new` | `under_review`, `rejected` |
| `under_review` | `assigned`, `in_progress`, `needs_info`, `resolved`, `rejected` |
| `assigned` | `in_progress`, `needs_info`, `resolved`, `rejected` |
| `in_progress` | `assigned`, `needs_info`, `resolved`, `rejected` |
| `needs_info` | `under_review`, `in_progress`, `rejected` |
| `resolved` | `in_progress` (переоткрытие) |
| `rejected` | `under_review` (пересмотр) |

Ответ заявителя на запрос уточнений (см. 2.3) автоматически переводит `needs_info → under_review`.

**Приоритеты** (`priority`): `low`, `normal`, `high`, `urgent`.
**Категории** (`category.code`): `equipment`, `furniture`, `electricity`, `network`, `building`, `cleaning`, `other`.
**Типы ответа ИИ** (`type`): `info_question`, `malfunction`, `need_more_info`, `danger`, `unknown`.

---

## 1. Справочные и публичные данные

### 1.1 `GET /locations/by-qr/{qr_key}` — место по QR-коду

Ответ `200`:
```json
{ "id": 1, "building": "Главный корпус", "floor": 2, "room": "214", "zone_name": null,
  "label": "Главный корпус, каб. 214" }
```
Ответ `404 not_found`, если ключ неизвестен или локация отключена. Фронтенд в этом случае показывает форму с ручным выбором места (а не пустую ошибку).

### 1.2 `GET /locations` — список мест для ручного выбора
Ответ `200`: `{ "items": [ { "id":1, "label":"Главный корпус, каб. 214", "building":"…", "floor":2, "room":"214", "zone_name":null } ] }`.
В ответе **нет** `qr_key`.

### 1.3 `GET /categories`
Ответ `200`: `{ "items": [ { "code":"equipment", "name":"Оборудование", "description":"Проекторы, компьютеры, принтеры…" } ] }`.

---

## 2. Публичные операции заявителя (без авторизации)

### 2.1 `POST /ai/analyze` — анализ сообщения ИИ

Запрос:
```json
{
  "message": "В кабинете 214 не работает проектор",
  "location_id": 1
}
```
`message`: 1–1000 символов. `location_id` необязателен (берётся из QR).

Ответ `200`:
```json
{
  "interaction_id": 57,
  "type": "malfunction",
  "confidence": 0.93,
  "answer": "Похоже, проектор неисправен. Проверьте, что он включён в розетку и выбран нужный вход. Если не помогло, оформите заявку.",
  "suggest_ticket": true,
  "clarifying_question": null,
  "danger_kind": null,
  "handoff_to_human": false,
  "kb_codes": [],
  "draft": {
    "title": "Не работает проектор",
    "description": "В кабинете 214 не работает проектор.",
    "category_code": "equipment",
    "priority_suggestion": "normal"
  }
}
```

Правила для фронтенда:
- `draft` не `null` только если `suggest_ticket = true`. Поля черновика подставляются в форму, пользователь может их править.
- `type = "danger"` → показать **заметный блок предупреждения** (цвет, иконка) с `answer`, кнопку «Оформить заявку» оставить, если `suggest_ticket = true`.
- `clarifying_question != null` → показать вопрос и поле ответа; ответ уходит новым вызовом `/ai/analyze` с объединённым текстом.
- `handoff_to_human = true` → предложить оформить заявку или обратиться к сотруднику; сообщить, что уверенность низкая.
- Если `confidence < 0.6`, бэкенд сам принудительно выставляет `handoff_to_human = true`.
- Ответ ИИ **никогда** не создаёт и не закрывает заявку. Заявку создаёт только пользователь через 2.2.

Ошибка `503 ai_unavailable`: фронтенд скрывает блок подсказок, показывает «Помощник временно недоступен, вы можете оформить заявку самостоятельно», форма остаётся рабочей.

### 2.2 `POST /tickets` — создание заявки

`Content-Type: multipart/form-data`. Заголовок **`Idempotency-Key: <uuid v4>`** обязателен: клиент генерирует его при открытии формы и держит, пока заявка не создана.

| Поле | Тип | Обяз. | Правила |
|---|---|---|---|
| `title` | string | да | 3–120 символов |
| `description` | string | да | 10–2000 символов |
| `category_code` | string | да | из справочника |
| `location_id` | int | нет* | из 1.1/1.2 |
| `location_text` | string | нет* | до 200 символов; *нужно одно из двух |
| `created_via_qr` | bool | нет | `true`, если место пришло из QR |
| `ai_interaction_id` | int | нет | из 2.1; связывает заявку со статистикой ИИ |
| `contact_name` | string | нет | до 100 символов |
| `contact_info` | string | нет | до 150 символов (почта/телефон/Telegram) для уточнений |
| `photos` | file[] | нет | до 3 файлов, правила в разделе 0 |

Ответ `201`:
```json
{
  "public_number": "UPK-000042",
  "status": "new",
  "created_at": "2026-11-02T09:30:00Z",
  "tracking_token": "q8Zl2…(43 символа)",
  "tracking_url": "https://host/track/q8Zl2…"
}
```
- Токен показывается пользователю и в базе хранится только как SHA-256-хеш.
- **Повторная отправка с тем же `Idempotency-Key`** → `200` с идентичным телом (тот же номер и тот же токен), новая заявка не создаётся. Для этого токен при наличии ключа вычисляется как `base64url(HMAC_SHA256(TRACKING_SECRET, idempotency_key))`, а без ключа генерируется случайно. Ключи, секрет и токены в логи не пишутся.
- Ошибки: `422 validation_error` (с `fields`), `413`, `415`, `429`, `500`. При любой ошибке заявка не создаётся, а клиент не показывает подтверждение.

### 2.3 Отслеживание по токену

**`GET /tracking/{token}`** — `200`:
```json
{
  "public_number": "UPK-000042",
  "title": "Не работает проектор",
  "description": "В кабинете 214 не работает проектор.",
  "category": { "code": "equipment", "name": "Оборудование" },
  "location_label": "Главный корпус, каб. 214",
  "status": "in_progress",
  "status_label": "В работе",
  "created_at": "2026-11-02T09:30:00Z",
  "updated_at": "2026-11-02T10:05:00Z",
  "resolution_note": null,
  "needs_reply": false,
  "history": [
    { "at": "2026-11-02T09:30:00Z", "status": "new", "status_label": "Новая", "note": null },
    { "at": "2026-11-02T10:05:00Z", "status": "in_progress", "status_label": "В работе", "note": "Взяли в работу" }
  ],
  "comments": [
    { "at": "2026-11-02T10:06:00Z", "author": "Сотрудник УПК", "body": "Проверим лампу проектора сегодня." }
  ]
}
```
Что **не** возвращается: внутренние комментарии (`visibility = internal`), имя исполнителя, `id`, чужие данные, контакты других людей. Неизвестный или неверный токен → `404` (одинаково, без различий).
`needs_reply = true`, когда статус `needs_info`.

**`POST /tracking/{token}/reply`** — ответ на запрос уточнений.
Запрос: `{ "body": "Проектор Epson, лампа мигает красным" }` (1–2000 символов).
Ответ `201`: `{ "ok": true }`. Создаёт публичный комментарий (`author_type = applicant`), запись в истории и переводит `needs_info → under_review`. Если статус не `needs_info` → `409 invalid_transition`.

---

## 3. Авторизация сотрудников

| Метод и путь | Тело | Ответ |
|---|---|---|
| `POST /auth/login` | `{ "login": "upk1", "password": "…" }` | `200 { "access_token": "…", "expires_in": 28800, "user": { "id":3, "name":"Иван П.", "role":"staff" } }` |
| `GET /auth/me` | — | `200 { "id":3, "name":"Иван П.", "login":"upk1", "role":"staff" }` |
| `POST /auth/logout` | — | `204` (клиент удаляет токен) |

Неверный логин или пароль → `401 unauthorized` с одинаковым текстом «Неверный логин или пароль» (без указания, что именно неверно). Неактивный пользователь → то же самое.

Все пути `/staff/*` требуют роль `staff` или `admin`; `/admin/*` — только `admin`. Роль проверяется **на сервере в каждом запросе**, а не только скрывается в интерфейсе.

---

## 4. Панель сотрудника (`/staff/*`, нужен Bearer)

### 4.1 `GET /staff/tickets` — список с фильтрами

| Параметр | Описание |
|---|---|
| `q` | Поиск по номеру (`UPK-000042` или `42`) и по тексту заголовка/описания |
| `status` | Один или несколько через запятую: `new,under_review` |
| `category` | `category.code` |
| `location_id` | Помещение |
| `assignee_id` | Исполнитель; `me` — свои; `none` — неназначенные |
| `priority` | Один или несколько |
| `date_from`, `date_to` | `YYYY-MM-DD`, по `created_at` |
| `sort` | `created_at` (по умолчанию), `updated_at`, `priority`; префикс `-` — по убыванию (по умолчанию `-created_at`) |
| `page`, `page_size` | По умолчанию `1` и `20`, максимум `100` |

Ответ `200`:
```json
{
  "items": [
    { "id": 42, "public_number": "UPK-000042", "title": "Не работает проектор",
      "category": { "code":"equipment", "name":"Оборудование" },
      "location_label": "Главный корпус, каб. 214",
      "status": "new", "status_label": "Новая", "priority": "normal",
      "assignee": null, "created_at": "2026-11-02T09:30:00Z", "updated_at": "2026-11-02T09:30:00Z" }
  ],
  "page": 1, "page_size": 20, "total": 1
}
```
Пустой результат — `items: []`, `total: 0` (фронтенд показывает пустое состояние с кнопкой «Сбросить фильтры»).

### 4.2 `GET /staff/tickets/{id}` — карточка
Возвращает всё из 4.1 плюс: `description`, `location_text`, `contact_name`, `contact_info`, `resolution_note`, `created_via_qr`, `ai` (`{ "type":"malfunction", "confidence":0.93, "answered":false }` или `null`), `attachments` (`[{ "id":7, "file_name":"photo.jpg", "file_type":"image/jpeg", "size_bytes":184233, "url":"/api/v1/staff/attachments/7" }]`), `comments` (**включая** внутренние, с полем `visibility`), `history` (полная: кто, когда, что изменил), `allowed_transitions` (список допустимых статусов из текущего, чтобы UI показал только их).

### 4.3 `PATCH /staff/tickets/{id}/status`
Запрос: `{ "status": "resolved", "note": "Заменили лампу проектора" }`.
`note` обязателен для `resolved` и `rejected` (иначе `422`); для остальных необязателен. Заметка при `resolved`/`rejected` сохраняется в `resolution_note` и показывается заявителю.
Ответ `200`: обновлённая карточка (как 4.2). Недопустимый переход → `409 invalid_transition` с `message` вида «Из статуса «Решена» нельзя перейти в «Новая»».
Каждое изменение записывается в `ticket_history` (старый и новый статус, автор, время, `note`).

### 4.4 `PATCH /staff/tickets/{id}/assignee`
Запрос: `{ "assignee_id": 3 }` (`null` — снять исполнителя). Исполнитель должен быть активным пользователем.
Если статус был `new` или `under_review`, он автоматически становится `assigned` (и это пишется в историю). Ответ `200` — карточка.

### 4.5 `PATCH /staff/tickets/{id}/priority`
Запрос: `{ "priority": "high" }`. Ответ `200` — карточка. Запись в историю.

### 4.6 `POST /staff/tickets/{id}/comments`
Запрос: `{ "body": "Запросили запасную лампу", "visibility": "public" }`; `visibility`: `public` (видит заявитель) или `internal` (только сотрудники). Ответ `201` — созданный комментарий. Заметка по умолчанию `internal`: UI должен явно предлагать выбор.

### 4.7 `GET /staff/attachments/{id}`
Отдаёт файл (`Content-Type` из БД, `Content-Disposition: inline`, `X-Content-Type-Options: nosniff`) только авторизованному сотруднику. Прямых публичных ссылок на файлы нет.

### 4.8 `GET /staff/users?active=true` — кого назначить
Ответ `200`: `{ "items": [ { "id":3, "name":"Иван П.", "role":"staff" } ] }`.

### 4.9 `GET /staff/stats` — статистика
Необязательные параметры `date_from`, `date_to`. Ответ `200`:
```json
{
  "totals": { "all": 120, "open": 34, "resolved": 80, "rejected": 6 },
  "by_status":   [ { "status": "new", "count": 10 } ],
  "by_category": [ { "code": "equipment", "name": "Оборудование", "count": 51 } ],
  "by_location": [ { "location_id": 1, "label": "Главный корпус, каб. 214", "count": 12 } ],
  "avg_resolution_hours": 27.4,
  "ai": { "interactions": 300, "answered_by_ai": 180, "converted_to_ticket": 95, "handoff": 25 }
}
```
`avg_resolution_hours = null`, если решённых заявок нет.

---

## 5. Администрирование (`/admin/*`, только `admin`)

| Метод и путь | Назначение |
|---|---|
| `GET /admin/locations` | Все локации (включая `qr_key` и `is_active`) |
| `POST /admin/locations` | Создать: `{ "building","floor","room","zone_name" }`; `qr_key` генерируется на сервере (`secrets.token_urlsafe(8)`) |
| `PATCH /admin/locations/{id}` | Изменить поля или `is_active`; `{ "regenerate_qr_key": true }` меняет ключ (старый QR перестаёт работать) |
| `GET /admin/locations/{id}/qr.png` | PNG QR-кода со ссылкой `https://host/new?loc={qr_key}`; параметр `?size=512` |
| `GET /admin/categories`, `POST`, `PATCH /admin/categories/{id}` | Управление категориями (`code`, `name`, `description`, `is_active`) |
| `GET /admin/users`, `POST /admin/users`, `PATCH /admin/users/{id}` | Управление сотрудниками (`name`, `login`, `role`, `password`, `is_active`); пароль уходит только в запросе, в ответах его и хеша нет |
| `GET /admin/knowledge`, `POST`, `PATCH /admin/knowledge/{id}` | Статьи базы знаний ИИ (опционально для MVP) |

---

## 6. Маршруты фронтенда (для согласования ссылок в QR и письмах)

| Путь | Экран |
|---|---|
| `/new?loc={qr_key}` | Форма заявки (место подставляется; неизвестный `loc` → ручной выбор) |
| `/track/{token}` | Отслеживание |
| `/staff/login` | Вход сотрудника |
| `/staff/tickets`, `/staff/tickets/:id`, `/staff/stats` | Панель |
| `/admin/locations` и т. д. | Администрирование |

---

## 7. Чек-лист соответствия приёмке

| ID | Проверка | Эндпоинты |
|---|---|---|
| AT-01 | QR → форма с местом | 1.1 |
| AT-02 | Создание, уникальный номер | 2.2 |
| AT-03, AT-04 | Заявка в панели, поиск и фильтры | 4.1 |
| AT-05, AT-12 | Статус, исполнитель, комментарий, история | 4.3–4.6 |
| AT-06 | Страница отслеживания | 2.3 |
| AT-07–AT-10 | Ответы ИИ (вопрос, неисправность, опасность, неизвестное) | 2.1 |
| AT-11 | Панель закрыта без входа | 3, `/staff/*` → 401 |
