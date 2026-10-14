"""
試算模擬器(投資情境模擬)的自動化測試。

這些測試全部是「給輸入、斷言輸出」的純計算：不連資料庫、不打網路(yfinance/證交所/SEC 都用假資料
或 mock 取代)，所以幾秒內跑完，改程式前後都可以直接跑：

    python manage.py test apps.calculator

為什麼要有：這個功能的錯誤特性是「算錯了不會報錯，只是數字默默變錯」(例如本益比差 3 倍、
EPS 沒還原分割)，只靠眼睛看畫面很難發現。這裡把已經踩過的坑變成測試，之後改動不會再默默弄壞。

涵蓋：
  1. 20% 規則的多空標記(bear_labels)
  2. 市場環境的抽樣權重(build_regime_weights)：偏空 max(60%, 歷史)、偏多 min(8%, 歷史)
  3. 近四季 EPS 與不適用標的的判斷(implied_assumptions)
  4. EDGAR 季度資料解析：最早公告、Q4 推算、分割還原、不合理值、推估公告日
  5. 基本面倍數 F、隱含本益比、輸出端乘倍數
  6. 手動輸入的搜尋排序
"""
from datetime import date
from unittest import mock

import numpy as np
import pandas as pd
from django.core.cache import cache
from django.test import SimpleTestCase
from rest_framework.test import APIRequestFactory

from apps.calculator.services import market_regime
from apps.calculator.services import implied_assumptions as ia
from apps.calculator import preview_views as pv


# ───────────────────────── 共用：用關鍵點造一條固定的價格路徑 ─────────────────────────
def path(points, start="2020-01-01"):
    """points=[(天數, 價格), ...] → 每個交易日一筆、線性內插的價格序列(沒有雜訊，結果可預期)。"""
    days = np.arange(points[-1][0] + 1)
    xs, ys = zip(*points)
    idx = pd.bdate_range(start=start, periods=len(days))
    return pd.Series(np.interp(days, xs, ys), index=idx)


# 兩段空頭(高點130→78 與 125→70)，中間夾一次 10% 的回檔(不算空頭)與一次 13% 的反彈(仍在空頭裡)
TWO_BEARS = [(0, 100), (60, 130), (150, 78), (230, 112), (260, 101), (330, 125),
             (380, 94), (410, 106), (460, 70), (540, 98), (600, 118)]


class BearLabelsTests(SimpleTestCase):
    def test_labels_are_only_bull_or_bear(self):
        lab = market_regime.bear_labels(path(TWO_BEARS))
        self.assertTrue(set(lab.unique()) <= {0.0, 1.0})
        self.assertEqual(len(lab), len(path(TWO_BEARS)))

    def test_bear_segment_runs_from_peak_to_trough(self):
        lab = market_regime.bear_labels(path(TWO_BEARS)).values
        # 第一個高點(第60天)到低點(第150天)之間都是空頭，低點之後換成多頭
        self.assertTrue((lab[61:149] == 0).all())
        self.assertEqual(lab[200], 1)
        # 第二段：高點第330天到低點第460天
        self.assertTrue((lab[332:458] == 0).all())
        self.assertEqual(lab[500], 1)

    def test_pullback_under_20pct_stays_bull(self):
        lab = market_regime.bear_labels(path(TWO_BEARS)).values
        # 第230天112 → 第260天101 只回檔 約10%，整段仍是多頭
        self.assertTrue((lab[230:262] == 1).all())

    def test_bounce_under_20pct_inside_bear_stays_bear(self):
        lab = market_regime.bear_labels(path(TWO_BEARS)).values
        # 第380天94 → 第410天106 只反彈 約13%，這幾天仍算空頭日(空頭日≠下跌日)
        self.assertTrue((lab[381:411] == 0).all())

    def test_no_pivot_means_all_bull(self):
        # 只在 ±10% 內來回，沒有任何一次達到 20% → 全部多頭
        s = path([(0, 100), (50, 110), (100, 99), (150, 108), (200, 100)])
        self.assertTrue((market_regime.bear_labels(s).values == 1).all())

    def test_last_leg_is_provisional(self):
        # 站在第352天只看到回落約11%，還沒到20%：最後一段暫時標成多頭；
        # 看完整段之後，同一段會被回頭改標成空頭(事後標記的特性)
        full = path(TWO_BEARS)
        early = market_regime.bear_labels(full.iloc[:353]).values
        late = market_regime.bear_labels(full).values
        self.assertEqual(early[-1], 1)
        self.assertEqual(late[352], 0)

    def test_trough_not_declared_until_20pct_rebound(self):
        # 100 → 70(−30%，空頭) → 80.5(只反彈15%) → 60(更低) → 75(反彈25%，確認低點在60)
        s = path([(0, 100), (40, 70), (60, 80.5), (100, 60), (130, 75)])
        lab = market_regime.bear_labels(s).values
        # 第60天附近(反彈15%、還沒確認)仍是空頭；整段到第100天的新低為止都是空頭
        self.assertTrue((lab[1:100] == 0).all())
        self.assertEqual(lab[115], 1)


