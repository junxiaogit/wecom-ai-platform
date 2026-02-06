# -*- coding: utf-8 -*-
"""
报表统计服务

提供日报、周报、月报的统计数据查询和格式化功能。
- 日报：每天9点，统计过去24小时
- 周报：每周一9点，统计过去7天
- 月报：每月1号9点，统计上个自然月
"""

from datetime import datetime, timedelta
from calendar import monthrange
from typing import Dict, List, Tuple, Optional
from sqlalchemy import func
from sqlalchemy.orm import Session
from loguru import logger

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.sql_models import WeComMessage, Issue, TicketDraft, RoomInfo


class ReportService:
    """报表统计服务"""

    # 问题类型列表
    ISSUE_TYPES = ["使用咨询", "问题反馈", "产品需求", "产品缺陷"]

    # 优先级映射（基于 risk_score）
    PRIORITY_THRESHOLDS = [
        (80, "非常紧急", "🔴"),
        (60, "紧急", "🟡"),
        (30, "普通", "🔵"),
        (0, "较低", "⚪"),
    ]

    @staticmethod
    def get_db() -> Session:
        """获取数据库会话"""
        return SessionLocal()

    @classmethod
    def get_report_stats(
        cls,
        since: datetime,
        until: datetime,
        db: Optional[Session] = None,
    ) -> Dict:
        """
        获取指定时间范围内的统计数据
        
        Args:
            since: 开始时间
            until: 结束时间
            db: 数据库会话（可选）
        
        Returns:
            统计数据字典
        """
        close_db = False
        if db is None:
            db = cls.get_db()
            close_db = True

        try:
            stats = {
                "since": since,
                "until": until,
                "msg_count": 0,
                "room_count": 0,
                "issue_count": 0,
                "ticket_count": 0,
                "issue_type_stats": {},
                "priority_stats": {},
                "platform_stats": {},
                "top_rooms": [],
            }

            # 1. 消息统计（指定时间范围内，所有 text 消息，含噪音）
            stats["msg_count"] = (
                db.query(func.count(WeComMessage.msg_id))
                .filter(
                    WeComMessage.msg_type == "text",
                    WeComMessage.msg_time >= since,
                    WeComMessage.msg_time < until,
                )
                .scalar() or 0
            )

            # 2. 活跃群聊数（指定时间范围内）
            stats["room_count"] = (
                db.query(func.count(func.distinct(WeComMessage.room_id)))
                .filter(
                    WeComMessage.is_noise == False,
                    WeComMessage.msg_time >= since,
                    WeComMessage.msg_time < until,
                )
                .scalar() or 0
            )

            # 3-6. 统计已建单的工单（只统计成功创建到 TB 的工单）
            created_tickets = (
                db.query(TicketDraft)
                .filter(
                    TicketDraft.created_at >= since,
                    TicketDraft.created_at < until,
                    TicketDraft.teambition_ticket_id != None,
                )
                .all()
            )
            
            stats["ticket_count"] = len(created_tickets)
            stats["issue_count"] = len(created_tickets)  # 问题反馈数 = 已建单数
            
            # 4. 问题类型分布、优先级分布、端口分布（基于已建单的工单）
            issue_type_stats = {}
            priority_stats = {"非常紧急": 0, "紧急": 0, "普通": 0, "较低": 0}
            platform_stats = {"CBS": 0, "客户端": 0, "ROM": 0, "移动端": 0, "其他": 0}
            
            for ticket in created_tickets:
                # 从 content JSON 中获取 issue_type 和 platform
                content = ticket.content or {}
                issue_type = content.get("issue_type", "问题反馈")
                issue_type_stats[issue_type] = issue_type_stats.get(issue_type, 0) + 1
                
                # 端口分布统计
                platform = content.get("platform", "其他")
                if platform in platform_stats:
                    platform_stats[platform] += 1
                else:
                    platform_stats["其他"] += 1
                
                # 从关联的 Issue 获取 risk_score，或从 content 中获取 priority
                if ticket.issue_id:
                    issue = db.query(Issue).filter(Issue.issue_id == ticket.issue_id).first()
                    if issue:
                        priority = cls._risk_to_priority(issue.risk_score or 0)
                        priority_stats[priority] += 1
                        continue
                
                # 备选：从 content 中获取 priority
                priority_str = content.get("priority", "普通")
                if priority_str in priority_stats:
                    priority_stats[priority_str] += 1
                else:
                    priority_stats["普通"] += 1
            
            stats["issue_type_stats"] = issue_type_stats
            stats["priority_stats"] = priority_stats
            stats["platform_stats"] = platform_stats

            # 7. 工单汇总列表（群名 + 标题 + 类型）
            ticket_summaries = []
            for ticket in created_tickets:
                content = ticket.content or {}
                room_id = ticket.room_id or ""
                # 查群名
                room_info = db.query(RoomInfo).filter(RoomInfo.room_id == room_id).first() if room_id else None
                room_name = room_info.room_name if room_info else (room_id[:20] if room_id else "未知群聊")
                # 取 TB 建单标题（优先 llm_title，其次 title）
                title = content.get("llm_title") or content.get("title") or ticket.title or "未知问题"
                issue_type = content.get("issue_type", "问题反馈")
                ticket_summaries.append({
                    "room_name": room_name,
                    "title": title,
                    "issue_type": issue_type,
                })
            stats["ticket_summaries"] = ticket_summaries

            return stats

        except Exception as e:
            logger.error(f"获取报表统计数据失败: {e}")
            return stats
        finally:
            if close_db:
                db.close()

    @classmethod
    def _risk_to_priority(cls, risk_score: int) -> str:
        """将 risk_score 转换为优先级"""
        for threshold, priority, _ in cls.PRIORITY_THRESHOLDS:
            if risk_score >= threshold:
                return priority
        return "较低"

    @classmethod
    def _get_priority_icon(cls, priority: str) -> str:
        """获取优先级图标"""
        for _, p, icon in cls.PRIORITY_THRESHOLDS:
            if p == priority:
                return icon
        return "⚪"

    @classmethod
    def format_daily_report(cls, stats: Dict) -> str:
        """
        格式化日报
        
        Args:
            stats: 统计数据
        
        Returns:
            格式化的 Markdown 文本
        """
        date_str = stats["since"].strftime("%Y-%m-%d")
        
        # 计算问题类型百分比
        total_issues = stats["issue_count"] or 1  # 避免除零
        
        issue_type_lines = []
        for issue_type in cls.ISSUE_TYPES:
            count = stats["issue_type_stats"].get(issue_type, 0)
            percent = round(count / total_issues * 100) if total_issues > 0 else 0
            issue_type_lines.append(f"- {issue_type}：{count} 条 ({percent}%)")
        
        priority_lines = []
        for _, priority, icon in cls.PRIORITY_THRESHOLDS:
            count = stats["priority_stats"].get(priority, 0)
            priority_lines.append(f"- {icon} {priority}：{count} 条")
        
        # 端口分布
        platform_order = ["CBS", "客户端", "ROM", "移动端", "其他"]
        platform_icons = {"CBS": "🖥", "客户端": "💻", "ROM": "📱", "移动端": "📲", "其他": "📋"}
        platform_lines = []
        platform_stats = stats.get("platform_stats", {})
        for platform in platform_order:
            count = platform_stats.get(platform, 0)
            percent = round(count / total_issues * 100) if total_issues > 0 else 0
            icon = platform_icons.get(platform, "📋")
            platform_lines.append(f"- {icon} {platform}：{count} 条 ({percent}%)")
        
        # 工单汇总列表
        ticket_summaries = stats.get("ticket_summaries", [])
        summary_lines = []
        for i, t in enumerate(ticket_summaries, 1):
            summary_lines.append(f"{i}. （{t['issue_type']}）{t['title']}")
        
        markdown = f"""### 📊 用户反馈日报

**📅 统计时间**：{date_str}

**【📈 数据概览】**

- 消息总数：{stats['msg_count']} 条
- 活跃群聊：{stats['room_count']} 个
- 问题反馈：{stats['issue_count']} 条
- 工单创建：{stats['ticket_count']} 个

**【🏷 问题类型分布】**

{chr(10).join(issue_type_lines)}

**【⚡ 优先级分布】**

{chr(10).join(priority_lines)}

**【💻 端口分布】**

{chr(10).join(platform_lines)}
"""
        
        if summary_lines:
            markdown += f"""
**【🔥 工单汇总】**

{chr(10).join(summary_lines)}
"""
        
        return markdown

    @classmethod
    def format_weekly_report(
        cls,
        stats: Dict,
        prev_stats: Optional[Dict] = None,
    ) -> str:
        """
        格式化周报
        
        Args:
            stats: 本周统计数据
            prev_stats: 上周统计数据（用于计算环比）
        
        Returns:
            格式化的 Markdown 文本
        """
        start_date = stats["since"].strftime("%Y-%m-%d")
        end_date = (stats["until"] - timedelta(days=1)).strftime("%Y-%m-%d")
        
        # 计算环比变化
        def calc_change(current: int, previous: int) -> str:
            if previous == 0:
                return "+100%" if current > 0 else "持平"
            change = round((current - previous) / previous * 100)
            if change > 0:
                return f"↑{change}%"
            elif change < 0:
                return f"↓{abs(change)}%"
            return "持平"
        
        msg_change = ""
        room_change = ""
        issue_change = ""
        ticket_change = ""
        
        if prev_stats:
            msg_change = f"（较上周 {calc_change(stats['msg_count'], prev_stats['msg_count'])}）"
            room_change = f"（较上周 {calc_change(stats['room_count'], prev_stats['room_count'])}）"
            issue_change = f"（较上周 {calc_change(stats['issue_count'], prev_stats['issue_count'])}）"
            ticket_change = f"（较上周 {calc_change(stats['ticket_count'], prev_stats['ticket_count'])}）"
        
        # 计算问题类型百分比
        total_issues = stats["issue_count"] or 1
        
        issue_type_lines = []
        for issue_type in cls.ISSUE_TYPES:
            count = stats["issue_type_stats"].get(issue_type, 0)
            percent = round(count / total_issues * 100) if total_issues > 0 else 0
            issue_type_lines.append(f"- {issue_type}：{count} 条 ({percent}%)")
        
        priority_lines = []
        for _, priority, icon in cls.PRIORITY_THRESHOLDS:
            count = stats["priority_stats"].get(priority, 0)
            priority_lines.append(f"- {icon} {priority}：{count} 条")
        
        # 端口分布
        platform_order = ["CBS", "客户端", "ROM", "移动端", "其他"]
        platform_icons = {"CBS": "🖥", "客户端": "💻", "ROM": "📱", "移动端": "📲", "其他": "📋"}
        platform_lines = []
        platform_stats = stats.get("platform_stats", {})
        for platform in platform_order:
            count = platform_stats.get(platform, 0)
            percent = round(count / total_issues * 100) if total_issues > 0 else 0
            icon = platform_icons.get(platform, "📋")
            platform_lines.append(f"- {icon} {platform}：{count} 条 ({percent}%)")
        
        markdown = f"""### 📊 用户反馈周报

**📅 统计时间**：{start_date} ~ {end_date}

**【📈 本周概览】**

- 消息总数：{stats['msg_count']} 条{msg_change}
- 活跃群聊：{stats['room_count']} 个{room_change}
- 问题反馈：{stats['issue_count']} 条{issue_change}
- 工单创建：{stats['ticket_count']} 个{ticket_change}

**【🏷 问题类型分布】**

{chr(10).join(issue_type_lines)}

**【⚡ 优先级分布】**

{chr(10).join(priority_lines)}

**【💻 端口分布】**

{chr(10).join(platform_lines)}
"""
        
        return markdown

    @classmethod
    def format_monthly_report(
        cls,
        stats: Dict,
        prev_stats: Optional[Dict] = None,
    ) -> str:
        """
        格式化月报
        
        Args:
            stats: 本月统计数据
            prev_stats: 上月统计数据（用于计算环比）
        
        Returns:
            格式化的 Markdown 文本
        """
        year = stats["since"].year
        month = stats["since"].month
        
        # 计算环比变化
        def calc_change(current: int, previous: int) -> str:
            if previous == 0:
                return "+100%" if current > 0 else "持平"
            change = round((current - previous) / previous * 100)
            if change > 0:
                return f"↑{change}%"
            elif change < 0:
                return f"↓{abs(change)}%"
            return "持平"
        
        msg_change = ""
        room_change = ""
        issue_change = ""
        ticket_change = ""
        
        if prev_stats:
            msg_change = f"（较上月 {calc_change(stats['msg_count'], prev_stats['msg_count'])}）"
            room_change = f"（较上月 {calc_change(stats['room_count'], prev_stats['room_count'])}）"
            issue_change = f"（较上月 {calc_change(stats['issue_count'], prev_stats['issue_count'])}）"
            ticket_change = f"（较上月 {calc_change(stats['ticket_count'], prev_stats['ticket_count'])}）"
        
        # 计算问题类型百分比
        total_issues = stats["issue_count"] or 1
        
        issue_type_lines = []
        for issue_type in cls.ISSUE_TYPES:
            count = stats["issue_type_stats"].get(issue_type, 0)
            percent = round(count / total_issues * 100) if total_issues > 0 else 0
            issue_type_lines.append(f"- {issue_type}：{count} 条 ({percent}%)")
        
        priority_lines = []
        for _, priority, icon in cls.PRIORITY_THRESHOLDS:
            count = stats["priority_stats"].get(priority, 0)
            priority_lines.append(f"- {icon} {priority}：{count} 条")
        
        # 端口分布
        platform_order = ["CBS", "客户端", "ROM", "移动端", "其他"]
        platform_icons = {"CBS": "🖥", "客户端": "💻", "ROM": "📱", "移动端": "📲", "其他": "📋"}
        platform_lines = []
        platform_stats = stats.get("platform_stats", {})
        for platform in platform_order:
            count = platform_stats.get(platform, 0)
            percent = round(count / total_issues * 100) if total_issues > 0 else 0
            icon = platform_icons.get(platform, "📋")
            platform_lines.append(f"- {icon} {platform}：{count} 条 ({percent}%)")
        
        # 计算日均反馈量
        days_in_month = (stats["until"] - stats["since"]).days or 1
        avg_daily = round(stats["issue_count"] / days_in_month, 1)
        
        markdown = f"""### 📊 用户反馈月报

**📅 统计时间**：{year}年{month}月

**【📈 本月概览】**

- 消息总数：{stats['msg_count']} 条{msg_change}
- 活跃群聊：{stats['room_count']} 个{room_change}
- 问题反馈：{stats['issue_count']} 条{issue_change}
- 工单创建：{stats['ticket_count']} 个{ticket_change}

**【🏷 问题类型分布】**

{chr(10).join(issue_type_lines)}

**【⚡ 优先级分布】**

{chr(10).join(priority_lines)}

**【💻 端口分布】**

{chr(10).join(platform_lines)}

**【📈 趋势分析】**

- 日均反馈量：{avg_daily} 条
"""
        
        return markdown


