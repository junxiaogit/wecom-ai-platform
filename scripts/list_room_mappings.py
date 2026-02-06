"""列出所有群名映射"""
import os
import sys

# 设置 UTF-8 编码
sys.stdout.reconfigure(encoding='utf-8')

# 确保可以导入 app
here = os.path.dirname(os.path.abspath(__file__))
proj_root = os.path.abspath(os.path.join(here, ".."))
if proj_root not in sys.path:
    sys.path.insert(0, proj_root)

from app.core.database import SessionLocal
from app.models.sql_models import RoomInfo

db = SessionLocal()
rooms = db.query(RoomInfo).order_by(RoomInfo.updated_at.desc()).all()

print(f"总计 {len(rooms)} 个群聊映射:\n")
print(f"{'序号':<6}{'room_id':<45}{'群名'}")
print("-" * 120)

for i, r in enumerate(rooms, 1):
    room_name = r.room_name or "(无)"
    print(f"{i:<6}{r.room_id:<45}{room_name}")

db.close()
