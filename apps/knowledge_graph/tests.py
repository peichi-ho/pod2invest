from datetime import date, timedelta
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase
from django.utils import timezone

from apps.knowledge_graph import views
from apps.knowledge_graph.services import asset_kg_score
from apps.knowledge_graph.services.asset_kg_score import compute_asset_scores, score_from_evidence
from apps.knowledge_graph.services.financial_data import (
    _expects_newer_quarter,
    _needs_refresh,
    _next_period_end,
)
from apps.knowledge_graph.services.graph_window import build_weighted_graph
from apps.knowledge_graph.services.ppr_utils import run_ppr


class NextPeriodEndTests(SimpleTestCase):
    def test_moves_three_months_to_month_end(self):
        self.assertEqual(_next_period_end(date(2026, 4, 30)), date(2026, 7, 31))
        self.assertEqual(_next_period_end(date(2026, 3, 31)), date(2026, 6, 30))

    def test_crosses_year_boundary(self):
        self.assertEqual(_next_period_end(date(2026, 11, 30)), date(2027, 2, 28))
        self.assertEqual(_next_period_end(date(2026, 12, 31)), date(2027, 3, 31))


class ExpectsNewerQuarterTests(SimpleTestCase):
    """依法定公告期限推算「下一季到截止日為止應該已經公布」。"""

    def test_stale_calendar_quarter(self):
        # 帆宣：快取停在 3/31，6/30 那季 8/14 前就該公布
        self.assertTrue(_expects_newer_quarter(date(2026, 3, 31), date(2026, 10, 28)))

    def test_up_to_date_calendar_quarter(self):
        # 台積電／達興材料：已經有 6/30，下一季（9/30）要到 11/14 才算該公布
        self.assertFalse(_expects_newer_quarter(date(2026, 6, 30), date(2026, 10, 28)))

    def test_non_calendar_fiscal_quarters_follow_their_own_cadence(self):
        # 輝達季末是 1/4/7/10 月底：停在 4/30 該補，已有 7/31 就不該補（日曆季會誤判）
        self.assertTrue(_expects_newer_quarter(date(2026, 4, 30), date(2026, 10, 28)))
        self.assertFalse(_expects_newer_quarter(date(2026, 7, 31), date(2026, 10, 28)))

    def test_deadline_day_is_inclusive(self):
        self.assertFalse(_expects_newer_quarter(date(2026, 6, 30), date(2026, 11, 13)))
        self.assertTrue(_expects_newer_quarter(date(2026, 6, 30), date(2026, 11, 14)))

    def test_annual_report_uses_longer_lag(self):
        # 下一期是 12/31（年報，約 90 天）：3/31 才算該公布
        self.assertFalse(_expects_newer_quarter(date(2025, 9, 30), date(2026, 3, 30)))
        self.assertTrue(_expects_newer_quarter(date(2025, 9, 30), date(2026, 3, 31)))


def _period(period_end, disclosure, fetched_ago_hours=None):
    return {
        "fiscal_period_end": period_end,
        "disclosure_date": disclosure,
        "fetched_at": timezone.now() - timedelta(hours=fetched_ago_hours) if fetched_ago_hours is not None else None,
    }


class NeedsRefreshTests(SimpleTestCase):
    AS_OF = date(2026, 10, 28)

    def test_stale_and_not_recently_tried(self):
        cached = [_period(date(2026, 3, 31), date(2026, 5, 7), fetched_ago_hours=24 * 50)]
        self.assertTrue(_needs_refresh(cached, cached, self.AS_OF))

    def test_recently_tried_is_throttled(self):
        # 一小時前才試過（不管有沒有抓到新資料），先不要再打外部 API
        cached = [_period(date(2026, 3, 31), date(2026, 5, 7), fetched_ago_hours=1)]
        self.assertFalse(_needs_refresh(cached, cached, self.AS_OF))

    def test_up_to_date_cache_is_left_alone(self):
        cached = [_period(date(2026, 6, 30), date(2026, 8, 14), fetched_ago_hours=24 * 50)]
        self.assertFalse(_needs_refresh(cached, cached, self.AS_OF))

    def test_newer_period_already_cached_but_not_yet_disclosed(self):
        # 查過去的截止日：6/30 那季快取裡有，只是公告日還沒到截止日，重抓不會多出東西
        newer = _period(date(2026, 6, 30), date(2026, 8, 14), fetched_ago_hours=24 * 50)
        older = _period(date(2026, 3, 31), date(2026, 5, 7), fetched_ago_hours=24 * 50)
        self.assertFalse(_needs_refresh([newer, older], [older], date(2026, 8, 1)))


