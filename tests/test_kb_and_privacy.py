import json
import os
import unittest
from unittest import mock

from tests.base import AppTestCase

DOC = """---
title: 院内腹泻处置规范（测试用）
source: 测试集团 医疗管理部
version: v1.2
published_on: 2026-02-01
license_note: 测试用自有文件
topics: [腹泻, 补液]
species: [犬]
---

## 3.1 初始评估

接诊时先记录持续时间、粪便性状与精神状态，并核对免疫与驱虫记录是否完整。

## 3.2 补液通路

建立静脉通路后按院内表格核算速率，每两小时复评一次精神状态与黏膜色泽。
"""


def write_doc(directory, name="d.md", text=DOC):
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


class TestPlaceholderMode(AppTestCase):
    kb_mode = "placeholder"

    def test_status_exposes_scope(self):
        s = self.client.get("/api/kb/status").get_json()
        self.assertEqual(s["mode"], "placeholder")
        self.assertTrue(s["planned_scope"])
        self.assertTrue(s["non_scope"])

    def test_medical_question_is_not_answered(self):
        r = self.client.post("/api/kb/ask", json={"question": "犬细小怎么治疗？剂量多少？"})
        body = r.get_json()
        self.assertEqual(body["mode"], "placeholder")
        self.assertFalse(body["answerable"])
        self.assertIn("尚未上线", body["message"])
        self.assertNotIn("claims", body)

    def test_llm_is_never_called_in_placeholder_mode(self):
        with mock.patch("app.kb.answer.chat") as m:
            self.client.post("/api/kb/ask", json={"question": "阿莫西林剂量？"})
            m.assert_not_called()


class TestOffMode(AppTestCase):
    kb_mode = "off"

    def test_off_mode_declines(self):
        body = self.client.post("/api/kb/ask", json={"question": "任何问题"}).get_json()
        self.assertEqual(body["mode"], "off")
        self.assertFalse(body["answerable"])


