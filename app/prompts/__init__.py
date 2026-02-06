"""
提示词模块 - 集中管理所有 LLM 提示词模板

模块结构:
- issue_extraction.py: 问题提取/优先级判断/分类模板
- dingtalk_message.py: 钉钉告警消息模板（新格式：优先级+问题+总结）
- teambition_ticket.py: TB 字段生成/备注/标题模板（简化版）

优先级体系:
- 非常紧急 🔴 (红色): 系统完全不可用、影响全部用户
- 紧急 🟡 (黄色): 核心功能异常、影响大部分用户
- 普通 🔵 (蓝色): 一般问题反馈、影响部分用户
- 较低 ⚪ (灰色): 普通咨询、影响极小

问题类型:
- 使用咨询: 用户询问如何使用
- 问题反馈: 用户反馈Bug、异常
- 产品需求: 用户提出新功能
- 产品缺陷: 确认的系统Bug
"""

# ============================================================
# 问题提取/分类/优先级模板
# ============================================================

from app.prompts.issue_extraction import (
    # 基础提示词
    ISSUE_ANALYSIS_PROMPT,
    RISK_SENTINEL_PROMPT,
    ISSUE_CLASSIFICATION_PROMPT,
    # 新增：问题类型分类
    ISSUE_TYPE_PROMPT,
    # 新增：优先级判断
    PRIORITY_PROMPT,
    # 新增：问题概括
    PHENOMENON_PROMPT,
    # 新增：总结生成
    SUMMARY_PROMPT,
    # 新增：版本提取
    VERSION_EXTRACTION_PROMPT,
    # 新增：完整分析（一次性输出所有字段）
    COMPLETE_ANALYSIS_PROMPT,
    # 新增：原声摘要
    NOTE_SUMMARY_PROMPT,
    # 新增：工单标题
    TICKET_TITLE_PROMPT,
    # 常量
    PRIORITY_ICONS,
    PRIORITY_TO_TB,
    VALID_ISSUE_TYPES,
    VALID_PRIORITIES,
    # 辅助函数
    get_issue_analysis_prompt,
    get_risk_sentinel_prompt,
    get_issue_classification_prompt,
    get_issue_type_prompt,
    get_priority_prompt,
    get_phenomenon_prompt,
    get_summary_prompt,
    get_version_extraction_prompt,
    get_complete_analysis_prompt,
    get_note_summary_prompt,
    get_ticket_title_prompt,
    get_priority_icon,
    normalize_priority,
    normalize_issue_type,
)

# ============================================================
# 钉钉消息模板
# ============================================================

from app.prompts.dingtalk_message import (
    # 模板
    DINGTALK_ALERT_TEMPLATE,
    DINGTALK_TICKET_UPDATE_TEMPLATE,
    AI_ASSISTANT_TEXT_TEMPLATE,
    # 优先级图标（也在这里导出）
    PRIORITY_ICONS as DINGTALK_PRIORITY_ICONS,
    # 辅助函数
    get_priority_icon as get_dingtalk_priority_icon,
    format_dingtalk_alert,
    format_ticket_update,
    format_ai_assistant_text,
    format_dingtalk_alert_legacy,
)

# ============================================================
# TB 建单模板
# ============================================================

from app.prompts.teambition_ticket import (
    # 新版模板
    TB_NOTE_TEMPLATE,
    TB_TITLE_PROMPT,
    TB_NOTE_SUMMARY_PROMPT,
    TB_COMPLETE_FIELDS_PROMPT,
    VERSION_EXTRACTION_PROMPT as TB_VERSION_EXTRACTION_PROMPT,
    # 字段映射配置
    CUSTOMFIELD_MAPPING,
    ISSUE_TYPE_CHOICE_MAP,
    SEVERITY_CHOICE_MAP,
    PRIORITY_TO_SEVERITY,
    # 辅助函数
    format_tb_note,
    format_tb_title,
    convert_priority_to_tb,
    convert_priority_to_severity,
    get_issue_type_choice_id,
    get_severity_choice_id,
    get_tb_title_prompt,
    get_tb_note_summary_prompt,
    get_tb_complete_fields_prompt,
    get_version_extraction_prompt as get_tb_version_extraction_prompt,
    get_customfield_mapping,
    # 兼容旧版（将被废弃）
    TB_CUSTOMFIELD_PROMPT,
    TB_FIELDS_GENERATION_PROMPT,
    TB_CUSTOM_FIELD_PROMPT,
    TB_TITLE_TEMPLATE,
    convert_risk_score_to_level,
    convert_severity_to_level,
    convert_risk_score_to_priority,
    get_tb_fields_prompt,
    get_tb_custom_field_prompt,
    get_tb_customfield_prompt,
)


