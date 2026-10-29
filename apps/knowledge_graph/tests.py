from datetime import date
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase

from apps.knowledge_graph import views
from apps.knowledge_graph.services import asset_kg_score
from apps.knowledge_graph.services.asset_kg_score import compute_asset_scores, score_from_evidence


def _link(lid, source, target, relation_type="Supply", reason="r", day=14, src="節目A - EP1"):
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
            _link(1, "ASML", "台積電", reason="ASML 供應台積電曝光機"),
            _link(2, "ASML", "三星", reason="ASML 供應三星曝光機", src="節目B - EP2"),
            _link(3, "帆宣", "台積電", reason="帆宣幫台積電設廠"),
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
        with patch.object(asset_kg_score, "fetch_asset_links", return_value=[_link(1, "ASML", "台積電")]), \
                patch.object(asset_kg_score, "_judge_company_impact", side_effect=lambda items: [None] * len(items)):
            r = compute_asset_scores(["ASML"], date(2026, 10, 28), 14)
        self.assertEqual(r["ASML"]["unjudged_count"], 1)
        self.assertEqual(r["ASML"]["score"], 50)
        self.assertEqual(r["ASML"]["label"], "中性")

    def test_batches_large_requests(self):
        links = [_link(i, "ASML", f"客戶{i}", reason=f"r{i}") for i in range(95)]
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
