from datetime import datetime
from sqlalchemy import Column, String, Integer, DateTime, Boolean, JSON, Text, BigInteger, Date
from app.core.database import Base


class WeComMessage(Base):
    """原始消息表 - 对应 L0 层"""

    __tablename__ = "wecom_messages"

    msg_id = Column(String, primary_key=True, index=True)
    seq = Column(Integer, index=True)
    room_id = Column(String, index=True)
    sender_id = Column(String)
    msg_type = Column(String)
    content_raw = Column(Text)
    content_clean = Column(Text)
    msg_time = Column(DateTime, default=datetime.now)
    is_noise = Column(Boolean, default=False)


class Issue(Base):
    """问题工单表 - 对应 AI 分析结果"""

    __tablename__ = "issues"

    issue_id = Column(Integer, primary_key=True, autoincrement=True)
    room_id = Column(String, index=True)
    summary = Column(String)
    category = Column(String)
    issue_type = Column(String, index=True)
    category_l1 = Column(String, index=True)
    category_l2 = Column(String, index=True)
    category_short = Column(String, index=True)
    labels = Column(JSON)
    severity = Column(String, index=True)
    confidence = Column(Integer)
    taxonomy_version = Column(String)
    classification_strategy = Column(String)
    risk_score = Column(Integer)
    is_bug = Column(Boolean, default=False)
    suggested_reply = Column(Text)
    reply_mode = Column(String)
    evidence = Column(JSON)
    status = Column(String, default="pending")
    created_at = Column(DateTime, default=datetime.now)


class IssueAggregation(Base):
    """问题聚合表 - 按天聚合统计"""

    __tablename__ = "issue_aggregations"

    aggregation_id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, index=True)
    room_id = Column(String, index=True)
    category_l1 = Column(String, index=True)
    category_l2 = Column(String, index=True)
    issue_type = Column(String, index=True)
    severity = Column(String, index=True)
    total_count = Column(Integer, default=0)
    hard_count = Column(Integer, default=0)
    alert_count = Column(Integer, default=0)
    risk_sum = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class AlertEvent(Base):
    """告警事件表 - 对应钉钉推送"""

    __tablename__ = "alert_events"

    alert_id = Column(Integer, primary_key=True, autoincrement=True)
    room_id = Column(String, index=True)
    issue_id = Column(Integer, index=True)
    category_l1 = Column(String)
    category_l2 = Column(String)
    category_short = Column(String)
    issue_type = Column(String)
    severity = Column(String)
    alert_level = Column(String, default="P2")
    dedup_key = Column(String, index=True)
    hit_count = Column(Integer, default=1)
    first_seen_at = Column(DateTime, default=datetime.now)
    last_seen_at = Column(DateTime, default=datetime.now)
    last_sent_at = Column(DateTime, default=datetime.now)
    summary = Column(String)
    reason = Column(String)
    risk_score = Column(Integer)
    sent_at = Column(DateTime, default=datetime.now)
    status = Column(String, default="sent")


class TicketDraft(Base):
    """工单草稿表 - 为 Teambition 对接预留"""

    __tablename__ = "ticket_drafts"

    draft_id = Column(Integer, primary_key=True, autoincrement=True)
    issue_id = Column(Integer, index=True)
    room_id = Column(String, index=True)  # 群聊ID，用于去重检查
    title = Column(String)
    severity = Column(String)
    category = Column(String)
    environment = Column(String)
    version = Column(String)
    repro_steps = Column(Text)
    attachments = Column(JSON)
    content = Column(JSON)
    status = Column(String, default="draft")
    assigned_to = Column(String)
    ignored_by = Column(String)
    ignored_at = Column(DateTime)
    approved_at = Column(DateTime)
    teambition_ticket_id = Column(String)
    mcp_status = Column(String, default="none")
    mcp_payload = Column(JSON)
    mcp_requested_at = Column(DateTime)
    mcp_completed_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.now)


class IngestState(Base):
    """采集游标表"""

    __tablename__ = "ingest_state"

    source = Column(String, primary_key=True)
    last_msgtime = Column(BigInteger, default=0)


class RoomPollingState(Base):
    """群聊轮询状态表 - 持久化每个群的处理状态，支持服务重启后恢复"""

    __tablename__ = "room_polling_state"

    room_id = Column(String(64), primary_key=True, index=True)
    last_msgtime = Column(BigInteger, default=0)  # 已处理到的游标（毫秒时间戳）
    pending_count = Column(Integer, default=0)  # 累积未分析的有效消息数（非噪音）
    raw_pending_count = Column(Integer, default=0)  # 累积未分析的原始消息数（所有 text）
    last_processed_at = Column(BigInteger, default=0)  # 上次处理时间（毫秒时间戳）
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class FaqItem(Base):
    """FAQ 知识库条目"""

    __tablename__ = "faq_items"

    faq_id = Column(Integer, primary_key=True, autoincrement=True)
    category_l1 = Column(String, index=True)
    category_l2 = Column(String, index=True)
    question = Column(Text)
    answer = Column(Text)
    source_issue_ids = Column(JSON)
    created_at = Column(DateTime, default=datetime.now)


class RoomAssignee(Base):
    """群 -> 负责人映射"""

    __tablename__ = "room_assignees"

    room_id = Column(String, primary_key=True)
    assignee = Column(String)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class RoomInfo(Base):
    """群 -> 群名映射"""

    __tablename__ = "room_info"

    room_id = Column(String, primary_key=True)
    room_name = Column(String)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
