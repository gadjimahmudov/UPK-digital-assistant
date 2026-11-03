"""Автотесты этапа 1. Запуск: python -m unittest test_api -v"""
import os, tempfile, unittest, io
_d = tempfile.mkdtemp()
os.environ.update(DB_PATH=os.path.join(_d, "t.db"), UPLOAD_DIR=os.path.join(_d, "up"), DEMO_PASSWORD="pw-test-1")
import app as A
A.init_db()
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 50
API = "/api/v1"

class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.c = A.app.test_client()
    def login(self, u="upk1"):
        r = self.c.post(f"{API}/auth/login", json={"login": u, "password": "pw-test-1"}); self.assertEqual(r.status_code, 200)
        return {"Authorization": "Bearer " + r.json["access_token"]}
    def mk(self, key, **kw):
        d = {"title": "Не работает проектор", "description": "В кабинете 214 не работает проектор", "category_code": "equipment", "location_id": "1"}; d.update(kw)
        return self.c.post(f"{API}/tickets", data=d, headers={"Idempotency-Key": key})

    def test_01_qr_location(self):
        r = self.c.get(f"{API}/locations/by-qr/k214-Xv7Qe3Ls"); self.assertEqual(r.json["label"], "Главный корпус, каб. 214")
        self.assertEqual(self.c.get(f"{API}/locations/by-qr/nope").status_code, 404)
        self.assertNotIn("qr_key", str(self.c.get(f"{API}/locations").json))
    def test_02_create_and_idempotency(self):
        r = self.mk("key-aaa-1"); self.assertEqual(r.status_code, 201); self.assertTrue(r.json["public_number"].startswith("UPK-"))
        r2 = self.mk("key-aaa-1"); self.assertEqual(r2.status_code, 200)
        self.assertEqual(r.json["public_number"], r2.json["public_number"]); self.assertEqual(r.json["tracking_token"], r2.json["tracking_token"])
    def test_03_validation_and_photo(self):
        r = self.mk("key-bbb-1", title="x", description="коротко"); self.assertEqual(r.status_code, 422); self.assertIn("title", r.json["error"]["fields"])
        bad = self.c.post(f"{API}/tickets", data={"title": "Тест тест", "description": "достаточно длинное описание", "category_code": "other", "location_text": "где-то",
                          "photos": (io.BytesIO(b"MZ not an image"), "x.png")}, headers={"Idempotency-Key": "key-ccc-1"}, content_type="multipart/form-data")
        self.assertEqual(bad.status_code, 415)
        ok = self.c.post(f"{API}/tickets", data={"title": "С фото", "description": "достаточно длинное описание", "category_code": "other", "location_text": "где-то",
                         "photos": (io.BytesIO(PNG), "x.png")}, headers={"Idempotency-Key": "key-ddd-1"}, content_type="multipart/form-data")
        self.assertEqual(ok.status_code, 201)
    def test_04_staff_requires_auth(self):
        self.assertEqual(self.c.get(f"{API}/staff/tickets").status_code, 401)
        self.assertEqual(self.c.get(f"{API}/staff/tickets", headers={"Authorization": "Bearer junk"}).status_code, 401)
        self.assertEqual(self.c.post(f"{API}/auth/login", json={"login": "upk1", "password": "bad"}).status_code, 401)
    def test_05_full_flow(self):
        t = self.mk("key-flow-1", title="Сломалась ручка окна").json; h = self.login()
        lst = self.c.get(f"{API}/staff/tickets?q=ручка&status=new", headers=h).json
        self.assertEqual(lst["total"], 1); tid = lst["items"][0]["id"]
        self.assertEqual(self.c.patch(f"{API}/staff/tickets/{tid}/status", json={"status": "resolved"}, headers=h).status_code, 409)
        self.assertEqual(self.c.patch(f"{API}/staff/tickets/{tid}/assignee", json={"assignee_id": 2}, headers=h).json["status"], "assigned")
        self.assertEqual(self.c.patch(f"{API}/staff/tickets/{tid}/status", json={"status": "in_progress"}, headers=h).status_code, 200)
        self.c.post(f"{API}/staff/tickets/{tid}/comments", json={"body": "ВНУТРЕННЕЕ: секрет", "visibility": "internal"}, headers=h)
        self.c.post(f"{API}/staff/tickets/{tid}/comments", json={"body": "Проверим сегодня", "visibility": "public"}, headers=h)
        r = self.c.patch(f"{API}/staff/tickets/{tid}/status", json={"status": "needs_info", "note": "Какое окно?"}, headers=h); self.assertEqual(r.status_code, 200)
        tr = self.c.get(f"{API}/tracking/{t['tracking_token']}").json
        self.assertTrue(tr["needs_reply"]); self.assertNotIn("секрет", str(tr)); self.assertNotIn("Сотрудник УПК 2", str(tr))
        self.assertEqual(self.c.post(f"{API}/tracking/{t['tracking_token']}/reply", json={"body": "Левое окно"}).status_code, 201)
        self.assertEqual(self.c.get(f"{API}/tracking/{t['tracking_token']}").json["status"], "under_review")
        self.assertEqual(self.c.post(f"{API}/tracking/{t['tracking_token']}/reply", json={"body": "ещё"}).status_code, 409)
        self.assertEqual(self.c.patch(f"{API}/staff/tickets/{tid}/status", json={"status": "resolved"}, headers=h).status_code, 422)
        self.assertEqual(self.c.patch(f"{API}/staff/tickets/{tid}/status", json={"status": "resolved", "note": "Заменили ручку"}, headers=h).status_code, 200)
        card = self.c.get(f"{API}/staff/tickets/{tid}", headers=h).json
        self.assertGreaterEqual(len(card["history"]), 6); self.assertEqual(card["allowed_transitions"], ["in_progress"])
        self.assertEqual(self.c.get(f"{API}/tracking/{t['tracking_token']}").json["resolution_note"], "Заменили ручку")
    def test_06_tracking_unknown_token_and_stats(self):
        self.assertEqual(self.c.get(f"{API}/tracking/{'a'*43}").status_code, 404)
        s = self.c.get(f"{API}/staff/stats", headers=self.login()).json
        self.assertGreaterEqual(s["totals"]["all"], 1); self.assertIn("ai", s)
    def test_07_json_errors(self):
        self.assertEqual(self.c.get(f"{API}/nothing").json["error"]["code"], "not_found")
        self.assertEqual(self.c.post(f"{API}/tickets", json={}).status_code, 400)

if __name__ == "__main__": unittest.main()
