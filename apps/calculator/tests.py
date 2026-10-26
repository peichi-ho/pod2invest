from datetime import date, timedelta

from django.test import SimpleTestCase

from apps.calculator.services.thesis_valuation import (
    apply_scenarios, build_baseline, default_levers, project, project_scenarios, ttm_series, waterfall,
)


def quarter_rows(n=12, revenue=100.0, growth=0.03, gross=0.5, opex=0.2, eps=1.0, net_ratio=0.8,
                 last_end=date(2026, 6, 30), with_gross=True):
    """造 n 季連續財報(由舊到新，每季營收成長 growth)，公告日是期末後 45 天。"""
    rows = []
    for i in range(n):
        end = last_end - timedelta(days=91 * (n - 1 - i))
        rev = revenue * (1 + growth) ** i
        op = rev * (gross - opex)
        rows.append({
            "fiscal_period_end": end, "disclosure_date": end + timedelta(days=45),
            "revenue": rev, "gross_profit": rev * gross if with_gross else None,
            "operating_income": op, "net_income": op * net_ratio, "eps": eps * (1 + growth) ** i,
        })
    return rows


AS_OF = date(2026, 9, 1)


class TtmSeriesTests(SimpleTestCase):
    def test_sums_last_four_disclosed_quarters(self):
        rows = quarter_rows(n=8)
        series = ttm_series(rows, AS_OF)
        self.assertAlmostEqual(series[0]["revenue"], sum(r["revenue"] for r in rows[-4:]))
        self.assertEqual(series[0]["end"], rows[-1]["fiscal_period_end"])

    def test_ignores_quarters_not_yet_disclosed(self):
        rows = quarter_rows(n=8)
        # 最後一季 8/14 才公告，8/1 時近四季應該停在前一季
        series = ttm_series(rows, date(2026, 8, 1))
        self.assertEqual(series[0]["end"], rows[-2]["fiscal_period_end"])

    def test_gap_in_quarters_breaks_window(self):
        rows = quarter_rows(n=6)
        del rows[3]
        series = ttm_series(rows, AS_OF)
        self.assertTrue(all(s["end"] != rows[-1]["fiscal_period_end"] or s["revenue"] for s in series))
        self.assertLess(len(series), 3)

    def test_missing_field_in_window_is_none_not_partial_sum(self):
        rows = quarter_rows(n=4)
        rows[1]["gross_profit"] = None
        self.assertIsNone(ttm_series(rows, AS_OF)[0]["gross_profit"])


class BaselineRouteTests(SimpleTestCase):
    def test_full_route_when_gross_profit_available(self):
        b = build_baseline("TEST", quarter_rows(), price=40.0, as_of=AS_OF)
        self.assertEqual(b.route, "full")
        self.assertAlmostEqual(b.gross_margin, 0.5)
        self.assertAlmostEqual(b.opex_ratio, 0.2)
        self.assertAlmostEqual(b.pe, 40.0 / b.eps)

    def test_no_gross_route_without_gross_profit(self):
        b = build_baseline("TEST", quarter_rows(with_gross=False), price=40.0, as_of=AS_OF)
        self.assertEqual(b.route, "no_gross")
        self.assertIsNone(b.gross_margin)

    def test_loss_making_company_uses_price_to_sales(self):
        b = build_baseline("TEST", quarter_rows(gross=0.1, opex=0.3, eps=-0.5), price=10.0, as_of=AS_OF)
        self.assertEqual(b.route, "ps")
        self.assertIsNone(b.pe)
        self.assertGreater(b.ps, 0)

    def test_net_margin_route_without_operating_income(self):
        rows = quarter_rows()
        for r in rows:
            r["operating_income"] = None
        b = build_baseline("TEST", rows, price=40.0, as_of=AS_OF)
        self.assertEqual(b.route, "net")
        levers = default_levers(b)
        levers["revenue_growth"] = 0.0
        self.assertAlmostEqual(project(b, levers).eps, b.eps)

    def test_loss_company_without_operating_income_still_uses_ps(self):
        rows = quarter_rows(gross=0.1, opex=0.3, eps=-0.5)
        for r in rows:
            r["operating_income"] = None
        self.assertEqual(build_baseline("TEST", rows, price=10.0, as_of=AS_OF).route, "ps")

    def test_no_revenue_is_not_supported(self):
        rows = quarter_rows()
        for r in rows:
            r["revenue"] = None
        b = build_baseline("TEST", rows, price=10.0, as_of=AS_OF)
        self.assertFalse(b.supported)

    def test_excluded_reason_short_circuits(self):
        b = build_baseline("^TWII", [], None, AS_OF, excluded_reason="指數沒有每股盈餘")
        self.assertFalse(b.supported)
        self.assertEqual(b.reason, "指數沒有每股盈餘")