class TestRagMode(AppTestCase):
    kb_mode = "rag"

    def _import(self):
        from app.kb import store
        return store.import_file(write_doc(os.path.join(self.tmp, "docs")))

    def test_import_requires_license_note(self):
        from app.kb import store
        bad = DOC.replace("license_note: 测试用自有文件\n", "")
        path = write_doc(os.path.join(self.tmp, "docs"), "bad.md", bad)
        with self.assertRaises(store.ImportError_) as ctx:
            store.import_file(path)
        self.assertIn("license_note", str(ctx.exception))

    def test_import_keeps_metadata_and_locators(self):
        from app import db
        r = self._import()
        self.assertGreaterEqual(r["chunks"], 2)
        doc = db.list_docs()[0]
        self.assertEqual(doc["version"], "v1.2")
        self.assertEqual(doc["published_on"], "2026-02-01")
        self.assertEqual(doc["source"], "测试集团 医疗管理部")
        locs = {c["locator"] for c in db.active_chunks()}
        self.assertIn("3.1 初始评估", locs)

    def test_empty_kb_refuses(self):
        body = self.client.post("/api/kb/ask", json={"question": "补液速率怎么算"}).get_json()
        self.assertFalse(body["answerable"])
        self.assertEqual(body["reason"], "no_documents")

    def test_out_of_scope_question_refuses_without_calling_model(self):
        self._import()
        with mock.patch("app.kb.answer.chat") as m:
            body = self.client.post(
                "/api/kb/ask", json={"question": "鹦鹉羽毛管螨用什么药"}).get_json()
            m.assert_not_called()
        self.assertFalse(body["answerable"])
        self.assertEqual(body["reason"], "no_evidence")

    def test_answer_carries_resolvable_citations(self):
        from app import db
        self._import()
        chunk = [c for c in db.active_chunks() if "3.2" in c["locator"]][0]
        payload = json.dumps({
            "answerable": True, "reason": None,
            "claims": [{"text": "建立静脉通路后按院内表格核算速率。", "citations": ["C1"]}],
            "caveats": ["每两小时复评一次。"],
        }, ensure_ascii=False)
        with mock.patch("app.kb.answer.chat", return_value=payload):
            body = self.client.post(
                "/api/kb/ask", json={"question": "补液通路建立后怎么核算速率"}).get_json()
        self.assertTrue(body["answerable"])
        cid = body["claims"][0]["citations"][0]["chunk_id"]
        src = self.client.get(f"/api/kb/source/{cid}").get_json()
        self.assertTrue(src["ok"])
        self.assertIn("静脉通路", src["text"])
        self.assertEqual(src["version"], "v1.2")

    def test_claim_without_citation_is_discarded(self):
        self._import()
        payload = json.dumps({
            "answerable": True, "reason": None,
            "claims": [{"text": "静脉补液速率一般为每小时10ml/kg。", "citations": []}],
            "caveats": [],
        }, ensure_ascii=False)
        with mock.patch("app.kb.answer.chat", return_value=payload):
            body = self.client.post(
                "/api/kb/ask", json={"question": "补液通路怎么建立"}).get_json()
        self.assertFalse(body["answerable"])
        self.assertEqual(body["reason"], "unverified_citation")
        self.assertEqual(body["claims"], [])

    def test_fabricated_citation_id_is_rejected(self):
        self._import()
        payload = json.dumps({
            "answerable": True, "reason": None,
            "claims": [{"text": "见第 47 页。", "citations": ["C99"]}],
            "caveats": [],
        }, ensure_ascii=False)
        with mock.patch("app.kb.answer.chat", return_value=payload):
            body = self.client.post(
                "/api/kb/ask", json={"question": "初始评估要记录什么"}).get_json()
        self.assertFalse(body["answerable"])
        self.assertEqual(body["reason"], "unverified_citation")

    def test_model_declared_conflict_is_surfaced(self):
        self._import()
        payload = json.dumps({"answerable": False, "reason": "conflict",
                              "claims": [], "caveats": []}, ensure_ascii=False)
        with mock.patch("app.kb.answer.chat", return_value=payload):
            body = self.client.post(
                "/api/kb/ask", json={"question": "初始评估要记录什么"}).get_json()
        self.assertFalse(body["answerable"])
        self.assertEqual(body["reason"], "conflict")

    def test_disabled_doc_is_not_retrieved(self):
        from app import db
        r = self._import()
        db.set_doc_active(r["doc_id"], False)
        body = self.client.post(
            "/api/kb/ask", json={"question": "补液通路建立后怎么核算速率"}).get_json()
        self.assertFalse(body["answerable"])
        self.assertEqual(body["reason"], "no_documents")


class TestPrivacy(AppTestCase):
    kb_mode = "placeholder"

    def test_scrub_removes_identifiers(self):
        from app import privacy
        text = "主人:张伟 电话13812345678 病例号:A20260913 邮箱 a@b.com"
        out, counts = privacy.scrub(text)
        self.assertNotIn("13812345678", out)
        self.assertNotIn("a@b.com", out)
        self.assertNotIn("A20260913", out)
        self.assertIn("phone_cn", counts)
        self.assertIn("case_no", counts)
        self.assertIn("email", counts)

    def test_event_log_contains_no_clinical_text(self):
        from app import db
        from unittest import mock as m2
        sid = self.new_session()
        self.set_transcript(sid, "宠主说狗拉稀，兽医给了甲硝唑10mg。")
        with m2.patch("app.soap.chat",
                      return_value='{"subjective":"腹泻","objective":"未提及",'
                                   '"assessment":"未提及","plan":"甲硝唑10mg"}'):
            self.client.post(f"/api/sessions/{sid}/note")
        conn = db.connect()
        rows = conn.execute("SELECT event, session_ref, payload FROM event_log").fetchall()
        conn.close()
        self.assertTrue(rows)
        for r in rows:
            blob = r["payload"]
            self.assertNotIn("甲硝唑", blob)
            self.assertNotIn("拉稀", blob)
            self.assertNotIn(sid, blob)
            self.assertNotEqual(r["session_ref"], sid)

    def test_raw_query_not_stored_by_default(self):
        from app import db
        self.client.post("/api/kb/ask", json={"question": "阿莫西林剂量", "store_raw": True})
        conn = db.connect()
        n = conn.execute("SELECT COUNT(*) c FROM raw_queries").fetchone()["c"]
        conn.close()
        self.assertEqual(n, 0)

    def test_raw_query_stored_scrubbed_when_enabled_and_consented(self):
        from app import db
        from app.config import reset_config_for_tests
        os.environ["ZS_ALLOW_RAW_QUERY_STORAGE"] = "true"
        reset_config_for_tests()
        from app.main import create_app
        client = create_app({"TESTING": True}).test_client()
        client.post("/api/kb/ask",
                    json={"question": "主人13812345678问阿莫西林剂量", "store_raw": True})
        conn = db.connect()
        rows = conn.execute("SELECT query_text, expires_at FROM raw_queries").fetchall()
        conn.close()
        self.assertEqual(len(rows), 1)
        self.assertNotIn("13812345678", rows[0]["query_text"])
        self.assertGreater(rows[0]["expires_at"], 0)

    def test_purge_and_delete_endpoints(self):
        self.assertEqual(self.client.post("/api/privacy/purge").status_code, 200)
        self.assertEqual(self.client.delete("/api/privacy/raw-queries").status_code, 200)

    def test_privacy_endpoint_lists_deletion_paths(self):
        p = self.client.get("/api/privacy").get_json()
        self.assertIn("single_session", p["how_to_delete"])
        self.assertIn("音频", p["what_is_not_logged"])


