"""清理今天的重复工单，只保留每个问题的第一个单"""
import os, sys
sys.stdout.reconfigure(encoding='utf-8')
os.chdir(r'd:\Coze\wecom-ai-platform')
sys.path.insert(0, r'd:\Coze\wecom-ai-platform')
from dotenv import load_dotenv
load_dotenv()

from app.core.database import SessionLocal
from app.models.sql_models import TicketDraft, Issue, AlertEvent
from datetime import datetime

db = SessionLocal()
since = datetime(2026, 2, 6, 0, 0, 0)

# 1. List all tickets
tickets = db.query(TicketDraft).filter(TicketDraft.created_at >= since).order_by(TicketDraft.created_at).all()
print(f"Total tickets today: {len(tickets)}")
print("=" * 100)

# Group by phenomenon
from collections import defaultdict
groups = defaultdict(list)
for t in tickets:
    phenomenon = ""
    if t.content and isinstance(t.content, dict):
        phenomenon = t.content.get("phenomenon", "") or ""
    groups[phenomenon].append(t)

# Show groups and identify duplicates
keep_ids = []
delete_ids = []

for phenomenon, ticket_list in groups.items():
    print(f"\nPhenomenon: '{phenomenon}'  ({len(ticket_list)} tickets)")
    for i, t in enumerate(ticket_list):
        tb_id = t.teambition_ticket_id or "N/A"
        room = t.room_id[:30] if t.room_id else "N/A"
        time_str = t.created_at.strftime("%H:%M:%S") if t.created_at else "N/A"
        status = "KEEP" if i == 0 else "DELETE"
        if i == 0:
            keep_ids.append(t.draft_id)
        else:
            delete_ids.append(t.draft_id)
        print(f"  [{status}] ID={t.draft_id}, room={room}, time={time_str}, TB={tb_id}")

print()
print("=" * 100)
print(f"Keep: {len(keep_ids)} tickets")
print(f"Delete: {len(delete_ids)} tickets")

if not delete_ids:
    print("No duplicates to delete!")
    db.close()
    sys.exit(0)

confirm = input("\nConfirm delete? (yes): ")
if confirm.lower() != "yes":
    print("Cancelled.")
    db.close()
    sys.exit(0)

# Delete duplicate tickets
deleted_tickets = db.query(TicketDraft).filter(TicketDraft.draft_id.in_(delete_ids)).delete(synchronize_session=False)
print(f"Deleted {deleted_tickets} TicketDraft records")

# Delete related Issues and AlertEvents for deleted tickets
# Issues don't have draft_id link, so delete by matching room_id + time for duplicates
# Just delete all issues/alerts created today except earliest per room
issues = db.query(Issue).filter(Issue.created_at >= since).order_by(Issue.created_at).all()
issue_first = {}
issue_delete_ids = []
for issue in issues:
    key = issue.room_id
    if key not in issue_first:
        issue_first[key] = issue.issue_id
    else:
        issue_delete_ids.append(issue.issue_id)

if issue_delete_ids:
    deleted_issues = db.query(Issue).filter(Issue.issue_id.in_(issue_delete_ids)).delete(synchronize_session=False)
    print(f"Deleted {deleted_issues} duplicate Issue records")

# AlertEvents - keep first per dedup_key
alerts = db.query(AlertEvent).filter(AlertEvent.sent_at >= since).order_by(AlertEvent.sent_at).all()
alert_first = {}
alert_delete_ids = []
for alert in alerts:
    key = alert.dedup_key or str(alert.alert_id)
    if key not in alert_first:
        alert_first[key] = alert.alert_id
    else:
        alert_delete_ids.append(alert.alert_id)

if alert_delete_ids:
    deleted_alerts = db.query(AlertEvent).filter(AlertEvent.alert_id.in_(alert_delete_ids)).delete(synchronize_session=False)
    print(f"Deleted {deleted_alerts} duplicate AlertEvent records")

db.commit()
print("\nDone! Duplicates cleaned up.")
db.close()
