"""УПК: Цифровой помощник. Этап 1: backend (Flask + SQLite). Контракт: ../docs/api-contract.md"""
import os, re, sqlite3, secrets, hashlib, hmac, base64, uuid
from datetime import datetime, timedelta, timezone
from functools import wraps
import jwt
from flask import Flask, request, jsonify, g, send_file
from werkzeug.security import generate_password_hash, check_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
CFG = {
    "DB_PATH": os.environ.get("DB_PATH", os.path.join(BASE, "upk.db")),
    "JWT_SECRET": os.environ.get("JWT_SECRET", "dev-jwt-secret-CHANGE-ME"),
    "TRACKING_SECRET": os.environ.get("TRACKING_SECRET", "dev-track-secret-CHANGE-ME"),
    "UPLOAD_DIR": os.environ.get("UPLOAD_DIR", os.path.join(BASE, "storage")),
    "DEMO_PASSWORD": os.environ.get("DEMO_PASSWORD", "demo12345"),
}
STATUS = {"new": "Новая", "under_review": "На рассмотрении", "assigned": "Назначен исполнитель",
          "in_progress": "В работе", "needs_info": "Нужны уточнения", "resolved": "Решена", "rejected": "Отклонена"}
TRANS = {"new": ["under_review", "rejected"],
         "under_review": ["assigned", "in_progress", "needs_info", "resolved", "rejected"],
         "assigned": ["in_progress", "needs_info", "resolved", "rejected"],
         "in_progress": ["assigned", "needs_info", "resolved", "rejected"],
         "needs_info": ["under_review", "in_progress", "rejected"],
         "resolved": ["in_progress"], "rejected": ["under_review"]}