__all__ = [
    # ============================================================
    # 问题提取/分类模板
    # ============================================================
    "ISSUE_ANALYSIS_PROMPT",
    "RISK_SENTINEL_PROMPT",
    "ISSUE_CLASSIFICATION_PROMPT",
    "ISSUE_TYPE_PROMPT",
    "PRIORITY_PROMPT",
    "PHENOMENON_PROMPT",
    "SUMMARY_PROMPT",
    "VERSION_EXTRACTION_PROMPT",
    "COMPLETE_ANALYSIS_PROMPT",
    "NOTE_SUMMARY_PROMPT",
    "TICKET_TITLE_PROMPT",
    # 常量
    "PRIORITY_ICONS",
    "PRIORITY_TO_TB",
    "VALID_ISSUE_TYPES",
    "VALID_PRIORITIES",
    # 辅助函数
    "get_issue_analysis_prompt",
    "get_risk_sentinel_prompt",
    "get_issue_classification_prompt",
    "get_issue_type_prompt",
    "get_priority_prompt",
    "get_phenomenon_prompt",
    "get_summary_prompt",
    "get_version_extraction_prompt",
    "get_complete_analysis_prompt",
    "get_note_summary_prompt",
    "get_ticket_title_prompt",
    "get_priority_icon",
    "normalize_priority",
    "normalize_issue_type",
    
    # ============================================================
    # 钉钉消息模板
    # ============================================================
    "DINGTALK_ALERT_TEMPLATE",
    "DINGTALK_TICKET_UPDATE_TEMPLATE",
    "AI_ASSISTANT_TEXT_TEMPLATE",
    "DINGTALK_PRIORITY_ICONS",
    "get_dingtalk_priority_icon",
    "format_dingtalk_alert",
    "format_ticket_update",
    "format_ai_assistant_text",
    "format_dingtalk_alert_legacy",
    
    # ============================================================
    # TB 建单模板
    # ============================================================
    "TB_NOTE_TEMPLATE",
    "TB_TITLE_PROMPT",
    "TB_NOTE_SUMMARY_PROMPT",
    "TB_COMPLETE_FIELDS_PROMPT",
    "TB_VERSION_EXTRACTION_PROMPT",
    "CUSTOMFIELD_MAPPING",
    "ISSUE_TYPE_CHOICE_MAP",
    "SEVERITY_CHOICE_MAP",
    "PRIORITY_TO_SEVERITY",
    "format_tb_note",
    "format_tb_title",
    "convert_priority_to_tb",
    "convert_priority_to_severity",
    "get_issue_type_choice_id",
    "get_severity_choice_id",
    "get_tb_title_prompt",
    "get_tb_note_summary_prompt",
    "get_tb_complete_fields_prompt",
    "get_tb_version_extraction_prompt",
    "get_customfield_mapping",
    
    # ============================================================
    # 兼容旧版（将被废弃）
    # ============================================================
    "TB_CUSTOMFIELD_PROMPT",
    "TB_FIELDS_GENERATION_PROMPT",
    "TB_CUSTOM_FIELD_PROMPT",
    "TB_TITLE_TEMPLATE",
    "convert_risk_score_to_level",
    "convert_severity_to_level",
    "convert_risk_score_to_priority",
    "get_tb_fields_prompt",
    "get_tb_custom_field_prompt",
    "get_tb_customfield_prompt",
]
