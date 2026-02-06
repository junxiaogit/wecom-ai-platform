from fastapi import FastAPI
import asyncio
from loguru import logger
from app.api.endpoints import router as api_router
from app.core.config import settings
from app.core.database import init_db
from app.services.polling_service import polling_loop
from app.services.half_day_review_service import run_scheduled_review, half_day_review_service
from app.tasks.scheduled_reports import run_scheduled_reports, run_report_once, run_end_of_cycle_task, run_end_of_cycle_once

app = FastAPI(
    title=settings.PROJECT_NAME,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    redoc_url="/api/redoc",
)
app.include_router(api_router, prefix="/api")


@app.on_event("startup")
def on_startup():
    init_db()
    logger.info(
        f"Teambition mode: {settings.TEAMBITION_MODE}, auto_create={settings.TEAMBITION_AUTO_CREATE}"
    )
    
    # 原有的实时轮询
    if settings.POLLING_ENABLED:
        logger.info(f"轮询服务已启用，间隔: {settings.POLLING_INTERVAL_SECONDS}秒")
        asyncio.create_task(polling_loop())
    else:
        logger.warning("轮询服务未启用 (POLLING_ENABLED=false)")
    
    # 新增：半日高频复盘定时任务
    if settings.HALF_DAY_REVIEW_ENABLED:
        logger.info(
            f"半日复盘模式已启用，执行时间: {settings.HALF_DAY_REVIEW_SCHEDULE}, "
            f"窗口: {settings.HALF_DAY_REVIEW_INTERVAL_HOURS}小时"
        )
        asyncio.create_task(run_scheduled_review())
    
    # 新增：定时报表任务（日报/周报/月报）
    if settings.REPORT_ENABLED:
        logger.info(
            f"定时报表已启用，发送时间: {settings.REPORT_SEND_HOUR}:00, "
            f"日报={settings.DAILY_REPORT_ENABLED}, "
            f"周报={settings.WEEKLY_REPORT_ENABLED}, "
            f"月报={settings.MONTHLY_REPORT_ENABLED}"
        )
        asyncio.create_task(run_scheduled_reports())
    
    # 新增：周期结束兜底分析任务
    if settings.END_OF_CYCLE_ANALYSIS_ENABLED:
        logger.info(
            f"周期结束兜底分析已启用，执行时间: {settings.END_OF_CYCLE_ANALYSIS_HOUR}:{settings.END_OF_CYCLE_ANALYSIS_MINUTE:02d}, "
            f"最低消息数: {settings.END_OF_CYCLE_MIN_MESSAGES}"
        )
        asyncio.create_task(run_end_of_cycle_task())


@app.get("/api/v1/review/trigger")
async def trigger_review(window_hours: int = 12):
    """
    手动触发半日复盘（测试/调试用）
    """
    from app.services.dingtalk_service import DingTalkService
    
    result = await half_day_review_service.run_review(window_hours=window_hours)
    
    # 推送到钉钉
    if result.status == "success" and result.reports:
        for report in result.reports:
            DingTalkService.send_review_report(report)
    
    return {
        "status": result.status,
        "message": result.message,
        "room_count": result.room_count,
        "total_messages": result.total_messages,
        "reports_summary": [
            {
                "room_name": r.room_name,
                "summary": r.summary,
                "total": r.stats.total_count,
                "high_risk": r.stats.high_risk_count,
            }
            for r in result.reports
        ] if result.reports else [],
    }


@app.get("/api/v1/review/preview")
async def preview_review(window_hours: int = 12):
    """
    预览半日复盘结果（不推送钉钉）
    """
    result = await half_day_review_service.run_review(window_hours=window_hours)
    
    return {
        "status": result.status,
        "message": result.message,
        "room_count": result.room_count,
        "total_messages": result.total_messages,
        "start_time": result.start_time,
        "end_time": result.end_time,
        "reports": [
            {
                "room_id": r.room_id,
                "room_name": r.room_name,
                "summary": r.summary,
                "stats": {
                    "total_count": r.stats.total_count,
                    "dimension_counts": r.stats.dimension_counts,
                    "avg_risk_score": r.stats.avg_risk_score,
                    "high_risk_count": r.stats.high_risk_count,
                    "emotion_distribution": r.stats.emotion_distribution,
                },
                "items": [
                    {
                        "dimension": item.dimension,
                        "readable_desc": item.readable_desc,
                        "emotion": item.emotion_level,
                        "risk_score": item.risk_score,
                        "action": item.action,
                    }
                    for item in r.items[:10]  # 只返回前10条
                ],
                "risk_alerts": [
                    {
                        "quote": alert.original_quote[:50],
                        "risk_score": alert.risk_score,
                        "reason": alert.reason,
                    }
                    for alert in r.risk_alerts
                ],
            }
            for r in result.reports
        ] if result.reports else [],
    }


