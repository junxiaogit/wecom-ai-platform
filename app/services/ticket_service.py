from datetime import datetime
import json
import os
from app.core.config import settings
import re
from app.services.dingtalk_service import DingTalkService, get_priority_icon, risk_score_to_priority
from app.core.utils import not_empty
from loguru import logger
from app.services.data_clean_service import DataCleanService


# 有效的问题类型和优先级
VALID_ISSUE_TYPES = ["使用咨询", "问题反馈", "产品需求", "产品缺陷"]
VALID_PRIORITIES = ["较低", "普通", "紧急", "非常紧急"]


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


def markdown_to_plain_text(markdown: str) -> str:
    """
    将钉钉 markdown 转换为纯文本格式（内容不变，去掉 markdown 符号）。
    """
    if not markdown:
        return ""
    text = markdown
    # 去掉 ### 标题符号
    text = re.sub(r"^###\s*", "", text, flags=re.M)
    # 去掉 --- 分隔线
    text = re.sub(r"^---+\s*$", "", text, flags=re.M)
    # 去掉 **加粗**
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    # 去掉 > 引用符号
    text = re.sub(r"^>\s*", "", text, flags=re.M)
    # 去掉 * 列表符号，替换为 •
    text = re.sub(r"^\*\s+", "•  ", text, flags=re.M)
    # 去掉 <font color='...'>...</font>
    text = re.sub(r"<font[^>]*>([^<]*)</font>", r"\1", text)
    # 将 [链接文字](url) 转为 url
    text = re.sub(r"\[([^\]]*)\]\(([^)]+)\)", r"\2", text)
    # 去掉多余空行（连续2个以上换行变成1个）
    text = re.sub(r"\n{3,}", "\n\n", text)
    # 去掉行首尾多余空格
    lines = [line.strip() for line in text.split("\n")]
    # 去掉空行
    result_lines = [l for l in lines if l.strip()]
    return "\n".join(result_lines)


def build_ticket_markdown(
    content: dict,
    *,
    issue_type: str | None = None,
    priority: str | None = None,
    phenomenon: str | None = None,
    summary: str | None = None,
    room_name: str | None = None,
    detail_url: str | None = None,
    issue_time: str | None = None,  # 新增：问题发生时间（已格式化，如 '2/3 09:22'）
    # 以下参数保留兼容（旧接口）
    risk_score: int = 0,
    issue_type_text: str | None = None,
    severity: str | None = None,
    category_display: str | None = None,
    assignee: str | None = None,
    detail_link: str | None = None,
    draft_id: int | None = None,
    hit_count: int | None = None,
    ticket_url: str | None = None,
    include_ticket_line: bool = True,
) -> str:
    """
    构建钉钉/TB 推送 Markdown（新格式）
    
    格式：
    问题类型：{issue_type}
    【优先级】{priority_icon} {priority}
    【问题】: {phenomenon}
    【总结】: {summary}
    【🏠客户群】: {room_name} | {detail_url}
    【时间】: {issue_time}
    """
    # 获取各字段值（优先使用新参数，否则从 content 获取）
    issue_type_val = issue_type or issue_type_text or content.get("issue_type") or "问题反馈"
    issue_type_val = normalize_issue_type(issue_type_val)
    
    # 优先级：优先使用明确传入的，否则从 risk_score 转换
    priority_val = priority or content.get("priority")
    if not priority_val:
        rs = risk_score or content.get("risk_score") or 0
        priority_val = risk_score_to_priority(int(rs))
    priority_val = normalize_priority(priority_val)
    
    # 问题描述
    phenomenon_val = phenomenon or content.get("phenomenon") or content.get("summary") or content.get("key_sentence") or "暂无"
    summary_val = summary or content.get("summary") or content.get("key_sentence") or phenomenon_val
    
    # 客户群和链接
    room_name_val = room_name or content.get("room_name") or content.get("room_id") or "-"
    detail_url_val = detail_url or detail_link or content.get("detail_url") or f"{settings.INTERNAL_BASE_URL}/api/ui/rooms/{content.get('room_id') or ''}"
    
    # 问题时间（优先使用传入的，否则从 content 获取）
    issue_time_val = issue_time or content.get("issue_time")
    
    # 构建新格式 Markdown
    return DingTalkService.build_markdown(
        issue_type=issue_type_val,
        priority=priority_val,
        phenomenon=phenomenon_val,
        summary=summary_val,
        room_name=room_name_val,
        detail_url=detail_url_val,
        issue_time=issue_time_val,
    )