def _link(source, target, episode):
    return {
        "source": source, "target": target, "relation_type": "Supply",
        "reason": f"{source}->{target}", "summary_date": date(2026, 10, 1),
        "podcast_source": episode,
    }


def _path_to(links, target, seed="S"):
    G, meta = build_weighted_graph(links, directed=True)
    return next(c for c in run_ppr(G, seed, edge_meta=meta) if c["node"] == target)["path"]


class PathPicksMostEpisodesTests(SimpleTestCase):
    """步數一樣短的路徑有好幾條時，顯示累計集數最多的那條。"""

    def test_prefers_route_with_more_distinct_episodes(self):
        # 名稱排序會先選 A，但 B 那條出自兩集、A 那條只有一集
        links = [
            _link("S", "A", "show - ep1"), _link("A", "T", "show - ep1"),
            _link("S", "B", "show - ep2"), _link("B", "T", "show - ep3"),
        ]
        self.assertEqual(_path_to(links, "T"), ["S", "B", "T"])

    def test_same_episode_on_two_segments_counts_once(self):
        # A 那條兩段都出自 ep1（算 1 集、2 次）；B 那條 ep2、ep3（算 2 集）
        links = [
            _link("S", "A", "show - ep1"), _link("S", "A", "show - ep1"),
            _link("A", "T", "show - ep1"),
            _link("S", "B", "show - ep2"), _link("B", "T", "show - ep3"),
        ]
        self.assertEqual(_path_to(links, "T"), ["S", "B", "T"])

    def test_episode_tie_falls_back_to_mention_count(self):
        links = [
            _link("S", "A", "show - ep1"), _link("A", "T", "show - ep2"),
            _link("S", "B", "show - ep1"), _link("B", "T", "show - ep2"),
            _link("B", "T", "show - ep2"),
        ]
        self.assertEqual(_path_to(links, "T"), ["S", "B", "T"])

    def test_full_tie_is_deterministic_by_name(self):
        links = [
            _link("S", "A", "show - ep1"), _link("A", "T", "show - ep2"),
            _link("S", "B", "show - ep1"), _link("B", "T", "show - ep2"),
        ]
        self.assertEqual(_path_to(links, "T"), ["S", "A", "T"])

    def test_never_trades_a_shorter_path_for_more_episodes(self):
        links = [
            _link("S", "A", "show - ep1"), _link("A", "T", "show - ep1"),
            _link("S", "C", "show - ep2"), _link("C", "D", "show - ep3"), _link("D", "T", "show - ep4"),
        ]
        self.assertEqual(_path_to(links, "T"), ["S", "A", "T"])


def _score_link(lid, source, target, relation_type="Supply", reason="r", day=14, src="節目A - EP1"):
    return {
        "id": lid, "source": source, "target": target, "relation_type": relation_type,
        "reason": reason, "summary_date": date(2026, 10, day), "podcast_source": src,
    }


class ScoreFromEvidenceTests(SimpleTestCase):
    def test_no_evidence_has_no_score(self):
        self.assertEqual(score_from_evidence([])["score"], None)
        self.assertEqual(score_from_evidence([])["label"], "無資料")

    def test_one_strong_supply_bullish(self):
        # P = 1，score = 50 + 50 × 1/3 ≈ 67
        r = score_from_evidence([{"relation_type": "Supply", "impact": "positive", "strength": "strong"}])
        self.assertEqual(r["score"], 67)
        self.assertEqual(r["label"], "偏多")

    def test_substitution_bearish_weighs_more(self):
        # N = 20/15 = 1.33，score = 50 - 50 × 1.33/3.33 = 30
        r = score_from_evidence([{"relation_type": "Substitution", "impact": "negative", "strength": "strong"}])
        self.assertEqual(r["score"], 30)
        self.assertEqual(r["label"], "偏空")

    def test_weak_and_neutral_and_unjudged(self):
        r = score_from_evidence([
            {"relation_type": "Supply", "impact": "positive", "strength": "weak"},  # 0.5
            {"relation_type": "Supply", "impact": "neutral", "strength": "strong"},
            {"relation_type": "Supply", "impact": None, "strength": None},
        ])
        self.assertEqual(r["score"], 60)  # 50 + 50 × 0.5/2.5
        self.assertEqual(r["positive"], 0.5)

    def test_score_stays_within_bounds(self):
        many = [{"relation_type": "Substitution", "impact": "positive", "strength": "strong"}] * 200
        self.assertLessEqual(score_from_evidence(many)["score"], 100)


