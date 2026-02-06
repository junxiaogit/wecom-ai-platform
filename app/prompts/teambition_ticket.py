"""
Teambition 建单模板 - 包含 TB 字段生成、备注、标题相关的 LLM 提示词和模板

模板变量说明:
- {chat_context}: 对话内容
- {text}: 待分析的文本
- {format_instructions}: JSON 格式说明
- {room_name}: 客户群名称
- {detail_url}: 原声链接（带时间窗口）
- {note_summary}: 备注摘要（原生摘要）
- {issue_type}: 问题类型 (使用咨询/问题反馈/产品需求/产品缺陷)
- {priority}: 优先级 (较低/普通/紧急/非常紧急)
- {phenomenon}: 问题现象
- {summary}: 问题总结

自定义字段映射表（按需求文档）:
| TB字段 | 数据来源 | 值类型 |
|--------|----------|--------|
| 问题类型 | issue_type | 下拉: 四选一 |
| 客户端版本 | client_version | 文本: AI提取 |
| CBS版本 | cbs_version | 文本: AI提取 |
| 镜像ID | image_id | 文本: AI提取 |
| 标签 | room_name | 文本: 群聊名称 |
"""

# ============================================================
# 优先级常量与映射（从 issue_extraction 导入，保持一致）
# ============================================================

PRIORITY_ICONS = {
    "非常紧急": "🔴",
    "紧急": "🟡",
    "普通": "🔵",
    "较低": "⚪",
}

PRIORITY_TO_TB = {
    "非常紧急": "紧急",
    "紧急": "较高",
    "普通": "普通",
    "较低": "较低",
}

VALID_ISSUE_TYPES = ["使用咨询", "问题反馈", "产品需求", "产品缺陷"]
VALID_PRIORITIES = ["较低", "普通", "紧急", "非常紧急"]


# ============================================================
# 1. TB 任务备注模板（简化版 - 只有两行）
# ============================================================

TB_NOTE_TEMPLATE = """- 原生摘要：{note_summary}
- 原声链接：{detail_url}
"""


# ============================================================
# 2. TB 任务标题生成提示词（30字以内，AI生成）
# ============================================================

TB_TITLE_PROMPT = """
你是企业级工单标题生成助手。根据问题信息，生成精炼的工单标题。

【问题现象】{phenomenon}
【问题总结】{summary}

【标题要求】
- 严格30字以内
- 只描述问题本身
- 不要带"用户反馈"、"客户反馈"等前缀
- 不要带设备ID（ACN/ATP开头的长串）
- 不要包含用户姓名、群聊名称、@提及

【示例】
- 输入：现象="关机失败、闪屏"，总结="关机失败，这台也闪屏"
  输出：多台设备关机失败及闪屏问题

- 输入：现象="代理设置失败"，总结="设置代理很多失败的"
  输出：批量代理设置失败

直接输出标题，不要加引号：
"""


# ============================================================
# 3. 原声摘要生成提示词（用于备注中的原生摘要）
# ============================================================

TB_NOTE_SUMMARY_PROMPT = """
你是企业级问题摘要助手。根据聊天内容，提取并总结核心问题。

【聊天内容】
{text}

【要求】
- 严格30字以内
- 只描述问题本身（如：系统卡顿、无法登录、数据丢失）
- 禁止包含：设备ID、用户名、JSON数据、引用格式、群聊名称
- 如果内容无法识别问题，输出"暂无明确问题"

【示例输出】
- 关机失败，这台也是还闪屏，还有设置代理很多失败的
- 开机速度异常缓慢，等待超过一分钟
- 改机属性操作失败，显示超时

直接输出问题摘要：
"""


# ============================================================
# 4. TB 完整字段生成提示词（一次性输出所有字段）
# ============================================================

