"""根据 chat_records 的消息数直接设置 pending_count"""
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')
os.chdir(r'd:\Coze\wecom-ai-platform')
sys.path.insert(0, r'd:\Coze\wecom-ai-platform')

from dotenv import load_dotenv
load_dotenv()

from app.core.database import SessionLocal
from app.models.sql_models import RoomPollingState
from app.models.chat_record import ChatRecord
from datetime import datetime
from sqlalchemy import func

db = SessionLocal()
today_9am = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
today_9am_ms = int(today_9am.timestamp() * 1000)

print(f"Setting pending counts based on chat_records since {today_9am}")
print()

# Get message counts per room since 9 AM
room_counts = db.query(
    ChatRecord.roomid,
    func.count(ChatRecord.msgid).label('count'),
    func.max(ChatRecord.msgtime).label('max_time')
).filter(
    ChatRecord.msgtime > today_9am_ms,
    ChatRecord.msgtype == 'text'
).group_by(ChatRecord.roomid).all()

print(f"Found {len(room_counts)} rooms with messages since 9 AM")
print("-" * 60)

updated = 0
for room_id, count, max_time in room_counts:
    if not room_id:
        continue
    state = db.query(RoomPollingState).filter(RoomPollingState.room_id == room_id).first()
    if state:
        state.pending_count = count
        state.last_msgtime = max_time
        updated += 1
        time_str = datetime.fromtimestamp(max_time/1000).strftime("%H:%M:%S")
        print(f"{room_id[:35]:38} pending={count:3}  last={time_str}")

db.commit()
db.close()

print("-" * 60)
print(f"Updated {updated} rooms")
print("Rooms with pending >= 20 will trigger LLM analysis")
