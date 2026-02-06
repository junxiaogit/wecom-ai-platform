"""
钉钉消息模板 - 包含钉钉告警消息、工单状态更新相关的模板

模板变量说明:
- {issue_type}: 问题类型 (使用咨询/问题反馈/产品需求/产品缺陷)
- {priority}: 优先级 (较低/普通/紧急/非常紧急)
- {priority_icon}: 优先级图标 (⚪/🔵/🟡/🔴)
- {phenomenon}: 问题现象描述（30字以内）
- {summary}: 问题总结（30-50字）
- {room_name}: 客户群名称
- {detail_url}: 详情链接（原声链接，带时间窗口）
- {draft_id}: 草稿ID
- {ticket_url}: 工单链接

优先级图标映射:
- 非常紧急: 🔴 (红色)
- 紧急: 🟡 (黄色)
- 普通: 🔵 (蓝色)
- 较低: ⚪ (灰色)
"""

# ============================================================
# 优先级图标映射
# ============================================================

PRIORITY_ICONS = {
    "非常紧急": "🔴",
    "紧急": "🟡",
    "普通": "🔵",
    "较低": "⚪",
}


# ============================================================
# 1. 钉钉告警消息模板 - 新格式（按需求文档）
# ============================================================

DINGTALK_ALERT_TEMPLATE = """### 问题类型：{issue_type}

**【优先级】** : {priority_icon} {priority}

**【问题】**: {phenomenon}

**【总结】**: {summary}

**【🏠客户群】**: {room_name} | [查看详情]({detail_url})
"""


# ============================================================
# 2. 工单状态更新消息模板
# ============================================================

DINGTALK_TICKET_UPDATE_TEMPLATE = """**工单已创建**
**🧾 草稿ID**: {draft_id}**🧾 提单**: 已建单 · [查看工单]({ticket_url})
"""


# ============================================================
# 3. AI辅助字段纯文本模板 - 用于 TB 自定义字段（简化版）
# ============================================================

AI_ASSISTANT_TEXT_TEMPLATE = """问题类型：{issue_type}
【优先级】{priority_icon} {priority}
【问题】: {phenomenon}
【总结】: {summary}
【🏠客户群】: {room_name} | {detail_url}
"""


# ============================================================
# 辅助函数 - 用于格式化模板
# ============================================================

def get_priority_icon(priority: str) -> str:
    """根据优先级获取对应图标"""
    return PRIORITY_ICONS.get(priority, "🔵")


def format_dingtalk_alert(
    issue_type: str,
    priority: str,
    phenomenon: str,
    summary: str,
    room_name: str,
    detail_url: str,
) -> str:
    """
    格式化钉钉告警消息（新格式）
    
    Args:
        issue_type: 问题类型 (使用咨询/问题反馈/产品需求/产品缺陷)
        priority: 优先级 (较低/普通/紧急/非常紧急)
        phenomenon: 问题现象描述（30字以内）
        summary: 问题总结（30-50字）
        room_name: 客户群名称
        detail_url: 详情链接
    
    Returns:
        格式化后的钉钉消息文本
    
    注意：使用 ### 三级标题（字体较小），每行之间用双换行确保钉钉正确显示
    """
    priority_val = priority or "普通"
    priority_icon = get_priority_icon(priority_val)
    urgent_prefix = "🚨 " if priority_val in ("紧急", "非常紧急") else ""
    # 使用 ### 三级标题，字体较小；每行之间双换行确保钉钉正确显示
    return (
        f"### {urgent_prefix}问题类型：{issue_type or '问题反馈'}\n\n"
        f"**【优先级】** : {priority_icon} {priority_val}\n\n"
        f"**【问题】**: {phenomenon or '暂无'}\n\n"
        f"**【总结】**: {summary or '暂无'}\n\n"
        f"**【🏠客户群】**: {room_name or '-'} | [查看详情]({detail_url or '-'})\n"
    )


def format_ticket_update(
    draft_id: int,
    ticket_url: str,
    room_name: str | None = None,  # 保留参数兼容但不再使用
) -> str:
    """格式化工单状态更新消息"""
    return DINGTALK_TICKET_UPDATE_TEMPLATE.format(
        draft_id=draft_id,
        ticket_url=ticket_url,
    )


def format_ai_assistant_text(
    issue_type: str,
    priority: str,
    phenomenon: str,
    summary: str,
    room_name: str,
    detail_url: str,
) -> str:
    """
    格式化 AI 辅助字段纯文本（新格式）
    
    Args:
        issue_type: 问题类型
        priority: 优先级
        phenomenon: 问题现象
        summary: 问题总结
        room_name: 客户群名称
        detail_url: 详情链接
    
    Returns:
        格式化后的纯文本
    """
    priority_icon = get_priority_icon(priority)
    return AI_ASSISTANT_TEXT_TEMPLATE.format(
        issue_type=issue_type or "问题反馈",
        priority_icon=priority_icon,
        priority=priority or "普通",
        phenomenon=phenomenon or "暂无",
        summary=summary or "暂无",
        room_name=room_name or "-",
        detail_url=detail_url or "-",
    )


# ============================================================
# 兼容性函数 - 支持旧参数调用（将被废弃）
# ============================================================

def format_dingtalk_alert_legacy(
    issue_type: str,
    risk_score: int,
    severity: str,
    category_display: str,
    assignee: str,
    phenomenon: str,
    key_sentence: str,
    room_name: str,
    detail_url: str,
) -> str:
    """
    兼容旧格式的钉钉告警消息格式化函数（将被废弃）
    
    将旧的 risk_score/severity 映射到新的 priority 体系
    """
    # 根据 risk_score 映射到新的 priority
    if risk_score >= 80:
        priority = "非常紧急"
    elif risk_score >= 60:
        priority = "紧急"
    elif risk_score >= 30:
        priority = "普通"
    else:
        priority = "较低"
    
    return format_dingtalk_alert(
        issue_type=issue_type,
        priority=priority,
        phenomenon=phenomenon,
        summary=key_sentence,  # 旧的 key_sentence 对应新的 summary
        room_name=room_name,
        detail_url=detail_url,
    )