def build_tb_note(note_summary: str, detail_url: str) -> str:
    """
    构建 TB 任务备注（简化版，只有两行）
    
    格式：
    - 原生摘要：{note_summary}
    - 原声链接：{detail_url}
    """
    return f"- 原生摘要：{note_summary or '暂无明确问题'}\n- 原声链接：{detail_url or '-'}\n"


def _ensure_full_dingtalk_markdown(content: dict) -> str | None:
    """
    Ensure content['dingtalk_markdown'] is the full DingTalk push markdown.
    Some older drafts may only have partial text in ai_assistant; this rebuilds
    the full markdown from structured fields when missing.
    
    关键：如果需要重建，必须先从现有markdown提取issue_type，避免丢失正确的问题类型
    """
    existing = content.get("dingtalk_markdown")
    
    if isinstance(existing, str) and ("问题类型" in existing and "【优先级】" in existing):
        # 关键修复：即使不重建markdown，也要确保content["issue_type"]与markdown一致
        extracted = _extract_issue_type_from_markdown(existing)
        if extracted and extracted != content.get("issue_type"):
            content["issue_type"] = extracted
        return existing

    # 需要重建 - 先从现有内容提取issue_type以保持一致性
    if isinstance(existing, str):
        extracted = _extract_issue_type_from_markdown(existing)
        if extracted and not content.get("issue_type"):
            content["issue_type"] = extracted

    try:
        rebuilt = build_ticket_markdown(content)
        content["dingtalk_markdown"] = rebuilt
        return rebuilt
    except Exception as e:
        logger.error(f"[_ensure_full_dingtalk_markdown] 重建失败: {e}")
        return existing if isinstance(existing, str) else None


def build_customfields_pending(content: dict) -> list[dict]:
    _ensure_full_dingtalk_markdown(content)
    # 关键：始终从 dingtalk_markdown 提取 issue_type，确保与钉钉推送一致
    # 这解决了"钉钉显示产品缺陷，但TB选择问题反馈"的不一致问题
    dingtalk_md = content.get("dingtalk_markdown")
    extracted_issue_type = _extract_issue_type_from_markdown(dingtalk_md)
    if extracted_issue_type:
        normalized = normalize_issue_type(extracted_issue_type)
        content["issue_type"] = normalized
    elif not content.get("issue_type"):
        content["issue_type"] = "问题反馈"  # 默认值
    # AI辅助 → 钉钉推送内容转为纯文本（内容完全一致，只去掉 markdown 符号）
    if not content.get("ai_assistant") or str(content.get("ai_assistant")).strip() in ("", "-"):
        dingtalk_md = content.get("dingtalk_markdown") or ""
        if dingtalk_md:
            content["ai_assistant"] = markdown_to_plain_text(dingtalk_md)
        else:
            # 如果没有 dingtalk_markdown，使用简化格式
            content["ai_assistant"] = build_tb_ai_assistant_text(content)

    mapping_entries = _load_customfield_mapping()
    pending: list[dict] = []
    if mapping_entries:
        for item in mapping_entries:
            cid = item.get("customfieldId") or item.get("id")
            if not cid:
                continue
            value = item.get("value")
            key = item.get("key") or item.get("content_key")
            if value is None and key:
                value = _resolve_content_value(content, key)
            if value is None:
                name = item.get("name")
                key = _guess_content_key_by_name(name or "")
                if key:
                    value = _resolve_content_value(content, key)
            pending.append({"customfieldId": cid, "value": value or "-"})
        content["customfields_pending"] = pending
        return pending

    # If no explicit mapping, try infer from customfield_dict.json by name.
    dict_items = _load_customfield_dict()
    if dict_items:
        for item in dict_items:
            cid = item.get("customfieldId") or item.get("id") or item.get("_id")
            if not cid:
                continue
            key = _guess_content_key_by_name(item.get("name") or "")
            if not key:
                continue
            value = _resolve_content_value(content, key)
            pending.append({"customfieldId": cid, "value": value or "-"})
        if pending:
            content["customfields_pending"] = pending
            return pending

    # Fallback: keep previous order-based mapping if nothing else is available.
    raw_ids = settings.CUSTOM_FIELDS_IDS or ""
    custom_ids = [i.strip() for i in raw_ids.split(",") if i.strip()]
    if not custom_ids:
        return []
    value_pool = [
        content.get("issue_type"),
        content.get("priority"),
        content.get("phenomenon"),
        content.get("summary"),
        content.get("room_name") or content.get("room_id"),
        content.get("client_version"),
        content.get("cbs_version"),
        content.get("image_id"),
    ]
    for idx, cid in enumerate(custom_ids):
        value = value_pool[idx] if idx < len(value_pool) else None
        pending.append({"customfieldId": cid, "value": value or "-"})
    content["customfields_pending"] = pending
    return pending


