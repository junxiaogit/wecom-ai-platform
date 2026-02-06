from pydantic import BaseModel
from typing import Optional, List


class MsgInput(BaseModel):
    room_id: str
    sender: str
    content: str
    msg_id: Optional[str] = None
    msg_type: str = "text"
    seq: Optional[int] = None
    msg_time: Optional[str] = None
    environment: Optional[str] = None
    version: Optional[str] = None
    repro_steps: Optional[str] = None
    attachments: Optional[List[str]] = None


class SentinelResult(BaseModel):
    is_alert: bool
    risk_score: int
    reason: str
    # AI 生成的简短摘要字段
    phenomenon: Optional[str] = None      # 问题现象总结（50字以内）
    key_sentence: Optional[str] = None    # 关键原话（一句话，不带用户名）
    # 分类相关字段
    issue_type: Optional[str] = None
    category_l1: Optional[str] = None
    category_l2: Optional[str] = None
    category_short: Optional[str] = None
    labels: Optional[List[str]] = None
    severity: Optional[str] = None
    is_bug: Optional[bool] = None
    confidence: Optional[float] = None
    taxonomy_version: Optional[str] = None
    classification_strategy: Optional[str] = None


class ClassificationResult(BaseModel):
    category_l1: str
    category_l2: str
    category_short: str
    labels: List[str]
    severity: str
    is_bug: bool
    confidence: float
    issue_type: Optional[str] = None


class AssistantInsight(BaseModel):
    phenomenon: str
    key_sentence: str
    similar_case_cause: str
    similar_case_solution: str
    ai_solution: str
    soothing_reply: str


class CustomFieldInsight(BaseModel):
    issue_type: str
    ai_solution: str
    ai_assistant: str


class TbFieldsInsight(BaseModel):
    # note
    note_summary: str

    # dropdown/internal codes
    issue_type: str
    severity: str
    risk_score: int

    # optional display
    category_short: Optional[str] = None

    # extracted / generated
    phenomenon: str
    key_sentence: str
    similar_case_cause: str
    similar_case_solution: str
    ai_solution: str
    soothing_reply: str

    # TB custom fields
    ai_assistant_text: str
    tag_text: str


# ============================================================
# 半日高频复盘模式 - 数据模型
# ============================================================

class EmotionAnalysis(BaseModel):
    """情绪分析结果"""
    emotion: str  # 正面/中性/负面
    risk_score: int  # 0-100 流失风险得分
    reason: str  # 判断依据


class ReviewItemResult(BaseModel):
    """单条消息的复盘分析结果"""
    dimension: str  # 四维度分类：问题反馈/客户需求/产品缺陷/使用咨询
    readable_desc: str  # 平民化事件描述
    emotion_level: str  # 情绪等级：正面/中性/负面
    emotion_icon: str  # 情绪图标
    risk_score: int  # 流失风险得分
    action: str  # 建议处理动作
    original: str  # 原文摘要
    msg_id: Optional[str] = None
    sender_id: Optional[str] = None


class RiskAlertItem(BaseModel):
    """风险预警项"""
    original_quote: str  # 原话摘录
    risk_score: int  # 流失风险得分
    reason: str  # 风险原因
    msg_id: Optional[str] = None


class RoomReviewStats(BaseModel):
    """群级别统计数据"""
    total_count: int  # 总消息数
    dimension_counts: dict  # 各维度数量
    avg_risk_score: float  # 平均风险得分
    high_risk_count: int  # 高风险数量(>=70分)
    emotion_distribution: dict  # 情绪分布


class RoomReviewReport(BaseModel):
    """单个群的半日复盘报告"""
    room_id: str
    room_name: str
    summary: str  # 100字以内通俗总结
    items: List[ReviewItemResult]  # 分类清单
    risk_alerts: List[RiskAlertItem]  # 风险预警
    stats: RoomReviewStats  # 统计数据
    review_time: str  # 复盘时间
    window_hours: int  # 时间窗口(小时)


class HalfDayReviewResult(BaseModel):
    """半日复盘总结果"""
    status: str  # success/no_data/error
    message: Optional[str] = None
    room_count: int = 0
    total_messages: int = 0
    reports: List[RoomReviewReport] = []
    start_time: Optional[str] = None
    end_time: Optional[str] = None


class DimensionClassifyResult(BaseModel):
    """四维度分类LLM输出"""
    dimension: str  # 问题反馈/客户需求/产品缺陷/使用咨询
    confidence: float  # 置信度
    reason: str  # 分类依据


class PlainLanguageResult(BaseModel):
    """平民化话术重组LLM输出"""
    readable_desc: str  # 通俗描述(30字以内)
    action_hint: str  # 处理提示
