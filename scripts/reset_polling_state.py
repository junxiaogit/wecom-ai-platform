"""重置所有群聊的轮询状态到今天9:00，重新拉取今日消息"""
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')
os.chdir(r'd:\Coze\wecom-ai-platform')
sys.path.insert(0, r'd:\Coze\wecom-ai-platform')

from dotenv import load_dotenv
load_dotenv()

from app.core.database import SessionLocal
from app.models.sql_models import RoomPollingState
from datetime import datetime

db = SessionLocal()

# 今天9:00的时间戳
today_9am = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
today_9am_ms = int(today_9am.timestamp() * 1000)

print(f"Target time: {today_9am}")
print(f"Timestamp (ms): {today_9am_ms}")
print()

# 查看当前状态
states = db.query(RoomPollingState).all()
print(f"Current states ({len(states)} rooms):")
print("-" * 60)
for s in states:
    last_time = datetime.fromtimestamp(s.last_msgtime/1000).strftime('%m-%d %H:%M:%S') if s.last_msgtime else 'N/A'
    print(f"  {s.room_id[:30]}... pending={s.pending_count}, last={last_time}")

print()
confirm = input("Reset all rooms to 9:00 AM? (y/n): ")

if confirm.lower() == 'y':
    # 重置所有群的 last_msgtime 到 9:00，pending_count 到 0
    count = db.query(RoomPollingState).update({
        RoomPollingState.last_msgtime: today_9am_ms,
        RoomPollingState.pending_count: 0
    })
    db.commit()
    print(f"\nDone! Reset {count} rooms to {today_9am}")
    print("The polling service will re-fetch all messages since 9:00 AM")
else:
    print("\nCancelled.")

db.close()
