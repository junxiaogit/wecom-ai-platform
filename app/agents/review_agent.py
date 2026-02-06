# app/agents/review_agent.py
"""
半日高频复盘专用Agent
负责：四维度分类、情绪分析、话术平民化重组
"""
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from loguru import logger
from app.core.llm_factory import get_smart_llm
from app.schemas.common import (
    DimensionClassifyResult,
    EmotionAnalysis,
    PlainLanguageResult,
)


class ReviewAgent:
    """半日复盘专用Agent"""

    # 四维度定义（用于 Prompt）
    FOUR_DIMENSIONS_DESC = """
**四维度分类定义（必须精准区分）**：

1. **问题反馈**：用户遇到的使用障碍或突发异常
   - 特征：用户正在使用时遇到困难，但未明确说是系统Bug
   - 示例："打开页面转了半天"、"操作了没反应"、"加载很慢"

2. **客户需求**：对功能扩展、业务场景的期望或建议
   - 特征：用户希望系统做得更好/更多
   - 示例："能不能增加导出功能"、"希望支持XX"、"建议优化"

3. **产品缺陷**：确认为系统Bug或程序逻辑错误
   - 特征：明确是系统层面的故障
   - 示例："点击报错"、"数据丢失"、"崩溃了"、"500错误"

4. **使用咨询**：关于操作方法、配置流程的简单询问
   - 特征：用户不会用，问怎么操作
   - 示例："怎么设置"、"请问如何操作"、"在哪里找"
"""

    def __init__(self):
        self.llm = get_smart_llm()

        # 四维度分类器
        self.dimension_parser = JsonOutputParser(pydantic_object=DimensionClassifyResult)
        self.dimension_prompt = ChatPromptTemplate.from_template(
            """你是企业级会话分类专家。请将客户消息精准归入四个维度之一。

{four_dimensions_desc}

**客户消息**：
{text}

**要求**：
- 仔细分析消息内容，精准归入四个维度之一
- dimension 只能是：问题反馈、客户需求、产品缺陷、使用咨询
- 给出分类依据

严格输出JSON：
{format_instructions}
"""
        )
        self.dimension_chain = self.dimension_prompt | self.llm | self.dimension_parser

        # 情绪分析器
        self.emotion_parser = JsonOutputParser(pydantic_object=EmotionAnalysis)
        self.emotion_prompt = ChatPromptTemplate.from_template(
            """你是客户情绪分析专家。请分析客户消息的情绪和流失风险。

**客户消息**：
{text}

**情绪等级定义**：
- 正面(0-30分)：满意、认可、感谢
- 中性(31-60分)：普通咨询、正常沟通
- 负面(61-100分)：不满、焦虑、愤怒、失望

**流失风险判断依据**：
- 反复提及同一问题
- 使用"投诉"、"换"、"算了"等词
- 情绪激动、语气强烈
- 明确表示不满或失望

**要求**：
- emotion 只能是：正面、中性、负面
- risk_score 是0-100的整数，代表客户流失可能性
- reason 简短说明判断依据（15字以内）

严格输出JSON：
{format_instructions}
"""
        )
        self.emotion_chain = self.emotion_prompt | self.llm | self.emotion_parser

        # 话术平民化重组器
        self.plain_parser = JsonOutputParser(pydantic_object=PlainLanguageResult)
        self.plain_prompt = ChatPromptTemplate.from_template(
            """你是业务话术转化专家。请用通俗易懂的话重新描述客户问题。

**原始消息**：
{text}

**问题类型**：{dimension}

**要求**：
- readable_desc：用30字以内的通俗话描述，像和同事口头说话一样自然
- 避免技术术语（如API、500错误、timeout等）
- 直接说问题本质，不要引用原话
- action_hint：给出简短的处理提示（10字以内）

**示例**：
- 原话"推流rtmp报错timeout" → "直播推流连不上"
- 原话"页面返回500" → "系统打不开了"
- 原话"接口调用失败" → "功能用不了"

严格输出JSON：
{format_instructions}
"""
        )
        self.plain_chain = self.plain_prompt | self.llm | self.plain_parser

        # 群级别摘要生成器
        self.summary_prompt = ChatPromptTemplate.from_template(
            """你是业务汇报专家。请为客户群生成一段通俗的半日复盘总结。

**群名称**：{room_name}
**时间范围**：过去{window_hours}小时
**消息统计**：
- 总消息数：{total_count}
- 分类分布：{dimension_counts}
- 平均风险得分：{avg_risk}
- 高风险消息数：{high_risk_count}

**典型问题**：
{typical_issues}

**要求**：
- 用{max_len}字以内的通俗话术总结
- 像向领导汇报工作一样简洁明了
- 突出重点问题和需要关注的事项
- 非技术人员能听懂

直接输出总结文本，不要JSON格式。
"""
        )
        self.summary_chain = self.summary_prompt | self.llm

    async def classify_dimension(self, text: str) -> dict:
        """
        四维度精准分类
        返回：dimension, confidence, reason
        """
        try:
            result = await self.dimension_chain.ainvoke(
                {
                    "text": text,
                    "four_dimensions_desc": self.FOUR_DIMENSIONS_DESC,
                    "format_instructions": self.dimension_parser.get_format_instructions(),
                }
            )
            # 确保返回合法的维度
            valid_dimensions = ["问题反馈", "客户需求", "产品缺陷", "使用咨询"]
            if result.get("dimension") not in valid_dimensions:
                result["dimension"] = "问题反馈"  # 默认兜底
            return result
        except Exception as e:
            logger.error(f"四维度分类失败: {e}")
            return {
                "dimension": "问题反馈",
                "confidence": 0.5,
                "reason": "分类失败，默认归类"
            }

    async def analyze_emotion(self, text: str) -> dict:
        """
        情绪分析 + 流失风险评分
        返回：emotion, risk_score, reason
        """
        try:
            result = await self.emotion_chain.ainvoke(
                {
                    "text": text,
                    "format_instructions": self.emotion_parser.get_format_instructions(),
                }
            )
            # 规范化
            valid_emotions = ["正面", "中性", "负面"]
            if result.get("emotion") not in valid_emotions:
                result["emotion"] = "中性"
            result["risk_score"] = max(0, min(100, int(result.get("risk_score", 30))))
            return result
        except Exception as e:
            logger.error(f"情绪分析失败: {e}")
            return {
                "emotion": "中性",
                "risk_score": 30,
                "reason": "分析失败"
            }

    async def rewrite_plain(self, text: str, dimension: str) -> dict:
        """
        话术平民化重组
        返回：readable_desc, action_hint
        """
        try:
            result = await self.plain_chain.ainvoke(
                {
                    "text": text,
                    "dimension": dimension,
                    "format_instructions": self.plain_parser.get_format_instructions(),
                }
            )
            # 限制长度
            desc = result.get("readable_desc", text[:30])
            if len(desc) > 30:
                desc = desc[:30]
            result["readable_desc"] = desc
            return result
        except Exception as e:
            logger.error(f"话术重组失败: {e}")
            return {
                "readable_desc": text[:30] if len(text) > 30 else text,
                "action_hint": "待处理"
            }

    async def generate_summary(
        self,
        room_name: str,
        window_hours: int,
        total_count: int,
        dimension_counts: dict,
        avg_risk: float,
        high_risk_count: int,
        typical_issues: str,
        max_len: int = 100,
    ) -> str:
        """
        生成群级别的通俗摘要
        """
        try:
            result = await self.summary_chain.ainvoke(
                {
                    "room_name": room_name,
                    "window_hours": window_hours,
                    "total_count": total_count,
                    "dimension_counts": dimension_counts,
                    "avg_risk": round(avg_risk, 1),
                    "high_risk_count": high_risk_count,
                    "typical_issues": typical_issues,
                    "max_len": max_len,
                }
            )
            # 提取纯文本
            summary = str(result.content if hasattr(result, 'content') else result)
            if len(summary) > max_len:
                summary = summary[:max_len]
            return summary
        except Exception as e:
            logger.error(f"摘要生成失败: {e}")
            return f"{room_name}群过去{window_hours}小时共{total_count}条消息，{high_risk_count}条需关注。"

    def get_emotion_icon(self, emotion: str) -> str:
        """获取情绪图标"""
        icons = {
            "正面": "🟢",
            "中性": "🟡",
            "负面": "🔴",
        }
        return icons.get(emotion, "🟡")

    def suggest_action(self, dimension: str, risk_score: int) -> str:
        """
        根据分类和风险得分生成建议处理动作
        """
        action_map = {
            "问题反馈": "跟进处理",
            "客户需求": "记录评估",
            "产品缺陷": "优先修复",
            "使用咨询": "回复指引",
        }
        base_action = action_map.get(dimension, "待处理")

        if risk_score >= 80:
            return f"⚠️ 紧急{base_action}"
        elif risk_score >= 60:
            return f"⏰ 尽快{base_action}"
        else:
            return f"📝 常规{base_action}"

    def get_dimension_icon(self, dimension: str) -> str:
        """获取维度图标"""
        icons = {
            "问题反馈": "⚡",
            "客户需求": "💡",
            "产品缺陷": "🔧",
            "使用咨询": "❓",
        }
        return icons.get(dimension, "📋")