class ComputeAssetScoresTests(SimpleTestCase):
    def test_judges_each_endpoint_and_aggregates(self):
        links = [
            _score_link(1, "ASML", "台積電", reason="ASML 供應台積電曝光機"),
            _score_link(2, "ASML", "三星", reason="ASML 供應三星曝光機", src="節目B - EP2"),
            _score_link(3, "帆宣", "台積電", reason="帆宣幫台積電設廠"),
        ]

        def fake_judge(items):
            out = []
            for key, desc in items:
                self.assertTrue(desc.startswith(f"[{key[2]}] "))
                out.append({"impact": "positive", "strength": "strong", "note": ""})
            return out

        with patch.object(asset_kg_score, "fetch_asset_links", return_value=links), \
                patch.object(asset_kg_score, "_judge_company_impact", side_effect=fake_judge) as judge:
            r = compute_asset_scores(["ASML", "帆宣", "沒提到"], date(2026, 10, 28), 14)

        # 台積電、三星不在要算分的 nodes 裡，只判斷 ASML×2、帆宣×1
        self.assertEqual(sum(len(c.args[0]) for c in judge.call_args_list), 3)
        self.assertEqual(r["ASML"]["link_count"], 2)
        self.assertEqual(r["ASML"]["episode_count"], 2)
        self.assertEqual(r["ASML"]["score"], 75)  # P = 2，50 + 50 × 2/4
        self.assertEqual(len(r["ASML"]["bullish_reasons"]), 2)
        self.assertEqual(r["帆宣"]["score"], 67)
        self.assertEqual(r["沒提到"]["score"], None)

    def test_failed_judgment_is_not_scored(self):
        with patch.object(asset_kg_score, "fetch_asset_links", return_value=[_score_link(1, "ASML", "台積電")]), \
                patch.object(asset_kg_score, "_judge_company_impact", side_effect=lambda items: [None] * len(items)):
            r = compute_asset_scores(["ASML"], date(2026, 10, 28), 14)
        self.assertEqual(r["ASML"]["unjudged_count"], 1)
        self.assertEqual(r["ASML"]["score"], 50)
        self.assertEqual(r["ASML"]["label"], "中性")

    def test_batches_large_requests(self):
        links = [_score_link(i, "ASML", f"客戶{i}", reason=f"r{i}") for i in range(95)]
        with patch.object(asset_kg_score, "fetch_asset_links", return_value=links), \
                patch.object(asset_kg_score, "_judge_company_impact",
                             side_effect=lambda items: [{"impact": "neutral", "strength": "weak"}] * len(items)) as judge:
            compute_asset_scores(["ASML"], date(2026, 10, 28), 30)
        self.assertEqual(sorted(len(c.args[0]) for c in judge.call_args_list), [15, 40, 40])


class AssetScoresApiTests(SimpleTestCase):
    def _get(self, query):
        return views.asset_scores_api(RequestFactory().get("/api/knowledge-graph/asset-scores/", query))

    def test_requires_node(self):
        self.assertEqual(self._get({}).status_code, 400)

    def test_rejects_bad_params(self):
        self.assertEqual(self._get({"node": "ASML", "end_date": "2026/10/28"}).status_code, 400)
        self.assertEqual(self._get({"node": "ASML", "window_days": "abc"}).status_code, 400)
        self.assertEqual(self._get({"node": [f"n{i}" for i in range(21)]}).status_code, 400)

    def test_passes_params_through(self):
        with patch("apps.knowledge_graph.services.asset_kg_score.compute_asset_scores",
                   return_value={"ASML": {"score": 67}}) as compute:
            res = self._get({"node": ["ASML", "帆宣", "ASML"], "end_date": "2026-10-28", "window_days": "14"})
        self.assertEqual(res.status_code, 200)
        compute.assert_called_once_with(["ASML", "帆宣"], date(2026, 10, 28), 14)
