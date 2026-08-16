# apps/assets/services/ranking_utils.py
"""
tw_stock_rankings.py 跟 tw_etf_rankings.py 共用的排序/分頁邏輯，避免兩邊各寫一份。
"""


def sort_rows(rows: list[dict], key: str, direction: str, limit: int, offset: int) -> tuple[list[dict], int]:
    """
    依 key 排序 rows，缺值（None）一律排在最後，不管是升冪還是降冪。
    回傳 (該頁資料, 總筆數)。
    """
    reverse = (direction == 'desc')

    def sort_key(r):
        v = r.get(key)
        if v is None:
            # reverse=True 時 sorted() 由大到小排，缺值要當作「最小」才會被排到最後；
            # reverse=False 時由小到大排，缺值要當作「最大」才會被排到最後。
            return float('-inf') if reverse else float('inf')
        return v

    ordered = sorted(rows, key=sort_key, reverse=reverse)
    total = len(ordered)
    return ordered[offset: offset + limit], total


def filter_by_query(rows: list[dict], q: str | None) -> list[dict]:
    """
    ASSETS 頁面搜尋欄用：代碼或名稱的子字串比對（不分大小寫），
    在傳進來的整份清單裡找（tw_stock_rankings 是全市場快取、tw_etf_rankings
    是當天全部 ETF），不是只在畫面上已經顯示的那一頁裡找。
    """
    if not q:
        return rows
    needle = q.strip().upper()
    if not needle:
        return rows
    return [r for r in rows if needle in r['symbol'].upper() or needle in (r.get('name') or '').upper()]