class TestInvalidMode(unittest.TestCase):

    def test_bad_kb_mode_fails_loudly(self):
        saved = os.environ.get("ZS_KB_MODE")
        os.environ["ZS_KB_MODE"] = "banana"
        try:
            from app.config import Config, reset_config_for_tests
            reset_config_for_tests()
            with self.assertRaises(ValueError):
                Config()
        finally:
            if saved is None:
                os.environ.pop("ZS_KB_MODE", None)
            else:
                os.environ["ZS_KB_MODE"] = saved
            from app.config import reset_config_for_tests as r
            r()


if __name__ == "__main__":
    unittest.main()


class TestRetrievalGate(AppTestCase):
    """The refusal gate must not depend on corpus size."""
    kb_mode = "rag"

    def setUp(self):
        super().setUp()
        from app.kb import store
        store.import_file(write_doc(os.path.join(self.tmp, "docs")))

    def test_matching_section_ranks_first(self):
        from app.kb import retrieve
        hits = retrieve.search("补液通路怎么建立", top_k=3)
        self.assertTrue(hits)
        self.assertIn("3.2", hits[0]["locator"])

    def test_coverage_is_between_zero_and_one(self):
        from app.kb import retrieve
        for h in retrieve.search("初始评估记录什么", top_k=5):
            self.assertGreaterEqual(h["coverage"], 0.0)
            self.assertLessEqual(h["coverage"], 1.0)

    def test_unrelated_query_returns_nothing(self):
        from app.kb import retrieve
        self.assertEqual(retrieve.search("鹦鹉羽毛管螨", top_k=5), [])

    def test_heading_terms_are_searchable(self):
        from app.kb import retrieve
        hits = retrieve.search("初始评估", top_k=3)
        self.assertTrue(hits)
        self.assertIn("3.1", hits[0]["locator"])