class BuildRegimeWeightsTests(SimpleTestCase):
    """用假的指數資料取代網路：_fetch_closes 直接回傳我們造的路徑。"""

    def setUp(self):
        # 夠長(>250天)、空頭日占比可預期的指數序列
        self.index = path(TWO_BEARS)
        self.pool = self.index.index
        bear = market_regime.bear_labels(self.index).values == 0
        self.actual = float(bear.mean())

    def _build(self, **kw):
        with mock.patch.object(market_regime, "_fetch_closes", return_value=self.index):
            return market_regime.build_regime_weights(self.pool, "2330.TW", date(2022, 6, 1), **kw)

    def test_benchmark_by_market(self):
        self.assertEqual(market_regime.benchmark_for("2330.TW"), "^TWII")
        self.assertEqual(market_regime.benchmark_for("5274.TWO"), "^TWII")
        self.assertEqual(market_regime.benchmark_for("NVDA"), "^GSPC")
        self.assertEqual(market_regime.benchmark_for("005930.KS"), "^GSPC")

    def test_no_preset_means_no_reweighting(self):
        w, info = self._build()
        self.assertIsNone(w)
        self.assertFalse(info["applied"])

    def test_weights_sum_to_one_and_hit_target_share(self):
        w, info = self._build(bear_share=0.5)
        self.assertAlmostEqual(float(w.sum()), 1.0, places=9)
        bear = market_regime.bear_labels(self.index).values == 0
        self.assertAlmostEqual(float(w[bear].sum()), 0.5, places=6)
        self.assertTrue(info["applied"])

    def test_bear_preset_is_at_least_60pct_and_at_least_actual(self):
        _, info = self._build(preset="bear")
        self.assertAlmostEqual(info["applied_bear_share"], max(0.60, self.actual), places=6)

    def test_bull_preset_is_at_most_8pct_and_at_most_actual(self):
        _, info = self._build(preset="bull")
        self.assertAlmostEqual(info["applied_bear_share"], max(min(0.08, self.actual), market_regime.BEAR_SHARE_MIN), places=6)

    def test_bear_preset_never_less_bearish_than_history(self):
        # 歷史空頭日本來就 70%：選偏空不能變成 60%(方向會反過來)
        high = path([(0, 100), (30, 130), (300, 60), (400, 70), (440, 120)])
        with mock.patch.object(market_regime, "_fetch_closes", return_value=high):
            _, info = market_regime.build_regime_weights(high.index, "2330.TW", None, preset="bear")
        self.assertGreaterEqual(info["applied_bear_share"], info["actual_bear_share"] - 1e-9)

    def test_custom_share_is_clamped(self):
        _, hi = self._build(bear_share=1.5)
        _, lo = self._build(bear_share=0.0)
        self.assertAlmostEqual(hi["applied_bear_share"], market_regime.BEAR_SHARE_MAX)
        self.assertAlmostEqual(lo["applied_bear_share"], market_regime.BEAR_SHARE_MIN)

    def test_too_few_days_in_one_regime_falls_back(self):
        flat = path([(0, 100), (400, 105)])            # 完全沒有空頭日
        with mock.patch.object(market_regime, "_fetch_closes", return_value=flat):
            w, info = market_regime.build_regime_weights(flat.index, "NVDA", None, preset="bear")
        self.assertIsNone(w)
        self.assertFalse(info["applied"])
        self.assertTrue(info["warning"])


