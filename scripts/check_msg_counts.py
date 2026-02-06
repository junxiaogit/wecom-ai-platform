"""检查今天 9:00 以来的消息数量统计"""
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')
os.chdir(r'd:\Coze\wecom-ai-platform')
sys.path.insert(0, r'd:\Coze\wecom-ai-platform')

from dotenv import load_dotenv
load_dotenv()

from app.core.database import SessionLocal
from app.models.sql_models import WeComMessage, RoomPollingState
from app.models.chat_record import ChatRecord
from datetime import datetime
from sqlalchemy import func

db = SessionLocal()

# 今天9:00的时间戳
today_9am = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
today_9am_ms = int(today_9am.timestamp() * 1000)

print(f"Today 9:00 AM: {today_9am}")
print(f"Timestamp (ms): {today_9am_ms}")
print()

# 检查 chat_records 表
print("=" * 60)
print("chat_records (source table) statistics:")
print("=" * 60)

chat_total = db.query(ChatRecord).count()
chat_since_9am = db.query(ChatRecord).filter(ChatRecord.msgtime > today_9am_ms).count()
chat_text_since_9am = db.query(ChatRecord).filter(
    ChatRecord.msgtime > today_9am_ms,
    ChatRecord.msgtype == "text"
).count()

print(f"Total records: {chat_total}")
print(f"Records since 9:00 AM: {chat_since_9am}")
print(f"Text messages since 9:00 AM: {chat_text_since_9am}")
print()

# 按群聊分组统计（只看今天9:00以后的）
room_stats = db.query(
    ChatRecord.roomid, 
    func.count(ChatRecord.msgid).label('count')
).filter(
    ChatRecord.msgtime > today_9am_ms,
    ChatRecord.msgtype == "text"
).group_by(ChatRecord.roomid).order_by(func.count(ChatRecord.msgid).desc()).limit(15).all()

print("Top 15 rooms by text message count since 9:00 AM:")
print("-" * 60)
for room_id, count in room_stats:
    if room_id and room_id != 'None':
        print(f"{str(room_id)[:40]:<42} {count:>6} msgs")

print()

# 检查 room_polling_state
print("=" * 60)
print("room_polling_state (polling state) statistics:")
print("=" * 60)

states = db.query(RoomPollingState).order_by(RoomPollingState.pending_count.desc()).all()
print(f"Total rooms tracked: {len(states)}")
print()
print("Room polling states:")
print("-" * 90)
print(f"{'room_id':<42} {'pending':>7} {'raw_pending':>11} {'last_msgtime':<18}")
print("-" * 90)
for s in states:
    last_time = datetime.fromtimestamp(s.last_msgtime/1000).strftime('%m-%d %H:%M:%S') if s.last_msgtime else 'N/A'
    raw_pending = getattr(s, 'raw_pending_count', 0) or 0
    print(f"{s.room_id[:40]:<42} {s.pending_count:>7} {raw_pending:>11} {last_time:<18}")

db.close()
