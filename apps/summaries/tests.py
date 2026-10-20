from django.test import SimpleTestCase

from apps.summaries.services.backtesting import _rule_based_ticker, resolve_ticker


class RuleBasedTickerTests(SimpleTestCase):
    def test_specific_index_keys_win_over_shorter_ones(self):
        self.assertEqual(_rule_based_ticker("納斯達克100指數"), "^NDX")
        self.assertEqual(_rule_based_ticker("那斯達克100指數"), "^NDX")
        self.assertEqual(_rule_based_ticker("納斯達克指數"), "^IXIC")
        self.assertEqual(_rule_based_ticker("日股大盤"), "^N225")
        self.assertEqual(_rule_based_ticker("美股大盤"), "^GSPC")
        self.assertEqual(_rule_based_ticker("台股大盤"), "^TWII")
        self.assertEqual(_rule_based_ticker("Stoxx 600指數"), "^STOXX")

    def test_short_names_mapped_to_real_tickers(self):
        for asset, ticker in [("TI", "TXN"), ("SOX", "^SOX"), ("VIX", "^VIX"),
                              ("NIKE", "NKE"), ("Nike", "NKE"), ("WTI", "CL=F")]:
            self.assertEqual(_rule_based_ticker(asset), ticker, asset)

    def test_inverse_etf_not_mapped_to_underlying(self):
        self.assertEqual(_rule_based_ticker("石油反一"), "00673R.TW")
        self.assertEqual(_rule_based_ticker("石油"), "CL=F")

    def test_raw_ticker_passthrough(self):
        self.assertEqual(_rule_based_ticker("AAOI"), "AAOI")
        self.assertIsNone(_rule_based_ticker("DRAM"))
        self.assertIsNone(_rule_based_ticker("AAOI", allow_raw_ticker=False))


class ResolveTickerTests(SimpleTestCase):
    # SimpleTestCase 不允許查資料庫，TickerMap 那層會被 resolve_ticker 的 try/except 吃掉
    def test_raw_ticker_after_ticker_map(self):
        self.assertEqual(resolve_ticker("AAOI"), "AAOI")
        self.assertEqual(resolve_ticker("CPU"), "")


class TwCodeTickerTests(SimpleTestCase):
    def test_letter_suffix_etf_codes(self):
        for asset, ticker in [("00685L", "00685L.TW"), ("00632R", "00632R.TW"),
                              ("00981A", "00981A.TW"), ("00635U", "00635U.TW"),
                              ("00679B", "00679B.TWO"), ("00679b", "00679B.TWO"),
                              ("2330", "2330.TW"), ("006208", "006208.TW")]:
            self.assertEqual(_rule_based_ticker(asset), ticker, asset)
            # SimpleTestCase 查不到大表，退回同一個預設
            self.assertEqual(resolve_ticker(asset), ticker, asset)

    def test_not_tw_code(self):
        self.assertIsNone(_rule_based_ticker("00685LL", allow_raw_ticker=False))
        self.assertIsNone(_rule_based_ticker("123", allow_raw_ticker=False))