class TtmEpsTests(SimpleTestCase):
    def rows(self, ends, eps=1.0, lag=45):
        out = []
        for e in ends:
            out.append({"fiscal_period_end": e, "disclosure_date": date.fromordinal(e.toordinal() + lag), "eps": eps})
        return out

    QUARTERS = [date(2024, 3, 31), date(2024, 6, 30), date(2024, 9, 30), date(2024, 12, 31)]

    def test_sum_of_four_consecutive_disclosed_quarters(self):
        self.assertEqual(ia._ttm_eps(self.rows(self.QUARTERS, eps=2.5), date(2025, 6, 1)), 10.0)

    def test_only_uses_disclosed_quarters(self):
        # 2025-01-01 時，最後一季(2024-12-31，+45天才公告)還沒公告 → 只有三季 → None
        self.assertIsNone(ia._ttm_eps(self.rows(self.QUARTERS), date(2025, 1, 1)))

    def test_missing_quarter_returns_none(self):
        ends = [date(2024, 3, 31), date(2024, 9, 30), date(2024, 12, 31), date(2025, 3, 31)]   # 缺 2024Q2
        self.assertIsNone(ia._ttm_eps(self.rows(ends), date(2025, 9, 1)))

    def test_16_week_quarter_is_accepted(self):
        # 52/53週制公司(Costco)的Q4約112天：不能被當成缺季
        ends = [date(2024, 2, 18), date(2024, 5, 12), date(2024, 8, 25), date(2024, 12, 15)]
        self.assertEqual(ia._ttm_eps(self.rows(ends, eps=1.0), date(2025, 3, 1)), 4.0)

    def test_non_equity_reasons(self):
        for t in ["^TWII", "GC=F", "BTC-USD", "0050.TW", "00878.TW"]:
            self.assertIsNotNone(ia._non_equity_reason(t), t)
        for t in ["2330.TW", "NVDA", "2317.TW", "BRK-B"]:
            self.assertIsNone(ia._non_equity_reason(t), t)


class EdgarQuarterlyTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from apps.summaries.management.commands import backfill_financial_reports_edgar as e
        cls.e = e

    @staticmethod
    def fact(s, e, v, filed, form):
        return {"start": s, "end": e, "val": v, "filed": filed, "form": form}

    def gaap(self, facts, concept="EarningsPerShareDiluted"):
        return {concept: {"units": {"USD/shares": facts}}}

    def year_2020(self, fy_val=3.0, fy_filed="2021-02-15"):
        return [
            self.fact("2020-01-01", "2020-03-31", 0.5, "2020-05-01", "10-Q"),
            self.fact("2020-04-01", "2020-06-30", 0.6, "2020-08-01", "10-Q"),
            self.fact("2020-07-01", "2020-09-30", 0.7, "2020-11-01", "10-Q"),
            self.fact("2020-01-01", "2020-12-31", fy_val, fy_filed, "10-K"),
        ]

    def test_q4_is_full_year_minus_first_three_quarters(self):
        q = self.e.quarterly_values(self.gaap(self.year_2020()), self.e.EPS_CONCEPTS, "USD/shares")
        self.assertAlmostEqual(q["2020-12-31"][0], 3.0 - (0.5 + 0.6 + 0.7))
        self.assertEqual(q["2020-12-31"][1], "2021-02-15")

    def test_earliest_filing_wins_over_later_recast(self):
        facts = self.year_2020() + [self.fact("2020-01-01", "2020-12-31", 3.6, "2022-02-20", "10-K")]
        q = self.e.quarterly_values(self.gaap(facts), self.e.EPS_CONCEPTS, "USD/shares")
        self.assertAlmostEqual(q["2020-12-31"][0], 1.2)           # 用原始 3.0，不是重列後的 3.6
        self.assertEqual(q["2020-12-31"][1], "2021-02-15")

    def test_split_adjustment_only_for_facts_filed_before_split(self):
        ratios = [(date(2020, 6, 1), 2.0)]                         # 2020-06-01 一拆二
        q = self.e.quarterly_values(self.gaap(self.year_2020()), self.e.EPS_CONCEPTS, "USD/shares", ratios)
        self.assertAlmostEqual(q["2020-03-31"][0], 0.25)           # 5月公告 → 分割前 → ÷2
        self.assertAlmostEqual(q["2020-06-30"][0], 0.6)            # 8月公告 → 分割後 → 不動

    def test_absurd_eps_is_dropped(self):
        facts = self.year_2020() + [self.fact("2019-01-01", "2019-03-31", 900000.0, "2019-05-01", "10-Q")]
        q = self.e.quarterly_values(self.gaap(facts), self.e.EPS_CONCEPTS, "USD/shares")
        self.assertNotIn("2019-03-31", q)

    def test_late_first_appearance_uses_estimated_disclosure(self):
        # 這一期(FY2021-01-29 的Q4)只在兩年後的年報才第一次出現：公告日改用「期末+90天」的推估
        facts = [
            self.fact("2020-02-01", "2020-05-01", 1.0, "2020-06-05", "10-Q"),
            self.fact("2020-05-02", "2020-07-31", 1.1, "2020-09-04", "10-Q"),
            self.fact("2020-08-01", "2020-10-30", 1.2, "2020-12-03", "10-Q"),
            self.fact("2020-02-01", "2021-01-29", 4.5, "2022-03-24", "10-K"),
        ]
        q = self.e.quarterly_values(self.gaap(facts), self.e.EPS_CONCEPTS, "USD/shares")
        self.assertAlmostEqual(q["2021-01-29"][0], 4.5 - 3.3)
        self.assertEqual(q["2021-01-29"][1], "2021-04-29")

    def test_non_quarter_durations_are_ignored(self):
        facts = self.year_2020() + [self.fact("2020-01-01", "2020-06-30", 1.1, "2020-08-01", "10-Q")]   # 半年累計
        q = self.e.quarterly_values(self.gaap(facts), self.e.EPS_CONCEPTS, "USD/shares")
        self.assertAlmostEqual(q["2020-06-30"][0], 0.6)            # 仍是單季值，不是累計的 1.1


class FundamentalFactorTests(SimpleTestCase):
    BASE = dict(years=1.0, g0=0.30, rho0=0.05, eps_growth=None, valuation=None, pe_target=None,
                pe_now=28.0, pe_pace=0.02, pe_implied=30.0)

    def fx(self, **kw):
        return pv.compute_fundamental_factors(**{**self.BASE, **kw})

    def test_untouched_is_exactly_one(self):
        r = self.fx(pe_target=30.0)                                # 本益比設成預設值、EPS 不動
        self.assertAlmostEqual(r["factor"], 1.0, places=12)
        self.assertAlmostEqual(self.fx()["factor"], 1.0 * self.fx()["f_val"], places=12)

    def test_eps_growth_factor(self):
        r = self.fx(eps_growth=0.20)
        self.assertAlmostEqual(r["f_growth"], 1.20 / 1.30, places=12)
        self.assertEqual(r["g1"], 0.20)

    def test_factors_multiply_not_add(self):
        r = self.fx(eps_growth=0.20, pe_target=33.0)
        self.assertAlmostEqual(r["factor"], r["f_growth"] * r["f_val"], places=12)
        self.assertAlmostEqual(r["f_val"], 33.0 / 30.0, places=12)

    def test_growth_scales_with_horizon(self):
        r = self.fx(years=3.0, eps_growth=0.20)
        self.assertAlmostEqual(r["f_growth"], (1.20 / 1.30) ** 3, places=12)

    def test_pe_target_is_clamped_to_quarter_to_four_times_now(self):
        self.assertAlmostEqual(self.fx(pe_target=1000.0)["pe_used"], 28.0 * 4)
        self.assertAlmostEqual(self.fx(pe_target=0.1)["pe_used"], 28.0 * 0.25)

    def test_legacy_valuation_path_without_pe_target(self):
        r = self.fx(valuation=0.10)
        self.assertAlmostEqual(r["f_val"], (1.10 / 1.05) ** 1.0, places=12)

    def test_final_price_equals_end_eps_times_target_pe(self):
        """核心恆等式：調整後，基準情境的終點本益比必須剛好等於使用者設的值(終點股價＝終點EPS×本益比)。"""
        years, div_log, g0 = 1.5, 0.018, 0.30
        pe_now, base_ret = 28.0, 0.25
        pe_implied = pv.implied_end_pe(pe_now, base_ret, years, div_log, g0)
        for g1, target in [(0.10, 20.0), (0.45, 45.0), (g0, pe_implied)]:
            r = pv.compute_fundamental_factors(years=years, g0=g0, rho0=0.0, eps_growth=g1, valuation=None,
                                               pe_target=target, pe_now=pe_now, pe_pace=0.0, pe_implied=pe_implied)
            adj_ret = (1 + base_ret) * r["factor"] - 1
            self.assertAlmostEqual(pv.implied_end_pe(pe_now, adj_ret, years, div_log, g1), r["pe_used"], places=9)

    def test_implied_end_pe_removes_dividend_and_eps_growth(self):
        # 現在28倍、報酬+30%、EPS年增30%、無股息、1年 → 倍數不變
        self.assertAlmostEqual(pv.implied_end_pe(28.0, 0.30, 1.0, 0.0, 0.30), 28.0, places=12)
        # 有股息貢獻時，倍數要再除掉
        self.assertLess(pv.implied_end_pe(28.0, 0.30, 1.0, 0.02, 0.30), 28.0)