def build_ai_assistant_text(content: dict) -> str:
    if content.get("dingtalk_markdown"):
        return str(content.get("dingtalk_markdown"))
    parts = []
    if content.get("phenomenon"):
        parts.append(f"现象: {content.get('phenomenon')}")
    if content.get("summary"):
        parts.append(f"总结: {content.get('summary')}")
    return "\n".join(parts)


def _extract_issue_type_from_markdown(markdown: str | None) -> str | None:
    if not markdown:
        return None
    
    # 格式1：# 或 ### 问题类型：xxx（支持 🚨 前缀）
    match = re.search(r"^#{1,3}\s+(?:🚨\s*)?问题类型：\s*(.+)$", markdown, flags=re.M)
    if match:
        text = match.group(1).strip()
        if "【" in text:
            text = text.split("【", 1)[0].strip()
        return text or None
    
    # 格式2：问题类型：xxx（无标题前缀）
    match = re.search(r"^问题类型：\s*(.+)$", markdown, flags=re.M)
    if match:
        text = match.group(1).strip()
        if "【" in text:
            text = text.split("【", 1)[0].strip()
        return text or None
    
    # 格式3：**问题类型**：xxx（加粗格式）
    match = re.search(r"^\*\*问题类型\*\*：\s*(.+)$", markdown, flags=re.M)
    if match:
        text = match.group(1).strip()
        if "【" in text:
            text = text.split("【", 1)[0].strip()
        return text or None
    
    return None


def _extract_phenomenon_from_markdown(markdown: str | None) -> str | None:
    if not markdown:
        return None
    # 新格式：【问题】: xxx
    match = re.search(r"【问题】:\s*(.+)", markdown)
    if match:
        return match.group(1).strip()
    # 旧格式
    match = re.search(r">\s*\*\*现象\*\*:\s*(.+)", markdown)
    if match:
        return match.group(1).strip()
    return None


def _resolve_content_value(content: dict, key: str):
    raw_key = key.replace("content.", "")
    return content.get(raw_key)