TB_COMPLETE_FIELDS_PROMPT = """
你是企业级客户反馈分析助手。分析用户反馈内容，为 Teambition 工单生成所有必要字段，输出 JSON。

【用户反馈内容】
{text}

【输出字段说明】
- title: 工单标题（30字以内，只描述问题本身，不要前缀）
- issue_type: 问题类型（使用咨询/问题反馈/产品需求/产品缺陷，四选一）
- priority: 优先级（较低/普通/紧急/非常紧急，四选一）
- note_summary: 原生摘要（30字以内，只描述问题）
- phenomenon: 问题概括（30字以内，只描述问题本身）
- summary: 问题总结（30-50字，用用户口吻描述）
- client_version: 客户端版本（没有就输出"-"）
- cbs_version: CBS版本（没有就输出"-"）
- image_id: 镜像ID（没有就输出"-"）
- tag_text: 标签（使用客户群名称）

【判断规则】

问题类型：
- 含"怎么/如何/在哪/操作" → 使用咨询
- 含"报错/异常/崩溃/白屏/无法/失败" → 问题反馈
- 含"希望/建议/能不能/最好" → 产品需求
- 明确是系统Bug需修复 → 产品缺陷
- 默认：问题反馈

优先级：
- 出现"紧急/急/马上/全部/崩溃/完全不能用" → 非常紧急
- 出现"尽快/影响使用/报错/无法正常" → 紧急
- 出现"问题/异常/反馈" → 普通
- 出现"咨询/请问/怎么/好的/收到" → 较低
- 默认：普通

【禁止输出】
- title/phenomenon/note_summary 包含用户姓名、群聊名称、@提及
- 包含完整设备ID（ACN/ATP开头的长串）
- 超过字数限制

【客户群名称】
{room_name}

【Few-shot 示例】

示例1：
输入："ACN123456789这台关机失败了，还闪屏，设置代理也失败"
输出：{{"title":"多台设备关机失败及闪屏问题","issue_type":"问题反馈","priority":"紧急","note_summary":"关机失败，闪屏，代理设置失败","phenomenon":"关机失败、闪屏、代理设置失败","summary":"关机失败，这台也闪屏，还有设置代理失败","client_version":"-","cbs_version":"-","image_id":"-","tag_text":"客户群"}}

示例2：
输入："请问怎么设置代理？版本是1.2.3"
输出：{{"title":"咨询代理设置方法","issue_type":"使用咨询","priority":"较低","note_summary":"咨询代理设置方法","phenomenon":"咨询代理设置方法","summary":"用户咨询如何设置代理","client_version":"1.2.3","cbs_version":"-","image_id":"-","tag_text":"客户群"}}

严格输出JSON：
"""


# ============================================================
# 5. 版本与镜像ID提取提示词
# ============================================================

VERSION_EXTRACTION_PROMPT = """
你是企业级信息抽取助手。请从聊天内容中提取以下字段，并严格输出JSON。

【聊天内容】
{text}

【字段要求】
- client_version：只输出客户端版本号（如 1.2.3 / v20251211），没有就输出 "-"
- cbs_version：只输出CBS版本号（如 20251211_12），没有就输出 "-"
- image_id：只输出镜像ID（如 img-25121161049），没有就输出 "-"
- 禁止包含人名、群聊名、@、以及大段原文

【识别规则】
- 客户端版本：通常出现在"版本号"、"客户端"、"APP版本"附近
- CBS版本：通常出现在"CBS"、"后端版本"附近，格式为日期_序号
- 镜像ID：通常以"img-"开头，或出现在"镜像"、"镜像ID"附近

【输出JSON格式】
{{"client_version":"1.2.3","cbs_version":"20251211_12","image_id":"img-25121161049"}}

严格输出JSON：
"""


# ============================================================
# 6. 自定义字段映射配置（按需求文档更新）
# ============================================================

CUSTOMFIELD_MAPPING = {
    "问题类型": {
        "customfieldId": "698154db491d1b2e56413d02",
        "source_field": "issue_type",
        "type": "dropdown",
        "options": ["使用咨询", "问题反馈", "产品需求", "产品缺陷"],
        "default": "问题反馈"
    },
    "严重程度": {
        "customfieldId": "698154db491d1b2e56413ba6",
        "source_field": "severity",
        "type": "dropdown",
        "mapping": {
            "致命": "5a2e597daddda52ad6921670",
            "严重": "5a2e597daddda52ad692166f",
            "一般": "5a2e597daddda52ad692166e",
            "轻微": "5a2e597daddda52ad692166d"
        },
        "default": "一般"
    },
    "客户端版本": {
        "customfieldId": "698154db491d1b2e56413d12",
        "source_field": "client_version",
        "type": "text",
        "default": "-"
    },
    "CBS版本": {
        "customfieldId": "698154db491d1b2e56413d18",
        "source_field": "cbs_version",
        "type": "text",
        "default": "-"
    },
    "镜像ID": {
        "customfieldId": "698154db491d1b2e56413d0c",
        "source_field": "image_id",
        "type": "text",
        "default": "-"
    },
    "标签": {
        "source_field": "room_name",
        "type": "text",
        "fallback": "room_id"
    }
}

# 问题类型选项ID映射
ISSUE_TYPE_CHOICE_MAP = {
    "使用咨询": "68d60a6f7097454e9f21537b",
    "问题反馈": "68d60a6f7097454e9f21537c",
    "产品需求": "68dfec86eb33e87afc5ac398",
    "产品缺陷": "6949192e0c31256acd2fbf4e"
}

# 严重程度选项ID映射
SEVERITY_CHOICE_MAP = {
    "致命": "5a2e597daddda52ad6921670",
    "严重": "5a2e597daddda52ad692166f",
    "一般": "5a2e597daddda52ad692166e",
    "轻微": "5a2e597daddda52ad692166d"
}