@app.get("/api/v1/polling/status")
async def get_polling_status():
    """
    获取轮询状态和数据库消息统计
    """
    from datetime import datetime
    from sqlalchemy import func
    from app.core.database import SessionLocal
    from app.models.chat_record import ChatRecord
    from app.models.sql_models import IngestState
    
    db = SessionLocal()
    try:
        # 获取当前 last_msgtime（SOURCE_KEY = "chat_records"）
        state = db.query(IngestState).filter(IngestState.source == "chat_records").first()
        last_msgtime = int(state.last_msgtime) if state else 0
        
        # 获取 chat_records 表的消息统计
        total_count = db.query(func.count(ChatRecord.id)).scalar() or 0
        max_msgtime = db.query(func.max(ChatRecord.msgtime)).scalar() or 0
        min_msgtime = db.query(func.min(ChatRecord.msgtime)).scalar() or 0
        
        # 查询比 last_msgtime 更新的消息数
        new_count = db.query(func.count(ChatRecord.id)).filter(
            ChatRecord.msgtime > last_msgtime
        ).scalar() or 0
        
        return {
            "status": "ok",
            "last_msgtime": last_msgtime,
            "last_msgtime_readable": datetime.fromtimestamp(last_msgtime / 1000).isoformat() if last_msgtime else None,
            "chat_records": {
                "total_count": total_count,
                "max_msgtime": max_msgtime,
                "max_msgtime_readable": datetime.fromtimestamp(max_msgtime / 1000).isoformat() if max_msgtime else None,
                "min_msgtime": min_msgtime,
                "new_messages_count": new_count,
            },
        }
    finally:
        db.close()


@app.post("/api/v1/polling/reset")
async def reset_polling_progress(reset_to: int = 0):
    """
    重置轮询进度（用于重新处理历史消息）
    
    Args:
        reset_to: 重置到的时间戳（毫秒），默认为0（从头开始）
    """
    from app.core.database import SessionLocal
    from app.models.sql_models import IngestState
    
    db = SessionLocal()
    try:
        state = db.query(IngestState).filter(IngestState.source == "chat_records").first()
        if state:
            old_value = state.last_msgtime
            state.last_msgtime = reset_to
            db.commit()
            return {
                "status": "ok",
                "message": f"已重置 last_msgtime: {old_value} -> {reset_to}",
            }
        else:
            # 如果不存在，创建一个
            from app.models.sql_models import IngestState as IS
            new_state = IS(source="chat_records", last_msgtime=reset_to)
            db.add(new_state)
            db.commit()
            return {
                "status": "ok",
                "message": f"已创建并设置 last_msgtime: {reset_to}",
            }
    finally:
        db.close()


@app.get("/api/v1/backfill/platform/stats")
async def get_platform_backfill_stats():
    """
    获取 platform 字段的统计信息
    """
    from scripts.backfill_platform import get_platform_stats
    
    stats = get_platform_stats()
    return {
        "status": "ok",
        "stats": stats,
    }


@app.post("/api/v1/backfill/platform")
async def backfill_platform(limit: int = 0, force: bool = False):
    """
    回填历史工单的 platform 字段
    
    Args:
        limit: 处理数量限制，0 表示不限制
        force: 是否强制重新分析所有工单（即使已有 platform）
    """
    from scripts.backfill_platform import backfill_all_tickets
    
    result = await backfill_all_tickets(limit=limit, force=force)
    return {
        "status": "ok",
        "result": result,
    }