def _guess_content_key_by_name(name: str) -> str | None:
    if not name:
        return None
    text = name.strip()
    mapping = {
        "问题类型": "issue_type",
        "反馈问题类型": "issue_type",
        "类型": "issue_type",
        "优先级": "priority",
        "严重度": "severity",
        "等级": "severity",
        "风险": "risk_score",
        "风险概率": "risk_score",
        "分类": "category_short",
        "现象": "phenomenon",
        "问题现象": "phenomenon",
        "问题": "phenomenon",
        "总结": "summary",
        "关键句": "key_sentence",
        "关键": "key_sentence",
        "AI辅助": "ai_assistant",
        "概括": "summary",
        "摘要": "summary",
        "客户群": "room_name",
        "群": "room_name",
        "标签": "room_name",
        "客户": "customer",
        "环境": "environment",
        "版本": "version",
        "客户端版本": "client_version",
        "CBS版本": "cbs_version",
        "镜像ID": "image_id",
        "镜像": "image_id",
        "复现": "repro_steps",
        "步骤": "repro_steps",
    }
    for keyword, key in mapping.items():
        if keyword in text:
            return key
    return None


def _load_customfield_mapping() -> list[dict]:
    path = settings.CUSTOM_FIELDS_MAPPING_PATH
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return [{"customfieldId": k, "key": v} for k, v in data.items()]
        if isinstance(data, list):
            return data
    except Exception:
        return []
    return []


def _load_customfield_dict() -> list[dict]:
    path = settings.CUSTOM_FIELDS_DICT_PATH
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except Exception:
        return []
    return []


def build_customfields_for_create(content: dict) -> list[dict]:
    _ensure_full_dingtalk_markdown(content)
    # Always rebuild to ensure latest rules apply.
    if content.get("ai_assistant") or content.get("dingtalk_markdown"):
        content.pop("customfields_pending", None)
    pending = build_customfields_pending(content)
    # Force write AI辅助 into AI辅助 field: 钉钉推送内容转为纯文本
    # 优先使用 dingtalk_markdown 转换，保证内容与钉钉推送一致
    dingtalk_md = content.get("dingtalk_markdown") or ""
    if dingtalk_md:
        ai_assistant_text = markdown_to_plain_text(dingtalk_md)
    else:
        ai_assistant_text = content.get("ai_assistant") or build_tb_ai_assistant_text(content)
    ai_assistant_cf_id = None
    for item in _load_customfield_mapping():
        key = item.get("key") or item.get("content_key")
        if key == "ai_assistant":
            ai_assistant_cf_id = item.get("customfieldId") or item.get("id")
            break
    customfields = []
    for item in pending or []:
        cf_id = item.get("customfieldId")
        value = item.get("value")
        if not cf_id:
            continue
        customfields.append(
            {
                "cfId": cf_id,
                "value": [
                    {
                        "title": str(value) if value is not None else "-",
                        "description": "",
                        "meta": "",
                        "metaString": "",
                    }
                ],
            }
        )
    if ai_assistant_text and ai_assistant_cf_id:
        replaced = False
        for item in customfields:
            if item.get("cfId") == ai_assistant_cf_id:
                item["value"] = [
                    {
                        "title": str(ai_assistant_text),
                        "description": "",
                        "meta": "",
                        "metaString": "",
                    }
                ]
                replaced = True
                break
        if not replaced:
            customfields.append(
                {
                    "cfId": ai_assistant_cf_id,
                    "value": [
                        {
                            "title": str(ai_assistant_text),
                            "description": "",
                            "meta": "",
                            "metaString": "",
                        }
                    ],
                }
            )
    return customfields


