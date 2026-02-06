"""
重置今日分析数据脚本

功能：
1. 删除今天9点后创建的 TicketDraft（工单草稿）
2. 删除今天9点后创建的 AlertEvent（告警事件）
3. 删除今天9点后创建的 Issue（问题记录）
4. 删除今天的 IssueAggregation（问题聚合统计）
5. 重置 RoomPollingState（轮询状态）到今天9点

使用方式：
1. 停止服务
2. 执行脚本：python scripts/reset_today.py
3. 重启服务：python main.py
"""
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')
os.chdir(r'd:\Coze\wecom-ai-platform')
sys.path.insert(0, r'd:\Coze\wecom-ai-platform')

from dotenv import load_dotenv
load_dotenv()

from app.core.database import SessionLocal
from app.models.sql_models import (
    TicketDraft, 
    AlertEvent, 
    Issue, 
    IssueAggregation, 
    RoomPollingState
)
from datetime import datetime, date

db = SessionLocal()

# 计算今天9点的时间
today_9am = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
today_9am_ms = int(today_9am.timestamp() * 1000)
today_date = date.today()

print("=" * 60)
print("重置今日分析数据")
print("=" * 60)
print(f"目标时间点: {today_9am}")
print(f"时间戳 (ms): {today_9am_ms}")
print(f"今日日期: {today_date}")
print()

# ========== 统计待删除数据 ==========
print("待删除数据统计:")
print("-" * 60)

# 1. TicketDraft
ticket_count = db.query(TicketDraft).filter(
    TicketDraft.created_at >= today_9am
).count()
print(f"  TicketDraft (工单草稿):      {ticket_count} 条")

# 2. AlertEvent
alert_count = db.query(AlertEvent).filter(
    AlertEvent.sent_at >= today_9am
).count()
print(f"  AlertEvent (告警事件):       {alert_count} 条")

# 3. Issue
issue_count = db.query(Issue).filter(
    Issue.created_at >= today_9am
).count()
print(f"  Issue (问题记录):            {issue_count} 条")

# 4. IssueAggregation
agg_count = db.query(IssueAggregation).filter(
    IssueAggregation.date == today_date
).count()
print(f"  IssueAggregation (聚合统计): {agg_count} 条")

# 5. RoomPollingState
state_count = db.query(RoomPollingState).count()
print(f"  RoomPollingState (轮询状态): {state_count} 条 (将重置)")

print()
total_delete = ticket_count + alert_count + issue_count + agg_count
print(f"总计将删除: {total_delete} 条数据")
print(f"总计将重置: {state_count} 个群聊状态")
print()

# ========== 确认执行 ==========
confirm = input("确认执行重置? (输入 'yes' 确认): ")

if confirm.lower() != 'yes':
    print("\n操作已取消。")
    db.close()
    sys.exit(0)

print()
print("正在执行重置...")
print("-" * 60)

# ========== 执行删除 ==========
try:
    # 1. 删除 TicketDraft
    deleted_tickets = db.query(TicketDraft).filter(
        TicketDraft.created_at >= today_9am
    ).delete(synchronize_session=False)
    print(f"  已删除 TicketDraft: {deleted_tickets} 条")

    # 2. 删除 AlertEvent
    deleted_alerts = db.query(AlertEvent).filter(
        AlertEvent.sent_at >= today_9am
    ).delete(synchronize_session=False)
    print(f"  已删除 AlertEvent: {deleted_alerts} 条")

    # 3. 删除 Issue
    deleted_issues = db.query(Issue).filter(
        Issue.created_at >= today_9am
    ).delete(synchronize_session=False)
    print(f"  已删除 Issue: {deleted_issues} 条")

    # 4. 删除 IssueAggregation
    deleted_aggs = db.query(IssueAggregation).filter(
        IssueAggregation.date == today_date
    ).delete(synchronize_session=False)
    print(f"  已删除 IssueAggregation: {deleted_aggs} 条")

    # 5. 重置 RoomPollingState
    # 先获取每个群今天的消息数
    from app.models.chat_record import ChatRecord
    from sqlalchemy import func
    
    room_msg_counts = (
        db.query(ChatRecord.roomid, func.count(ChatRecord.msgid))
        .filter(
            ChatRecord.msgtime >= today_9am_ms,
            ChatRecord.msgtype == "text"
        )
        .group_by(ChatRecord.roomid)
        .all()
    )
    room_count_map = {r[0]: r[1] for r in room_msg_counts}
    
    # 重置每个群的状态：last_msgtime 到 9:00，pending_count 设为实际消息数
    # 同时计算非噪音消息数（用于 pending_count）
    from app.services.data_clean_service import DataCleanService
    from app.services.data_service import _extract_content
    
    # 计算每个群的非噪音消息数
    room_effective_counts = {}
    for room_id, raw_count in room_count_map.items():
        records = (
            db.query(ChatRecord)
            .filter(
                ChatRecord.roomid == room_id,
                ChatRecord.msgtime >= today_9am_ms,
                ChatRecord.msgtype == "text"
            )
            .all()
        )
        effective = 0
        for r in records:
            content = None
            try:
                import json
                data = json.loads(r.msgData) if r.msgData else {}
                content = data.get("content", "")
            except:
                content = ""
            if content:
                clean = DataCleanService.sanitize(content)
                if clean and not DataCleanService.is_noise(clean):
                    effective += 1
        room_effective_counts[room_id] = effective
    
    reset_count = 0
    for state in db.query(RoomPollingState).all():
        state.last_msgtime = today_9am_ms
        state.pending_count = room_effective_counts.get(state.room_id, 0)
        state.raw_pending_count = room_count_map.get(state.room_id, 0)
        reset_count += 1
    
    print(f"  已重置 RoomPollingState: {reset_count} 条")
    print(f"  各群消息数统计:")
    for room_id, count in sorted(room_count_map.items(), key=lambda x: -x[1])[:10]:
        print(f"    {room_id[:30]}... = {count} 条")

    # 提交事务
    db.commit()
    
    print()
    print("=" * 60)
    print("重置完成!")
    print("=" * 60)
    print()
    print("下一步:")
    print("  1. 重启服务: python main.py")
    print("  2. 服务将从今天9点重新处理所有消息")
    print()

except Exception as e:
    db.rollback()
    print(f"\n错误: {e}")
    print("操作已回滚。")
    raise

finally:
    db.close()
