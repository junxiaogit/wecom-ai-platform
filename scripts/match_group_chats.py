import os
import re
import sys
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text


def _ensure_app_importable() -> None:
    """
    Ensure we can import app.* when running as a script.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    proj_root = os.path.abspath(os.path.join(here, ".."))
    if proj_root not in sys.path:
        sys.path.insert(0, proj_root)


_ensure_app_importable()

from app.core.database import SessionLocal  # noqa: E402


@dataclass
class MatchResult:
    query_name: str
    matched: bool
    match_type: str  # exact|fuzzy|none
    matched_name: str | None = None
    matched_id: str | None = None
    extra: dict[str, Any] | None = None


def _read_names_from_md(path: str, start_line: int, end_line: int) -> list[str]:
    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()
    names: list[str] = []

    # If the requested line range exceeds the file length, treat it as "Lxxx:" labeled lines.
    if start_line > len(lines):
        for ln in lines:
            m = re.match(r"^L(\d+):\s*(.*)$", ln.strip())
            if not m:
                continue
            idx = int(m.group(1))
            if idx < start_line or idx > end_line:
                continue
            s = (m.group(2) or "").strip()
            if s:
                names.append(s)
    else:
        # 1-based inclusive file line slicing
        chunk = lines[start_line - 1 : end_line]
        for ln in chunk:
            s = ln.strip()
            if not s:
                continue
            # Strip possible "Lxxx:" prefixes if present
            s = re.sub(r"^L\d+:\s*", "", s).strip()
            if s:
                names.append(s)
    # de-dup while preserving order
    seen = set()
    out = []
    for n in names:
        if n in seen:
            continue
        seen.add(n)
        out.append(n)
    return out


def _name_variants(name: str) -> list[str]:
    """
    Generate search variants for a name string.
    """
    variants: list[str] = []
    base = name.strip()
    if not base:
        return variants

    variants.append(base)

    # Remove Chinese/English parentheses content
    no_paren = re.sub(r"[（(].*?[)）]", "", base).strip()
    if no_paren and no_paren != base:
        variants.append(no_paren)

    # Split by & / ＆
    for sep in ["&", "＆"]:
        if sep in base:
            parts = [p.strip() for p in base.split(sep) if p.strip()]
            for p in parts:
                variants.append(p)

    # Normalize spaces
    collapsed = re.sub(r"\s+", " ", base).strip()
    if collapsed and collapsed != base:
        variants.append(collapsed)

    # If contains "群" but not "交流群", add a few heuristic trims
    if "群" in base:
        variants.append(base.replace("交流群", "群").strip())
        variants.append(base.replace("群聊", "群").strip())

    # De-dup
    seen = set()
    out = []
    for v in variants:
        v = v.strip()
        if not v or v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def _tokens_for_strict_match(name: str) -> list[str]:
    """
    Extract meaningful tokens for strict matching (AND semantics).
    """
    s = name.strip()
    if not s:
        return []
    # Remove parentheses content
    s = re.sub(r"[（(].*?[)）]", "", s).strip()
    # Split by & / ＆
    for sep in ["&", "＆"]:
        s = s.replace(sep, "&")
    parts = [p.strip() for p in s.split("&") if p.strip()]
    tokens: list[str] = []
    for p in parts:
        p = re.sub(r"\s+", " ", p).strip()
        if not p:
            continue
        # Avoid extremely short tokens (too noisy)
        if len(p) < 2:
            continue
        tokens.append(p)
    # de-dup
    seen = set()
    out = []
    for t in tokens:
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _detect_group_chats_columns(db) -> tuple[list[str], str | None]:
    """
    Returns (columns, schema_qualified_table).
    """
    # Detect schema (prefer public)
    table = "group_chats"
    schema = "public"
    exists = db.execute(
        text(
            """
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = :schema AND table_name = :table
            LIMIT 1
            """
        ),
        {"schema": schema, "table": table},
    ).fetchone()

    if not exists:
        # fallback: search any schema
        row = db.execute(
            text(
                """
                SELECT table_schema
                FROM information_schema.tables
                WHERE table_name = :table
                ORDER BY CASE WHEN table_schema='public' THEN 0 ELSE 1 END, table_schema
                LIMIT 1
                """
            ),
            {"table": table},
        ).fetchone()
        if not row:
            return [], None
        schema = str(row[0])

    cols = db.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = :schema AND table_name = :table
            ORDER BY ordinal_position
            """
        ),
        {"schema": schema, "table": table},
    ).fetchall()
    columns = [str(r[0]) for r in cols]
    return columns, f"{schema}.{table}"