def build_tb_ai_assistant_text(content: dict) -> str:
    """
    构建 TB AI辅助字段的文本内容（新格式，简化版）
    
    格式：
    问题类型：{issue_type}
    【优先级】{priority_icon} {priority}
    【问题】: {phenomenon}
    【总结】: {summary}
    【🏠客户群】: {room_name} | {detail_url}
    """
    issue_type = normalize_issue_type(content.get("issue_type") or "问题反馈")
    
    # 获取优先级
    priority = content.get("priority")
    if not priority:
        risk_score = int(content.get("risk_score") or 0)
        priority = risk_score_to_priority(risk_score)
    priority = normalize_priority(priority)
    priority_icon = get_priority_icon(priority)
    
    phenomenon = content.get("phenomenon") if not_empty(content.get("phenomenon")) else (content.get("summary") if not_empty(content.get("summary")) else "暂无")
    summary = content.get("summary") if not_empty(content.get("summary")) else (content.get("key_sentence") if not_empty(content.get("key_sentence")) else phenomenon)
    
    room_label = content.get("room_name") or content.get("room_id") or "-"
    detail_url = content.get("detail_url") or f"{settings.INTERNAL_BASE_URL}/api/ui/rooms/{content.get('room_id') or ''}"

    return "\n".join(
        [
            f"问题类型：{issue_type}",
            f"【优先级】{priority_icon} {priority}",
            f"【问题】: {phenomenon}",
            f"【总结】: {summary}",
            f"【🏠客户群】: {room_label} | {detail_url}",
        ]
    )


def build_customfields_block(content: dict) -> str:
    pending = content.get("customfields_pending")
    if not isinstance(pending, list) or not pending:
        pending = build_customfields_pending(content)
    if not pending:
        return ""
    lines = ["【自定义字段（预填）】"]
    for item in pending:
        cid = item.get("customfieldId") or "-"
        val = item.get("value") or "-"
        lines.append(f"- cf:{cid} = {val}")
    return "\n".join(lines)


async def generate_ticket_title_llm(phenomenon: str, summary: str, max_len: int = 45) -> str:
    """
    使用 LLM 生成详细的工单标题
    - 30-40字
    - 完整描述问题
    - 不带"用户反馈"等前缀
    - 不包含群名和用户名
    """
    from app.core.llm_factory import get_fast_llm
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.output_parsers import StrOutputParser

    prompt = ChatPromptTemplate.from_template("""
你是企业级工单标题生成助手。根据问题信息，生成详细的工单标题。

【问题现象】{phenomenon}
【问题总结】{summary}

【标题要求】
- 字数控制在30-40字
- 完整描述问题：包含问题类型 + 具体现象 + 影响范围
- 只描述问题本身，不要带"用户反馈"、"客户反馈"等前缀
- 不要带设备ID（ACN/ATP开头的长串）
- 不要包含用户姓名、群聊名称、@提及

【示例】
- 输入：现象="关机失败、闪屏"，总结="多台设备出现关机失败和闪屏问题"
  输出：多台设备出现关机失败和闪屏问题，影响批量设备管理和运维操作

- 输入：现象="代理设置失败"，总结="批量设置代理操作失败率较高"
  输出：批量代理设置操作失败率高，多次重试仍无法完成配置任务

- 输入：现象="云机打不开画面"，总结="云机实例无法正常显示画面"
  输出：云机实例画面无法正常显示，用户无法进行远程操作和业务处理

直接输出标题，不要加引号：
""")

    try:
        chain = prompt | get_fast_llm() | StrOutputParser()
        title = await chain.ainvoke({
            "phenomenon": phenomenon or "",
            "summary": summary or ""
        })
        title = title.strip().strip('"').strip("'")
        # 清理可能残留的设备ID
        title = re.sub(r'\b(ACN|ATP)\d{10,}\b', '', title)
        title = " ".join(title.split())  # 清理多余空白
        return title[:max_len] if title else (phenomenon or "未知问题")[:max_len]
    except Exception as e:
        logger.warning(f"LLM 生成标题失败: {e}")
        return (phenomenon or summary or "未知问题")[:max_len]


