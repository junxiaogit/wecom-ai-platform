from loguru import logger
import re
from app.core.config import settings
from app.services.ticket_service import build_ticket_markdown, build_customfields_block, build_customfields_pending


async def generate_note_summary_llm(raw_text: str, max_len: int = 30) -> str:
    """
    使用 LLM 生成精炼的问题概括（用于TB备注）
    - 30字以内
    - 只描述问题本身
    - 不包含设备ID、用户名、JSON数据
    """
    from app.core.llm_factory import get_fast_llm
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.output_parsers import StrOutputParser

    # 先预清洗：移除明显的设备ID和JSON格式
    clean_text = re.sub(r'\b(ACN|ATP)\d{10,}\b', '', raw_text)
    clean_text = re.sub(r'\{[^}]+\}', '', clean_text)
    clean_text = re.sub(r'\[[^\]]+\]', '', clean_text)
    clean_text = re.sub(r'"[^"]{50,}"', '', clean_text)  # 移除过长的引用
    clean_text = " ".join(clean_text.split())[:500]  # 限制输入长度

    if not clean_text or len(clean_text) < 5:
        return "问题信息不足"

    prompt = ChatPromptTemplate.from_template("""
根据以下聊天内容，提取并总结核心问题。

【原始内容】
{text}

【要求】
- 严格30字以内
- 只描述问题本身（如：系统卡顿、无法登录、数据丢失等）
- 禁止包含：设备ID、用户名、JSON数据、引用格式
- 如果内容无法识别问题，输出"暂无明确问题"

【示例输出】
- 实例白屏，重启后仍无法恢复
- 开机速度异常缓慢，等待超过一分钟
- 改机属性操作失败，显示超时

直接输出问题概括：
""")

    try:
        chain = prompt | get_fast_llm() | StrOutputParser()
        summary = await chain.ainvoke({"text": clean_text})
        summary = summary.strip().strip('"').strip("'")
        # 再次清理可能残留的脏数据
        summary = re.sub(r'\b(ACN|ATP)\d+\b', '', summary)
        summary = re.sub(r'[{}\[\]]', '', summary)
        summary = " ".join(summary.split())
        return summary[:max_len] if summary else "暂无明确问题"
    except Exception as e:
        logger.warning(f"LLM 生成问题概括失败: {e}")
        return "问题概括生成失败"


def _build_task_note(content: dict | None, description: str) -> str:
    """
    生成 TB 工单备注字段（结构化版，避免“一句话工单”）
    优先使用 llm_note_summary（LLM生成的），并补充关键字段与原文摘录。
    """
    if not content:
        return description

    issue_type = content.get("issue_type") or "问题反馈"
    severity = content.get("severity") or "-"
    risk_score = content.get("risk_score")
    category = content.get("category_short") or content.get("category") or "-"
    room_label = content.get("room_name") or content.get("room_id") or "-"
    customer = content.get("customer") or "-"
    detail_url = content.get("detail_url") or "-"

    # 优先使用 LLM 生成的概括
    summary = (
        content.get("llm_note_summary")
        or content.get("phenomenon")
        or content.get("key_sentence")
        or (description[:50] if description else "")
        or "暂无明确问题"
    )
    phenomenon = content.get("phenomenon") or "-"
    key_sentence = content.get("key_sentence") or "-"
    environment = content.get("environment") or "-"
    version = content.get("version") or "-"
    repro_steps = content.get("repro_steps") or "-"
    attachments = content.get("attachments") or []

    # 原文摘录：避免过长，且清理明显的设备ID
    raw_excerpt = (content.get("description") or description or "")
    raw_excerpt = re.sub(r"\b(ACN|ATP)\d{10,}\b", "[设备ID]", raw_excerpt)
    raw_excerpt = " ".join(str(raw_excerpt).split())
    if len(raw_excerpt) > 600:
        raw_excerpt = raw_excerpt[:600] + "…"

    risk_text = f"{int(risk_score)}分" if isinstance(risk_score, (int, float)) else (str(risk_score) if risk_score is not None else "-")
    attach_text = ", ".join([str(a) for a in attachments]) if isinstance(attachments, list) and attachments else "-"

    # 按需求文档：备注(note) 只写两行
    return "\n".join(
        [
            f"- 原生摘要：{str(summary)[:30]}",
            f"- 原声链接：{detail_url}",
        ]
    )


def build_task_payload(title: str, description: str, content: dict | None = None) -> dict | None:
    project_id = settings.TEAMBITION_PROJECT_ID
    if not project_id:
        logger.warning("Teambition 未配置 project_id")
        return None
    note = _build_task_note(content, description)
    payload = {"content": title, "note": note, "projectId": project_id}
    # 默认状态（例如：待处理）
    if settings.TEAMBITION_DEFAULT_TASKFLOWSTATUS_ID:
        payload["taskflowstatusId"] = settings.TEAMBITION_DEFAULT_TASKFLOWSTATUS_ID
    tag_text = (content or {}).get("tag_text") or (content or {}).get("room_name") or (content or {}).get("room_id")
    if tag_text:
        payload["tagNames"] = [str(tag_text)]
    if settings.TEAMBITION_TASK_TYPE_ID:
        payload["scenariofieldconfigId"] = settings.TEAMBITION_TASK_TYPE_ID
    return payload


def create_task(title: str, description: str) -> str | None:
    logger.info("Teambition 建单已切换为 MCP 模式，跳过 API 调用")
    return None


def get_task_url(task_id: str | None) -> str | None:
    if not task_id:
        return None
    base = settings.TEAMBITION_TASK_URL_BASE.rstrip("/")
    return f"{base}/{task_id}"
