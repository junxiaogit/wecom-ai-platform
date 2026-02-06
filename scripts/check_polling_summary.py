"""汇总 room_polling_state 与 chat_records 对比"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

from app.core.database import SessionLocal
from app.models.sql_models import RoomInfo, RoomPollingState
from app.models.chat_record import ChatRecord
from sqlalchemy import func
from datetime import datetime

db = SessionLocal()

# Get all room names
rooms = {r.room_id: r.room_name for r in db.query(RoomInfo).all()}

# Get polling states
states = db.query(RoomPollingState).all()

# 9 AM today
today_9am = datetime(2026, 2, 6, 9, 0, 0)
today_9am_ms = int(today_9am.timestamp() * 1000)

# Count chat_records per room since 9am (text only)
cr_counts = dict(
    db.query(ChatRecord.roomid, func.count(ChatRecord.id))
    .filter(ChatRecord.msgtime >= str(today_9am_ms), ChatRecord.msgtype == "text")
    .group_by(ChatRecord.roomid)
    .all()
)

print("=" * 110)
print(f"轮询状态汇总 (polling_state vs chat_records)")
print(f"时间范围: 2026-02-06 09:00 至今")
print("=" * 110)

header = f"{'群聊名称':<28} {'pending':>7} {'原始消息':>8} {'过滤率':>7} {'最后消息时间':<18} {'状态'}"
print(header)
print("-" * 110)

# Sort by pending desc, then by cr_count desc
sorted_states = sorted(states, key=lambda s: -(s.pending_count or 0))

total_pending = 0
total_raw = 0
active_rooms = 0

for s in sorted_states:
    name = rooms.get(s.room_id, "未知群聊")
    if len(name) > 26:
        name = name[:26] + ".."
    pending = s.pending_count or 0
    raw_pending = getattr(s, 'raw_pending_count', 0) or 0
    raw = cr_counts.get(s.room_id, 0)
    last_time = datetime.fromtimestamp(s.last_msgtime / 1000).strftime("%m-%d %H:%M") if s.last_msgtime else "N/A"
    
    total_pending += pending
    total_raw += raw
    
    if raw > 0:
        active_rooms += 1
    
    # Filter rate
    if raw > 0:
        filter_rate = f"{(1 - pending / raw) * 100:.0f}%"
    else:
        filter_rate = "-"
    
    # Status (双阈值: 有效>=5 OR 原始>=15)
    if pending >= 5 or raw_pending >= 15:
        status = ">>> 将触发分析"
    elif pending >= 2:
        status = "兜底分析范围(>=2)"
    elif pending > 0:
        status = "累积中"
    elif raw > 0:
        status = "有消息,pending=0"
    else:
        status = "无新消息"
    
    if raw > 0 or pending > 0:
        print(f"  {name:<28} {pending:>7} {raw:>8} {filter_rate:>7} {last_time:<18} {status}")

print("-" * 110)

# Rooms with pending=0 and no messages
idle_count = sum(1 for s in states if (s.pending_count or 0) == 0 and cr_counts.get(s.room_id, 0) == 0)
print(f"  (另有 {idle_count} 个群聊无新消息，pending=0，已省略)")
print()
print(f"  合计: pending总数={total_pending}, 原始text消息={total_raw}, 活跃群={active_rooms}")
if total_raw > 0:
    print(f"  总体过滤率: {(1 - total_pending / total_raw) * 100:.1f}% (原始{total_raw}条 → 有效{total_pending}条)")

# Check rooms in chat_records but NOT in polling_state
state_room_ids = {s.room_id for s in states}
missing = []
for room_id, count in cr_counts.items():
    if room_id not in state_room_ids and count > 0:
        name = rooms.get(room_id, "未知群聊")
        missing.append((name, room_id, count))

if missing:
    print()
    print("=" * 110)
    print("[WARNING] 以下群聊有消息但未被轮询监控：")
    print("-" * 110)
    for name, rid, cnt in sorted(missing, key=lambda x: x[2], reverse=True):
        name = str(name or "未知群聊")
        rid = str(rid or "unknown")
        print(f"  {name:<28} {rid:<45} {cnt:>5} 条消息")
    print(f"  共 {sum(m[2] for m in missing)} 条消息未被监控！")

db.close()