class ProjectionTests(SimpleTestCase):
    def setUp(self):
        self.b = build_baseline("TEST", quarter_rows(), price=40.0, as_of=AS_OF)

    def test_zero_growth_and_unchanged_margins_returns_ttm_eps(self):
        levers = default_levers(self.b)
        levers["revenue_growth"] = 0.0
        p = project(self.b, levers)
        self.assertAlmostEqual(p.eps, self.b.eps)
        self.assertAlmostEqual(p.target_price, self.b.price)
        self.assertAlmostEqual(p.implied_return, 0.0)

    def test_gross_margin_change_flows_into_eps(self):
        """舊的 compute_fair_value 毛利率不影響 EPS，這裡一定要影響"""
        levers = default_levers(self.b)
        levers["revenue_growth"] = 0.0
        levers["gross_margin"] += 0.03     # 營業利益率 30% → 33%
        self.assertAlmostEqual(project(self.b, levers).eps, self.b.eps * 0.33 / 0.30)

    def test_same_direction_scenarios_do_not_stack(self):
        one, _ = apply_scenarios(self.b, [{"type": "DEMAND_UP"}])
        two, _ = apply_scenarios(self.b, [{"type": "DEMAND_UP"}, {"type": "NEW_BUSINESS"}])
        self.assertAlmostEqual(one["revenue_growth"], two["revenue_growth"])

    def test_bull_scenarios_raise_and_bear_scenarios_lower_target(self):
        base = project_scenarios(self.b, [])
        bull = project_scenarios(self.b, [{"type": "DEMAND_UP"}, {"type": "PRICE_UP"}])
        bear = project_scenarios(self.b, [{"type": "DEMAND_DOWN"}, {"type": "COST_UP"}])
        self.assertGreater(bull.target_price, base.target_price)
        self.assertLess(bear.target_price, base.target_price)

    def test_eps_forecast_and_pe_figures_are_used_directly(self):
        p = project_scenarios(self.b, [
            {"type": "EPS_FORECAST", "figures": {"eps": 75.0, "year": 2026}},
            {"type": "RERATING_UP", "figures": {"pe": 20.0}},
        ])
        self.assertAlmostEqual(p.eps, 75.0)
        self.assertAlmostEqual(p.target_price, 1500.0)

    def test_rerating_moves_one_step_toward_median_first(self):
        self.b.history["pe"] = {"p25": 30.0, "p50": 40.0, "p75": 50.0, "n": 20}
        self.b.pe = 20.0
        levers, _ = apply_scenarios(self.b, [{"type": "RERATING_UP"}])
        self.assertAlmostEqual(levers["pe"], 40.0)      # 低於中位數 → 先到中位數，不是直接跳到 P75
        self.b.pe = 45.0
        levers, _ = apply_scenarios(self.b, [{"type": "RERATING_UP"}])
        self.assertAlmostEqual(levers["pe"], 50.0)

    def test_margin_step_is_capped_relative_to_current_margin(self):
        self.b.history["gross_margin"] = {"p25": 0.1, "p50": 0.5, "p75": 0.9, "n": 20}
        levers, _ = apply_scenarios(self.b, [{"type": "COST_UP"}])
        self.assertAlmostEqual(levers["gross_margin"], 0.5 - 0.5 * 0.25)

    def test_financial_risk_is_marked_provisional(self):
        _, notes = apply_scenarios(self.b, [{"type": "FINANCIAL_RISK"}])
        self.assertTrue(any("暫定" in n for n in notes))

    def test_price_to_sales_route_projects_from_revenue(self):
        b = build_baseline("TEST", quarter_rows(gross=0.1, opex=0.3, eps=-0.5), price=10.0, as_of=AS_OF)
        levers = default_levers(b)
        levers["revenue_growth"] = 0.0
        p = project(b, levers)
        self.assertAlmostEqual(p.target_price, b.price)
        self.assertIsNone(p.eps)


class WaterfallTests(SimpleTestCase):
    def setUp(self):
        self.b = build_baseline("TEST", quarter_rows(), price=40.0, as_of=AS_OF)

    def test_steps_add_up_to_final_target(self):
        chosen = [{"type": "DEMAND_UP"}, {"type": "PRICE_UP"}, {"type": "COST_UP"}]
        steps = waterfall(self.b, chosen, ["需求", "漲價", "成本"])
        final = project_scenarios(self.b, chosen).target_price
        self.assertAlmostEqual(self.b.price + sum(s["delta"] for s in steps), final)
        self.assertAlmostEqual(steps[-1]["value"], final)

    def test_repeated_same_direction_scenario_contributes_zero(self):
        steps = waterfall(self.b, [{"type": "DEMAND_UP"}, {"type": "NEW_BUSINESS"}], ["需求", "新產品"])
        self.assertGreater(steps[1]["delta"], 0)
        self.assertAlmostEqual(steps[2]["delta"], 0.0)

    def test_manual_override_is_its_own_step(self):
        steps = waterfall(self.b, [], [], overrides={"pe": 50.0})
        self.assertEqual(steps[-1]["kind"], "manual")
        levers = default_levers(self.b)
        levers["pe"] = 50.0
        self.assertAlmostEqual(steps[-1]["value"], project(self.b, levers).target_price)


