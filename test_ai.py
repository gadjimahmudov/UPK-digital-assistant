"""Тесты ИИ-модуля и главной страницы. Запуск: python -m unittest test_ai -v"""
import os, json, tempfile, unittest
_d = tempfile.mkdtemp()
os.environ.update(DB_PATH=os.path.join(_d, "t.db"), UPLOAD_DIR=os.path.join(_d, "up"), JWT_SECRET="test-jwt", TRACKING_SECRET="test-track")
import app as A
A.init_db()
HERE = os.path.dirname(os.path.abspath(__file__))
MAP = {"info_question": "question", "malfunction": "fault", "danger": "danger", "unknown": "unknown", "need_more_info": "unknown"}
API = "/api/v1"

class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.c = A.app.test_client()
    def test_10_cases_from_tz(self):
        cases = json.load(open(os.path.join(HERE, "test_cases.json"), encoding="utf-8"))["cases"]
        for t in cases:
            r = A._ai_reply(t["input"])
            with self.subTest(case=t["id"], text=t["input"]):
                self.assertEqual(r["type"], MAP[t["expect_type"]])
                for code in t.get("must", []): self.assertIn(code, r["kb_codes"])
                for w in t.get("must_not", []): self.assertNotIn(w.lower(), r["answer"].lower())
    def test_danger_phrasings(self):
        for p in ["В розетке искрит", "Пахнет паленым", "Из розетки задымилось", "Что-то горит в лаборатории", "Горит проводка", "Током бьёт",
                  "Розетка раскалилась и оплавилась", "Монитор перегрелся", "Запахло горелым", "Капает вода на розетку", "Принтер задымил"]:
            with self.subTest(p): self.assertEqual(A._ai_reply(p)["type"], "danger")
        self.assertEqual(A._ai_reply("В розетке искрит")["priority_suggestion"], "urgent")
    def test_no_false_alarms(self):
        for p in ["Лампа не горит", "Как подключить проектор?", "В кабинете грязно", "Гарантия на ноутбук"]:
            with self.subTest(p): self.assertNotEqual(A._ai_reply(p)["type"], "danger")
    def test_fault_with_question_mark_offers_ticket(self):
        self.assertEqual(A._ai_reply("Не работает проектор?")["type"], "fault")
        r = A._ai_reply("В кабинете 214 не работает проектор, что делать?")
        self.assertEqual(r["type"], "question"); self.assertTrue(r["suggest_ticket"]); self.assertEqual(r["category_code"], "equipment")
    def test_secrets_and_unsafe(self):
        r = A._ai_reply("Какой пароль от Wi-Fi сотрудников?"); self.assertEqual(r["type"], "unknown"); self.assertFalse(r["suggest_ticket"])
        r = A._ai_reply("Как самостоятельно починить розетку?"); self.assertEqual(r["type"], "danger"); self.assertFalse(r["suggest_ticket"])
    def test_endpoint(self):
        self.assertEqual(self.c.post(f"{API}/ai/analyze", json={"message": "  "}).status_code, 422)
        r = self.c.post(f"{API}/ai/analyze", json={"message": "В розетке искрит"}); self.assertEqual(r.status_code, 200)
        self.assertIn("interaction_id", r.json); self.assertIn("ОПАСНО", r.json["answer"])
    def test_index_served_and_http_errors_are_json(self):
        r = self.c.get("/"); self.assertEqual(r.status_code, 200); self.assertIn("УПК", r.get_data(as_text=True)); r.close()
        self.assertEqual(self.c.get(f"{API}/tickets").status_code, 405)

if __name__ == "__main__": unittest.main()
