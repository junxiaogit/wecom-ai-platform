import base64
import hashlib
import hmac
import time
import urllib.parse
import requests
from loguru import logger
from app.core.config import settings
from app.core.utils import not_empty


# 优先级图标映射
PRIORITY_ICONS = {
    "非常紧急": "🔴",
    "紧急": "🟡",
    "普通": "🔵",
    "较低": "⚪",
}


def get_priority_icon(priority: str) -> str:
    """根据优先级获取对应图标"""
    return PRIORITY_ICONS.get(priority, "🔵")


def risk_score_to_priority(risk_score: int) -> str:
    """将风险分数转换为优先级"""
    if risk_score >= 80:
        return "非常紧急"
    elif risk_score >= 60:
        return "紧急"
    elif risk_score >= 30:
        return "普通"
    else:
        return "较低"


class DingTalkService:
    @staticmethod
    def _post_payload(payload: dict) -> None:
        try:
            webhook = settings.DINGTALK_WEBHOOK
            if settings.DINGTALK_SECRET:
                timestamp = str(int(time.time() * 1000))
                string_to_sign = f"{timestamp}\n{settings.DINGTALK_SECRET}"
                sign = hmac.new(
                    settings.DINGTALK_SECRET.encode("utf-8"),
                    string_to_sign.encode("utf-8"),
                    digestmod=hashlib.sha256,
                ).digest()
                sign = urllib.parse.quote_plus(base64.b64encode(sign))
                webhook = f"{webhook}&timestamp={timestamp}&sign={sign}"

            resp = requests.post(webhook, json=payload, timeout=10)
            logger.info(f"钉钉推送结果: {resp.text}")
        except Exception as e:
            logger.error(f"钉钉发送失败: {e}")

    @staticmethod
    def send_alert(
        summary: str,
        risk: int,
        reason: str,
        room_id: str,
        room_name: str | None = None,
        issue_type: str | None = None,
        priority: str | None = None,  # 新增：优先级参数
        phenomenon: str | None = None,
        detail_url: str | None = None,
        ticket_url: str | None = None,
        draft_id: int | None = None,
        include_ticket_line: bool = True,
        markdown_text: str | None = None,
        # 以下参数保留兼容，但不再使用
        category: str = "",
        category_short: str | None = None,
        severity: str = "",
        assignee: str | None = None,
        hit_count: int | None = None,
        key_sentence: str | None = None,
        similar_case_cause: str | None = None,
        similar_case_solution: str | None = None,
        ai_solution: str | None = None,
        soothing_reply: str | None = None,
        draft_url: str | None = None,
        ignore_url: str | None = None,
        assign_url: str | None = None,
        suggested_reply: str | None = None,
    ):
        """
        发送钉钉告警消息（新格式：优先级+问题+总结）
        
        新格式：
        问题类型：{issue_type}
        【优先级】{priority_icon} {priority}
        【问题】: {phenomenon}
        【总结】: {summary}
        【🏠客户群】: {room_name} | {detail_url}
        """
        if not settings.DINGTALK_WEBHOOK:
            logger.warning("未配置钉钉 Webhook，跳过发送")
            return

        # 标准化参数
        issue_type_text = issue_type or "问题反馈"
        room_label = room_name or room_id
        detail_link = detail_url or f"{settings.INTERNAL_BASE_URL}/api/ui/rooms/{room_id}"
        
        # 如果没有提供 priority，从 risk_score 转换
        if not priority:
            priority = risk_score_to_priority(risk)
        
        # 获取问题描述（优先使用 phenomenon，否则使用 summary）
        trimmed_summary = summary[: settings.ALERT_SUMMARY_LEN] if summary else ""
        phenomenon_text = phenomenon if not_empty(phenomenon) else (trimmed_summary if not_empty(trimmed_summary) else "暂无")
        summary_text = key_sentence if not_empty(key_sentence) else (trimmed_summary if not_empty(trimmed_summary) else "暂无")

        # 如果没有提供 markdown_text，使用新格式构建
        if not markdown_text:
            markdown_text = DingTalkService.build_markdown(
                issue_type=issue_type_text,
                priority=priority,
                phenomenon=phenomenon_text,
                summary=summary_text,
                room_name=room_label,
                detail_url=detail_link,
            )

        # 根据优先级决定是否 @all
        is_urgent = priority in ["非常紧急", "紧急"] or risk > 80
        
        payload = {
            "msgtype": "markdown",
            "markdown": {"title": "问题提醒", "text": markdown_text},
            "at": {"isAtAll": True if (settings.DINGTALK_AT_ALL and is_urgent) else False},
        }

        DingTalkService._post_payload(payload)

    @staticmethod
    def build_markdown(
        issue_type: str,
        priority: str,
        phenomenon: str,
        summary: str,
        room_name: str,
        detail_url: str,
        issue_time: str | None = None,  # 新增：问题发生时间（已格式化，如 '2/3 09:22'）
    ) -> str:
        """
        构建钉钉推送 Markdown（新格式）
        
        格式：
        ### (🚨) 问题类型：{issue_type}
        **【优先级】** : {priority_icon} {priority}
        **【问题】**: {phenomenon}
        **【总结】**: {summary}
        **【🏠客户群】**: {room_name} | [查看详情]({detail_url})
        **【时间】**: {issue_time}
        
        注意：使用 ### 三级标题（字体较小），每行之间用双换行确保钉钉正确显示
        """
        priority_icon = get_priority_icon(priority)
        urgent_prefix = "🚨 " if priority in ("紧急", "非常紧急") else ""
        time_line = f"**【时间】**: {issue_time}\n" if issue_time else ""
        return (
            f"### {urgent_prefix}问题类型：{issue_type}\n\n"
            f"**【优先级】** : {priority_icon} {priority}\n\n"
            f"**【问题】**: {phenomenon}\n\n"
            f"**【总结】**: {summary}\n\n"
            f"**【🏠客户群】**: {room_name} | [查看详情]({detail_url})\n\n"
            f"{time_line}"
        )

    @staticmethod
    def build_markdown_legacy(
        risk: int,
        color: str,
        issue_type_text: str,
        severity: str,
        category_display: str,
        assignee: str,
        phenomenon_text: str,
        key_sentence_text: str,
        footer: str,
        draft_line: str,
    ) -> str:
        """
        构建钉钉推送 Markdown（旧格式，保留兼容）
        """
        # 将旧格式参数转换为新格式
        priority = risk_score_to_priority(risk)
        priority_icon = get_priority_icon(priority)
        urgent_prefix = "🚨 " if priority in ("紧急", "非常紧急") else ""
        return (
            f"### {urgent_prefix}问题类型：{issue_type_text}\n\n"
            f"**【优先级】** : {priority_icon} {priority}\n\n"
            f"**【问题】**: {phenomenon_text}\n\n"
            f"**【总结】**: {key_sentence_text}\n\n"
            f"{footer}"
            f"{draft_line}"
        )

    @staticmethod
    def send_ticket_update(
        draft_id: int,
        ticket_url: str,
        room_label: str | None = None,
        markdown_text: str | None = None,
    ):
        if not settings.DINGTALK_WEBHOOK:
            logger.warning("未配置钉钉 Webhook，跳过发送")
            return
        if not markdown_text:
            markdown_text = (
                "**工单已创建**\n"
                f"**🧾 草稿ID**: {draft_id}**🧾 提单**: 已建单 · [查看工单]({ticket_url})\n"
            )
        payload = {
            "msgtype": "markdown",
            "markdown": {"title": "工单已创建", "text": markdown_text},
        }
        DingTalkService._post_payload(payload)

    @staticmethod
    def send_review_report(report) -> None:
        """
        推送半日复盘报告到钉钉
        Args:
            report: RoomReviewReport 对象
        """
        if not settings.DINGTALK_WEBHOOK:
            logger.warning("未配置钉钉 Webhook，跳过发送")
            return

        markdown_text = DingTalkService.build_review_markdown(report)
        
        payload = {
            "msgtype": "markdown",
            "markdown": {
                "title": f"📊 {report.room_name} 半日复盘",
                "text": markdown_text,
            },
        }
        
        DingTalkService._post_payload(payload)
        logger.info(f"半日复盘报告已推送: {report.room_name}")

    @staticmethod
    def build_review_markdown(report) -> str:
        """
        构建半日复盘报告的 Markdown 内容
        简洁易读，非技术人员能理解
        """
        room_name = report.room_name or report.room_id
        stats = report.stats
        
        # 标题
        lines = [
            f"### 📊 【{room_name}】半日复盘报告",
            "---",
        ]
        
        # 摘要
        lines.append("**【摘要】**")
        lines.append(f"> {report.summary}")
        lines.append("")
        
        # 统计概览
        lines.append("**【统计】**")
        lines.append(f"- 📝 消息数: {stats.total_count}")
        lines.append(f"- ⚠️ 需关注: {stats.high_risk_count}")
        lines.append(f"- 📊 平均风险: {stats.avg_risk_score}分")
        lines.append("")
        
        # 分类清单（只显示前5条最重要的）
        lines.append("---")
        lines.append("**【分类清单】**")
        lines.append("")
        
        # 按风险得分排序，取前5条
        sorted_items = sorted(report.items, key=lambda x: x.risk_score, reverse=True)[:5]
        
        if sorted_items:
            # 简化的表格格式（钉钉Markdown对表格支持有限，用列表代替）
            for item in sorted_items:
                dim_icon = DingTalkService._get_dimension_icon(item.dimension)
                lines.append(
                    f"- {dim_icon} **{item.dimension}** | {item.readable_desc} | "
                    f"{item.emotion_icon}{item.emotion_level} | {item.action}"
                )
        else:
            lines.append("- 暂无记录")
        
        lines.append("")
        
        # 风险预警
        if report.risk_alerts:
            lines.append("---")
            lines.append("**【🚨 风险预警】**")
            lines.append("")
            
            for alert in report.risk_alerts[:3]:  # 最多显示3条
                lines.append(f"> **原话**: \"{alert.original_quote[:50]}...\"")
                lines.append(f"> **风险**: {alert.risk_score}分 | **原因**: {alert.reason}")
                lines.append("")
        
        # 底部
        lines.append("---")
        detail_url = f"{settings.INTERNAL_BASE_URL}/api/ui/rooms/{report.room_id}"
        lines.append(f"🏠 **客户群**: {room_name} | [查看原声]({detail_url})")
        
        return "\n".join(lines)

    @staticmethod
    def _get_dimension_icon(dimension: str) -> str:
        """获取维度图标"""
        icons = {
            "问题反馈": "⚡",
            "客户需求": "💡",
            "产品缺陷": "🔧",
            "使用咨询": "❓",
        }
        return icons.get(dimension, "📋")

    @staticmethod
    def send_daily_summary(reports: list) -> None:
        """
        推送每日汇总（可选，汇总所有群的情况）
        """
        if not settings.DINGTALK_WEBHOOK:
            return
        
        if not reports:
            return
        
        total_messages = sum(r.stats.total_count for r in reports)
        total_high_risk = sum(r.stats.high_risk_count for r in reports)
        
        lines = [
            "### 📈 每日客户沟通汇总",
            "---",
            f"- 📊 **群数**: {len(reports)}",
            f"- 💬 **消息总数**: {total_messages}",
            f"- ⚠️ **需关注**: {total_high_risk}",
            "",
            "**各群概况**:",
        ]
        
        for report in reports[:10]:  # 最多显示10个群
            risk_icon = "🔴" if report.stats.high_risk_count > 0 else "🟢"
            lines.append(
                f"- {risk_icon} {report.room_name}: "
                f"{report.stats.total_count}条消息, "
                f"{report.stats.high_risk_count}条需关注"
            )
        
        if len(reports) > 10:
            lines.append(f"- ... 还有 {len(reports) - 10} 个群")
        
        payload = {
            "msgtype": "markdown",
            "markdown": {
                "title": "📈 每日客户沟通汇总",
                "text": "\n".join(lines),
            },
        }
        
        DingTalkService._post_payload(payload)

    @staticmethod
    def send_report(report_type: str, markdown_text: str) -> None:
        """
        发送定时报表到钉钉
        
        Args:
            report_type: 报表类型（日报/周报/月报）
            markdown_text: Markdown 格式的报表内容
        """
        # 优先使用独立的报表 Webhook，否则使用默认 Webhook
        webhook = settings.REPORT_DINGTALK_WEBHOOK or settings.DINGTALK_WEBHOOK
        
        if not webhook:
            logger.warning("未配置钉钉 Webhook，跳过报表发送")
            return
        
        title_map = {
            "日报": "📊 用户反馈日报",
            "周报": "📊 用户反馈周报",
            "月报": "📊 用户反馈月报",
        }
        title = title_map.get(report_type, f"📊 用户反馈{report_type}")
        
        payload = {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": markdown_text,
            },
        }
        
        # 如果使用独立的报表 Webhook，需要单独处理签名
        if settings.REPORT_DINGTALK_WEBHOOK:
            try:
                import time
                import hmac
                import hashlib
                import base64
                import urllib.parse
                
                if settings.DINGTALK_SECRET:
                    timestamp = str(int(time.time() * 1000))
                    string_to_sign = f"{timestamp}\n{settings.DINGTALK_SECRET}"
                    sign = hmac.new(
                        settings.DINGTALK_SECRET.encode("utf-8"),
                        string_to_sign.encode("utf-8"),
                        digestmod=hashlib.sha256,
                    ).digest()
                    sign = urllib.parse.quote_plus(base64.b64encode(sign))
                    webhook = f"{webhook}&timestamp={timestamp}&sign={sign}"
                
                resp = requests.post(webhook, json=payload, timeout=10)
                logger.info(f"[定时报表] {report_type}推送结果: {resp.text}")
            except Exception as e:
                logger.error(f"[定时报表] {report_type}发送失败: {e}")
        else:
            # 使用默认的 _post_payload 方法
            DingTalkService._post_payload(payload)
            logger.info(f"[定时报表] {report_type}已推送")