@app.get("/api/v1/report/trigger")
async def trigger_report(report_type: str = "daily"):
    """
    手动触发报表发送（测试/调试用）
    
    Args:
        report_type: 报表类型 (daily/weekly/monthly)
    """
    from app.services.report_service import (
        get_daily_report_data,
        get_weekly_report_data,
        get_monthly_report_data,
    )
    
    if report_type == "daily":
        stats, markdown = get_daily_report_data()
    elif report_type == "weekly":
        stats, markdown = get_weekly_report_data()
    elif report_type == "monthly":
        stats, markdown = get_monthly_report_data()
    else:
        return {"status": "error", "message": f"未知报表类型: {report_type}"}
    
    # 发送报表
    await run_report_once(report_type)
    
    return {
        "status": "success",
        "report_type": report_type,
        "stats": {
            "msg_count": stats["msg_count"],
            "room_count": stats["room_count"],
            "issue_count": stats["issue_count"],
            "ticket_count": stats["ticket_count"],
            "issue_type_stats": stats["issue_type_stats"],
            "priority_stats": stats["priority_stats"],
        },
    }


@app.get("/api/v1/report/preview")
async def preview_report(report_type: str = "daily"):
    """
    预览报表内容（不发送钉钉）
    
    Args:
        report_type: 报表类型 (daily/weekly/monthly)
    """
    from app.services.report_service import (
        get_daily_report_data,
        get_weekly_report_data,
        get_monthly_report_data,
    )
    
    if report_type == "daily":
        stats, markdown = get_daily_report_data()
    elif report_type == "weekly":
        stats, markdown = get_weekly_report_data()
    elif report_type == "monthly":
        stats, markdown = get_monthly_report_data()
    else:
        return {"status": "error", "message": f"未知报表类型: {report_type}"}
    
    return {
        "status": "success",
        "report_type": report_type,
        "stats": {
            "since": stats["since"].isoformat() if stats.get("since") else None,
            "until": stats["until"].isoformat() if stats.get("until") else None,
            "msg_count": stats["msg_count"],
            "room_count": stats["room_count"],
            "issue_count": stats["issue_count"],
            "ticket_count": stats["ticket_count"],
            "issue_type_stats": stats["issue_type_stats"],
            "priority_stats": stats["priority_stats"],
            "top_rooms": stats["top_rooms"],
        },
        "markdown": markdown,
    }


@app.get("/api/v1/end-of-cycle/trigger")
async def trigger_end_of_cycle_analysis():
    """
    手动触发周期结束兜底分析（测试/调试用）
    
    对未达正常阈值（10条）但有累积消息（>=3条）的群聊进行 LLM 分析。
    """
    result = await run_end_of_cycle_once()
    
    return {
        "status": "success",
        "result": result,
    }


@app.get("/api/v1/end-of-cycle/preview")
async def preview_end_of_cycle_analysis():
    """
    预览周期结束兜底分析的待处理群聊（不执行分析）
    
    返回满足条件的群聊列表及其累积消息数。
    """
    from app.services.polling_service import _room_state
    
    qualifying_rooms = []
    for room_id, state in _room_state.items():
        pending_count = state.get("pending_count", 0)
        if (
            pending_count >= settings.END_OF_CYCLE_MIN_MESSAGES
            and pending_count < settings.ROOM_MIN_MESSAGES_FOR_ANALYZE
        ):
            qualifying_rooms.append({
                "room_id": room_id,
                "pending_count": pending_count,
                "last_msgtime": state.get("last_msgtime", 0),
            })
    
    # 按 pending_count 降序排列
    qualifying_rooms.sort(key=lambda x: x["pending_count"], reverse=True)
    
    return {
        "status": "success",
        "config": {
            "min_messages": settings.END_OF_CYCLE_MIN_MESSAGES,
            "normal_threshold": settings.ROOM_MIN_MESSAGES_FOR_ANALYZE,
            "analysis_time": f"{settings.END_OF_CYCLE_ANALYSIS_HOUR}:{settings.END_OF_CYCLE_ANALYSIS_MINUTE:02d}",
        },
        "total_rooms": len(qualifying_rooms),
        "rooms": qualifying_rooms[:50],  # 最多返回50个
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