async def generate_note_summary_llm(text: str, max_len: int = 30) -> str:
    """
    使用 LLM 生成原声摘要（用于 TB 备注）
    - 30字以内
    - 只描述问题本身
    """
    from app.core.llm_factory import get_fast_llm
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.output_parsers import StrOutputParser

    # 控制输入长度
    src = " ".join(str(text or "").split())[:800]
    if not src:
        return "暂无明确问题"

    prompt = ChatPromptTemplate.from_template("""
你是企业级问题摘要助手。根据聊天内容，提取并总结核心问题。

【聊天内容】
{text}

【要求】
- 严格30字以内
- 只描述问题本身（如：系统卡顿、无法登录、数据丢失）
- 禁止包含：设备ID、用户名、JSON数据、引用格式、群聊名称
- 如果内容无法识别问题，输出"暂无明确问题"

直接输出问题摘要：
""")

    try:
        chain = prompt | get_fast_llm() | StrOutputParser()
        result = await chain.ainvoke({"text": src})
        result = result.strip()
        return result[:max_len] if result else "暂无明确问题"
    except Exception as e:
        logger.warning(f"LLM 生成摘要失败: {e}")
        return "暂无明确问题"


async def extract_versions_and_image_llm(text: str) -> dict:
    """
    从对话中提取版本与镜像信息（用于 TB 自定义字段）
    输出字段：
    - client_version: 客户端版本（如未知输出 "-"）
    - cbs_version: CBS版本（如未知输出 "-"）
    - image_id: 镜像ID（如未知输出 "-"）
    """
    from app.core.llm_factory import get_fast_llm
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.output_parsers import StrOutputParser

    # 控制输入长度，避免把整段超长聊天塞给模型
    src = " ".join(str(text or "").split())
    src = src[:800]
    if not src:
        return {"client_version": "-", "cbs_version": "-", "image_id": "-"}

    prompt = ChatPromptTemplate.from_template(
        """
你是企业级信息抽取助手。请从聊天内容中提取以下字段，并严格输出 JSON（不要输出多余文字）。

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

输出 JSON 例子：
{{"client_version":"1.2.3","cbs_version":"20251211_12","image_id":"img-25121161049"}}
"""
    )

    try:
        chain = prompt | get_fast_llm() | StrOutputParser()
        raw = await chain.ainvoke({"text": src})
        raw = (raw or "").strip()
        # 尝试从输出中截取 JSON
        if "{" in raw and "}" in raw:
            raw = raw[raw.find("{") : raw.rfind("}") + 1]
        data = json.loads(raw)
        return {
            "client_version": str(data.get("client_version") or "-").strip() or "-",
            "cbs_version": str(data.get("cbs_version") or "-").strip() or "-",
            "image_id": str(data.get("image_id") or "-").strip() or "-",
        }
    except Exception:
        return {"client_version": "-", "cbs_version": "-", "image_id": "-"}


VALID_PLATFORMS = ["CBS", "客户端", "ROM", "移动端", "其他"]


def normalize_platform(platform: str | None) -> str:
    """标准化端口分类，确保返回有效值"""
    if platform in VALID_PLATFORMS:
        return platform
    return "其他"