class ScenarioCheckRuleTests(SimpleTestCase):
    """2026-10-26 抽樣檢查(19 類各 5 句)後加的程式端規則，見 thesis_scenario.apply_checks"""

    def check(self, raw, source=None, thesis="", direction="bullish"):
        from apps.summaries.services.thesis_scenario import apply_checks
        source = source if source is not None else raw["quote"]
        return apply_checks([{"chain": ["a", "營收成長"], **raw}], direction, source + thesis,
                            "台積電", "2330.TW", published_year=2026, thesis=thesis)

    def test_direction_conflict_is_dropped(self):
        kept, dropped = self.check({"type": "COMPETITION", "effect": "up", "quote": "長期競爭力短期難以被其他公司超越"})
        self.assertEqual(kept, [])
        self.assertEqual(dropped[0]["reason"], "方向跟類別相反")

    def test_current_state_dropped_but_future_kept(self):
        _, dropped = self.check({"type": "MIX_UPGRADE", "effect": "up", "quote": "毛利率也大幅提升至接近50%"})
        self.assertEqual(dropped[0]["reason"], "現況不是未來的變化")
        kept, _ = self.check({"type": "MIX_UPGRADE", "effect": "up", "quote": "毛利率將提升至55%以上"})
        self.assertEqual(kept[0]["type"], "MIX_UPGRADE")

    def test_macro_through_demand_becomes_demand(self):
        kept, _ = self.check({"type": "MACRO_EASING", "effect": "up", "quote": "未來降息可能刺激台積電客戶的銷售"})
        self.assertEqual(kept[0]["type"], "DEMAND_UP")

    def test_macro_through_fx_becomes_cost_up(self):
        kept, _ = self.check({"type": "MACRO_TIGHTENING", "effect": "down", "quote": "台幣急速升值對台積電產生不利影響"})
        self.assertEqual(kept[0]["type"], "COST_UP")

    def test_financial_risk_split(self):
        kept, _ = self.check({"type": "FINANCIAL_RISK", "effect": "down", "quote": "面臨較高的反壟斷調查風險"})
        self.assertEqual(kept[0]["type"], "REGULATORY_RISK")
        _, dropped = self.check({"type": "FINANCIAL_RISK", "effect": "down", "quote": "以及多位高管離職的挑戰"})
        self.assertEqual(dropped[0]["reason"], "經營風險不參與試算")

    def test_capacity_margin_drag_only_with_new_fab_words(self):
        kept, _ = self.check({"type": "CAPACITY_EXPANSION", "effect": "up", "quote": "在美國擴廠後市占率將大幅提升"})
        self.assertTrue(kept[0]["margin_drag"])
        kept, _ = self.check({"type": "CAPACITY_EXPANSION", "effect": "up", "quote": "利用其閒置產能即可擴大產能"})
        self.assertFalse(kept[0]["margin_drag"])

    def test_target_price_source_and_missing_number(self):
        kept, _ = self.check({"type": "ANALYST_TARGET", "effect": "up", "quote": "瑞銀調升目標價至1625美元",
                              "figures": {"target_low": 1625, "target_high": 1625}})
        self.assertEqual(kept[0]["source"], "analyst")
        kept, _ = self.check({"type": "ANALYST_TARGET", "effect": "up", "quote": "股價有機會挑戰300元大關",
                              "figures": {"target_low": 300, "target_high": 300}})
        self.assertEqual(kept[0]["source"], "podcast")
        _, dropped = self.check({"type": "ANALYST_TARGET", "effect": "up", "quote": "外資所喊的目標價可以期待"})
        self.assertEqual(dropped[0]["reason"], "目標價沒有數字")

    def test_eps_filled_from_thesis(self):
        thesis = "台達電EPS持續調高，2026年預估32塊，2027年甚至50塊。"
        kept, _ = self.check({"type": "EPS_FORECAST", "effect": "up", "quote": "台達電EPS又調高"}, thesis=thesis)
        figs = sorted((s["figures"]["year"], s["figures"]["eps"]) for s in kept)
        self.assertEqual(figs, [(2026, 32.0), (2027, 50.0)])
        self.assertEqual(kept[0]["figures_source"], "thesis")

    def test_eps_fill_ignores_target_prices(self):
        kept, _ = self.check({"type": "EPS_FORECAST", "effect": "up", "quote": "EPS預估將持續成長"},
                             thesis="EPS預估將持續成長，2026年目標價上看1600元")
        self.assertIsNone(kept[0]["figures"].get("eps"))


class CapacityMarginTests(SimpleTestCase):
    def test_capacity_without_margin_drag_keeps_gross_margin(self):
        b = build_baseline("TEST", quarter_rows(), price=40.0, as_of=AS_OF)
        plain, _ = apply_scenarios(b, [{"type": "CAPACITY_EXPANSION"}])
        drag, _ = apply_scenarios(b, [{"type": "CAPACITY_EXPANSION", "margin_drag": True}])
        self.assertAlmostEqual(plain["gross_margin"], b.gross_margin)
        self.assertLess(drag["gross_margin"], b.gross_margin)
