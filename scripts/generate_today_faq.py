"""
Generate non-duplicated FAQ from today's issues (cycle-based).

Definition of "today" follows the product daily cycle:
- If now >= DAILY_CYCLE_START_HOUR: from today at that hour
- Else: from yesterday at that hour

This script queries `issues` created in the current cycle, generates FAQ Q/A via FaqService,
and deduplicates similar questions before printing.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timedelta
from difflib import SequenceMatcher

try:
    # Keep consistent with other scripts in this repo (Windows absolute path).
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# Ensure `app` can be imported when running as a script
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
os.chdir(ROOT_DIR)
sys.path.insert(0, ROOT_DIR)

from dotenv import load_dotenv

load_dotenv()

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.sql_models import Issue
from app.services.faq_service import FaqService


def _get_cycle_start_dt() -> datetime:
    now = datetime.now()
    hour = settings.DAILY_CYCLE_START_HOUR
    today_start = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    return today_start if now >= today_start else (today_start - timedelta(days=1))


_PUNCT_RE = re.compile(r"[\s\-_，。！？,.!?:：；;()\[\]{}<>\"'“”‘’/\\|]+")


def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = _PUNCT_RE.sub("", s)
    return s


def _is_similar_question(a: str, b: str, threshold: float = 0.82) -> bool:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    # containment helps for small edits
    if len(na) >= 10 and (na in nb or nb in na):
        return True
    return SequenceMatcher(None, na, nb).ratio() >= threshold


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-group", type=int, default=2, help="Minimum issues per category group to generate an FAQ")
    ap.add_argument("--max-groups", type=int, default=20, help="Max category groups to consider (by frequency)")
    ap.add_argument("--max-items", type=int, default=10, help="Max FAQ items to print after dedup")
    args = ap.parse_args()

    cycle_start_dt = _get_cycle_start_dt()

    db = SessionLocal()
    try:
        issues = (
            db.query(Issue)
            .filter(Issue.created_at >= cycle_start_dt)
            .order_by(Issue.created_at.desc())
            .all()
        )
    finally:
        db.close()

    print(f"📅 统计周期：{cycle_start_dt.strftime('%Y-%m-%d %H:%M')} ~ 现在")
    print(f"🧾 今日问题（Issue）数量：{len(issues)}")
    if not issues:
        return 0

    faq_service = FaqService()
    # Generate per category groups inside the service.
    items = await faq_service.generate_from_issues(
        issues=issues, min_group=int(args.min_group), max_groups=int(args.max_groups)
    )

    # Deduplicate by question similarity
    picked = []
    for it in items:
        if not it.question:
            continue
        if any(_is_similar_question(it.question, p.question) for p in picked):
            continue
        picked.append(it)
        if len(picked) >= int(args.max_items):
            break

    print(f"\n✅ 生成 FAQ（去重后）：{len(picked)} 条\n")
    for idx, it in enumerate(picked, 1):
        cat = f"{it.category_l1 or '-'} / {it.category_l2 or '-'}"
        print(f"{idx}. 【{cat}】")
        print(f"Q：{(it.question or '').strip()}")
        print(f"A：{(it.answer or '').strip()}\n")

    return 0


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(main()))

