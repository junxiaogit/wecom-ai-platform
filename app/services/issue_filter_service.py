from app.core.config import settings
from loguru import logger
import re

# 延迟导入以避免循环依赖
_vector_kb = None

def _get_vector_kb():
    """延迟加载 vector_kb 以避免循环导入"""
    global _vector_kb
    if _vector_kb is None:
        from app.services.vector_service import vector_kb
        _vector_kb = vector_kb
    return _vector_kb


def _severity_rank(severity: str | None) -> int:
    mapping = {"S0": 0, "S1": 1, "S2": 2, "S3": 3, "S4": 4}
    return mapping.get(severity or "S0", 0)


# 已解决模式的关键词/短语
RESOLVED_PATTERNS = [
    # 客服确认会修复
    r"下个版本.*修复",
    r"下一个版本.*修复",
    r"下版本.*修复",
    r"会.*修复",
    r"已.*修复",
    r"修复.*了",
    r"已经.*解决",
    r"问题.*已.*处理",
    r"问题.*解决",
    # 用户确认
    r"^好的$",
    r"^好$",
    r"^ok$",
    r"^OK$",
    r"^收到$",
    r"^明白$",
    r"^了解$",
    r"^知道了$",
    r"^谢谢$",
    r"^感谢$",
    r"^好的.*谢谢",
    r"^好.*收到",
]

# 无意义/非问题消息
NOISE_PATTERNS = [
    r"^[好的收到明白了解谢谢感谢OK]+[!！。.~～]*$",
    r"^[表情]$",
    r"^[图片]$",
    r"^\s*$",
]


def is_resolved_or_noise(text: str) -> bool:
    """
    判断消息是否是"已解决确认"或"无意义噪音"
    这类消息不应该触发提单
    """
    text_clean = text.strip()
    
    # 检查是否匹配已解决模式
    for pattern in RESOLVED_PATTERNS:
        if re.search(pattern, text_clean, re.IGNORECASE):
            return True
    
    # 检查是否是噪音
    for pattern in NOISE_PATTERNS:
        if re.match(pattern, text_clean, re.IGNORECASE):
            return True
    
    return False


def is_context_resolved(chat_lines: list[str] | None) -> bool:
    """
    检查对话上下文是否表明问题已解决
    如果最近的对话包含"客服确认修复 + 用户确认"的模式，返回 True
    """
    if not chat_lines or len(chat_lines) < 2:
        return False
    
    # 检查最近的几条消息
    recent = chat_lines[-5:] if len(chat_lines) >= 5 else chat_lines
    combined = "\n".join(recent)
    
    # 客服确认修复的模式
    staff_resolve_patterns = [
        r"下个版本.*修复",
        r"下一个版本.*修复",
        r"会.*修复",
        r"已.*修复",
        r"已经.*解决",
        r"问题.*已.*处理",
        r"这个.*修复",
        r"排查.*解决",
        r"已.*处理",
    ]
    
    # 用户确认的模式
    user_confirm_patterns = [
        r"好的",
        r"^好$",
        r"收到",
        r"明白",
        r"了解",
        r"知道了",
        r"谢谢",
        r"感谢",
        r"ok",
    ]
    
    has_staff_resolve = False
    has_user_confirm = False
    
    for pattern in staff_resolve_patterns:
        if re.search(pattern, combined, re.IGNORECASE):
            has_staff_resolve = True
            break
    
    # 检查最后一条消息是否是用户确认
    last_msg = chat_lines[-1].strip() if chat_lines else ""
    for pattern in user_confirm_patterns:
        if re.search(pattern, last_msg, re.IGNORECASE):
            has_user_confirm = True
            break
    
    return has_staff_resolve and has_user_confirm


def _check_similar_hard_issue(text: str) -> bool:
    """
    使用 RAG 检查历史中是否有类似的硬问题
    """
    try:
        vector_kb = _get_vector_kb()
        return vector_kb.has_similar_hard_issue(text, k=2)
    except Exception as e:
        logger.warning(f"RAG 硬问题检索失败: {e}")
        return False


def check_resolved_status(text: str, chat_lines: list[str] | None = None) -> bool:
    """
    检查问题是否已解决（不影响推送判断，仅用于标记）
    
    Args:
        text: 当前消息文本
        chat_lines: 最近的对话上下文（可选）
    
    Returns:
        True 表示问题已解决，需要在标题后添加"（已解决）"标记
    """
    if is_resolved_or_noise(text):
        return True
    if chat_lines and is_context_resolved(chat_lines):
        return True
    return False


def is_hard_issue(text: str, analysis: dict, chat_lines: list[str] | None = None) -> bool:
    """
    判断是否是需要处理的"难题"
    返回 False 表示跳过提单
    
    注意：已解决状态不再影响此判断，而是通过 check_resolved_status 单独标记
    
    Args:
        text: 当前消息文本
        analysis: SentinelAgent 分析结果
        chat_lines: 最近的对话上下文（可选，此参数保留但不再用于跳过判断）
    """
    if not settings.PROCESS_ONLY_HARD:
        return True

    # 检查 severity 和 is_bug（OR 逻辑：任一满足即为硬问题）
    severity_ok = _severity_rank(analysis.get("severity")) >= _severity_rank(settings.HARD_MIN_SEVERITY)
    is_bug = analysis.get("is_bug", False)
    if severity_ok or is_bug:
        return True

    keywords = [k.strip() for k in settings.HARD_KEYWORDS.split(",") if k.strip()]
    for k in keywords:
        if k in text:
            return True

    # RAG 增强：检查历史中是否有类似的硬问题
    # 如果历史中有类似问题被标记为硬问题，当前问题也应被视为硬问题
    if _check_similar_hard_issue(text):
        logger.debug(f"RAG: 检测到历史相似硬问题，标记当前消息为硬问题")
        return True

    return False
