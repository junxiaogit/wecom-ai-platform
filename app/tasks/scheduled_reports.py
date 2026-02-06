# -*- coding: utf-8 -*-
"""
定时报表任务调度

实现日报、周报、月报的定时发送功能。
- 日报：每天指定时间发送（默认9点）
- 周报：每周一指定时间发送
- 月报：每月1号指定时间发送

以及周期结束兜底分析任务：
- 在每日周期结束前（默认8:30）对未达阈值的群聊进行分析
"""

import asyncio
from datetime import datetime
from loguru import logger

from app.core.config import settings
from app.services.report_service import (
    get_daily_report_data,
    get_weekly_report_data,
    get_monthly_report_data,
)
from app.services.dingtalk_service import DingTalkService


async def send_daily_report():
    """发送日报"""
    try:
        logger.info("[定时报表] 开始生成日报...")
        stats, markdown = get_daily_report_data()
        
        if stats["issue_count"] == 0 and stats["msg_count"] == 0:
            logger.info("[定时报表] 日报无数据，跳过发送")
            return
        
        DingTalkService.send_report("日报", markdown)
        logger.info(
            f"[定时报表] 日报发送成功: "
            f"消息={stats['msg_count']}, 群聊={stats['room_count']}, "
            f"反馈={stats['issue_count']}, 工单={stats['ticket_count']}"
        )
    except Exception as e:
        logger.error(f"[定时报表] 日报发送失败: {e}")


async def send_weekly_report():
    """发送周报"""
    try:
        logger.info("[定时报表] 开始生成周报...")
        stats, markdown = get_weekly_report_data()
        
        if stats["issue_count"] == 0 and stats["msg_count"] == 0:
            logger.info("[定时报表] 周报无数据，跳过发送")
            return
        
        DingTalkService.send_report("周报", markdown)
        logger.info(
            f"[定时报表] 周报发送成功: "
            f"消息={stats['msg_count']}, 群聊={stats['room_count']}, "
            f"反馈={stats['issue_count']}, 工单={stats['ticket_count']}"
        )
    except Exception as e:
        logger.error(f"[定时报表] 周报发送失败: {e}")


async def send_monthly_report():
    """发送月报"""
    try:
        logger.info("[定时报表] 开始生成月报...")
        stats, markdown = get_monthly_report_data()
        
        if stats["issue_count"] == 0 and stats["msg_count"] == 0:
            logger.info("[定时报表] 月报无数据，跳过发送")
            return
        
        DingTalkService.send_report("月报", markdown)
        logger.info(
            f"[定时报表] 月报发送成功: "
            f"消息={stats['msg_count']}, 群聊={stats['room_count']}, "
            f"反馈={stats['issue_count']}, 工单={stats['ticket_count']}"
        )
    except Exception as e:
        logger.error(f"[定时报表] 月报发送失败: {e}")


async def run_scheduled_reports():
    """
    定时报表任务主循环
    
    每分钟检查一次是否到达发送时间：
    - 日报：每天 REPORT_SEND_HOUR 点发送
    - 周报：每周一 REPORT_SEND_HOUR 点发送
    - 月报：每月1号 REPORT_SEND_HOUR 点发送
    """
    logger.info(
        f"[定时报表] 任务启动, 发送时间={settings.REPORT_SEND_HOUR}:00, "
        f"日报={settings.DAILY_REPORT_ENABLED}, "
        f"周报={settings.WEEKLY_REPORT_ENABLED}, "
        f"月报={settings.MONTHLY_REPORT_ENABLED}"
    )
    
    last_check_hour = -1
    
    while True:
        try:
            now = datetime.now()
            current_hour = now.hour
            current_minute = now.minute
            
            # 只在整点（分钟=0）且当前小时为设定时间时检查
            # 使用 last_check_hour 避免同一小时内重复发送
            if (
                current_minute == 0
                and current_hour == settings.REPORT_SEND_HOUR
                and current_hour != last_check_hour
            ):
                last_check_hour = current_hour
                logger.info(f"[定时报表] 到达发送时间: {now.strftime('%Y-%m-%d %H:%M')}")
                
                # 日报：每天发送
                if settings.DAILY_REPORT_ENABLED:
                    await send_daily_report()
                
                # 周报：周一发送（weekday() == 0 表示周一）
                if settings.WEEKLY_REPORT_ENABLED and now.weekday() == 0:
                    await send_weekly_report()
                
                # 月报：1号发送
                if settings.MONTHLY_REPORT_ENABLED and now.day == 1:
                    await send_monthly_report()
            
            # 每小时重置检查标记（允许下一个整点再次触发）
            if current_minute != 0:
                last_check_hour = -1
            
        except Exception as e:
            logger.error(f"[定时报表] 任务循环异常: {e}")
        
        # 每30秒检查一次（避免错过整点）
        await asyncio.sleep(30)


async def run_report_once(report_type: str = "daily"):
    """
    手动触发一次报表发送（用于测试）
    
    Args:
        report_type: 报表类型 (daily/weekly/monthly)
    """
    if report_type == "daily":
        await send_daily_report()
    elif report_type == "weekly":
        await send_weekly_report()
    elif report_type == "monthly":
        await send_monthly_report()
    else:
        logger.warning(f"[定时报表] 未知报表类型: {report_type}")


# ============ 周期结束兜底分析任务 ============

async def run_end_of_cycle_task():
    """
    周期结束兜底分析定时任务
    
    在每日 END_OF_CYCLE_ANALYSIS_HOUR:END_OF_CYCLE_ANALYSIS_MINUTE（默认 8:30）执行，
    对未达正常阈值（10条）但有累积消息（>=3条）的群聊进行分析。
    
    这确保低活跃群聊的问题不会在周期重置时被丢弃。
    """
    # 延迟导入，避免循环依赖
    from app.services.polling_service import run_end_of_cycle_analysis
    
    logger.info(
        f"[兜底分析任务] 启动, 执行时间={settings.END_OF_CYCLE_ANALYSIS_HOUR}:{settings.END_OF_CYCLE_ANALYSIS_MINUTE:02d}, "
        f"最低消息数={settings.END_OF_CYCLE_MIN_MESSAGES}"
    )
    
    # 记录上次执行的日期，防止同一天重复执行
    last_run_date: str = ""
    
    while True:
        try:
            now = datetime.now()
            current_hour = now.hour
            current_minute = now.minute
            current_date = now.strftime("%Y-%m-%d")
            
            # 检查是否到达执行时间
            if (
                current_hour == settings.END_OF_CYCLE_ANALYSIS_HOUR
                and current_minute == settings.END_OF_CYCLE_ANALYSIS_MINUTE
                and current_date != last_run_date
            ):
                logger.info(f"[兜底分析任务] 到达执行时间: {now.strftime('%Y-%m-%d %H:%M')}")
                
                # 执行兜底分析
                result = await run_end_of_cycle_analysis()
                
                # 记录执行日期
                last_run_date = current_date
                
                logger.info(
                    f"[兜底分析任务] 执行完成: "
                    f"总计={result['total_rooms']}, 分析={result['analyzed_count']}, "
                    f"预判断跳过={result['skipped_pre_judge']}, 去重跳过={result['skipped_duplicate']}"
                )
            
        except Exception as e:
            logger.error(f"[兜底分析任务] 循环异常: {e}")
            import traceback
            traceback.print_exc()
        
        # 每30秒检查一次（避免错过指定时间）
        await asyncio.sleep(30)


async def run_end_of_cycle_once():
    """
    手动触发一次兜底分析（用于测试）
    """
    from app.services.polling_service import run_end_of_cycle_analysis
    return await run_end_of_cycle_analysis()