class ApplyFactorTests(SimpleTestCase):
    def payload(self):
        return {
            "median_path": [100.0, 110.0, 120.0], "band_low": [100.0, 90.0, 80.0], "band_high": [100.0, 130.0, 160.0],
            "conservative_path": [100.0, 105.0, 110.0], "aggressive_path": [100.0, 115.0, 130.0],
            "sample_paths": [[100.0, 120.0, 140.0]],
            "median_return": 0.20, "p10_return": -0.20, "p90_return": 0.60,
            "conservative_return": 0.10, "aggressive_return": 0.30,
        }

    def test_factor_one_changes_nothing(self):
        p = self.payload()
        before = {k: (list(v) if isinstance(v, list) else v) for k, v in p.items()}
        pv.apply_fundamental_factor(p, 1.0)
        for k, v in before.items():
            if isinstance(v, float):
                self.assertAlmostEqual(p[k], v, places=12, msg=k)      # (1+r)×1−1 在浮點數會有 1e-16 的誤差
            elif k == "sample_paths":
                for a, b in zip(p[k], v):
                    for x, y in zip(a, b):
                        self.assertAlmostEqual(x, y, places=9)
            else:
                for x, y in zip(p[k], v):
                    self.assertAlmostEqual(x, y, places=9, msg=k)

    def test_path_is_scaled_progressively_and_start_unchanged(self):
        p = self.payload()
        pv.apply_fundamental_factor(p, 0.64)
        self.assertAlmostEqual(p["median_path"][0], 100.0)               # 起點不動
        self.assertAlmostEqual(p["median_path"][1], 110.0 * 0.64 ** 0.5)  # 中間乘 F^(t/N)
        self.assertAlmostEqual(p["median_path"][2], 120.0 * 0.64)         # 終點乘 F

    def test_returns_are_scaled_as_price_ratios(self):
        p = self.payload()
        pv.apply_fundamental_factor(p, 0.8)
        self.assertAlmostEqual(p["median_return"], 1.20 * 0.8 - 1)
        self.assertAlmostEqual(p["p10_return"], 0.80 * 0.8 - 1)

    def test_quantile_order_is_preserved(self):
        p = self.payload()
        pv.apply_fundamental_factor(p, 1.7)
        self.assertLess(p["p10_return"], p["conservative_return"])
        self.assertLess(p["conservative_return"], p["median_return"])
        self.assertLess(p["median_return"], p["aggressive_return"])
        self.assertLess(p["aggressive_return"], p["p90_return"])


