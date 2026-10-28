from datetime import date, timedelta

from django.test import SimpleTestCase
from django.utils import timezone

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
