from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.core.config import settings
from app.models.sql_models import AlertEvent, Issue


def compute_alert_level(severity: str | None, risk_score: int) -> str:
    if risk_score >= 90:
        return "P0"
    if severity == "S4":
        return "P0"
    if severity == "S3":
        return "P1"
    if severity == "S2":
        return "P2"
    return "P3"


def _dedup_window_seconds(level: str) -> int:
    if level == "P0":
        return settings.ALERT_DEDUP_P0_SECONDS
    if level == "P1":
        return settings.ALERT_DEDUP_P1_SECONDS
    return settings.ALERT_DEDUP_P2_SECONDS


def _level_rank(level: str) -> int:
    # 数字越小越严重
    mapping = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    return mapping.get(level or "P3", 3)


def should_send_alert(
    db: Session,
    room_id: str,
    category_l1: str,
    category_l2: str,
    severity: str | None,
    risk_score: int,
    is_alert: bool,
    is_bug: bool,
) -> tuple[bool, str, AlertEvent | None]:
    alert_level = compute_alert_level(severity, risk_score)
    if not (is_alert or severity in {"S2", "S3", "S4"} or (is_bug and risk_score >= 80)):
        return False, alert_level, None

    # 去重 key 不再包含 alert_level，避免“同一问题因严重度/抖动导致重复推送”
    # 去重 key 仅用 room_id，避免同群同问题因 LLM 分类不同而绕过去重
    dedup_key_base = f"{room_id}"
    # 兼容旧记录格式
    legacy_key_1 = f"{room_id}:{category_l1}:{category_l2}"
    legacy_key_2 = f"{room_id}:{category_l1}:{category_l2}:{alert_level}"
    latest = (
        db.query(AlertEvent)
        .filter(AlertEvent.dedup_key.in_([dedup_key_base, legacy_key_1, legacy_key_2]))
        .order_by(AlertEvent.last_sent_at.desc())
        .first()
    )

    now = datetime.utcnow()
    # 升级策略：同一去重 key 下，如果新等级更严重，允许立即推送一次
    if latest and latest.alert_level and _level_rank(alert_level) < _level_rank(latest.alert_level):
        latest.alert_level = alert_level
        latest.last_seen_at = now
        latest.hit_count = (latest.hit_count or 1) + 1
        latest.last_sent_at = now
        latest.dedup_key = dedup_key_base
        db.commit()
        return True, alert_level, latest

    if latest and latest.last_sent_at:
        window = timedelta(seconds=_dedup_window_seconds(alert_level))
        if now - latest.last_sent_at < window:
            latest.hit_count = (latest.hit_count or 1) + 1
            latest.last_seen_at = now
            latest.alert_level = alert_level
            latest.dedup_key = dedup_key_base
            db.commit()
            if latest.hit_count < settings.ALERT_MIN_HITS_TO_SEND:
                return False, alert_level, latest
            latest.last_sent_at = now
            db.commit()
            return True, alert_level, latest

    if latest and latest.last_sent_at is None:
        latest.hit_count = (latest.hit_count or 0) + 1
        latest.last_seen_at = now
        latest.alert_level = alert_level
        latest.dedup_key = dedup_key_base
        if latest.hit_count >= settings.ALERT_MIN_HITS_TO_SEND:
            latest.last_sent_at = now
            db.commit()
            return True, alert_level, latest
        db.commit()
        return False, alert_level, latest

    if not latest:
        latest = AlertEvent(
            room_id=room_id,
            alert_level=alert_level,
            dedup_key=dedup_key_base,
            hit_count=1,
            first_seen_at=now,
            last_seen_at=now,
            last_sent_at=None,
        )
        if settings.ALERT_MIN_HITS_TO_SEND <= 1:
            latest.last_sent_at = now
        db.add(latest)
        db.commit()
        return (latest.last_sent_at is not None), alert_level, latest

    return False, alert_level, latest


def build_aggregate_summary(
    db: Session,
    room_id: str,
    category_l1: str,
    category_l2: str,
    since_time: datetime | None,
    limit: int,
) -> str:
    query = db.query(Issue).filter(Issue.room_id == room_id)
    if category_l1:
        query = query.filter(Issue.category_l1 == category_l1)
    if category_l2:
        query = query.filter(Issue.category_l2 == category_l2)
    if since_time:
        query = query.filter(Issue.created_at >= since_time)
    items = query.order_by(Issue.created_at.desc()).limit(limit).all()
    summaries = [f"- {i.summary}" for i in items]
    return "\n".join(summaries) if summaries else ""