PRIORITIES = ["low", "normal", "high", "urgent"]
SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, login TEXT UNIQUE NOT NULL,
  role TEXT NOT NULL CHECK(role IN('staff','admin')), password_hash TEXT NOT NULL, is_active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS categories(id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
  description TEXT, is_active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS locations(id INTEGER PRIMARY KEY AUTOINCREMENT, building TEXT NOT NULL, floor INTEGER, room TEXT,
  zone_name TEXT, qr_key TEXT UNIQUE NOT NULL, is_active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS ai_interactions(id INTEGER PRIMARY KEY AUTOINCREMENT, location_id INTEGER, input_text TEXT NOT NULL,
  result_type TEXT NOT NULL, danger_kind TEXT, confidence REAL, suggest_ticket INTEGER DEFAULT 0, handoff INTEGER DEFAULT 0,
  kb_codes TEXT DEFAULT '', source TEXT DEFAULT 'llm', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tickets(id INTEGER PRIMARY KEY AUTOINCREMENT, public_number TEXT UNIQUE, title TEXT NOT NULL,
  description TEXT NOT NULL, category_id INTEGER NOT NULL REFERENCES categories(id), location_id INTEGER REFERENCES locations(id),
  location_text TEXT, status TEXT NOT NULL DEFAULT 'new', priority TEXT NOT NULL DEFAULT 'normal',
  assignee_id INTEGER REFERENCES users(id), tracking_token_hash TEXT UNIQUE NOT NULL, idempotency_key TEXT UNIQUE,
  ai_interaction_id INTEGER REFERENCES ai_interactions(id), created_via_qr INTEGER DEFAULT 0, contact_name TEXT, contact_info TEXT,
  resolution_note TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, resolved_at TEXT);
CREATE INDEX IF NOT EXISTS ix_t_status ON tickets(status); CREATE INDEX IF NOT EXISTS ix_t_created ON tickets(created_at);
CREATE TABLE IF NOT EXISTS comments(id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id INTEGER NOT NULL REFERENCES tickets(id),
  author_id INTEGER REFERENCES users(id), author_type TEXT NOT NULL, body TEXT NOT NULL,
  visibility TEXT NOT NULL DEFAULT 'public', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ticket_history(id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id INTEGER NOT NULL REFERENCES tickets(id),
  event_type TEXT NOT NULL, old_status TEXT, new_status TEXT, old_assignee_id INTEGER, new_assignee_id INTEGER,
  actor_id INTEGER, actor_type TEXT NOT NULL, note TEXT, is_public INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS attachments(id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id INTEGER NOT NULL REFERENCES tickets(id),
  storage_key TEXT UNIQUE NOT NULL, file_name TEXT NOT NULL, file_type TEXT NOT NULL, size_bytes INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS knowledge_base(id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL, question TEXT, answer TEXT,
  category TEXT, source TEXT, keywords TEXT, is_active INTEGER DEFAULT 1);
"""
CATS = [("equipment", "Оборудование", "Проекторы, компьютеры, принтеры, интерактивные доски"),
        ("furniture", "Мебель и имущество", "Столы, стулья, шкафы, жалюзи"),
        ("electricity", "Электрика и освещение", "Свет, розетки, выключатели"),
        ("network", "Сеть и интернет", "Wi-Fi, сетевые розетки"),
        ("building", "Здание", "Двери, окна, замки, потолок, стены"),
        ("cleaning", "Уборка и чистота", "Загрязнения, мусор"), ("other", "Другое", "Если ничего не подходит")]
LOCS = [("Главный корпус", 2, "214", None, "k214-Xv7Qe3Ls"), ("Главный корпус", 3, "305", None, "k305-Rt9Mn2Wp"),
        ("Главный корпус", 1, None, "Холл", "khall-Zb4Hc8Yd"),
        ("Лабораторный корпус", 1, "101", "Лаборатория информатики", "klab101-Ua6Fj5Qs"),
        ("Главный корпус", 1, None, "Столовая", "kcafe-Pe2Gk7Vn")]

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024
app.json.ensure_ascii = False  # кириллица в ответах читается как есть

class APIError(Exception):
    def __init__(self, code, message, http=400, fields=None):
        self.code, self.message, self.http, self.fields = code, message, http, fields

@app.errorhandler(APIError)
def _api_err(e):
    body = {"code": e.code, "message": e.message}
    if e.fields: body["fields"] = e.fields
    return jsonify(error=body), e.http
@app.errorhandler(404)
def _404(_): return jsonify(error={"code": "not_found", "message": "Не найдено"}), 404
@app.errorhandler(405)
def _405(_): return jsonify(error={"code": "bad_request", "message": "Метод не поддерживается"}), 405
@app.errorhandler(413)
def _413(_): return jsonify(error={"code": "file_too_large", "message": "Файл слишком большой"}), 413
@app.errorhandler(Exception)
def _500(e):
    app.logger.exception(e)
    return jsonify(error={"code": "internal_error", "message": "Ошибка сервера, попробуйте ещё раз"}), 500

# ---------- БД ----------
def db():
    if "db" not in g:
        g.db = sqlite3.connect(CFG["DB_PATH"]); g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db
@app.teardown_appcontext
def _close(_):
    d = g.pop("db", None)
    if d: d.close()
def q(sql, a=()):
    cur = db().execute(sql, a)
    return cur if sql.lstrip()[:6].upper() in ("INSERT", "UPDATE", "DELETE") else cur.fetchall()
def q1(sql, a=()): return db().execute(sql, a).fetchone()
def now(): return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def init_db():
    c = sqlite3.connect(CFG["DB_PATH"]); c.executescript(SCHEMA)
    if not c.execute("SELECT 1 FROM categories").fetchone():
        c.executemany("INSERT INTO categories(code,name,description) VALUES(?,?,?)", CATS)
        c.executemany("INSERT INTO locations(building,floor,room,zone_name,qr_key) VALUES(?,?,?,?,?)", LOCS)
        for name, login, role in [("Сотрудник УПК", "upk1", "staff"), ("Сотрудник УПК 2", "upk2", "staff"), ("Администратор", "admin", "admin")]:
            c.execute("INSERT INTO users(name,login,role,password_hash) VALUES(?,?,?,?)",
                      (name, login, role, generate_password_hash(CFG["DEMO_PASSWORD"])))
    c.commit(); c.close(); os.makedirs(CFG["UPLOAD_DIR"], exist_ok=True)

# ---------- Утилиты ----------
def loc_label(r):
    if r["location_id"] is None: return r["location_text"]
    if r["room"]: return f'{r["building"]}, каб. {r["room"]}'
    return f'{r["building"]}, {r["zone_name"]}'
def sha(s): return hashlib.sha256(s.encode()).hexdigest()
def make_token(idem):
    if idem: return base64.urlsafe_b64encode(hmac.new(CFG["TRACKING_SECRET"].encode(), idem.encode(), hashlib.sha256).digest()).decode().rstrip("=")
    return secrets.token_urlsafe(32)
def history(tid, event, old=None, new=None, actor=None, atype="system", note=None, public=1, oa=None, na=None):
    q("INSERT INTO ticket_history(ticket_id,event_type,old_status,new_status,old_assignee_id,new_assignee_id,actor_id,actor_type,note,is_public,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
      (tid, event, old, new, oa, na, actor, atype, note, public, now()))
def body_json():
    return request.get_json(silent=True) or {}
def sniff(b):
    if b[:3] == b"\xff\xd8\xff": return "image/jpeg"
    if b[:8] == b"\x89PNG\r\n\x1a\n": return "image/png"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP": return "image/webp"
    return None

# ---------- Авторизация ----------
def require(*roles):
    def deco(fn):
        @wraps(fn)
        def w(*a, **k):
            h = request.headers.get("Authorization", "")
            if not h.startswith("Bearer "): raise APIError("unauthorized", "Требуется вход", 401)
            try: p = jwt.decode(h[7:], CFG["JWT_SECRET"], algorithms=["HS256"])
            except jwt.PyJWTError: raise APIError("unauthorized", "Сессия истекла, войдите снова", 401)
            u = q1("SELECT * FROM users WHERE id=? AND is_active=1", (p["sub"],))
            if not u: raise APIError("unauthorized", "Требуется вход", 401)
            if u["role"] not in roles: raise APIError("forbidden", "Недостаточно прав", 403)
            g.user = u; return fn(*a, **k)
        return w
    return deco
staff_only = require("staff", "admin")

BASE_URL = "/api/v1"
def route(path, **kw): return app.route(BASE_URL + path, **kw)

@route("/auth/login", methods=["POST"])
def login():
    d = body_json()
    u = q1("SELECT * FROM users WHERE login=? AND is_active=1", (str(d.get("login", "")),))
    if not u or not check_password_hash(u["password_hash"], str(d.get("password", ""))):
        raise APIError("unauthorized", "Неверный логин или пароль", 401)
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    tok = jwt.encode({"sub": str(u["id"]), "role": u["role"], "exp": exp}, CFG["JWT_SECRET"], algorithm="HS256")
    return jsonify(access_token=tok, expires_in=28800, user={"id": u["id"], "name": u["name"], "role": u["role"]})
@route("/auth/me")
@staff_only
def me(): u = g.user; return jsonify(id=u["id"], name=u["name"], login=u["login"], role=u["role"])
@route("/auth/logout", methods=["POST"])
def logout(): return "", 204

# ---------- Справочники ----------
def loc_dict(r): return {"id": r["id"], "building": r["building"], "floor": r["floor"], "room": r["room"],
                          "zone_name": r["zone_name"], "label": loc_label({**dict(r), "location_id": r["id"], "location_text": None})}
@route("/locations/by-qr/<qr_key>")
def loc_by_qr(qr_key):
    r = q1("SELECT * FROM locations WHERE qr_key=? AND is_active=1", (qr_key,))
    if not r: raise APIError("not_found", "Место не найдено", 404)
    return jsonify(loc_dict(r))
@route("/locations")
def locs(): return jsonify(items=[loc_dict(r) for r in q("SELECT * FROM locations WHERE is_active=1 ORDER BY building,floor,room")])
@route("/categories")
def cats(): return jsonify(items=[dict(code=r["code"], name=r["name"], description=r["description"]) for r in q("SELECT * FROM categories WHERE is_active=1")])

# ---------- Создание заявки ----------
@route("/tickets", methods=["POST"])
def create_ticket():
    idem = request.headers.get("Idempotency-Key", "").strip()
    if not idem: raise APIError("bad_request", "Нужен заголовок Idempotency-Key", 400)
    d = request.form if request.form else body_json()
    g_ = lambda k: (str(d.get(k)).strip() if d.get(k) is not None else "")
    f = {}
    if not 3 <= len(g_("title")) <= 120: f["title"] = "Заголовок: от 3 до 120 символов"
    if not 10 <= len(g_("description")) <= 2000: f["description"] = "Опишите проблему: от 10 до 2000 символов"
    cat = q1("SELECT id FROM categories WHERE code=? AND is_active=1", (g_("category_code"),))
    if not cat: f["category_code"] = "Выберите категорию"
    loc = None
    if g_("location_id"):
        loc = q1("SELECT id FROM locations WHERE id=? AND is_active=1", (g_("location_id"),) if g_("location_id").isdigit() else (0,))
        if not loc: f["location_id"] = "Место не найдено"
    elif not g_("location_text"): f["location_id"] = "Укажите место"
    files = request.files.getlist("photos")
    if len(files) > 3: f["photos"] = "Не больше 3 фото"
    if f: raise APIError("validation_error", "Проверьте поля формы", 422, f)
    token = make_token(idem)
    base = {"status": "new"}
    ex = q1("SELECT * FROM tickets WHERE idempotency_key=?", (idem,))
    if ex:
        return jsonify(public_number=ex["public_number"], status=ex["status"], created_at=ex["created_at"],
                       tracking_token=token, tracking_url=f"{request.host_url}track/{token}"), 200
    blobs = []
    for fl in files:
        b = fl.read(5 * 1024 * 1024 + 1)
        if len(b) > 5 * 1024 * 1024: raise APIError("file_too_large", "Файл больше 5 МБ", 413)
        mt = sniff(b)
        if not mt: raise APIError("unsupported_file_type", "Допустимы JPEG, PNG и WebP", 415)
        blobs.append((fl.filename or "photo", mt, b))
    ai_id = int(g_("ai_interaction_id")) if g_("ai_interaction_id").isdigit() else None
    t = now()
    cur = q("INSERT INTO tickets(title,description,category_id,location_id,location_text,tracking_token_hash,idempotency_key,ai_interaction_id,created_via_qr,contact_name,contact_info,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (g_("title"), g_("description"), cat["id"], loc["id"] if loc else None, g_("location_text")[:200] or None, sha(token), idem, ai_id,
             1 if g_("created_via_qr").lower() in ("true", "1") else 0, g_("contact_name")[:100] or None, g_("contact_info")[:150] or None, t, t))
    tid = cur.lastrowid; num = f"UPK-{tid:06d}"
    q("UPDATE tickets SET public_number=? WHERE id=?", (num, tid))
    history(tid, "created", None, "new", None, "applicant", "Заявка создана")
    for name, mt, b in blobs:
        key = uuid.uuid4().hex
        with open(os.path.join(CFG["UPLOAD_DIR"], key), "wb") as fh: fh.write(b)
        q("INSERT INTO attachments(ticket_id,storage_key,file_name,file_type,size_bytes,created_at) VALUES(?,?,?,?,?,?)", (tid, key, os.path.basename(name)[:100], mt, len(b), t))
    db().commit()
    return jsonify(public_number=num, status="new", created_at=t, tracking_token=token, tracking_url=f"{request.host_url}track/{token}"), 201

# ---------- Отслеживание ----------
TICKET_SQL = """SELECT t.*, c.code cat_code, c.name cat_name, l.building, l.floor, l.room, l.zone_name, u.name assignee_name
  FROM tickets t JOIN categories c ON c.id=t.category_id LEFT JOIN locations l ON l.id=t.location_id LEFT JOIN users u ON u.id=t.assignee_id """
def by_token(token):
    r = q1(TICKET_SQL + "WHERE t.tracking_token_hash=?", (sha(token),))
    if not r: raise APIError("not_found", "Заявка не найдена", 404)
    return r
@route("/tracking/<token>")
def tracking(token):
    r = by_token(token)
    hs = q("SELECT * FROM ticket_history WHERE ticket_id=? AND is_public=1 AND new_status IS NOT NULL ORDER BY id", (r["id"],))
    cs = q("SELECT * FROM comments WHERE ticket_id=? AND visibility='public' ORDER BY id", (r["id"],))
    return jsonify(public_number=r["public_number"], title=r["title"], description=r["description"],
                   category={"code": r["cat_code"], "name": r["cat_name"]}, location_label=loc_label(r), status=r["status"],
                   status_label=STATUS[r["status"]], created_at=r["created_at"], updated_at=r["updated_at"],
                   resolution_note=r["resolution_note"], needs_reply=r["status"] == "needs_info",
                   history=[{"at": h["created_at"], "status": h["new_status"], "status_label": STATUS[h["new_status"]], "note": h["note"]} for h in hs],
                   comments=[{"at": c["created_at"], "author": "Сотрудник УПК" if c["author_type"] == "staff" else "Вы", "body": c["body"]} for c in cs])
@route("/tracking/<token>/reply", methods=["POST"])
def reply(token):
    r = by_token(token); text = str(body_json().get("body", "")).strip()
    if not 1 <= len(text) <= 2000: raise APIError("validation_error", "Проверьте поля формы", 422, {"body": "Ответ: от 1 до 2000 символов"})
    if r["status"] != "needs_info": raise APIError("invalid_transition", "Сейчас уточнения не требуются", 409)
    q("INSERT INTO comments(ticket_id,author_type,body,visibility,created_at) VALUES(?,?,?,?,?)", (r["id"], "applicant", text, "public", now()))
    q("UPDATE tickets SET status='under_review', updated_at=? WHERE id=?", (now(), r["id"]))
    history(r["id"], "applicant_reply", "needs_info", "under_review", None, "applicant", "Заявитель ответил на уточнение")
    db().commit(); return jsonify(ok=True), 201

# ---------- Панель сотрудника ----------
def ticket_brief(r):
    return {"id": r["id"], "public_number": r["public_number"], "title": r["title"], "category": {"code": r["cat_code"], "name": r["cat_name"]},
            "location_label": loc_label(r), "status": r["status"], "status_label": STATUS[r["status"]], "priority": r["priority"],
            "assignee": {"id": r["assignee_id"], "name": r["assignee_name"]} if r["assignee_id"] else None,
            "created_at": r["created_at"], "updated_at": r["updated_at"]}
@route("/staff/tickets")
@staff_only
def staff_list():
    a = request.args; where, p = [], []
    if a.get("q"):
        s = a["q"].strip(); m = re.fullmatch(r"(?:UPK-)?0*(\d+)", s, re.I)
        like = f"%{s}%"
        where.append("(t.title LIKE ? OR t.description LIKE ?" + (" OR t.id=?" if m else "") + ")"); p += [like, like] + ([int(m.group(1))] if m else [])
    for key, col in (("status", "t.status"), ("priority", "t.priority")):
        if a.get(key):
            vals = a[key].split(","); where.append(f"{col} IN ({','.join('?' * len(vals))})"); p += vals
    if a.get("category"): where.append("c.code=?"); p.append(a["category"])
    if a.get("location_id"): where.append("t.location_id=?"); p.append(a["location_id"])
    if a.get("assignee_id") == "me": where.append("t.assignee_id=?"); p.append(g.user["id"])
    elif a.get("assignee_id") == "none": where.append("t.assignee_id IS NULL")
    elif a.get("assignee_id"): where.append("t.assignee_id=?"); p.append(a["assignee_id"])
    if a.get("date_from"): where.append("t.created_at>=?"); p.append(a["date_from"])
    if a.get("date_to"): where.append("t.created_at<=?"); p.append(a["date_to"] + "T23:59:59Z")
    sort = a.get("sort", "-created_at"); col = sort.lstrip("-")
    if col not in ("created_at", "updated_at", "priority"): raise APIError("bad_request", "Неверный sort", 400)
    order = f"t.{col} {'DESC' if sort.startswith('-') else 'ASC'}, t.id DESC"
    try: page = max(1, int(a.get("page", 1))); size = min(100, max(1, int(a.get("page_size", 20))))
    except ValueError: raise APIError("bad_request", "Неверная страница", 400)
    w = (" WHERE " + " AND ".join(where)) if where else ""
    total = q1("SELECT count(*) n FROM tickets t JOIN categories c ON c.id=t.category_id" + w, p)["n"]
    rows = q(TICKET_SQL + w + f" ORDER BY {order} LIMIT ? OFFSET ?", p + [size, (page - 1) * size])
    return jsonify(items=[ticket_brief(r) for r in rows], page=page, page_size=size, total=total)

def staff_card(tid):
    r = q1(TICKET_SQL + "WHERE t.id=?", (tid,))
    if not r: raise APIError("not_found", "Заявка не найдена", 404)
    d = ticket_brief(r)
    ai = q1("SELECT * FROM ai_interactions WHERE id=?", (r["ai_interaction_id"],)) if r["ai_interaction_id"] else None
    d.update(description=r["description"], location_text=r["location_text"], contact_name=r["contact_name"], contact_info=r["contact_info"],
             resolution_note=r["resolution_note"], created_via_qr=bool(r["created_via_qr"]),
             ai={"type": ai["result_type"], "confidence": ai["confidence"], "answered": False} if ai else None,
             attachments=[{"id": x["id"], "file_name": x["file_name"], "file_type": x["file_type"], "size_bytes": x["size_bytes"], "url": f"{BASE_URL}/staff/attachments/{x['id']}"} for x in q("SELECT * FROM attachments WHERE ticket_id=?", (tid,))],
             comments=[{"id": c["id"], "at": c["created_at"], "author_type": c["author_type"], "body": c["body"], "visibility": c["visibility"],
                        "author": (q1("SELECT name FROM users WHERE id=?", (c["author_id"],)) or {"name": None})["name"]} for c in q("SELECT * FROM comments WHERE ticket_id=? ORDER BY id", (tid,))],
             history=[{"at": h["created_at"], "event": h["event_type"], "old_status": h["old_status"], "new_status": h["new_status"],
                       "actor_type": h["actor_type"], "actor_id": h["actor_id"], "note": h["note"]} for h in q("SELECT * FROM ticket_history WHERE ticket_id=? ORDER BY id", (tid,))],
             allowed_transitions=TRANS[r["status"]])
    return d
@route("/staff/tickets/<int:tid>")
@staff_only
def staff_get(tid): return jsonify(staff_card(tid))

@route("/staff/tickets/<int:tid>/status", methods=["PATCH"])
@staff_only
def set_status(tid):
    r = q1("SELECT * FROM tickets WHERE id=?", (tid,))
    if not r: raise APIError("not_found", "Заявка не найдена", 404)
    d = body_json(); new = d.get("status"); note = (str(d.get("note") or "").strip()) or None
    if new not in STATUS: raise APIError("validation_error", "Неизвестный статус", 422, {"status": "Неизвестный статус"})
    if new not in TRANS[r["status"]]:
        raise APIError("invalid_transition", f"Из статуса «{STATUS[r['status']]}» нельзя перейти в «{STATUS[new]}»", 409)
    if new in ("resolved", "rejected") and not note:
        raise APIError("validation_error", "Укажите комментарий: итог решения или причину отказа", 422, {"note": "Обязательное поле"})
    t = now()
    q("UPDATE tickets SET status=?, updated_at=?, resolved_at=?, resolution_note=? WHERE id=?",
      (new, t, t if new == "resolved" else None, note if new in ("resolved", "rejected") else r["resolution_note"], tid))
    history(tid, "status_changed", r["status"], new, g.user["id"], "staff", note)
    db().commit(); return jsonify(staff_card(tid))

@route("/staff/tickets/<int:tid>/assignee", methods=["PATCH"])
@staff_only
def set_assignee(tid):
    r = q1("SELECT * FROM tickets WHERE id=?", (tid,))
    if not r: raise APIError("not_found", "Заявка не найдена", 404)
    aid = body_json().get("assignee_id")
    if aid is not None and not q1("SELECT 1 FROM users WHERE id=? AND is_active=1", (aid,)):
        raise APIError("validation_error", "Исполнитель не найден", 422, {"assignee_id": "Исполнитель не найден"})
    t = now(); history(tid, "assigned", None, None, g.user["id"], "staff", None, 0, r["assignee_id"], aid)
    q("UPDATE tickets SET assignee_id=?, updated_at=? WHERE id=?", (aid, t, tid))
    if aid is not None and r["status"] in ("new", "under_review"):
        q("UPDATE tickets SET status='assigned' WHERE id=?", (tid,))
        history(tid, "status_changed", r["status"], "assigned", g.user["id"], "staff", "Назначен исполнитель")
    db().commit(); return jsonify(staff_card(tid))

@route("/staff/tickets/<int:tid>/priority", methods=["PATCH"])
@staff_only
def set_priority(tid):
    r = q1("SELECT * FROM tickets WHERE id=?", (tid,))
    if not r: raise APIError("not_found", "Заявка не найдена", 404)
    pr = body_json().get("priority")
    if pr not in PRIORITIES: raise APIError("validation_error", "Неверный приоритет", 422, {"priority": "low, normal, high, urgent"})
    q("UPDATE tickets SET priority=?, updated_at=? WHERE id=?", (pr, now(), tid))
    history(tid, "priority_changed", None, None, g.user["id"], "staff", f"Приоритет: {r['priority']} → {pr}", 0)
    db().commit(); return jsonify(staff_card(tid))

@route("/staff/tickets/<int:tid>/comments", methods=["POST"])
@staff_only
def add_comment(tid):
    if not q1("SELECT 1 FROM tickets WHERE id=?", (tid,)): raise APIError("not_found", "Заявка не найдена", 404)
    d = body_json(); text = str(d.get("body", "")).strip(); vis = d.get("visibility", "internal")
    if not 1 <= len(text) <= 2000 or vis not in ("public", "internal"):
        raise APIError("validation_error", "Проверьте поля формы", 422, {"body": "Комментарий: от 1 до 2000 символов"})
    cur = q("INSERT INTO comments(ticket_id,author_id,author_type,body,visibility,created_at) VALUES(?,?,?,?,?,?)", (tid, g.user["id"], "staff", text, vis, now()))
    history(tid, "comment_added", None, None, g.user["id"], "staff", None, 0)
    q("UPDATE tickets SET updated_at=? WHERE id=?", (now(), tid)); db().commit()
    return jsonify(id=cur.lastrowid, body=text, visibility=vis, at=now()), 201

@route("/staff/attachments/<int:aid>")
@staff_only
def get_attachment(aid):
    a = q1("SELECT * FROM attachments WHERE id=?", (aid,))
    if not a: raise APIError("not_found", "Файл не найден", 404)
    resp = send_file(os.path.join(CFG["UPLOAD_DIR"], a["storage_key"]), mimetype=a["file_type"])
    resp.headers["X-Content-Type-Options"] = "nosniff"; return resp

@route("/staff/users")
@staff_only
def staff_users(): return jsonify(items=[dict(id=u["id"], name=u["name"], role=u["role"]) for u in q("SELECT * FROM users WHERE is_active=1")])

@route("/staff/stats")
@staff_only
def stats():
    c = q1("SELECT count(*) a, sum(status NOT IN('resolved','rejected')) o, sum(status='resolved') r, sum(status='rejected') x FROM tickets")
    avg = q1("SELECT avg((julianday(resolved_at)-julianday(created_at))*24) h FROM tickets WHERE resolved_at IS NOT NULL")["h"]
    ai = q1("""SELECT count(*) n, sum(NOT EXISTS(SELECT 1 FROM tickets t WHERE t.ai_interaction_id=a.id)) ans,
               sum(EXISTS(SELECT 1 FROM tickets t WHERE t.ai_interaction_id=a.id)) conv, sum(handoff) h FROM ai_interactions a""")
    return jsonify(totals={"all": c["a"], "open": c["o"] or 0, "resolved": c["r"] or 0, "rejected": c["x"] or 0},
                   by_status=[{"status": r["status"], "count": r["n"]} for r in q("SELECT status, count(*) n FROM tickets GROUP BY status")],
                   by_category=[{"code": r["code"], "name": r["name"], "count": r["n"]} for r in q("SELECT c.code,c.name,count(*) n FROM tickets t JOIN categories c ON c.id=t.category_id GROUP BY c.id ORDER BY n DESC")],
                   by_location=[{"location_id": r["id"], "label": loc_label({**dict(r), "location_id": r["id"], "location_text": None}), "count": r["n"]}
                                for r in q("SELECT l.*, count(*) n FROM tickets t JOIN locations l ON l.id=t.location_id GROUP BY l.id ORDER BY n DESC")],
                   avg_resolution_hours=round(avg, 1) if avg is not None else None,
                   ai={"interactions": ai["n"], "answered_by_ai": ai["ans"] or 0, "converted_to_ticket": ai["conv"] or 0, "handoff": ai["h"] or 0})

import json as _json

# ---------- CORS (без доп. библиотек; работает и для file://) ----------
@app.after_request
def _cors(r):
    r.headers["Access-Control-Allow-Origin"] = "*"
    r.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, Idempotency-Key"
    r.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, OPTIONS"
    return r

# ---------- ИИ-модуль на правилах (без внешних API) ----------
def _load_kb():
    for p in (os.path.join(BASE, "knowledge_base.json"), os.path.join(BASE, "..", "ai", "knowledge_base.json")):
        if os.path.exists(p):
            return _json.load(open(p, encoding="utf-8"))["entries"]
    return [{"id": "kb_projector_connect", "keywords": ["проектор", "hdmi", "подключить"],
             "answer": "1) Включите проектор и дождитесь прогрева.\n2) Подключите ноутбук кабелем HDMI или VGA.\n3) На проекторе выберите вход (Source/Input).\n4) На ноутбуке нажмите Win+P → «Повторяющийся».\nНе помогло — оформите заявку."},
            {"id": "kb_check_status", "keywords": ["статус", "заявка", "отслеживание"],
             "answer": "После отправки заявки вы получаете номер и ссылку для отслеживания. Откройте её, чтобы увидеть статус и комментарии."}]
KB = _load_kb()
R_DANGER = re.compile(r"искр|\bдым|запах\s+гар|\bгар(ь|и|ью)\b|\bогон|пожар|замыкани|бьет\s+током|ударил\w*\s+током|оголенн|запах\s+газа")
R_UNSAFE = re.compile(r"(проводк|розетк|щит).{0,30}(сам|самостоят)|(сам|самостоят).{0,30}(проводк|розетк|щит)|вскрыть\s+(щит|розетк)")
R_SECRET = re.compile(r"парол|код\s+доступа|логин\s+от")
R_QUESTION = re.compile(r"\b(как|где|почему|зачем|куда|когда|что\s+делать)\b|\?")
R_FAULT = re.compile(r"слом|не\s+работа|не\s+включа|не\s+печата|не\s+горит|не\s+откры|не\s+закры|неисправ|поломк|разбит|треснул|течет|протека|грязн|нет\s+(света|интернета|воды)|заклини|оторва")
CAT_HINTS = [("equipment", r"проектор|компьютер|ноутбук|принтер|доск|монитор"), ("electricity", r"свет|ламп|розетк|выключат|электр|провод"),
             ("network", r"интернет|wi-?fi|сеть|сетев"), ("building", r"окн|двер|замок|потолок|стен|ручк|кран"),
             ("furniture", r"стул|\bстол\b|шкаф|парт|жалюзи|мебел"), ("cleaning", r"грязн|мусор|уборк|пролит")]
ANS_DANGER = "⚠️ ОПАСНО! Не трогайте оборудование, отойдите и сообщите сотруднику!"

def _ai_reply(text):
    t = text.lower().replace("ё", "е")
    cat = next((c for c, p in CAT_HINTS if re.search(p, t)), "other")
    scored = sorted(((sum(1 for k in e["keywords"] if k.lower()[:max(4, len(k) - 2)] in t), e) for e in KB), key=lambda x: -x[0])
    kb = scored[0][1] if scored and scored[0][0] > 0 else None
    out = {"type": "unknown", "answer": "Я не уверен, но лучше оформить заявку.", "suggest_ticket": True, "category_code": cat, "kb_codes": [], "confidence": 0.4}
    if R_DANGER.search(t):
        out.update(type="danger", answer=ANS_DANGER, confidence=0.95, priority_suggestion="urgent")
    elif R_UNSAFE.search(t):
        out.update(type="danger", answer="Работы с проводкой опасны для жизни, я не могу дать такую инструкцию. Обратитесь к ответственному сотруднику.", suggest_ticket=False, category_code=None, confidence=0.9)
    elif R_SECRET.search(t):
        out.update(answer="Пароли и коды доступа я не выдаю. Обратитесь к ответственному сотруднику по официальной процедуре.", suggest_ticket=False, category_code=None, confidence=0.9)
    elif R_QUESTION.search(t) and kb:
        out.update(type="question", answer=kb["answer"], suggest_ticket=False, category_code=None, kb_codes=[kb["id"]], confidence=0.8)
    elif R_FAULT.search(t):
        out.update(type="fault", answer="Похоже на поломку. Оформите заявку, и мы придем.", confidence=0.85)
    return out

@route("/ai/analyze", methods=["POST"])
def ai_analyze():
    d = body_json(); msg = str(d.get("message", "")).strip()
    if not 1 <= len(msg) <= 1000:
        raise APIError("validation_error", "Введите сообщение (до 1000 символов)", 422, {"message": "От 1 до 1000 символов"})
    r = _ai_reply(msg)
    lid = d.get("location_id") if isinstance(d.get("location_id"), int) else None
    cur = q("INSERT INTO ai_interactions(location_id,input_text,result_type,confidence,suggest_ticket,handoff,kb_codes,source,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (lid, msg, {"question": "info_question", "fault": "malfunction"}.get(r["type"], r["type"]), r["confidence"],
             int(r["suggest_ticket"]), int(r["type"] == "unknown"), ",".join(r["kb_codes"]), "rules", now()))
    db().commit(); r["interaction_id"] = cur.lastrowid
    return jsonify(r)
# =====================================================================================================

if __name__ == "__main__":
    init_db(); app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=os.environ.get("DEBUG") == "1")
