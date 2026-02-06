"""
通用工具函数 - 提供空值检查、默认值等公共函数

避免在多个文件中重复定义相同的逻辑
"""

# 空值标识列表
EMPTY_VALUES = (
    "",
    "-",
    "无",
    "暂无",
    "N/A",
    "暂无AI建议",
    "暂无建议回复",
    "暂无历史案例",
    "暂无历史方案",
    "未提供",
)

# 问题类型对应的默认 AI 解决方案
DEFAULT_AI_SOLUTIONS = {
    "问题反馈": "1.排查问题原因并记录日志; 2.尝试重启应用或清除缓存; 3.如问题持续请联系技术支持",
    "使用咨询": "1.查阅产品使用文档; 2.参考FAQ常见问题; 3.如仍有疑问请联系客服",
    "产品需求": "1.记录需求详情并反馈产品团队; 2.评估需求优先级; 3.跟进需求进展并及时回复用户",
    "产品缺陷": "1.复现问题并收集日志; 2.提交Bug给开发团队; 3.修复后通知用户验证",
}

# 默认解决方案（通用）
DEFAULT_AI_SOLUTION = "1.请提供更多问题详情; 2.检查网络和设备状态; 3.如问题持续请联系技术支持"

# 默认安抚话术
DEFAULT_SOOTHING_REPLY = "感谢您的反馈，我们会尽快处理，如有进展会及时通知您。"

# 默认历史案例提示
DEFAULT_SIMILAR_CAUSE = "暂无历史案例"
DEFAULT_SIMILAR_SOLUTION = "暂无历史方案"


def is_empty_value(value) -> bool:
    """
    检查值是否为空（包括 None、空字符串、'-'、'无'、'暂无'、'N/A' 等）
    
    Args:
        value: 要检查的值
        
    Returns:
        True 如果值为空，否则 False
    """
    if value is None:
        return True
    s = str(value).strip()
    return s in EMPTY_VALUES or len(s) == 0


def not_empty(value) -> bool:
    """
    检查值是否非空（is_empty_value 的反向）
    
    Args:
        value: 要检查的值
        
    Returns:
        True 如果值非空，否则 False
    """
    return not is_empty_value(value)


def get_valid_value(*values, default: str = "") -> str:
    """
    从多个值中获取第一个有效（非空）的值
    
    Args:
        *values: 要检查的值列表
        default: 如果所有值都为空，返回的默认值
        
    Returns:
        第一个非空的值，或默认值
    """
    for v in values:
        if not_empty(v):
            return str(v)
    return default


def get_ai_solution(issue_type: str | None, ai_solution: str | None) -> str:
    """
    获取 AI 解决方案，如果为空则返回默认值
    
    Args:
        issue_type: 问题类型
        ai_solution: AI 生成的解决方案
        
    Returns:
        有效的解决方案字符串
    """
    if not_empty(ai_solution):
        return str(ai_solution)
    
    # 根据问题类型返回默认方案
    if issue_type and issue_type in DEFAULT_AI_SOLUTIONS:
        return DEFAULT_AI_SOLUTIONS[issue_type]
    
    return DEFAULT_AI_SOLUTION


def get_soothing_reply(soothing_reply: str | None, suggested_reply: str | None = None) -> str:
    """
    获取安抚话术，如果为空则返回默认值
    
    Args:
        soothing_reply: 安抚话术
        suggested_reply: 建议回复（备选）
        
    Returns:
        有效的安抚话术字符串
    """
    if not_empty(soothing_reply):
        return str(soothing_reply)
    if not_empty(suggested_reply):
        return str(suggested_reply)
    return DEFAULT_SOOTHING_REPLY


def get_similar_cause(cause: str | None, hit_count: int | None = None) -> str:
    """
    获取历史案例原因，如果为空则返回默认值
    
    Args:
        cause: 历史案例原因
        hit_count: 命中次数（用于判断是否有历史案例）
        
    Returns:
        有效的历史案例原因字符串
    """
    if not_empty(cause):
        return str(cause)
    if hit_count and hit_count > 0:
        return "未提取到有效原因"
    return DEFAULT_SIMILAR_CAUSE


def get_similar_solution(solution: str | None, hit_count: int | None = None) -> str:
    """
    获取历史案例方案，如果为空则返回默认值
    
    Args:
        solution: 历史案例方案
        hit_count: 命中次数（用于判断是否有历史案例）
        
    Returns:
        有效的历史案例方案字符串
    """
    if not_empty(solution):
        return str(solution)
    if hit_count and hit_count > 0:
        return "未提取到有效方案"
    return DEFAULT_SIMILAR_SOLUTION
