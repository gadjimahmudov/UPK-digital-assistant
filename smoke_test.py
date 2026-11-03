"""Быстрая проверка ЖИВОГО сервера (без доп. библиотек).
Запуск:  python smoke_test.py            (сервер на http://localhost:8000)
         python smoke_test.py http://localhost:8000 upk1 demo12345
"""
import sys, json, uuid, urllib.request, urllib.error

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/") + "/api/v1"
LOGIN = sys.argv[2] if len(sys.argv) > 2 else "upk1"
PASSWORD = sys.argv[3] if len(sys.argv) > 3 else "demo12345"
ok_count = fail_count = 0

def call(method, path, body=None, token=None, headers=None):
    h = {"Content-Type": "application/json", **(headers or {})}
    if token: h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, method=method, headers=h, data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read(); return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read(); return e.code, (json.loads(raw) if raw else None)
    except urllib.error.URLError as e:
        print(f"\n❌ Сервер недоступен по адресу {BASE}\n   Запущен ли `python app.py`? Причина: {e.reason}"); sys.exit(1)

def check(name, cond, extra=""):
    global ok_count, fail_count
    if cond: ok_count += 1; print(f"  ✅ {name}")
    else: fail_count += 1; print(f"  ❌ {name}  {extra}")

print(f"Проверяю {BASE}\n")
print("1. Справочники и QR")
s, d = call("GET", "/categories"); check("категории загружаются", s == 200 and len(d["items"]) >= 5, s)
s, d = call("GET", "/locations/by-qr/k214-Xv7Qe3Ls"); check("QR k214 → «Главный корпус, каб. 214»", s == 200 and "214" in d["label"], d)
s, d = call("GET", "/locations/by-qr/unknown"); check("неизвестный QR → 404", s == 404)

print("\n2. Создание заявки (как заявитель)")
key = str(uuid.uuid4())
payload = {"title": "Не работает проектор", "description": "В кабинете 214 не работает проектор, не включается",
           "category_code": "equipment", "location_id": 1, "created_via_qr": True}
s, t = call("POST", "/tickets", payload, headers={"Idempotency-Key": key}); check("заявка создана (201)", s == 201, (s, t))
number, token = t.get("public_number"), t.get("tracking_token")
print(f"     → номер {number}, ссылка отслеживания: {t.get('tracking_url')}")
s, t2 = call("POST", "/tickets", payload, headers={"Idempotency-Key": key})
check("повторная отправка не создаёт дубль", s == 200 and t2["public_number"] == number, (s, t2))
s, d = call("POST", "/tickets", {"title": "x", "description": "коротко", "category_code": "equipment", "location_id": 1}, headers={"Idempotency-Key": str(uuid.uuid4())})
check("кривые поля → 422 с подсказками", s == 422 and "fields" in d["error"], d)
s, d = call("GET", f"/tracking/{token}"); check("страница отслеживания: статус «Новая»", s == 200 and d["status"] == "new", d)

print("\n3. Панель сотрудника")
s, d = call("GET", "/staff/tickets"); check("без входа → 401", s == 401)
s, d = call("POST", "/auth/login", {"login": LOGIN, "password": "неверный"}); check("неверный пароль → 401", s == 401)
s, d = call("POST", "/auth/login", {"login": LOGIN, "password": PASSWORD}); check(f"вход как {LOGIN}", s == 200, (s, d))
if s != 200: print("\n   Не удалось войти: проверьте логин/пароль (по умолчанию upk1 / demo12345)."); sys.exit(1)
tok = d["access_token"]
s, d = call("GET", f"/staff/tickets?q={number}", token=tok); check("поиск по номеру находит заявку", s == 200 and d["total"] >= 1, d)
tid = d["items"][0]["id"]
s, d = call("PATCH", f"/staff/tickets/{tid}/status", {"status": "resolved"}, token=tok); check("«Новая → Решена» запрещено (409)", s == 409, d)
s, d = call("PATCH", f"/staff/tickets/{tid}/status", {"status": "under_review"}, token=tok); check("«Новая → На рассмотрении»", s == 200)
s, d = call("PATCH", f"/staff/tickets/{tid}/assignee", {"assignee_id": 2}, token=tok); check("назначен исполнитель → статус «Назначен»", s == 200 and d["status"] == "assigned", d)
call("POST", f"/staff/tickets/{tid}/comments", {"body": "ВНУТРЕННЯЯ заметка", "visibility": "internal"}, token=tok)
call("POST", f"/staff/tickets/{tid}/comments", {"body": "Проверим сегодня", "visibility": "public"}, token=tok)
s, d = call("PATCH", f"/staff/tickets/{tid}/status", {"status": "resolved"}, token=tok); check("«Решена» без комментария → 422", s == 422, d)
call("PATCH", f"/staff/tickets/{tid}/status", {"status": "in_progress"}, token=tok)
s, d = call("PATCH", f"/staff/tickets/{tid}/status", {"status": "resolved", "note": "Заменили лампу"}, token=tok); check("«Решена» с комментарием", s == 200)

print("\n4. Что видит заявитель после обработки")
s, d = call("GET", f"/tracking/{token}")
check("статус «Решена» и итог виден", d["status"] == "resolved" and d["resolution_note"] == "Заменили лампу", d)
check("публичный комментарий виден", "Проверим сегодня" in json.dumps(d, ensure_ascii=False))
check("внутренняя заметка скрыта", "ВНУТРЕННЯЯ" not in json.dumps(d, ensure_ascii=False))
s, d = call("GET", "/staff/stats", token=tok); check("статистика считается", s == 200 and d["totals"]["all"] >= 1, d)

print(f"\nИтого: ✅ {ok_count}   ❌ {fail_count}")
sys.exit(1 if fail_count else 0)
