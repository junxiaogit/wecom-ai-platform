"""
拉取2月6号9点后所有群聊新消息

功能：
- 查询 chat_records 表中 msgtime >= 2026-02-06 09:00:00 的记录
- 关联 room_info 获取群聊名称
- 解析 msgData JSON 获取消息内容
- 以列表形式展示：群聊名、用户名、消息内容

使用方式：
python scripts/fetch_messages_since_feb6.py
"""
import os
import sys
import json
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')
os.chdir(r'd:\Coze\wecom-ai-platform')
sys.path.insert(0, r'd:\Coze\wecom-ai-platform')

from dotenv import load_dotenv
load_dotenv()

from app.core.database import SessionLocal
from app.models.chat_record import ChatRecord
from app.models.sql_models import RoomInfo
from sqlalchemy import func

db = SessionLocal()

# 计算 2026-02-06 09:00:00 的毫秒时间戳
target_time = datetime(2026, 2, 6, 9, 0, 0)
target_time_ms = int(target_time.timestamp() * 1000)

print("=" * 80)
print(f"拉取群聊消息 - 自 {target_time} 起")
print("=" * 80)
print(f"时间戳 (ms): {target_time_ms}")
print()

# 查询消息
messages = (
    db.query(ChatRecord)
    .filter(
        ChatRecord.msgtime >= target_time_ms,
        ChatRecord.msgtype == "text",
    )
    .order_by(ChatRecord.msgtime.asc())
    .all()
)

print(f"共找到 {len(messages)} 条消息")
print()

# 获取所有群聊名称映射
room_ids = list(set(m.roomid for m in messages if m.roomid))
room_info_map = {}
if room_ids:
    room_infos = db.query(RoomInfo).filter(RoomInfo.room_id.in_(room_ids)).all()
    room_info_map = {r.room_id: r.room_name for r in room_infos}

# 解析消息内容
def extract_content(msg_data_str: str) -> str:
    """从 msgData JSON 中提取消息内容"""
    if not msg_data_str:
        return ""
    try:
        data = json.loads(msg_data_str)
        # 文本消息的内容通常在 content 或 text 字段
        if isinstance(data, dict):
            return data.get("content") or data.get("text") or str(data)
        return str(data)
    except:
        return msg_data_str

def format_time(msgtime_ms: int) -> str:
    """格式化时间戳"""
    if not msgtime_ms:
        return "-"
    dt = datetime.fromtimestamp(msgtime_ms / 1000)
    return dt.strftime("%m/%d %H:%M:%S")

# 按群聊分组统计
room_msg_count = {}
for m in messages:
    room_id = m.roomid or "unknown"
    room_msg_count[room_id] = room_msg_count.get(room_id, 0) + 1

print("=" * 80)
print("群聊消息统计")
print("=" * 80)
for room_id, count in sorted(room_msg_count.items(), key=lambda x: -x[1]):
    room_name = room_info_map.get(room_id, room_id[:20])
    print(f"  {room_name[:30]:<32} : {count} 条")
print()

# 输出消息列表
print("=" * 80)
print("消息详情列表")
print("=" * 80)
print(f"{'时间':<18} {'群聊名':<25} {'发送者':<20} {'消息内容'}")
print("-" * 80)

for i, m in enumerate(messages, 1):
    room_name = room_info_map.get(m.roomid, m.roomid[:15] if m.roomid else "未知群聊")
    sender = m.sender or "未知用户"
    content = extract_content(m.msgData)
    time_str = format_time(m.msgtime)
    
    # 截断过长的内容
    if len(content) > 50:
        content = content[:50] + "..."
    if len(room_name) > 23:
        room_name = room_name[:20] + "..."
    if len(sender) > 18:
        sender = sender[:15] + "..."
    
    print(f"{time_str:<18} {room_name:<25} {sender:<20} {content}")

print()
print("=" * 80)
print(f"总计: {len(messages)} 条消息，来自 {len(room_msg_count)} 个群聊")
print("=" * 80)

db.close()
