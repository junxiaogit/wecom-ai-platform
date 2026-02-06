# app/core/database.py
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base
from app.core.config import settings

# 创建数据库引擎
engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)

# 创建会话工厂
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 创建 ORM 基类
Base = declarative_base()

# 初始化数据库（仅创建缺失表）
def init_db():
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE issues ADD COLUMN IF NOT EXISTS category_l1 TEXT"))
        conn.execute(text("ALTER TABLE issues ADD COLUMN IF NOT EXISTS category_l2 TEXT"))
        conn.execute(text("ALTER TABLE issues ADD COLUMN IF NOT EXISTS category_short TEXT"))
        conn.execute(text("ALTER TABLE issues ADD COLUMN IF NOT EXISTS issue_type TEXT"))
        conn.execute(text("ALTER TABLE issues ADD COLUMN IF NOT EXISTS labels JSONB"))
        conn.execute(text("ALTER TABLE issues ADD COLUMN IF NOT EXISTS severity TEXT"))
        conn.execute(text("ALTER TABLE issues ADD COLUMN IF NOT EXISTS confidence INT"))
        conn.execute(text("ALTER TABLE issues ADD COLUMN IF NOT EXISTS taxonomy_version TEXT"))
        conn.execute(text("ALTER TABLE issues ADD COLUMN IF NOT EXISTS classification_strategy TEXT"))
        conn.execute(text("ALTER TABLE issues ADD COLUMN IF NOT EXISTS suggested_reply TEXT"))
        conn.execute(text("ALTER TABLE issues ADD COLUMN IF NOT EXISTS reply_mode TEXT"))
        conn.execute(text("ALTER TABLE alert_events ADD COLUMN IF NOT EXISTS category_l1 TEXT"))
        conn.execute(text("ALTER TABLE alert_events ADD COLUMN IF NOT EXISTS category_l2 TEXT"))
        conn.execute(text("ALTER TABLE alert_events ADD COLUMN IF NOT EXISTS category_short TEXT"))
        conn.execute(text("ALTER TABLE alert_events ADD COLUMN IF NOT EXISTS issue_type TEXT"))
        conn.execute(text("ALTER TABLE alert_events ADD COLUMN IF NOT EXISTS severity TEXT"))
        conn.execute(text("ALTER TABLE alert_events ADD COLUMN IF NOT EXISTS dedup_key TEXT"))
        conn.execute(text("ALTER TABLE alert_events ADD COLUMN IF NOT EXISTS hit_count INT"))
        conn.execute(text("ALTER TABLE alert_events ADD COLUMN IF NOT EXISTS first_seen_at TIMESTAMP"))
        conn.execute(text("ALTER TABLE alert_events ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMP"))
        conn.execute(text("ALTER TABLE alert_events ADD COLUMN IF NOT EXISTS last_sent_at TIMESTAMP"))
        conn.execute(text("ALTER TABLE ticket_drafts ADD COLUMN IF NOT EXISTS environment TEXT"))
        conn.execute(text("ALTER TABLE ticket_drafts ADD COLUMN IF NOT EXISTS version TEXT"))
        conn.execute(text("ALTER TABLE ticket_drafts ADD COLUMN IF NOT EXISTS repro_steps TEXT"))
        conn.execute(text("ALTER TABLE ticket_drafts ADD COLUMN IF NOT EXISTS attachments JSONB"))
        conn.execute(text("ALTER TABLE ticket_drafts ADD COLUMN IF NOT EXISTS assigned_to TEXT"))
        conn.execute(text("ALTER TABLE ticket_drafts ADD COLUMN IF NOT EXISTS ignored_by TEXT"))
        conn.execute(text("ALTER TABLE ticket_drafts ADD COLUMN IF NOT EXISTS ignored_at TIMESTAMP"))
        conn.execute(text("ALTER TABLE ticket_drafts ADD COLUMN IF NOT EXISTS approved_at TIMESTAMP"))
        conn.execute(text("ALTER TABLE ticket_drafts ADD COLUMN IF NOT EXISTS teambition_ticket_id TEXT"))
        conn.execute(text("ALTER TABLE ticket_drafts ADD COLUMN IF NOT EXISTS mcp_status TEXT"))
        conn.execute(text("ALTER TABLE ticket_drafts ADD COLUMN IF NOT EXISTS mcp_payload JSONB"))
        conn.execute(text("ALTER TABLE ticket_drafts ADD COLUMN IF NOT EXISTS mcp_requested_at TIMESTAMP"))
        conn.execute(text("ALTER TABLE ticket_drafts ADD COLUMN IF NOT EXISTS mcp_completed_at TIMESTAMP"))

# 依赖项函数
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()