async def analyze_complete_llm(text: str) -> dict:
    """
    使用 LLM 一次性分析所有字段（完整分析）
    输出字段：
    - issue_type: 问题类型
    - priority: 优先级
    - phenomenon: 问题概括
    - summary: 问题总结
    - problem_quote: 问题原文关键句（用于定位问题消息）
    - platform: 端口分类（CBS/客户端/ROM/移动端/其他）
    - client_version: 客户端版本
    - cbs_version: CBS版本
    - image_id: 镜像ID
    """
    from app.core.llm_factory import get_fast_llm
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.output_parsers import StrOutputParser
    from app.prompts.issue_extraction import COMPLETE_ANALYSIS_PROMPT

    # 控制输入长度
    src = " ".join(str(text or "").split())[:1500]
    if not src:
        return {
            "issue_type": "问题反馈",
            "priority": "普通",
            "phenomenon": "暂无",
            "summary": "暂无",
            "problem_quote": "",
            "platform": "其他",
            "client_version": "-",
            "cbs_version": "-",
            "image_id": "-",
        }

    prompt = ChatPromptTemplate.from_template(COMPLETE_ANALYSIS_PROMPT)

    try:
        chain = prompt | get_fast_llm() | StrOutputParser()
        raw = await chain.ainvoke({"text": src})
        raw = (raw or "").strip()
        # 尝试从输出中截取 JSON
        if "{" in raw and "}" in raw:
            raw = raw[raw.find("{") : raw.rfind("}") + 1]
        data = json.loads(raw)
        return {
            "issue_type": normalize_issue_type(data.get("issue_type")),
            "priority": normalize_priority(data.get("priority")),
            "phenomenon": str(data.get("phenomenon") or "暂无")[:30],
            "summary": str(data.get("summary") or "暂无")[:50],
            "problem_quote": str(data.get("problem_quote") or "").strip()[:60],
            "first_problem_quote": str(data.get("first_problem_quote") or "").strip()[:60],
            "last_discussion_quote": str(data.get("last_discussion_quote") or "").strip()[:60],
            "platform": normalize_platform(data.get("platform")),
            "client_version": str(data.get("client_version") or "-").strip() or "-",
            "cbs_version": str(data.get("cbs_version") or "-").strip() or "-",
            "image_id": str(data.get("image_id") or "-").strip() or "-",
        }
    except Exception as e:
        logger.warning(f"LLM 完整分析失败: {e}")
        return {
            "issue_type": "问题反馈",
            "priority": "普通",
            "phenomenon": "暂无",
            "summary": "暂无",
            "problem_quote": "",
            "platform": "其他",
            "client_version": "-",
            "cbs_version": "-",
            "image_id": "-",
        }


async def pre_judge_has_issue(chat_context: str) -> tuple[bool, str]:
    """
    轻量级 LLM 预判断：对话中是否包含有效问题
    
    用于在完整分析前快速判断消息批次是否包含需要处理的问题，
    避免对纯闲聊/确认回复/日常问候等无效内容进行完整分析，节省 tokens 和时间。
    
    Args:
        chat_context: 对话内容（多条消息拼接）
    
    Returns:
        (has_issue: bool, reason: str)
        - has_issue: 是否包含有效问题
        - reason: 判断原因（简短，如"功能异常"、"确认回复"等）
    """
    from app.core.llm_factory import get_fast_llm
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.output_parsers import StrOutputParser
    from app.prompts.issue_extraction import get_pre_judge_prompt
    
    # 控制输入长度（预判断只需较短内容，降低 token 消耗）
    src = " ".join(str(chat_context or "").split())[:800]
    if not src:
        return False, "内容为空"
    
    # 快速规则预判（在调用 LLM 前先用规则过滤明显的情况）
    # 如果内容很短且没有问题相关关键词，直接判定无问题
    problem_keywords = [
        "报错", "错误", "失败", "异常", "崩溃", "白屏", "黑屏", "闪退", "卡", "慢",
        "不能", "无法", "不行", "打不开", "进不去", "用不了", "显示不", "加载不",
        "怎么", "如何", "在哪", "为什么", "什么原因",
        "希望", "建议", "能不能", "最好", "需要",
        "bug", "Bug", "BUG", "问题", "故障",
    ]
    has_keyword = any(kw in src for kw in problem_keywords)
    
    # 如果长度较短且无关键词，大概率是噪音
    if len(src) < 50 and not has_keyword:
        return False, "内容过短"
    
    prompt_template = get_pre_judge_prompt()
    prompt = ChatPromptTemplate.from_template(prompt_template)
    
    try:
        chain = prompt | get_fast_llm() | StrOutputParser()
        raw = await chain.ainvoke({"chat_context": src})
        raw = (raw or "").strip()
        
        # 尝试从输出中截取 JSON
        if "{" in raw and "}" in raw:
            raw = raw[raw.find("{") : raw.rfind("}") + 1]
        
        data = json.loads(raw)
        has_issue = bool(data.get("has_issue", False))
        reason = str(data.get("reason", "未知"))[:20]
        
        return has_issue, reason
        
    except Exception as e:
        logger.warning(f"LLM 预判断失败: {e}，默认放行")
        # 预判断失败时默认放行，不阻塞正常流程
        return True, "预判断异常"


