"""清理今天同群聊语义重复的工单"""
import os, sys, re
sys.stdout.reconfigure(encoding='utf-8')
os.chdir(r'd:\Coze\wecom-ai-platform')
sys.path.insert(0, r'd:\Coze\wecom-ai-platform')
from dotenv import load_dotenv
load_dotenv()

from app.core.database import SessionLocal
from app.models.sql_models import TicketDraft, Issue
from datetime import datetime
from collections import defaultdict

db = SessionLocal()
since = datetime(2026, 2, 6, 9, 0, 0)

# Get all tickets today
tickets = db.query(TicketDraft).filter(
    TicketDraft.created_at >= since
).order_by(TicketDraft.created_at).all()

print(f"Total tickets since 9AM: {len(tickets)}")
print("=" * 80)

# Group by room_id
room_groups = defaultdict(list)
for t in tickets:
    room_groups[t.room_id or "unknown"].append(t)

def _extract_bigrams(text):
    text = text.lower().strip()
    text = re.sub(r'[，。、！？：；""\'\'（）\(\)\[\]\s]+', '', text)
    if len(text) < 2:
        return set()
    return {text[i:i+2] for i in range(len(text) - 1)}

def is_similar(a, b, threshold=0.6):
    """Check if two phenomena are similar using containment + Jaccard"""
    bg_a = _extract_bigrams(a)
    bg_b = _extract_bigrams(b)
    if not bg_a or not bg_b:
        return False
    overlap = len(bg_a & bg_b)
    union = len(bg_a | bg_b)
    min_len = min(len(bg_a), len(bg_b))
    jaccard = overlap / union if union > 0 else 0
    containment = overlap / min_len if min_len > 0 else 0
    sim = max(jaccard, containment)
    return sim >= threshold

keep_ids = []
delete_ids = []

for room_id, room_tickets in room_groups.items():
    if len(room_tickets) <= 1:
        keep_ids.append(room_tickets[0].draft_id)
        continue
    
    print(f"\nRoom: {room_id[:30]}")
    
    # For each room, keep first of each unique issue, delete semantic duplicates
    kept_phenomena = []  # (draft_id, phenomenon)
    
    for t in room_tickets:
        content = t.content or {}
        phenomenon = content.get("phenomenon", "") or ""
        title = content.get("title", "") or t.title or ""
        time_str = t.created_at.strftime("%H:%M:%S") if t.created_at else "N/A"
        
        # Check if this is a duplicate of any kept ticket
        is_dup = False
        dup_of = None
        for kept_id, kept_phen in kept_phenomena:
            if is_similar(phenomenon, kept_phen):
                is_dup = True
                dup_of = kept_id
                break
        
        if is_dup:
            delete_ids.append(t.draft_id)
            print(f"  [DELETE] ID={t.draft_id} | {time_str} | {phenomenon}")
            print(f"           (duplicate of ID={dup_of})")
        else:
            keep_ids.append(t.draft_id)
            kept_phenomena.append((t.draft_id, phenomenon))
            print(f"  [KEEP]   ID={t.draft_id} | {time_str} | {phenomenon}")

print()
print("=" * 80)
print(f"Keep: {len(keep_ids)}, Delete: {len(delete_ids)}")

if not delete_ids:
    print("No duplicates found!")
    db.close()
    sys.exit(0)

confirm = input("\nConfirm delete? (yes): ")
if confirm.lower() != "yes":
    print("Cancelled.")
    db.close()
    sys.exit(0)

# Delete duplicate tickets
deleted = db.query(TicketDraft).filter(TicketDraft.draft_id.in_(delete_ids)).delete(synchronize_session=False)
print(f"Deleted {deleted} TicketDraft records")

# Delete related Issues
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

db.commit()
print("Done!")
db.close()