# 便捷函数
def get_daily_report_data() -> Tuple[Dict, str]:
    """
    获取日报数据和格式化文本
    
    Returns:
        (stats, markdown_text)
    """
    now = datetime.now()
    # 统计范围：昨天 9:00 ~ 今天 9:00（与每日周期对齐）
    since = now.replace(hour=9, minute=0, second=0, microsecond=0) - timedelta(days=1)
    until = since + timedelta(days=1)
    
    stats = ReportService.get_report_stats(since, until)
    markdown = ReportService.format_daily_report(stats)
    
    return stats, markdown


def get_weekly_report_data() -> Tuple[Dict, str]:
    """
    获取周报数据和格式化文本
    
    Returns:
        (stats, markdown_text)
    """
    now = datetime.now()
    # 本周一
    this_monday = now - timedelta(days=now.weekday())
    this_monday = this_monday.replace(hour=0, minute=0, second=0, microsecond=0)
    # 上周一
    last_monday = this_monday - timedelta(days=7)
    
    # 本周统计（上周一到本周一）
    stats = ReportService.get_report_stats(last_monday, this_monday)
    
    # 上周统计（上上周一到上周一）
    prev_monday = last_monday - timedelta(days=7)
    prev_stats = ReportService.get_report_stats(prev_monday, last_monday)
    
    markdown = ReportService.format_weekly_report(stats, prev_stats)
    
    return stats, markdown


def get_monthly_report_data() -> Tuple[Dict, str]:
    """
    获取月报数据和格式化文本
    
    Returns:
        (stats, markdown_text)
    """
    now = datetime.now()
    
    # 上个月的第一天和最后一天
    if now.month == 1:
        last_month_year = now.year - 1
        last_month = 12
    else:
        last_month_year = now.year
        last_month = now.month - 1
    
    _, last_day = monthrange(last_month_year, last_month)
    
    since = datetime(last_month_year, last_month, 1)
    until = datetime(last_month_year, last_month, last_day, 23, 59, 59)
    
    stats = ReportService.get_report_stats(since, until)
    
    # 上上个月统计
    if last_month == 1:
        prev_month_year = last_month_year - 1
        prev_month = 12
    else:
        prev_month_year = last_month_year
        prev_month = last_month - 1
    
    _, prev_last_day = monthrange(prev_month_year, prev_month)
    prev_since = datetime(prev_month_year, prev_month, 1)
    prev_until = datetime(prev_month_year, prev_month, prev_last_day, 23, 59, 59)
    
    prev_stats = ReportService.get_report_stats(prev_since, prev_until)
    
    markdown = ReportService.format_monthly_report(stats, prev_stats)
    
    return stats, markdown