class ParsingTests(SimpleTestCase):
    def test_parse_fraction(self):
        self.assertIsNone(pv._parse_fraction(""))
        self.assertIsNone(pv._parse_fraction("   "))
        self.assertAlmostEqual(pv._parse_fraction("0.2"), 0.2)
        self.assertEqual(pv._parse_fraction("10"), pv.FUNDAMENTAL_MAX)
        self.assertEqual(pv._parse_fraction("-3"), pv.FUNDAMENTAL_MIN)
        with self.assertRaises(ValueError):
            pv._parse_fraction("abc")

    def test_public_implied_unsupported(self):
        self.assertEqual(pv.public_implied({"supported": False, "reason": "ETF"}), {"supported": False, "reason": "ETF"})

    def test_public_implied_supported_maps_fields(self):
        raw = {"supported": True, "g0_simple": 0.3, "rho0": 0.0, "mu": 0.2, "eps_start": 1.0, "eps_end": 2.0,
               "window_start": date(2021, 1, 1), "window_end": date(2026, 1, 1), "years_used": 5.0,
               "pe_start": 20.0, "pe_end": 25.0, "pe_pace": 0.04, "div_log": 0.01}
        out = pv.public_implied(raw)
        self.assertTrue(out["supported"])
        self.assertAlmostEqual(out["g0"], 0.3)
        self.assertEqual(out["pe_now"], 25.0)                           # 現在本益比＝視窗終點
        self.assertEqual(out["window_end"], "2026-01-01")


class StockSearchTests(SimpleTestCase):
    ROWS = [
        {"ticker": "2330.TW", "asset_name": "台積電", "zh_name": "台積電", "exchange": "TWSE"},
        {"ticker": "TSM", "asset_name": "台積電ADR", "zh_name": "台積電ADR", "exchange": "NYSE"},
        {"ticker": "NVDA", "asset_name": "輝達", "zh_name": "輝達", "exchange": "NASDAQ"},
        {"ticker": "AMD", "asset_name": "超微", "zh_name": "超微", "exchange": "NASDAQ"},
    ]
    TWSE = {"2330": "台積電", "2317": "鴻海", "1503": "士電", "2303": "聯電", "0050": "元大台灣50"}

    def setUp(self):
        cache.set("ticker-map-rows:v1", self.ROWS, 60)
        patcher = mock.patch("apps.calculator.views._get_tw_stock_map", return_value=self.TWSE)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.factory = APIRequestFactory()

    def search(self, q):
        req = self.factory.get("/x", {"q": q})
        resp = pv.StockSearchPreviewAPIView.as_view()(req)
        return resp.data["results"]

    def test_empty_query(self):
        self.assertEqual(self.search(""), [])

    def test_code_exact_match_first(self):
        r = self.search("2330")
        self.assertEqual(r[0]["ticker"], "2330.TW")
        self.assertTrue(r[0]["exact"])

    def test_chinese_name_finds_both_listings(self):
        tickers = [x["ticker"] for x in self.search("台積")]
        self.assertEqual(tickers[:2], ["2330.TW", "TSM"])

    def test_exact_name_is_marked_exact(self):
        r = self.search("輝達")
        self.assertEqual(r[0]["ticker"], "NVDA")
        self.assertTrue(r[0]["exact"])

    def test_ticker_is_case_insensitive(self):
        self.assertEqual(self.search("nvda")[0]["ticker"], "NVDA")

    def test_name_only_in_twse_list_is_found(self):
        self.assertEqual(self.search("鴻海")[0]["ticker"], "2317.TW")

    def test_unknown_us_ticker_falls_back_to_itself(self):
        r = self.search("PLTR")
        self.assertEqual(r[0]["ticker"], "PLTR")
        self.assertFalse(r[0]["exact"])

    def test_digits_only_fallback_gets_tw_suffix(self):
        self.assertEqual(self.search("9999")[0]["ticker"], "9999.TW")

    def test_ambiguous_query_returns_several_not_exact(self):
        r = self.search("電")
        self.assertGreaterEqual(len(r), 2)
        self.assertFalse(any(x["exact"] for x in r))

    def test_market_labels(self):
        labels = {x["ticker"]: x["market"] for x in self.search("台積")}
        self.assertEqual(labels["2330.TW"], "台股")
        self.assertEqual(labels["TSM"], "美股")