def build_ticket_title(content: dict, max_len: int = 45) -> str:
    """
    构建 TB 工单标题：优先使用 LLM 生成的标题（30-40字）
    """
    # 如果已有 LLM 生成的标题，直接使用
    if content.get("llm_title"):
        return content["llm_title"][:max_len]

    # 否则使用 phenomenon
    phenomenon = (
        _extract_phenomenon_from_markdown(content.get("dingtalk_markdown"))
        or content.get("phenomenon")
        or content.get("summary")
        or content.get("key_sentence")
        or content.get("description")
        or "未提供"
    )
    text = " ".join(str(phenomenon).split())  # 清理多余空白
    return text[:max_len]


def build_ticket_draft(
    room_id: str,
    summary: str,
    issue_type: str | None = None,
    priority: str | None = None,
    phenomenon: str | None = None,
    room_name: str | None = None,
    detail_url: str | None = None,
    platform: str | None = None,  # 端口分类
    client_version: str | None = None,
    cbs_version: str | None = None,
    image_id: str | None = None,
    # 以下参数保留兼容
    category: str | None = None,
    severity: str | None = None,
    risk_score: int = 0,
    raw_text: str | None = None,
    customer: str | None = None,
    key_sentence: str | None = None,
    ai_solution: str | None = None,
    similar_case_solution: str | None = None,
    suggested_reply: str | None = None,
    environment: str | None = None,
    version: str | None = None,
    repro_steps: str | None = None,
    attachments: list[str] | None = None,
) -> dict:
    """
    构建 TicketDraft.content（结构化 + 更适合直接建单）
    
    新格式字段：
    - issue_type: 问题类型
    - priority: 优先级
    - phenomenon: 问题概括
    - summary: 问题总结
    - platform: 端口分类（CBS/客户端/ROM/移动端/其他）
    - client_version/cbs_version/image_id: 版本信息
    """
    # 标准化字段
    issue_type_val = normalize_issue_type(issue_type)
    if not priority:
        priority = risk_score_to_priority(risk_score)
    priority_val = normalize_priority(priority)
    
    # 构建描述（简化版）
    desc_parts: list[str] = []
    if not_empty(phenomenon):
        desc_parts.append(f"【问题现象】\n{str(phenomenon).strip()}")
    if not_empty(summary):
        desc_parts.append(f"【问题摘要】\n{str(summary).strip()}")
    if not_empty(raw_text):
        # 原文放最后并截断，避免过长
        cleaned_raw = DataCleanService.clean_for_llm(str(raw_text))
        if len(cleaned_raw) > 1200:
            cleaned_raw = cleaned_raw[:1200] + "…"
        desc_parts.append(f"【客户原文】\n{cleaned_raw}")
    description_text = "\n\n".join([p for p in desc_parts if p]) or (raw_text or "")

    return {
        "title": build_ticket_title({"phenomenon": phenomenon, "summary": summary}),
        "room_id": room_id,
        "room_name": room_name,
        "issue_type": issue_type_val,
        "priority": priority_val,
        "phenomenon": phenomenon,
        "summary": summary,
        "platform": normalize_platform(platform),  # 端口分类
        "detail_url": detail_url or f"{settings.INTERNAL_BASE_URL}/api/ui/rooms/{room_id}",
        "client_version": client_version or "-",
        "cbs_version": cbs_version or "-",
        "image_id": image_id or "-",
        "description": description_text,
        # 保留兼容字段
        "customer": customer,
        "category": category,
        "severity": severity,
        "risk_score": risk_score,
        "key_sentence": key_sentence,
        "ai_solution": ai_solution,
        "similar_case_solution": similar_case_solution,
        "suggested_reply": suggested_reply,
        "environment": environment,
        "version": version,
        "repro_steps": repro_steps,
        "attachments": attachments or [],
        "created_at": datetime.utcnow().isoformat(),
        "status": "draft",
    }
