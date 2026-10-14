from django.test import SimpleTestCase

from apps.glossary.services.annotator import _surface_pattern


class SurfacePatternTests(SimpleTestCase):
    """英文詞要有邊界；中文詞維持字面比對。"""

    def test_ascii_term_does_not_match_inside_word(self):
        pat = _surface_pattern("TER")
        self.assertIsNone(pat.search("這集的 podcaster 很有名"))
        self.assertIsNone(pat.search("terminal"))

    def test_ascii_term_matches_standalone_case_insensitive(self):
        pat = _surface_pattern("TER")
        self.assertIsNotNone(pat.search("這檔的 TER 是 0.4%"))
        self.assertIsNotNone(pat.search("ter 越低越好"))
        self.assertIsNotNone(pat.search("總費用率(TER)很重要"))

    def test_chinese_term_still_literal(self):
        pat = _surface_pattern("本益比")
        self.assertIsNotNone(pat.search("目前本益比偏高"))
        self.assertIsNotNone(pat.search("預估本益比2成長"))

    def test_mixed_term_edges(self):
        pat = _surface_pattern("P/E")
        self.assertIsNotNone(pat.search("P/E 約 25 倍"))
        self.assertIsNone(pat.search("TYP/EX"))
