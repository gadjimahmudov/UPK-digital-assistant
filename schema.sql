-- ============================================================
-- УПК: Цифровой помощник колледжа — схема БД (PostgreSQL 14+)
-- Запуск: psql "$DATABASE_URL" -f db/schema.sql && psql "$DATABASE_URL" -f db/seed.sql
-- ============================================================

BEGIN;

-- ---------- Пользователи (только сотрудники; заявители не регистрируются) ----------
CREATE TABLE users (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name            TEXT        NOT NULL,
    login           TEXT        NOT NULL UNIQUE,
    role            TEXT        NOT NULL CHECK (role IN ('staff', 'admin')),
    password_hash   TEXT        NOT NULL,                -- bcrypt/argon2, никогда не открытый пароль
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- Справочники ----------
CREATE TABLE categories (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code        TEXT    NOT NULL UNIQUE,                 -- equipment, furniture, ...
    name        TEXT    NOT NULL,
    description TEXT,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE locations (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    building    TEXT    NOT NULL,
    floor       INTEGER,
    room        TEXT,                                    -- NULL для общих зон
    zone_name   TEXT,                                    -- «Холл», «Лаборатория 1» и т.п.
    qr_key      TEXT    NOT NULL UNIQUE,                 -- случайный идентификатор в QR, без ПДн
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    CHECK (room IS NOT NULL OR zone_name IS NOT NULL)
);

-- ---------- ИИ-обращения (нужны для статистики «ответил ИИ / передано сотруднику») ----------
CREATE TABLE ai_interactions (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    location_id     BIGINT REFERENCES locations(id),
    input_text      TEXT        NOT NULL,
    result_type     TEXT        NOT NULL CHECK (result_type IN
                        ('info_question','malfunction','need_more_info','danger','unknown')),
    danger_kind     TEXT        CHECK (danger_kind IN ('active_hazard','unsafe_request')),
    confidence      NUMERIC(3,2) CHECK (confidence BETWEEN 0 AND 1),
    suggest_ticket  BOOLEAN     NOT NULL DEFAULT FALSE,
    handoff         BOOLEAN     NOT NULL DEFAULT FALSE,   -- передано человеку (низкая уверенность, неизвестное)
    kb_codes        TEXT[]      NOT NULL DEFAULT '{}',
    source          TEXT        NOT NULL DEFAULT 'llm' CHECK (source IN ('llm','rules','fallback')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- Заявки ----------
CREATE SEQUENCE ticket_public_seq START 1;

CREATE TABLE tickets (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_number       TEXT        NOT NULL UNIQUE
                            DEFAULT ('UPK-' || lpad(nextval('ticket_public_seq')::text, 6, '0')),
    title               TEXT        NOT NULL CHECK (char_length(title) BETWEEN 3 AND 120),
    description         TEXT        NOT NULL CHECK (char_length(description) BETWEEN 10 AND 2000),
    category_id         BIGINT      NOT NULL REFERENCES categories(id),
    location_id         BIGINT      REFERENCES locations(id),
    location_text       TEXT,                            -- место вручную, если нет привязки
    status              TEXT        NOT NULL DEFAULT 'new' CHECK (status IN
                            ('new','under_review','assigned','in_progress','needs_info','resolved','rejected')),
    priority            TEXT        NOT NULL DEFAULT 'normal' CHECK (priority IN ('low','normal','high','urgent')),
    assignee_id         BIGINT      REFERENCES users(id),
    tracking_token_hash TEXT        NOT NULL UNIQUE,     -- SHA-256 от токена; сам токен не хранится
    idempotency_key     TEXT        UNIQUE,              -- защита от дублей при повторной отправке
    ai_interaction_id   BIGINT      REFERENCES ai_interactions(id),
    created_via_qr      BOOLEAN     NOT NULL DEFAULT FALSE,
    contact_name        TEXT,                            -- необязательно; минимум ПДн
    contact_info        TEXT,
    resolution_note     TEXT,                            -- итог при «Решена» / причина при «Отклонена»
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at         TIMESTAMPTZ,                     -- для «среднего времени до решения»
    CHECK (location_id IS NOT NULL OR location_text IS NOT NULL),
    CHECK (status NOT IN ('resolved','rejected') OR resolution_note IS NOT NULL)
);

CREATE INDEX idx_tickets_status     ON tickets (status);
CREATE INDEX idx_tickets_category   ON tickets (category_id);
CREATE INDEX idx_tickets_location   ON tickets (location_id);
CREATE INDEX idx_tickets_created_at ON tickets (created_at DESC);
CREATE INDEX idx_tickets_assignee   ON tickets (assignee_id);
-- Полнотекстовый поиск по заголовку и описанию
CREATE INDEX idx_tickets_fts ON tickets
    USING GIN (to_tsvector('russian', title || ' ' || description));

-- ---------- Комментарии ----------
CREATE TABLE comments (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ticket_id   BIGINT      NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    author_id   BIGINT      REFERENCES users(id),        -- NULL = ответ заявителя
    author_type TEXT        NOT NULL CHECK (author_type IN ('staff','applicant')),
    body        TEXT        NOT NULL CHECK (char_length(body) BETWEEN 1 AND 2000),
    visibility  TEXT        NOT NULL DEFAULT 'public' CHECK (visibility IN ('public','internal')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (author_type = 'staff' OR visibility = 'public')   -- заявитель не пишет внутренних заметок
);
CREATE INDEX idx_comments_ticket ON comments (ticket_id, created_at);

-- ---------- История изменений ----------
CREATE TABLE ticket_history (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ticket_id        BIGINT      NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    event_type       TEXT        NOT NULL CHECK (event_type IN
                        ('created','status_changed','assigned','priority_changed','comment_added','applicant_reply')),
    old_status       TEXT,
    new_status       TEXT,
    old_assignee_id  BIGINT REFERENCES users(id),
    new_assignee_id  BIGINT REFERENCES users(id),
    actor_id         BIGINT REFERENCES users(id),        -- NULL = система или заявитель
    actor_type       TEXT        NOT NULL CHECK (actor_type IN ('system','staff','applicant')),
    note             TEXT,
    is_public        BOOLEAN     NOT NULL DEFAULT TRUE,  -- показывать ли запись на странице отслеживания
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_history_ticket ON ticket_history (ticket_id, created_at);

-- ---------- Вложения (метаданные; файлы лежат вне публичной папки) ----------
CREATE TABLE attachments (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ticket_id   BIGINT      NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    storage_key TEXT        NOT NULL UNIQUE,             -- случайное имя (uuid), не оригинальное
    file_name   TEXT        NOT NULL,                    -- оригинальное имя, только для отображения
    file_type   TEXT        NOT NULL CHECK (file_type IN ('image/jpeg','image/png','image/webp')),
    size_bytes  INTEGER     NOT NULL CHECK (size_bytes BETWEEN 1 AND 5242880),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_attachments_ticket ON attachments (ticket_id);

-- ---------- База знаний ИИ ----------
CREATE TABLE knowledge_base (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code        TEXT    NOT NULL UNIQUE,                 -- kb_projector_connect и т.д.
    question    TEXT    NOT NULL,
    answer      TEXT    NOT NULL,
    category    TEXT,
    source      TEXT,
    keywords    TEXT[]  NOT NULL DEFAULT '{}',
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- Автообновление updated_at ----------
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_tickets_updated_at BEFORE UPDATE ON tickets
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------- Автоматическая отметка времени решения ----------
CREATE OR REPLACE FUNCTION set_resolved_at() RETURNS trigger AS $$
BEGIN
    IF NEW.status = 'resolved' AND OLD.status IS DISTINCT FROM 'resolved' THEN
        NEW.resolved_at = now();
    ELSIF NEW.status <> 'resolved' THEN
        NEW.resolved_at = NULL;                          -- переоткрытие заявки
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_tickets_resolved_at BEFORE UPDATE OF status ON tickets
    FOR EACH ROW EXECUTE FUNCTION set_resolved_at();

COMMIT;

-- ============================================================
-- Запросы для статистики (для справки бэкенду)
-- ============================================================
-- Всего / открытые / решённые:
--   SELECT count(*) total,
--          count(*) FILTER (WHERE status NOT IN ('resolved','rejected')) open,
--          count(*) FILTER (WHERE status = 'resolved') resolved
--   FROM tickets;
-- По категориям:
--   SELECT c.name, count(*) FROM tickets t JOIN categories c ON c.id=t.category_id GROUP BY c.name ORDER BY 2 DESC;
-- По помещениям:
--   SELECT l.building, l.room, l.zone_name, count(*) FROM tickets t JOIN locations l ON l.id=t.location_id
--   GROUP BY 1,2,3 ORDER BY 4 DESC;
-- Среднее время до решения (часы):
--   SELECT round(avg(extract(epoch FROM resolved_at - created_at))/3600, 1) FROM tickets WHERE resolved_at IS NOT NULL;
-- ИИ: ответил сам / привёл к заявке / передано человеку:
--   SELECT count(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM tickets t WHERE t.ai_interaction_id = a.id)) AS answered_by_ai,
--          count(*) FILTER (WHERE EXISTS     (SELECT 1 FROM tickets t WHERE t.ai_interaction_id = a.id)) AS converted_to_ticket,
--          count(*) FILTER (WHERE handoff) AS handoff
--   FROM ai_interactions a;
