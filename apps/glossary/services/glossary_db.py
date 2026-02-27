# glossary_db.py
from __future__ import annotations
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import json

DB_PATH = Path(__file__).resolve().parent / "glossary.sqlite3"


def get_conn(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path = DB_PATH) -> None:
    """
    建立專有名詞資料庫（不含 abbreviation 欄位）
    """
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS glossary_term (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        term TEXT NOT NULL UNIQUE,
        aliases_json TEXT NOT NULL DEFAULT '[]',
        short_definition TEXT NOT NULL,
        long_definition TEXT NOT NULL DEFAULT '',
        category TEXT NOT NULL DEFAULT '',
        lang TEXT NOT NULL DEFAULT 'zh-TW',
        is_active INTEGER NOT NULL DEFAULT 1
    );
    """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_glossary_term_active "
                "ON glossary_term(is_active);")
    conn.commit()
    conn.close()


@dataclass(frozen=True)
class Term:
    id: int
    term: str
    short_definition: str
    aliases: list[str]
    long_definition: str
    category: str
    lang: str
    is_active: bool

    def all_surface_forms(self) -> list[str]:
        """
        回傳所有可在摘要中被比對的寫法：
        - term 本身
        - aliases
        """
        forms = [self.term] + (self.aliases or [])
        seen = set()
        out = []
        for f in forms:
            f = (f or "").strip()
            if not f:
                continue
            key = f.lower()
            if key not in seen:
                out.append(f)
                seen.add(key)
        return out


# --- add below in apps/glossary/services/glossary_db.py ---


def _row_to_term(row: sqlite3.Row) -> Term:
    aliases = json.loads(row["aliases_json"] or "[]")
    return Term(
        id=row["id"],
        term=row["term"],
        short_definition=row["short_definition"],
        aliases=aliases,
        long_definition=row["long_definition"] or "",
        category=row["category"] or "",
        lang=row["lang"] or "zh-TW",
        is_active=bool(row["is_active"]),
    )


def list_active_terms(db_path: Path = DB_PATH) -> list[Term]:
    """
    回傳所有啟用中的名詞，給 annotator 用
    """
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM glossary_term
        WHERE is_active = 1
        ORDER BY LENGTH(term) DESC, term ASC
        """
    )
    rows = cur.fetchall()
    conn.close()
    return [_row_to_term(r) for r in rows]


def lookup(q: str, db_path: Path = DB_PATH, limit: int = 20) -> list[Term]:
    q = (q or "").strip()
    if not q:
        return []

    conn = get_conn(db_path)
    cur = conn.cursor()

    # term 精準/模糊
    cur.execute(
        """
        SELECT * FROM glossary_term
        WHERE is_active = 1 AND (LOWER(term) = LOWER(?) OR term LIKE ?)
        LIMIT ?
        """,
        (q, f"%{q}%", limit),
    )
    rows = cur.fetchall()

    # alias 模糊（MVP 先用 LIKE）
    if len(rows) < limit:
        cur.execute(
            """
            SELECT * FROM glossary_term
            WHERE is_active = 1 AND LOWER(aliases_json) LIKE ?
            LIMIT ?
            """,
            (f'%"{q.lower()}"%', limit - len(rows)),
        )
        rows += cur.fetchall()

    conn.close()

    # 去重
    seen = set()
    out = []
    for r in rows:
        t = _row_to_term(r)
        if t.id not in seen:
            out.append(t)
            seen.add(t.id)
    return out


def upsert_term_orm(
    term: str,
    short_definition: str,
    aliases: Optional[list[str]] = None,
    long_definition: str = "",
    category: str = "",
    lang: str = "zh-TW",
    is_active: bool = True,
    db_path: Path = DB_PATH,
) -> None:
    """
    新增或更新一筆專有名詞（以 term 為唯一鍵）
    """
    if not term or not short_definition:
        raise ValueError("term 與 short_definition 不可為空")

    aliases_json = json.dumps(aliases or [], ensure_ascii=False)

    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        """
    INSERT INTO glossary_term
        (term, aliases_json, short_definition, long_definition, category, lang, is_active)
    VALUES
        (?,    ?,           ?,               ?,               ?,        ?,    ?)
    ON CONFLICT(term) DO UPDATE SET
        aliases_json=excluded.aliases_json,
        short_definition=excluded.short_definition,
        long_definition=excluded.long_definition,
        category=excluded.category,
        lang=excluded.lang,
        is_active=excluded.is_active;
    """, (
            term.strip(),
            aliases_json,
            short_definition.strip(),
            long_definition.strip(),
            category.strip(),
            lang.strip(),
            1 if is_active else 0,
        )
    )

    conn.commit()
    conn.close()