# 优先级到严重程度的映射
PRIORITY_TO_SEVERITY = {
    "非常紧急": "致命",
    "紧急": "严重",
    "普通": "一般",
    "较低": "轻微",
}


# ============================================================
# 辅助函数 - 用于格式化模板和字段转换
# ============================================================

def format_tb_note(note_summary: str, detail_url: str) -> str:
    """
    格式化 TB 任务备注（简化版，只有两行）
    
    格式：
    - 原生摘要：{note_summary}
    - 原声链接：{detail_url}
    """
    return TB_NOTE_TEMPLATE.format(
        note_summary=note_summary or "暂无明确问题",
        detail_url=detail_url or "-",
    )


def format_tb_title(title: str, max_len: int = 30) -> str:
    """
    格式化 TB 任务标题
    
    要求：
    - 30字以内
    - 只描述问题本身
    - 不要前缀
    """
    # 清理标题中的多余空白和换行
    title_clean = " ".join(str(title or "未提供").split())
    
    # 限制标题长度
    if len(title_clean) > max_len:
        title_clean = title_clean[:max_len - 1] + "…"
    
    return title_clean


def get_priority_icon(priority: str) -> str:
    """根据优先级获取对应图标"""
    return PRIORITY_ICONS.get(priority, "🔵")


def convert_priority_to_tb(priority: str) -> str:
    """将优先级转换为 TB 优先级"""
    return PRIORITY_TO_TB.get(priority, "普通")


def convert_priority_to_severity(priority: str) -> str:
    """将优先级转换为严重程度"""
    return PRIORITY_TO_SEVERITY.get(priority, "一般")


def get_issue_type_choice_id(issue_type: str) -> str | None:
    """获取问题类型对应的选项ID"""
    return ISSUE_TYPE_CHOICE_MAP.get(issue_type)


def get_severity_choice_id(severity: str) -> str | None:
    """获取严重程度对应的选项ID"""
    return SEVERITY_CHOICE_MAP.get(severity)


def normalize_issue_type(issue_type: str | None) -> str:
    """标准化问题类型，确保返回有效值"""
    if issue_type in VALID_ISSUE_TYPES:
        return issue_type
    return "问题反馈"


def normalize_priority(priority: str | None) -> str:
    """标准化优先级，确保返回有效值"""
    if priority in VALID_PRIORITIES:
        return priority
    return "普通"


def get_tb_title_prompt() -> str:
    """获取 TB 标题生成提示词"""
    return TB_TITLE_PROMPT


def get_tb_note_summary_prompt() -> str:
    """获取原声摘要生成提示词"""
    return TB_NOTE_SUMMARY_PROMPT


def get_tb_complete_fields_prompt() -> str:
    """获取 TB 完整字段生成提示词"""
    return TB_COMPLETE_FIELDS_PROMPT


def get_version_extraction_prompt() -> str:
    """获取版本提取提示词"""
    return VERSION_EXTRACTION_PROMPT


def get_customfield_mapping() -> dict:
    """获取自定义字段映射配置"""
    return CUSTOMFIELD_MAPPING


# ============================================================
# 兼容性函数（保留旧版接口，将被逐步废弃）
# ============================================================

def convert_risk_score_to_level(risk_score: int) -> str:
    """将风险分数转换为风险概率等级（兼容旧版）"""
    if risk_score >= 61:
        return "大"
    elif risk_score >= 31:
        return "中"
    else:
        return "小"


def convert_severity_to_level(severity: str) -> str:
    """将严重程度编码转换为风险等级（兼容旧版）"""
    mapping = {"S1": "高", "S2": "中", "S3": "低"}
    return mapping.get(severity, "低")


def convert_risk_score_to_priority(risk_score: int) -> str:
    """将风险分数转换为优先级（兼容旧版）"""
    if risk_score >= 80:
        return "非常紧急"
    elif risk_score >= 60:
        return "紧急"
    elif risk_score >= 30:
        return "普通"
    else:
        return "较低"


# 兼容旧版提示词（将被废弃）
TB_CUSTOMFIELD_PROMPT = get_tb_complete_fields_prompt()
TB_FIELDS_GENERATION_PROMPT = get_tb_complete_fields_prompt()
TB_CUSTOM_FIELD_PROMPT = get_tb_complete_fields_prompt()
TB_TITLE_TEMPLATE = "{title}"


def get_tb_fields_prompt() -> str:
    """获取 TB 字段生成提示词（兼容旧版）"""
    return TB_COMPLETE_FIELDS_PROMPT


def get_tb_custom_field_prompt() -> str:
    """获取 TB 自定义字段生成提示词（兼容旧版）"""
    return TB_COMPLETE_FIELDS_PROMPT


def get_tb_customfield_prompt() -> str:
    """获取 TB 自定义字段生成提示词（兼容旧版）"""
    return TB_COMPLETE_FIELDS_PROMPT