class TestDemandCollection(AppTestCase):
    """placeholder mode collects requirements. It must never answer, and it
    must only claim success when a row was actually written."""
    kb_mode = "placeholder"

    def test_status_carries_the_required_copy(self):
        c = self.client.get("/api/kb/status").get_json()["request_copy"]
        self.assertIn("知识问答尚未开放", c["intro"])
        self.assertIn("目前不会生成答案", c["intro"])
        self.assertEqual(c["placeholder"], "你希望知识助手帮你查什么？")
        self.assertEqual(c["submit_label"], "提交需求")
        self.assertIn("请勿填写宠主姓名、联系方式、病例号等可识别信息", c["pii_warning"])
        self.assertIn("需求分析", c["pii_warning"])

    def test_submission_is_saved_and_confirmed(self):
        from app import db
        r = self.client.post("/api/kb/requests",
                             json={"text": "希望能查院内犬腹泻的补液规范"})
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["saved"])
        self.assertEqual(body["message"], "已收到你的需求，感谢帮助我们确定优先支持的内容。")
        self.assertEqual(db.count_feature_requests(), 1)
        self.assertEqual(db.list_feature_requests()[0]["text_scrubbed"],
                         "希望能查院内犬腹泻的补液规范")

    def test_submission_never_returns_an_answer(self):
        body = self.client.post("/api/kb/requests",
                                json={"text": "犬细小的治疗剂量是多少"}).get_json()
        for forbidden in ("claims", "answer", "answer_text", "sources"):
            self.assertNotIn(forbidden, body)

    def test_submission_never_calls_the_model(self):
        with mock.patch("app.kb.answer.chat") as m1, mock.patch("app.soap.chat") as m2:
            self.client.post("/api/kb/requests", json={"text": "阿莫西林怎么用"})
            m1.assert_not_called()
            m2.assert_not_called()

    def test_empty_submission_is_rejected_and_not_saved(self):
        from app import db
        r = self.client.post("/api/kb/requests", json={"text": "   "})
        self.assertEqual(r.status_code, 400)
        body = r.get_json()
        self.assertFalse(body["saved"])
        self.assertNotIn("已收到", body["message"])
        self.assertEqual(db.count_feature_requests(), 0)

    def test_overlong_submission_is_rejected(self):
        from app import db
        r = self.client.post("/api/kb/requests", json={"text": "问" * 2001})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.get_json()["saved"])
        self.assertEqual(db.count_feature_requests(), 0)

    def test_pii_is_scrubbed_before_storage_and_reported(self):
        from app import db
        r = self.client.post("/api/kb/requests", json={
            "text": "主人:张伟 电话13812345678 病例号:A20260913，想查腹泻处置"})
        body = r.get_json()
        self.assertTrue(body["saved"])
        self.assertIn("phone_cn", body["scrubbed"])
        self.assertIn("case_no", body["scrubbed"])
        stored = db.list_feature_requests()[0]["text_scrubbed"]
        self.assertNotIn("13812345678", stored)
        self.assertNotIn("A20260913", stored)
        self.assertIn("腹泻处置", stored)

    def test_storage_failure_reports_failure_not_success(self):
        from app import db
        with mock.patch("app.routes.db.insert_feature_request",
                        side_effect=RuntimeError("disk full")):
            r = self.client.post("/api/kb/requests", json={"text": "想查补液规范"})
        self.assertEqual(r.status_code, 500)
        body = r.get_json()
        self.assertFalse(body["ok"])
        self.assertFalse(body["saved"])
        self.assertIn("没有保存", body["message"])
        self.assertNotIn("已收到", body["message"])
        self.assertEqual(db.count_feature_requests(), 0)

    def test_retention_is_set_and_purge_removes_expired(self):
        import time
        from app import db
        self.client.post("/api/kb/requests", json={"text": "想查补液规范"})
        row = db.list_feature_requests()[0]
        self.assertGreater(row["expires_at"], row["created_at"])
        self.assertEqual(db.purge_expired()["feature_requests_deleted"], 0)
        self.assertEqual(
            db.purge_expired(now=time.time() + 999 * 86400)["feature_requests_deleted"], 1)

    def test_operator_can_delete_all_requests(self):
        from app import db
        self.client.post("/api/kb/requests", json={"text": "想查补液规范"})
        r = self.client.delete("/api/privacy/feature-requests")
        self.assertEqual(r.get_json()["deleted"], 1)
        self.assertEqual(db.count_feature_requests(), 0)

    def test_event_log_keeps_no_request_text(self):
        from app import db
        self.client.post("/api/kb/requests", json={"text": "想查犬腹泻补液规范"})
        conn = db.connect()
        rows = conn.execute("SELECT payload FROM event_log").fetchall()
        conn.close()
        for row in rows:
            self.assertNotIn("腹泻", row["payload"])
            self.assertNotIn("补液", row["payload"])
