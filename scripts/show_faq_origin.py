"""
Show the raw (chat_records) origin for a FAQ-like topic from today's issues.

Usage:
  python scripts/show_faq_origin.py --query "盒子 手机App 安卓版本"
  python scripts/show_faq_origin.py --issue-id 12345

It will:
- find the most relevant Issue in the current daily cycle (9:00~now)
- extract anchor msg_id from Issue.evidence (replay:<msg_id>)
- query chat_records for that msg_id
- print raw content and a detail URL (since/until ±5min)
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

from dotenv import load_dotenv

load_dotenv()

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.sql_models import Issue, WeComMessage
from app.models.chat_record import ChatRecord
from app.services.data_service import _extract_content


def _get_cycle_start_dt() -> datetime:
    now = datetime.now()
    h = settings.DAILY_CYCLE_START_HOUR
    today = now.replace(hour=h, minute=0, second=0, microsecond=0)
    return today if now >= today else (today - timedelta(days=1))


def _extract_anchor_msg_id(issue: Issue) -> str | None:
    ev = issue.evidence
    if not ev:
        return None
    evs = ev if isinstance(ev, list) else [ev]
    for item in evs:
        if isinstance(item, str) and item.startswith("replay:"):
            return item.split("replay:", 1)[1]
    return None


def _extract_any_evidence_id(issue: Issue) -> str | None:
    ev = issue.evidence
    if not ev:
        return None
    if isinstance(ev, list):
        for item in ev:
            if isinstance(item, str) and item.strip():
                return item.strip()
        return None
    if isinstance(ev, str) and ev.strip():
        return ev.strip()
    return None


def _score_issue(issue: Issue, terms: list[str]) -> int:
    text = (issue.summary or "").lower()
    score = 0
    for t in terms:
        tt = t.lower().strip()
        if not tt:
            continue
        if tt in text:
            score += 10
    # prefer OTHER/OTHER if query is about product info
    if (issue.category_l1, issue.category_l2) == ("OTHER", "OTHER"):
        score += 2
    # prefer newer
    score += int(issue.created_at.timestamp()) // 3600
    return score


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", type=str, default="", help="keywords to locate issue origin")
    ap.add_argument("--issue-id", type=int, default=0, help="direct issue_id")
    args = ap.parse_args()

    cycle_start = _get_cycle_start_dt()

    db = SessionLocal()
    try:
        q = db.query(Issue).filter(Issue.created_at >= cycle_start)
        if args.issue_id:
            q = q.filter(Issue.issue_id == int(args.issue_id))
        issues = q.order_by(Issue.created_at.desc()).all()
        if not issues:
            print(f"cycle_start={cycle_start} | no issues found")
            return 0

        picked: Issue | None = None
        if args.issue_id:
            picked = issues[0]
        else:
            query = (args.query or "").strip()
            # Default to FAQ#3-like query
            if not query:
                query = "盒子 手机 app 安卓 版本"
            terms = [t for t in query.replace(",", " ").replace("，", " ").split(" ") if t.strip()]
            picked = max(issues, key=lambda it: _score_issue(it, terms))

        print(f"cycle_start={cycle_start}")
        print(f"issue_id={picked.issue_id}")
        print(f"created_at={picked.created_at}")
        print(f"room_id={picked.room_id}")
        print(f"category={picked.category_l1}/{picked.category_l2}")
        print(f"summary={picked.summary}")
        print(f"evidence={picked.evidence}")

        anchor_msg_id = _extract_anchor_msg_id(picked)
        ev_any = _extract_any_evidence_id(picked)
        print(f"anchor_msg_id={anchor_msg_id}")

        rec = None
        if anchor_msg_id:
            rec = db.query(ChatRecord).filter(ChatRecord.msgid == str(anchor_msg_id)).first()
            if not rec:
                print("raw_chat_record=NOT_FOUND (msgid not found in chat_records)")

        # Fallback: evidence might be a WeComMessage.msg_id (e.g. *_external)
        if rec is None and ev_any:
            wm = db.query(WeComMessage).filter(WeComMessage.msg_id == str(ev_any)).first()
            if wm:
                print("\n--- RAW wecom_messages ---")
                print(f"wecom_msg_id={wm.msg_id}")
                print(f"room_id={wm.room_id}")
                print(f"sender_id={wm.sender_id}")
                print(f"msg_time={wm.msg_time}")
                print(f"content_raw={wm.content_raw}")
                anchor_ms = int(wm.msg_time.timestamp() * 1000) if wm.msg_time else None

                # Try to locate nearest chat_records in the same room by time+content
                if anchor_ms and wm.room_id:
                    window_ms = 60 * 60 * 1000
                    candidates = (
                        db.query(ChatRecord)
                        .filter(
                            ChatRecord.roomid == str(wm.room_id),
                            ChatRecord.msgtype == "text",
                            ChatRecord.msgtime >= anchor_ms - window_ms,
                            ChatRecord.msgtime <= anchor_ms + window_ms,
                        )
                        .order_by(ChatRecord.msgtime.asc())
                        .limit(300)
                        .all()
                    )
                    query_terms = []
                    if args.query:
                        query_terms = [t for t in args.query.replace(",", " ").replace("，", " ").split() if t.strip()]
                    else:
                        query_terms = ["盒子", "手机", "app", "安卓", "版本"]

                    best = None
                    best_score = -1
                    for c in candidates:
                        c_text = _extract_content(c.msgData) or ""
                        c_low = c_text.lower()
                        hit = sum(1 for t in query_terms if t.lower() in c_low)
                        time_penalty = abs(int(c.msgtime) - anchor_ms) // (60 * 1000)  # minutes
                        score = hit * 10 - int(time_penalty)
                        if score > best_score:
                            best_score = score
                            best = c
                    rec = best
            else:
                print("\nraw_chat_record=NOT_FOUND (no replay: evidence, and no matching wecom_messages)")

        if not rec:
            return 0

        content = _extract_content(rec.msgData)
        print("\n--- RAW chat_records ---")
        print(f"roomid={rec.roomid}")
        print(f"msgtime={int(rec.msgtime)}")
        print(f"sender={rec.sender}")
        print(f"content={content}")

        since = int(rec.msgtime) - 5 * 60 * 1000
        until = int(rec.msgtime) + 5 * 60 * 1000
        base = settings.INTERNAL_BASE_URL
        print("\n--- DETAIL URL ---")
        print(f"{base}/api/ui/rooms/{rec.roomid}?since={since}&until={until}")

        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

