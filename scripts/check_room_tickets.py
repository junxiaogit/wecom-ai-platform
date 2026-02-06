"""检查指定群的工单和告警"""
import os, sys
sys.stdout.reconfigure(encoding='utf-8')
os.chdir(r'd:\Coze\wecom-ai-platform')
sys.path.insert(0, r'd:\Coze\wecom-ai-platform')
from dotenv import load_dotenv
load_dotenv()

from app.core.database import SessionLocal
from app.models.sql_models import TicketDraft, AlertEvent
from datetime import datetime

db = SessionLocal()
room_id = "wroCqZGwAAXaen2i3JT4VKSEFJ2I8Jpw"
since = datetime(2026, 2, 6, 9, 0, 0)

tickets = db.query(TicketDraft).filter(
    TicketDraft.room_id == room_id,
    TicketDraft.created_at >= since
).order_by(TicketDraft.created_at).all()

print(f"Tickets for 978BCD09 today: {len(tickets)}")
for t in tickets:
    content = t.content or {}
    phenomenon = content.get("phenomenon", "")
    title = content.get("title", "") or t.title or ""
    time_str = t.created_at.strftime("%H:%M:%S") if t.created_at else "N/A"
    print(f"  ID={t.draft_id} | {time_str} | phenomenon={phenomenon}")
    print(f"    title={title[:70]}")
    print()

alerts = db.query(AlertEvent).filter(
    AlertEvent.room_id == room_id,
    AlertEvent.sent_at >= since
).order_by(AlertEvent.sent_at).all()
print(f"Alerts for this room today: {len(alerts)}")
for a in alerts:
    time_str = a.sent_at.strftime("%H:%M:%S") if a.sent_at else "N/A"
    print(f"  ID={a.alert_id} | {time_str} | key={a.dedup_key} | hits={a.hit_count}")

db.close()
