from datetime import datetime
from sqlalchemy.orm import Session
from app.models.sql_models import Issue, IssueAggregation


def update_issue_aggregation(
    db: Session,
    issue: Issue,
    *,
    is_hard: bool,
    is_alert: bool,
) -> None:
    if not issue:
        return
    today = datetime.utcnow().date()
    row = (
        db.query(IssueAggregation)
        .filter(IssueAggregation.date == today)
        .filter(IssueAggregation.room_id == issue.room_id)
        .filter(IssueAggregation.category_l1 == issue.category_l1)
        .filter(IssueAggregation.category_l2 == issue.category_l2)
        .filter(IssueAggregation.issue_type == issue.issue_type)
        .filter(IssueAggregation.severity == issue.severity)
        .first()
    )
    if not row:
        row = IssueAggregation(
            date=today,
            room_id=issue.room_id,
            category_l1=issue.category_l1,
            category_l2=issue.category_l2,
            issue_type=issue.issue_type,
            severity=issue.severity,
            total_count=0,
            hard_count=0,
            alert_count=0,
            risk_sum=0,
        )
        db.add(row)
        db.flush()

    row.total_count = int(row.total_count or 0) + 1
    row.risk_sum = int(row.risk_sum or 0) + int(issue.risk_score or 0)
    if is_hard:
        row.hard_count = int(row.hard_count or 0) + 1
    if is_alert:
        row.alert_count = int(row.alert_count or 0) + 1
    db.commit()