def _detect_table_columns(db, table_name: str) -> tuple[list[str], str | None]:
    """
    Generic table detector. Returns (columns, schema_qualified_table).
    """
    schema = "public"
    exists = db.execute(
        text(
            """
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = :schema AND table_name = :table
            LIMIT 1
            """
        ),
        {"schema": schema, "table": table_name},
    ).fetchone()
    if not exists:
        row = db.execute(
            text(
                """
                SELECT table_schema
                FROM information_schema.tables
                WHERE table_name = :table
                ORDER BY CASE WHEN table_schema='public' THEN 0 ELSE 1 END, table_schema
                LIMIT 1
                """
            ),
            {"table": table_name},
        ).fetchone()
        if not row:
            return [], None
        schema = str(row[0])

    cols = db.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = :schema AND table_name = :table
            ORDER BY ordinal_position
            """
        ),
        {"schema": schema, "table": table_name},
    ).fetchall()
    columns = [str(r[0]) for r in cols]
    return columns, f"{schema}.{table_name}"


def _pick_name_column(columns: list[str]) -> str | None:
    priority = [
        "group_name",
        "name",
        "chat_name",
        "room_name",
        "title",
    ]
    lower_map = {c.lower(): c for c in columns}
    for p in priority:
        if p in lower_map:
            return lower_map[p]
    # fallback: any column containing "name"
    for c in columns:
        if "name" in c.lower():
            return c
    return None


def _pick_id_column(columns: list[str]) -> str | None:
    priority = [
        "roomid",
        "room_id",
        "chat_id",
        "group_id",
        "id",
    ]
    lower_map = {c.lower(): c for c in columns}
    for p in priority:
        if p in lower_map:
            return lower_map[p]
    return None


def _query_one(db, table: str, name_col: str, id_col: str | None, name: str) -> MatchResult:
    # exact
    cols_to_select = [name_col]
    if id_col:
        cols_to_select.append(id_col)
    select_sql = f"SELECT {', '.join(cols_to_select)} FROM {table} WHERE {name_col} = :name LIMIT 1"
    row = db.execute(text(select_sql), {"name": name}).fetchone()
    if row:
        matched_name = str(row[0]) if row[0] is not None else None
        matched_id = str(row[1]) if id_col and len(row) > 1 and row[1] is not None else None
        return MatchResult(query_name=name, matched=True, match_type="exact", matched_name=matched_name, matched_id=matched_id)

    # fuzzy
    select_sql = f"SELECT {', '.join(cols_to_select)} FROM {table} WHERE {name_col} ILIKE :pat LIMIT 3"
    rows = db.execute(text(select_sql), {"pat": f"%{name}%"}).fetchall()
    if rows:
        best = rows[0]
        matched_name = str(best[0]) if best[0] is not None else None
        matched_id = str(best[1]) if id_col and len(best) > 1 and best[1] is not None else None
        candidates = []
        for r in rows:
            candidates.append(
                {
                    "name": str(r[0]) if r[0] is not None else None,
                    "id": str(r[1]) if id_col and len(r) > 1 and r[1] is not None else None,
                }
            )
        return MatchResult(
            query_name=name,
            matched=True,
            match_type="fuzzy",
            matched_name=matched_name,
            matched_id=matched_id,
            extra={"candidates": candidates},
        )

    return MatchResult(query_name=name, matched=False, match_type="none")


def _query_by_all_tokens(
    db,
    table: str,
    name_col: str,
    id_col: str | None,
    tokens: list[str],
    limit: int = 5,
) -> list[dict[str, str | None]]:
    if not tokens:
        return []
    cols_to_select = [name_col]
    if id_col:
        cols_to_select.append(id_col)

    where = []
    params: dict[str, Any] = {}
    for i, t in enumerate(tokens[:5]):
        key = f"t{i}"
        where.append(f"{name_col} ILIKE :{key}")
        params[key] = f"%{t}%"
    sql = f"SELECT {', '.join(cols_to_select)} FROM {table} WHERE " + " AND ".join(where) + f" LIMIT {int(limit)}"
    rows = db.execute(text(sql), params).fetchall()
    out = []
    for r in rows:
        out.append(
            {
                "name": str(r[0]) if r[0] is not None else None,
                "id": str(r[1]) if id_col and len(r) > 1 and r[1] is not None else None,
            }
        )
    return out


def _query_by_any_token(
    db,
    table: str,
    name_col: str,
    id_col: str | None,
    tokens: list[str],
    per_token_limit: int = 3,
) -> list[dict[str, str | None]]:
    """
    OR-like helper: query each token separately and merge.
    """
    cols_to_select = [name_col]
    if id_col:
        cols_to_select.append(id_col)
    seen = set()
    out: list[dict[str, str | None]] = []
    for t in tokens[:5]:
        sql = f"SELECT {', '.join(cols_to_select)} FROM {table} WHERE {name_col} ILIKE :pat LIMIT {int(per_token_limit)}"
        rows = db.execute(text(sql), {"pat": f"%{t}%"}).fetchall()
        for r in rows:
            nm = str(r[0]) if r[0] is not None else None
            cid = str(r[1]) if id_col and len(r) > 1 and r[1] is not None else None
            key = (nm or "", cid or "")
            if key in seen:
                continue
            seen.add(key)
            out.append({"name": nm, "id": cid})
    return out


def main() -> int:
    md_path = os.environ.get("GROUP_NAMES_MD", r"d:\Coze\Note\会话内容分析.md")
    start_line = int(os.environ.get("GROUP_NAMES_START", "368"))
    end_line = int(os.environ.get("GROUP_NAMES_END", "498"))

    names = _read_names_from_md(md_path, start_line, end_line)
    if not names:
        print(f"[error] no names found in {md_path} lines {start_line}-{end_line}")
        return 2

    db = SessionLocal()
    try:
        columns, table = _detect_group_chats_columns(db)
        if not table:
            print("[error] table group_chats not found in database")
            return 3
        name_col = _pick_name_column(columns)
        if not name_col:
            print(f"[error] cannot detect name column in {table}. columns={columns}")
            return 4
        id_col = _pick_id_column(columns)

        # Optional: room_info fallback
        room_info_cols, room_info_table = _detect_table_columns(db, "room_info")
        room_info_name_col = None
        room_info_id_col = None
        if room_info_table:
            # common naming
            lower_map = {c.lower(): c for c in room_info_cols}
            room_info_name_col = lower_map.get("room_name")
            room_info_id_col = lower_map.get("room_id")

        print(f"[info] group_chats={table}, name_col={name_col}, id_col={id_col or '-'}")
        if room_info_table and room_info_name_col and room_info_id_col:
            print(f"[info] room_info={room_info_table}, name_col={room_info_name_col}, id_col={room_info_id_col}")
        else:
            print("[info] room_info fallback: unavailable")
        print(f"[info] input_names={len(names)} (deduped)")
        print("")

        results: list[MatchResult] = []
        for raw in names:
            # 0) exact (raw + a few safe variants)
            exact_hit: MatchResult | None = None
            for v in _name_variants(raw)[:3]:
                r = _query_one(db, table, name_col, id_col, v)
                if r.matched and r.match_type == "exact":
                    exact_hit = MatchResult(
                        query_name=raw,
                        matched=True,
                        match_type="exact",
                        matched_name=r.matched_name,
                        matched_id=r.matched_id,
                    )
                    break
            if exact_hit:
                results.append(exact_hit)
                continue

            # 1) strict: all tokens must appear (AND)
            tokens = _tokens_for_strict_match(raw)
            strict = _query_by_all_tokens(db, table, name_col, id_col, tokens, limit=5)
            if strict:
                best = strict[0]
                results.append(
                    MatchResult(
                        query_name=raw,
                        matched=True,
                        match_type="fuzzy",
                        matched_name=best.get("name"),
                        matched_id=best.get("id"),
                        extra={"mode": "all_tokens", "tokens": tokens, "candidates": strict},
                    )
                )
                continue

            # 2) weak: any token match (for manual confirmation)
            weak = _query_by_any_token(db, table, name_col, id_col, tokens, per_token_limit=3)
            if weak:
                best = weak[0]
                results.append(
                    MatchResult(
                        query_name=raw,
                        matched=True,
                        match_type="fuzzy",
                        matched_name=best.get("name"),
                        matched_id=best.get("id"),
                        extra={"mode": "any_token", "tokens": tokens, "candidates": weak},
                    )
                )
                continue

            # 3) fallback: try room_info table
            if room_info_table and room_info_name_col and room_info_id_col:
                strict2 = _query_by_all_tokens(db, room_info_table, room_info_name_col, room_info_id_col, tokens, limit=5)
                if strict2:
                    best2 = strict2[0]
                    results.append(
                        MatchResult(
                            query_name=raw,
                            matched=True,
                            match_type="fuzzy",
                            matched_name=best2.get("name"),
                            matched_id=best2.get("id"),
                            extra={"mode": "room_info_all_tokens", "tokens": tokens, "candidates": strict2},
                        )
                    )
                    continue
                weak2 = _query_by_any_token(db, room_info_table, room_info_name_col, room_info_id_col, tokens, per_token_limit=3)
                if weak2:
                    best2 = weak2[0]
                    results.append(
                        MatchResult(
                            query_name=raw,
                            matched=True,
                            match_type="fuzzy",
                            matched_name=best2.get("name"),
                            matched_id=best2.get("id"),
                            extra={"mode": "room_info_any_token", "tokens": tokens, "candidates": weak2},
                        )
                    )
                    continue

            results.append(MatchResult(query_name=raw, matched=False, match_type="none"))

        exact_n = sum(1 for r in results if r.match_type == "exact")
        fuzzy_n = sum(1 for r in results if r.match_type == "fuzzy")
        none_n = sum(1 for r in results if r.match_type == "none")
        print(f"[summary] exact={exact_n}, fuzzy={fuzzy_n}, none={none_n}")
        print("")

        # Print a small reference list for common group-like keywords (both tables)
        group_like = []
        for raw in names:
            # heuristic: take segments containing "群"
            segs = re.split(r"[&＆]", re.sub(r"[（(].*?[)）]", "", raw))
            for s in segs:
                s = (s or "").strip()
                if "群" in s and len(s) >= 3:
                    group_like.append(s)
        # de-dup
        group_like_dedup = []
        seen_gl = set()
        for g in group_like:
            if g in seen_gl:
                continue
            seen_gl.add(g)
            group_like_dedup.append(g)

        if group_like_dedup:
            print("[reference] group-like keywords -> group_chats candidates (top 10 each)")
            for kw in group_like_dedup[:20]:
                sql = f"SELECT {name_col}{', ' + id_col if id_col else ''} FROM {table} WHERE {name_col} ILIKE :pat LIMIT 10"
                rows = db.execute(text(sql), {"pat": f"%{kw}%"}).fetchall()
                print(f"  - keyword={kw}  candidates={len(rows)}")
                for rr in rows:
                    nm = str(rr[0]) if rr[0] is not None else None
                    cid = str(rr[1]) if id_col and len(rr) > 1 and rr[1] is not None else None
                    print(f"      - {nm} ({cid or '-'})")
            if room_info_table and room_info_name_col and room_info_id_col:
                print("[reference] group-like keywords -> room_info candidates (top 10 each)")
                for kw in group_like_dedup[:20]:
                    sql = f"SELECT {room_info_name_col}, {room_info_id_col} FROM {room_info_table} WHERE {room_info_name_col} ILIKE :pat LIMIT 10"
                    rows = db.execute(text(sql), {"pat": f"%{kw}%"}).fetchall()
                    print(f"  - keyword={kw}  candidates={len(rows)}")
                    for rr in rows:
                        nm = str(rr[0]) if rr[0] is not None else None
                        cid = str(rr[1]) if rr[1] is not None else None
                        print(f"      - {nm} ({cid or '-'})")
            print("")

        # Print table
        for r in results:
            if r.match_type == "none":
                print(f"- [NONE ] {r.query_name}")
                continue
            if r.match_type == "exact":
                print(f"- [EXACT] {r.query_name}  =>  {r.matched_name}  ({r.matched_id or '-'})")
                continue
            # fuzzy
            mode = (r.extra or {}).get("mode") if isinstance(r.extra, dict) else None
            mode_label = f"{mode}" if mode else "fuzzy"
            print(f"- [FUZZY:{mode_label}] {r.query_name}  =>  {r.matched_name}  ({r.matched_id or '-'})")
            if r.extra and isinstance(r.extra, dict) and r.extra.get("tokens"):
                print(f"         - tokens: {r.extra.get('tokens')}")
            if r.extra and isinstance(r.extra, dict) and r.extra.get("candidates"):
                cands = r.extra["candidates"]
                for c in cands[:5]:
                    nm = c.get("name")
                    cid = c.get("id") or "-"
                    print(f"         - cand: {nm} ({cid})")

        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

