import json
from unittest import mock

from django.test import SimpleTestCase, override_settings

from .services import ai_assistant as svc
from .services.ai_assistant import FinanceTool, Orchestrator, PodcastRetrieverTool, PodcastSearchUnavailable


# Podcast 檢索「壞掉」（向量模型載不進來、資料庫連不上）跟「查了但沒有相關內容」要分得開：
# 前者回答結尾要固定補一句提醒，不能讓助理在完全沒有資料的情況下照常回答、看起來只是變笨。
# 這裡全部用假的 LLM／檢索函式，不碰資料庫、不呼叫 Gemini。

_PLAN = json.dumps({
    "use_tools": True,
    "out_of_scope": False,
    "fallback_to_finance_llm": False,
    "actions": [{"tool": "search_podcast_transcript", "args_json": json.dumps({"query": "台積電展望"})}],
})
_FINAL = "台積電的看法如下。\n<SOURCES></SOURCES>\n<FOLLOWUP>\n1. 問題一\n2. 問題二\n3. 問題三\n</FOLLOWUP>"


def _agent(search_fn, seen_prompts=None):
    def planner(system, user_input, history=None):
        return _PLAN

    def final(system, user, history=None):
        if seen_prompts is not None:
            seen_prompts.append(user)
        return _FINAL

    return Orchestrator(
        tools=[PodcastRetrieverTool(search_fn=search_fn)],
        planner_llm=planner, finance_qa_llm=final, final_llm=final,
    )


def _failing_search(**kwargs):
    raise PodcastSearchUnavailable(reason="RuntimeError: boom")


class RetrievalFailureNoticeTests(SimpleTestCase):
    def test_failure_appends_notice_and_keeps_follow_ups(self):
        result = _agent(_failing_search).answer("台積電展望", [])
        self.assertIn("Podcast 知識庫目前無法檢索", result["answer"])
        self.assertEqual(result["follow_ups"], ["問題一", "問題二", "問題三"])

    def test_technical_detail_is_not_sent_to_llm(self):
        seen = []
        _agent(_failing_search, seen).answer("台積電展望", [])
        self.assertTrue(seen)
        self.assertNotIn("boom", seen[0])              # 原始錯誤不給 LLM，免得它照念給使用者
        self.assertIn("Podcast 知識庫目前無法檢索", seen[0])  # 但要讓它知道這次檢索失敗

    @override_settings(DEBUG=False)
    def test_reason_hidden_outside_debug(self):
        result = _agent(_failing_search).answer("台積電展望", [])
        self.assertNotIn("boom", result["answer"])

    @override_settings(DEBUG=True)
    def test_reason_shown_in_debug(self):
        result = _agent(_failing_search).answer("台積電展望", [])
        self.assertIn("RuntimeError: boom", result["answer"])

    def test_empty_result_is_not_a_failure(self):
        result = _agent(lambda **kwargs: []).answer("台積電展望", [])
        self.assertNotIn("無法檢索", result["answer"])

    def test_any_podcast_tool_exception_triggers_the_notice(self):
        # 不管是哪種例外，只要 Podcast 檢索工具失敗，這次回答就沒有節目內容可參考
        def unexpected(**kwargs):
            raise ValueError("bad args")
        result = _agent(unexpected).answer("台積電展望", [])
        self.assertIn("Podcast 知識庫目前無法檢索", result["answer"])

    def test_stock_tool_failure_does_not_trigger_the_podcast_notice(self):
        plan = json.dumps({
            "use_tools": True, "out_of_scope": False, "fallback_to_finance_llm": False,
            "actions": [{"tool": "get_realtime_stock_data", "args_json": json.dumps({"ticker": "TSM"})}],
        })

        def broken_fetch(ticker):
            raise RuntimeError("yfinance down")

        agent = Orchestrator(
            tools=[FinanceTool(fetch_fn=broken_fetch)],
            planner_llm=lambda system, user_input, history=None: plan,
            finance_qa_llm=lambda system, user, history=None: _FINAL,
            final_llm=lambda system, user, history=None: _FINAL,
        )
        result = agent.answer("TSM 股價", [])
        self.assertNotIn("無法檢索", result["answer"])


class SearchRaisesOnInfrastructureFailureTests(SimpleTestCase):
    def test_embedding_failure_raises_instead_of_returning_empty(self):
        with mock.patch.object(svc, "_embed_query", side_effect=RuntimeError("model not loadable")):
            with self.assertRaises(PodcastSearchUnavailable) as ctx:
                svc.search_podcast_transcript("台積電展望", question_type="A", use_multi_query=False)
        self.assertIn("model not loadable", ctx.exception.reason)
        self.assertIsInstance(ctx.exception.__cause__, RuntimeError)
