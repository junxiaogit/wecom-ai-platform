import asyncio
import time
from collections import defaultdict
from datetime import datetime, timedelta
from loguru import logger
from typing import List
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.exc import IntegrityError
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.utils import (
    is_empty_value,
    not_empty,
    get_ai_solution,
    get_soothing_reply,
    get_similar_cause,
    get_similar_solution,
    DEFAULT_AI_SOLUTION,
    DEFAULT_SOOTHING_REPLY,
    DEFAULT_AI_SOLUTIONS,
)
from app.models.chat_record import ChatRecord
from app.models.sql_models import (
    WeComMessage,
    Issue,
    TicketDraft,
    IngestState,
    RoomAssignee,
    AlertEvent,
    RoomInfo,
    RoomPollingState,
)
from app.services.data_service import _extract_content
from app.services.data_clean_service import DataCleanService
from app.services.ticket_service import (
    build_ticket_draft,
    build_ticket_markdown,
    build_ticket_title,
    build_tb_note,
    build_customfields_pending,
    build_customfields_for_create,
    build_ai_assistant_text,
    build_tb_ai_assistant_text,
    normalize_issue_type,
    normalize_priority,
    markdown_to_plain_text,
    generate_ticket_title_llm,
    generate_note_summary_llm,
    extract_versions_and_image_llm,
    analyze_complete_llm,
    pre_judge_has_issue,
)
from app.services.reply_service import generate_reply
from app.services import data_service
from app.services.vector_service import vector_kb
from app.agents.assistant import AssistantAgent
from app.services.wecom_service import WeComService
from app.agents.sentinel import SentinelAgent
from app.services.dingtalk_service import DingTalkService, risk_score_to_priority
from app.services.alert_policy_service import should_send_alert, build_aggregate_summary
from app.services.aggregation_service import update_issue_aggregation
from app.services.faq_service import FaqService
from app.services.issue_filter_service import is_hard_issue, check_resolved_status
from app.services.teambition_service import create_task, get_task_url, build_task_payload
from app.services.mcp_bridge_service import submit_mcp_task
from app.services.teambition_oapi_service import create_task_oapi, update_task_customfield


SOURCE_KEY = "chat_records"
ARCHIVE_SOURCE_KEY = "wecom_archive"

# ============ 批处理模式：冷却时间跟踪 ============
# 记录每个 room_id 上次完整处理的时间戳（内存字典，服务重启会重置）
_room_last_full_process: dict[str, float] = {}

# ============ 排除群列表缓存（避免每轮全表扫描） ============
_excluded_rooms_cache: set[str] = set()
_excluded_rooms_cache_ts: float = 0

# ============ 群聊维度轮询：每个群的状态 ============
# 结构: { "room_id": {"last_msgtime": int, "pending_count": int, "last_processed_at": float} }
_room_state: dict[str, dict] = {}

# ============ 每日周期跟踪 ============
# 当前周期日期（格式 YYYY-MM-DD），用于检测是否跨越9:00进入新周期
_current_cycle_date: str = ""


def _get_current_cycle_start() -> int:
    """
    获取当前周期的起始时间戳（毫秒）
    
    周期定义：
    - 如果当前时间 >= 当日 DAILY_CYCLE_START_HOUR 点，返回今天该时刻
    - 如果当前时间 < 当日 DAILY_CYCLE_START_HOUR 点，返回昨天该时刻
    
    例如 DAILY_CYCLE_START_HOUR=9：
    - 当前 2/5 14:00 -> 返回 2/5 09:00
    - 当前 2/5 08:00 -> 返回 2/4 09:00
    
    Returns:
        周期起始时间的毫秒时间戳
    """
    now = datetime.now()
    cycle_hour = settings.DAILY_CYCLE_START_HOUR
    today_cycle_start = now.replace(hour=cycle_hour, minute=0, second=0, microsecond=0)
    
    if now >= today_cycle_start:
        cycle_start = today_cycle_start
    else:
        cycle_start = today_cycle_start - timedelta(days=1)
    
    return int(cycle_start.timestamp() * 1000)


def _check_and_reset_cycle(db: Session = None) -> bool:
    """
    检测是否进入新的每日周期，如果是则重置所有群聊状态
    
    每天 DAILY_CYCLE_START_HOUR 点（默认9:00）开始新周期：
    - 重置所有群的 last_msgtime 到新周期起点
    - 重置所有群的 pending_count 为 0
    - 如果提供了 db 参数，会将重置后的状态持久化到数据库
    
    Args:
        db: 数据库会话（可选），用于持久化重置后的状态
    
    Returns:
        True 如果发生了周期重置，否则 False
    """
    global _current_cycle_date, _room_state
    
    cycle_start_ms = _get_current_cycle_start()
    cycle_date = datetime.fromtimestamp(cycle_start_ms / 1000).strftime("%Y-%m-%d")
    
    if _current_cycle_date != cycle_date:
        old_cycle = _current_cycle_date or "(首次启动)"
        
        # 重置前：标记有未分析消息的群聊为 needs_flush，避免消息丢失
        flush_rooms = []
        for room_id, state in _room_state.items():
            p = state.get("pending_count", 0)
            r = state.get("raw_pending_count", 0)
            if p >= 2 or r >= 5:
                state["needs_flush"] = True
                flush_rooms.append((room_id, p, r))
        
        if flush_rooms:
            logger.info(
                f"[周期重置] 发现 {len(flush_rooms)} 个群有未分析消息，已标记为 needs_flush，"
                f"将在下一轮优先分析后再清零"
            )
            for rid, p, r in flush_rooms:
                logger.debug(f"  needs_flush: room={rid}, pending={p}, raw={r}")
        
        logger.info(
            f"[周期重置] 进入新周期: {cycle_date}（上一周期: {old_cycle}），"
            f"重置 {len(_room_state)} 个群聊状态，周期起点={datetime.fromtimestamp(cycle_start_ms/1000)}"
        )
        _current_cycle_date = cycle_date
        
        # 重置所有群的游标到新周期起点
        # 注意：needs_flush 的群保留 pending_count，等分析完再清零
        for room_id in _room_state:
            _room_state[room_id]["last_msgtime"] = cycle_start_ms
            if not _room_state[room_id].get("needs_flush"):
                _room_state[room_id]["pending_count"] = 0
                _room_state[room_id]["raw_pending_count"] = 0
        
        # 持久化重置后的状态到数据库
        if db and _room_state:
            _save_all_room_states(db)
        
        return True
    return False


def _get_room_state(room_id: str) -> dict:
    """获取或初始化群聊状态"""
    if room_id not in _room_state:
        _room_state[room_id] = {
            "last_msgtime": 0,          # 该群已处理到的游标
            "pending_count": 0,          # 累积未分析的有效消息数（非噪音）
            "raw_pending_count": 0,      # 累积未分析的原始消息数（所有 text）
            "last_processed_at": 0,      # 上次处理时间（用于排序）
        }
    return _room_state[room_id]


def _load_all_room_states(db: Session) -> int:
    """
    启动时从数据库加载所有群聊状态到内存
    
    同时设置当前周期日期，避免误触发周期重置
    
    Args:
        db: 数据库会话
    
    Returns:
        加载的状态数量
    """
    global _room_state, _current_cycle_date
    
    try:
        states = db.query(RoomPollingState).all()
        for state in states:
            _room_state[state.room_id] = {
                "last_msgtime": int(state.last_msgtime or 0),
                "pending_count": int(state.pending_count or 0),
                "raw_pending_count": int(state.raw_pending_count or 0),
                "last_processed_at": float(state.last_processed_at or 0) / 1000,  # 转换为秒
            }
        
        # 加载状态后，设置当前周期日期，避免 _check_and_reset_cycle 误触发重置
        # 这样只有真正跨天（过了9:00）时才会重置
        if states:
            cycle_start_ms = _get_current_cycle_start()
            _current_cycle_date = datetime.fromtimestamp(cycle_start_ms / 1000).strftime("%Y-%m-%d")
            logger.info(f"[状态加载] 从数据库加载了 {len(states)} 个群聊状态，当前周期={_current_cycle_date}")
        else:
            logger.info("[状态加载] 数据库无历史状态，将从头开始")
        
        return len(states)
    except Exception as e:
        logger.warning(f"[状态加载] 加载失败: {e}，将从头开始")
        return 0


def _save_room_state(db: Session, room_id: str) -> None:
    """
    保存单个群聊状态到数据库
    
    Args:
        db: 数据库会话
        room_id: 群聊ID
    """
    state = _room_state.get(room_id)
    if not state:
        return
    
    try:
        db_state = db.query(RoomPollingState).filter(
            RoomPollingState.room_id == room_id
        ).first()
        
        if db_state:
            db_state.last_msgtime = state["last_msgtime"]
            db_state.pending_count = state["pending_count"]
            db_state.raw_pending_count = state.get("raw_pending_count", 0)
            db_state.last_processed_at = int(state["last_processed_at"] * 1000)  # 转换为毫秒
        else:
            db_state = RoomPollingState(
                room_id=room_id,
                last_msgtime=state["last_msgtime"],
                pending_count=state["pending_count"],
                raw_pending_count=state.get("raw_pending_count", 0),
                last_processed_at=int(state["last_processed_at"] * 1000),
            )
            db.add(db_state)
        
        db.commit()
    except Exception as e:
        logger.warning(f"[状态保存] 保存 room={room_id} 状态失败: {e}")
        db.rollback()


def _save_all_room_states(db: Session) -> None:
    """
    保存所有群聊状态到数据库（用于周期重置时批量保存）
    
    Args:
        db: 数据库会话
    """
    saved_count = 0
    for room_id in _room_state:
        _save_room_state(db, room_id)
        saved_count += 1
    
    logger.info(f"[状态保存] 批量保存了 {saved_count} 个群聊状态")


def _is_in_cooldown(room_id: str) -> bool:
    """检查 room_id 是否在冷却期内"""
    last_time = _room_last_full_process.get(room_id, 0)
    return (time.time() - last_time) < settings.ROOM_COOLDOWN_SECONDS


def _update_cooldown(room_id: str) -> None:
    """更新 room_id 的冷却时间"""
    _room_last_full_process[room_id] = time.time()


async def _ai_dedup_check(new_phenomenon: str, existing_phenomena: list[str]) -> bool:
    """
    AI 语义去重兜底：判断新问题是否与已有问题列表中的某个问题本质相同。
    仅在算法层未命中、且同群已有工单时调用。
    
    Args:
        new_phenomenon: 新问题的现象描述
        existing_phenomena: 已有问题的现象描述列表
    
    Returns:
        True 如果 AI 判定为重复问题
    """
    from app.core.llm_factory import get_fast_llm
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.output_parsers import StrOutputParser
    
    if not existing_phenomena:
        return False
    
    existing_list = "\n".join(f"- {p}" for p in existing_phenomena if p)
    if not existing_list:
        return False
    
    prompt_text = (
        "判断【新问题】是否与【已有问题】列表中的某个问题是同一个问题（不同表述但本质相同）。\n\n"
        f"【新问题】：{new_phenomenon}\n\n"
        f"【已有问题】：\n{existing_list}\n\n"
        "只输出 YES 或 NO。YES=同一问题的不同表述，NO=完全不同的问题。"
    )
    
    try:
        prompt = ChatPromptTemplate.from_template("{text}")
        chain = prompt | get_fast_llm() | StrOutputParser()
        result = await chain.ainvoke({"text": prompt_text})
        answer = (result or "").strip().upper()
        is_dup = answer.startswith("YES")
        logger.debug(f"[AI去重] 新='{new_phenomenon[:20]}', 已有={len(existing_phenomena)}个, AI回答={answer}, 判定={'重复' if is_dup else '不同'}")
        return is_dup
    except Exception as e:
        logger.warning(f"[AI去重] LLM调用失败: {e}，默认放行")
        return False


async def _is_duplicate_issue(db: Session, room_id: str, phenomenon: str, cycle_start_ms: int) -> bool:
    """
    检查是否已存在相似问题的工单（支持全局去重）
    
    去重逻辑：
    - 如果 ISSUE_DEDUP_GLOBAL=True: 全局查询（跨群聊），时间窗口为 ISSUE_DEDUP_DAYS 天
    - 如果 ISSUE_DEDUP_GLOBAL=False: 仅同 room_id + 当前周期（原逻辑）
    - phenomenon（问题现象）相似度超过阈值（基于关键词重叠）
    
    Args:
        db: 数据库会话
        room_id: 群聊ID
        phenomenon: 新问题的现象描述
        cycle_start_ms: 当前周期起始时间（毫秒）
    
    Returns:
        True 如果发现重复问题，应跳过建单；False 表示是新问题
    """
    if not phenomenon:
        return False
    
    # 根据配置决定查询范围
    if settings.ISSUE_DEDUP_GLOBAL:
        # 全局去重：跨群聊，使用可配置的天数窗口
        since_dt = datetime.now() - timedelta(days=settings.ISSUE_DEDUP_DAYS)
        existing_tickets = (
            db.query(TicketDraft)
            .filter(TicketDraft.created_at >= since_dt)
            .all()
        )
        dedup_scope = f"全局({settings.ISSUE_DEDUP_DAYS}天)"
    else:
        # 原逻辑：仅同 room_id + 当前周期
        cycle_start_dt = datetime.fromtimestamp(cycle_start_ms / 1000)
        existing_tickets = (
            db.query(TicketDraft)
            .filter(
                TicketDraft.room_id == room_id,
                TicketDraft.created_at >= cycle_start_dt,
            )
            .all()
        )
        dedup_scope = "同群+当前周期"
    
    if not existing_tickets:
        return False
    
    # 提取关键词：使用字符级 bigram（2-gram）支持中文去重
    # 例如 "云机无法开机" → {"云机", "机无", "无法", "法开", "开机"}
    def _extract_bigrams(text: str) -> set:
        """提取中文 bigram 集合，同时保留英文单词"""
        import re
        text = text.lower().strip()
        # 移除标点符号
        text = re.sub(r'[，。、！？：；""''（）\(\)\[\]\s]+', '', text)
        if len(text) < 2:
            return set()
        # 提取所有连续2字符的组合
        bigrams = {text[i:i+2] for i in range(len(text) - 1)}
        return bigrams
    
    new_bigrams = _extract_bigrams(phenomenon)
    
    if not new_bigrams:
        return False
    
    threshold = settings.ISSUE_DEDUP_SIMILARITY_THRESHOLD
    
    for ticket in existing_tickets:
        # 从 content JSON 中提取 phenomenon
        old_phenomenon = ""
        if ticket.content and isinstance(ticket.content, dict):
            old_phenomenon = ticket.content.get("phenomenon", "")
        
        if not old_phenomenon:
            continue
        
        old_bigrams = _extract_bigrams(old_phenomenon)
        
        if not old_bigrams:
            continue
        
        # 计算相似度：Jaccard + containment 取最大值
        # containment 解决长短文本稀释问题（短文本大部分 bigram 被长文本包含即视为重复）
        overlap = len(new_bigrams & old_bigrams)
        union = len(new_bigrams | old_bigrams)
        min_len = min(len(new_bigrams), len(old_bigrams))
        jaccard = overlap / union if union > 0 else 0
        containment = overlap / min_len if min_len > 0 else 0
        similarity = max(jaccard, containment)
        
        if similarity >= threshold:
            # 增强日志：显示首次出现的群聊和时间
            first_room = ticket.room_id or "未知"
            first_time = ticket.created_at.strftime("%Y-%m-%d %H:%M") if ticket.created_at else "未知"
            logger.info(
                f"[去重命中] 范围={dedup_scope}, 当前群={room_id}, "
                f"首次出现群={first_room}, 首次时间={first_time}, "
                f"相似度={similarity:.2f}, 阈值={threshold}, "
                f"新问题='{phenomenon[:30]}...', 已有='{old_phenomenon[:30]}...'"
            )
            return True
    
    # ========== AI 语义去重兜底 ==========
    # 算法层未命中，但如果同群已有工单，调用 LLM 做最终确认
    same_room_tickets = [t for t in existing_tickets if t.room_id == room_id]
    if same_room_tickets:
        existing_phenomena = []
        for t in same_room_tickets:
            if t.content and isinstance(t.content, dict):
                p = t.content.get("phenomenon", "")
                if p:
                    existing_phenomena.append(p)
        
        if existing_phenomena:
            is_dup = await _ai_dedup_check(phenomenon, existing_phenomena)
            if is_dup:
                logger.info(
                    f"[AI去重命中] 范围={dedup_scope}, room={room_id}, "
                    f"新问题='{phenomenon[:30]}', AI判定与已有工单重复"
                )
                return True
    
    return False


def _format_issue_time(msgtime_ms: int | None) -> str | None:
    """
    将毫秒时间戳格式化为 '月/日 时:分' 格式
    
    Args:
        msgtime_ms: 毫秒时间戳
    
    Returns:
        格式化的时间字符串，如 '2/3 09:22'
    """
    if not msgtime_ms:
        return None
    from datetime import datetime
    try:
        dt = datetime.fromtimestamp(msgtime_ms / 1000)
        # Windows 使用 %#m/%#d，Linux 使用 %-m/%-d
        # 为兼容性，使用 lstrip('0') 手动去除前导零
        month = str(dt.month)
        day = str(dt.day)
        time_str = dt.strftime("%H:%M")
        return f"{month}/{day} {time_str}"
    except Exception:
        return None


# ============ 批处理模式：高风险关键词检测 ============
def _contains_high_risk_keyword(text: str) -> bool:
    """检查文本是否包含高风险关键词，命中则绕过冷却立即处理"""
    if not text:
        return False
    keywords = [k.strip() for k in settings.HIGH_RISK_KEYWORDS.split(",") if k.strip()]
    text_lower = text.lower()
    return any(kw in text_lower for kw in keywords)


# ============ 批处理模式：按 room_id 聚合消息 ============
def _group_messages_by_room(records: list) -> dict[str, list]:
    """将消息按 room_id 分组，跳过 roomid 为空/异常的记录"""
    groups: dict[str, list] = defaultdict(list)
    skipped = 0
    for r in records:
        # 跳过 roomid 为空/None 的记录，避免 room='None' 进入分析链路
        if not r.roomid or r.roomid is None:
            skipped += 1
            continue
        room_id = str(r.roomid)
        # 额外检查：跳过字符串 'None' 或空字符串
        if room_id in ('None', ''):
            skipped += 1
            continue
        groups[room_id].append(r)
    if skipped > 0:
        logger.debug(f"[分组] 跳过 {skipped} 条 roomid 为空的消息")
    return groups


def _merge_chat_context(messages: list) -> str:
    """将多条消息合并成一个 chat_context，用于批量分析（不过滤噪音，保留完整对话流）"""
    lines = []
    for m in messages:
        content = _extract_content(m.msgData)
        if content:
            clean = DataCleanService.sanitize(content)
            if clean:
                # 为 LLM 进一步清洗：移除 @用户名、引用格式、设备ID 等
                llm_clean = DataCleanService.clean_for_llm(clean)
                if llm_clean:
                    lines.append(llm_clean)
    return "\n".join(lines)


def _get_room_history_context(
    db: Session, 
    room_id: str, 
    limit: int = None,
    min_msgtime: int = None,
) -> tuple[str, list[dict]]:
    """
    从数据库获取房间历史消息作为上下文。
    用于扩大LLM分析的上下文范围，避免因当前批次消息过少导致总结简短。
    
    Args:
        db: 数据库会话
        room_id: 房间ID
        limit: 查询的最大消息数，默认使用配置 CONTEXT_HISTORY_COUNT
        min_msgtime: 最小消息时间戳（毫秒），用于过滤当前周期内的消息
    
    Returns:
        (合并后的历史消息文本, 消息列表[{msg_id, content}])
        消息列表用于后续根据 problem_quote 匹配定位问题消息
    """
    if limit is None:
        limit = settings.CONTEXT_HISTORY_COUNT
    
    # 构建查询条件（不再过滤 is_noise，让 LLM 看到完整对话流，包括确认消息）
    filters = [
        WeComMessage.room_id == room_id,
        WeComMessage.msg_type == "text",
    ]
    
    # 如果指定了最小时间戳，只获取该时间之后的消息（当前周期内）
    if min_msgtime:
        # min_msgtime 是毫秒时间戳，需要转换为 datetime 对象
        min_datetime = datetime.fromtimestamp(min_msgtime / 1000)
        filters.append(WeComMessage.msg_time >= min_datetime)
    
    # 查询该房间最近的历史消息（非噪音）
    recent_messages = (
        db.query(WeComMessage)
        .filter(*filters)
        .order_by(WeComMessage.seq.desc())
        .limit(limit)
        .all()
    )
    
    if not recent_messages:
        return "", []
    
    # 反转为时间顺序（从旧到新）
    recent_messages.reverse()
    
    # 构建消息列表和合并内容
    msg_list = []
    lines = []
    for msg in recent_messages:
        content = msg.content_clean or msg.content_raw
        if content:
            llm_clean = DataCleanService.clean_for_llm(content)
            if llm_clean:
                lines.append(llm_clean)
                msg_list.append({
                    "msg_id": msg.msg_id,
                    "content": llm_clean,
                })
    
    return "\n".join(lines), msg_list


def _find_best_anchor_msg(msg_list: list[dict], problem_quote: str) -> str | None:
    """
    根据 LLM 返回的 problem_quote（问题原文关键句），在消息列表中模糊匹配最相关的消息。
    
    Args:
        msg_list: 消息列表 [{msg_id, content}, ...]
        problem_quote: LLM 提取的问题原文关键句
    
    Returns:
        最匹配消息的 msg_id，如果找不到则返回 None
    """
    if not msg_list or not problem_quote:
        return msg_list[0]["msg_id"] if msg_list else None
    
    # 清理 problem_quote 用于匹配
    quote_clean = problem_quote.strip().lower()
    if len(quote_clean) < 5:
        return msg_list[0]["msg_id"] if msg_list else None
    
    best_match = None
    best_score = 0
    
    for msg in msg_list:
        content = msg.get("content", "").lower()
        if not content:
            continue
        
        # 计算匹配分数
        score = 0
        
        # 1. 完全包含关系（最高优先级）
        if quote_clean in content:
            score = 100 + len(quote_clean)  # 包含的越长分数越高
        elif content in quote_clean:
            score = 80 + len(content)
        else:
            # 2. 关键词匹配
            quote_words = set(quote_clean.split())
            content_words = set(content.split())
            common_words = quote_words & content_words
            # 过滤掉太短的词
            meaningful_common = [w for w in common_words if len(w) >= 2]
            if meaningful_common:
                score = len(meaningful_common) * 10
        
        if score > best_score:
            best_score = score
            best_match = msg["msg_id"]
    
    # 如果没有找到匹配，返回第一条消息（最早的）
    if not best_match and msg_list:
        best_match = msg_list[0]["msg_id"]
    
    return best_match


def _find_msg_time_by_quote(
    db: Session, 
    room_id: str, 
    msg_list: list[dict], 
    quote: str
) -> int | None:
    """
    根据 LLM 返回的引用句，在消息列表中模糊匹配最相关的消息，并返回其时间戳。
    
    Args:
        db: 数据库会话
        room_id: 房间ID
        msg_list: 消息列表 [{msg_id, content}, ...]
        quote: LLM 提取的原文关键句
    
    Returns:
        匹配消息的 msgtime（毫秒时间戳），如果找不到则返回 None
    """
    if not msg_list or not quote:
        return None
    
    # 清理 quote 用于匹配
    quote_clean = quote.strip().lower()
    if len(quote_clean) < 3:
        return None
    
    best_match_id = None
    best_score = 0
    
    for msg in msg_list:
        content = msg.get("content", "").lower()
        if not content:
            continue
        
        # 计算匹配分数
        score = 0
        
        # 1. 完全包含关系（最高优先级）
        if quote_clean in content:
            score = 100 + len(quote_clean)
        elif content in quote_clean:
            score = 80 + len(content)
        else:
            # 2. 关键词匹配
            quote_words = set(quote_clean.split())
            content_words = set(content.split())
            common_words = quote_words & content_words
            meaningful_common = [w for w in common_words if len(w) >= 2]
            if meaningful_common:
                score = len(meaningful_common) * 10
        
        if score > best_score:
            best_score = score
            best_match_id = msg["msg_id"]
    
    if not best_match_id:
        return None
    
    # 查询消息时间
    rec = db.query(ChatRecord.msgtime).filter(
        ChatRecord.roomid == room_id,
        ChatRecord.msgid == str(best_match_id),
    ).first()
    
    return int(rec[0]) if rec and rec[0] else None


def _build_detail_url_with_time_window(
    db: Session, 
    room_id: str, 
    anchor_msg_id: str | None = None,
    anchor_msgtime: int | None = None,
    before_minutes: int = 5,
    use_latest_as_until: bool = True,
    since_msgtime: int | None = None,
    until_msgtime: int | None = None,
) -> str:
    """
    构建带时间窗口的 detail_url，用于定位到问题发生的消息片段。
    
    Args:
        db: 数据库会话
        room_id: 房间ID
        anchor_msg_id: 锚点消息ID（用于确定时间基准）
        anchor_msgtime: 锚点消息时间戳（毫秒）
        before_minutes: 问题发生前的时间窗口（分钟），默认5分钟
        use_latest_as_until: 是否使用该群最新消息作为结束时间（确保捕获完整对话）
        since_msgtime: AI 分析确定的时间范围起点（毫秒时间戳，优先级最高）
        until_msgtime: AI 分析确定的时间范围终点（毫秒时间戳，优先级最高）
    
    Returns:
        带 since/until 参数的 URL，如：
        {base_url}/api/ui/rooms/{room_id}?since={since_ts}&until={until_ts}
    """
    base_url = f"{settings.INTERNAL_BASE_URL}/api/ui/rooms/{room_id}"
    
    from sqlalchemy import func

    # 选取锚点时间：
    # - 优先使用调用方提供的 anchor_msgtime
    # - 其次用 anchor_msg_id 在 chat_records 中反查对应 msgtime
    # - 最后才回退到“该群最新消息”（可能与问题无关，仅兜底）
    anchor_ts: int | None = int(anchor_msgtime) if anchor_msgtime else None

    if anchor_ts is None and anchor_msg_id:
        rec = (
            db.query(ChatRecord.msgtime)
            .filter(
                ChatRecord.roomid == room_id,
                ChatRecord.msgid == str(anchor_msg_id),
            )
            .first()
        )
        if rec and rec[0]:
            anchor_ts = int(rec[0])

    # 获取该群最新消息时间（用于兜底和计算 until）
    latest_msg = (
        db.query(func.max(ChatRecord.msgtime).label("max_msgtime"))
        .filter(
            ChatRecord.roomid == room_id,
            ChatRecord.msgtype == "text",
        )
        .first()
    )
    latest_msgtime = int(latest_msg.max_msgtime) if latest_msg and latest_msg.max_msgtime else None

    if anchor_ts is None:
        if not latest_msgtime:
            return base_url
        anchor_ts = latest_msgtime
    
    # 计算时间窗口（毫秒）
    # 优先使用 AI 分析确定的精确时间范围
    if since_msgtime:
        # AI 确定的起点，再往前留 2 分钟缓冲
        since_ts = since_msgtime - (2 * 60 * 1000)
    else:
        # 兜底：问题发生前 before_minutes 分钟
        since_ts = anchor_ts - (before_minutes * 60 * 1000)
    
    if until_msgtime:
        # AI 确定的终点，再往后留 2 分钟缓冲
        until_ts = until_msgtime + (2 * 60 * 1000)
    elif use_latest_as_until and latest_msgtime:
        # 使用该群最新消息时间，确保捕获从问题发生到讨论结束的完整对话
        until_ts = latest_msgtime
    else:
        # 兜底：锚点后 60 分钟
        until_ts = anchor_ts + (60 * 60 * 1000)
    
    return f"{base_url}?since={since_ts}&until={until_ts}"


def _get_last_msgtime(db: Session) -> int:
    state = db.query(IngestState).filter(IngestState.source == SOURCE_KEY).first()
    if not state:
        state = IngestState(source=SOURCE_KEY, last_msgtime=0)
        db.add(state)
        db.commit()
    return int(state.last_msgtime or 0)


def _get_last_seq(db: Session) -> int:
    state = db.query(IngestState).filter(IngestState.source == ARCHIVE_SOURCE_KEY).first()
    if not state:
        state = IngestState(source=ARCHIVE_SOURCE_KEY, last_msgtime=0)
        db.add(state)
        db.commit()
    return int(state.last_msgtime or 0)


def _set_last_msgtime(db: Session, last_msgtime: int) -> None:
    state = db.query(IngestState).filter(IngestState.source == SOURCE_KEY).first()
    if not state:
        state = IngestState(source=SOURCE_KEY, last_msgtime=last_msgtime)
        db.add(state)
    else:
        state.last_msgtime = last_msgtime
    db.commit()


def _set_last_seq(db: Session, last_seq: int) -> None:
    state = db.query(IngestState).filter(IngestState.source == ARCHIVE_SOURCE_KEY).first()
    if not state:
        state = IngestState(source=ARCHIVE_SOURCE_KEY, last_msgtime=last_seq)
        db.add(state)
    else:
        state.last_msgtime = last_seq
    db.commit()


def _fetch_new_messages(db: Session, last_msgtime: int) -> List[ChatRecord]:
    """旧版：全局按时间拉取（保留兼容）"""
    return (
        db.query(ChatRecord)
        .filter(ChatRecord.msgtype == "text", ChatRecord.msgtime > last_msgtime)
        .order_by(ChatRecord.msgtime.asc())
        .limit(200)
        .all()
    )


def _fetch_room_messages(
    db: Session, 
    room_id: str, 
    last_msgtime: int, 
    cycle_start_ms: int = 0,
    limit: int = None,
) -> List[ChatRecord]:
    """
    按群聊维度拉取消息（仅拉取当前周期内的消息）
    
    Args:
        db: 数据库会话
        room_id: 群聊ID
        last_msgtime: 该群已处理到的游标
        cycle_start_ms: 当前周期起始时间（毫秒），用于过滤历史消息
        limit: 最大拉取数量，默认使用配置 ROOM_FETCH_LIMIT
    
    Returns:
        该群聊的新消息列表（仅包含 >= max(last_msgtime, cycle_start_ms) 的消息）
    """
    if limit is None:
        limit = settings.ROOM_FETCH_LIMIT
    
    # 使用 last_msgtime 和 cycle_start_ms 中较大的值作为过滤起点
    # 这确保不会处理当前周期之前的历史消息
    min_msgtime = max(last_msgtime, cycle_start_ms)
    
    return (
        db.query(ChatRecord)
        .filter(
            ChatRecord.roomid == room_id,
            ChatRecord.msgtype == "text",
            ChatRecord.msgtime > min_msgtime,
        )
        .order_by(ChatRecord.msgtime.asc())
        .limit(limit)
        .all()
    )


def _get_excluded_room_ids(db: Session) -> set:
    """
    获取需要排除的 room_id 集合（带 TTL 缓存）
    规则：如果某个群内有被排除的 sender 发过消息，则整个群被排除
    """
    global _excluded_rooms_cache, _excluded_rooms_cache_ts
    
    exclude_senders = [s.strip() for s in settings.EXCLUDE_SENDERS.split(",") if s.strip()]
    if not exclude_senders:
        return set()
    
    # 检查缓存是否有效
    ttl = settings.EXCLUDE_ROOMS_CACHE_TTL_SECONDS
    now = time.time()
    if _excluded_rooms_cache and (now - _excluded_rooms_cache_ts) < ttl:
        return _excluded_rooms_cache
    
    # 缓存过期或为空，重新查询
    excluded_rooms = (
        db.query(ChatRecord.roomid)
        .filter(ChatRecord.sender.in_(exclude_senders))
        .distinct()
        .all()
    )
    _excluded_rooms_cache = {r[0] for r in excluded_rooms if r[0]}
    _excluded_rooms_cache_ts = now
    logger.debug(f"[缓存刷新] 排除群列表已更新，共 {len(_excluded_rooms_cache)} 个群组，TTL={ttl}秒")
    return _excluded_rooms_cache


def _get_active_mohe_rooms(db: Session, cycle_start_ms: int = 0) -> list[str]:
    """
    获取当前周期内活跃的魔盒群列表（排除非魔盒群）
    
    Args:
        db: 数据库会话
        cycle_start_ms: 当前周期起始时间（毫秒），只考虑 msgtime > cycle_start_ms 的消息所在的群
    
    Returns:
        当前周期内有新消息的魔盒群 room_id 列表
    """
    # 查询当前周期内有新消息的所有群聊
    query = db.query(ChatRecord.roomid).filter(
        ChatRecord.msgtype == "text",
        ChatRecord.msgtime > cycle_start_ms,
    ).distinct()
    
    all_rooms = {r[0] for r in query.all() if r[0] and r[0] not in ('None', '')}
    
    # 排除非魔盒群
    excluded_rooms = _get_excluded_room_ids(db)
    mohe_rooms = all_rooms - excluded_rooms
    
    logger.debug(
        f"[活跃群聊] 周期起点={datetime.fromtimestamp(cycle_start_ms/1000) if cycle_start_ms else 'N/A'}, "
        f"总计 {len(all_rooms)} 个群，排除 {len(excluded_rooms)} 个非魔盒群，剩余 {len(mohe_rooms)} 个魔盒群"
    )
    return list(mohe_rooms)


def _archive_new_messages(db: Session, wecom: WeComService) -> None:
    if not settings.WECOM_ARCHIVE_ENABLED:
        return

    last_seq = _get_last_seq(db)
    records = wecom.fetch_messages(after_seq=last_seq, limit=settings.WECOM_ARCHIVE_LIMIT)
    if not records:
        return

    max_seq = last_seq
    for item in records:
        msg_id = str(item.get("msgid") or "")
        if not msg_id:
            continue
        exists = db.query(ChatRecord).filter(ChatRecord.msgid == msg_id).first()
        if exists:
            continue

        seq = int(item.get("seq") or 0)
        msgtime = int(item.get("msgtime") or 0)
        if seq:
            max_seq = max(max_seq, seq)

        record = ChatRecord(
            msgid=msg_id,
            action=item.get("action") or "",
            sender=item.get("from") or "",
            tolist=item.get("tolist") or "",
            roomid=item.get("roomid") or "",
            msgtime=msgtime,
            msgtype=item.get("msgtype") or "",
            msgData=item.get("msgData") or "",
            seq=seq,
        )
        db.add(record)

    db.commit()
    _set_last_seq(db, max_seq)


def _resolve_assignee(db: Session, room_id: str, issue_type: str | None) -> str:
    type_mapping = {
        "使用咨询": settings.ISSUE_TYPE_ASSIGNEE_USAGE,
        "问题反馈": settings.ISSUE_TYPE_ASSIGNEE_FEEDBACK,
        "产品需求": settings.ISSUE_TYPE_ASSIGNEE_REQUIREMENT,
        "产品缺陷": settings.ISSUE_TYPE_ASSIGNEE_DEFECT,
    }
    mapped = type_mapping.get(issue_type or "")
    if mapped:
        return mapped
    mapping = db.query(RoomAssignee).filter(RoomAssignee.room_id == room_id).first()
    if mapping and mapping.assignee:
        return mapping.assignee
    return settings.DEFAULT_ASSIGNEE


def _resolve_room_name(db: Session, room_id: str) -> str:
    if not room_id:
        return room_id
    mapping = db.query(RoomInfo).filter(RoomInfo.room_id == room_id).first()
    if mapping and mapping.room_name:
        return mapping.room_name
    return room_id


async def process_message(
    db: Session,
    *,
    msg_id: str,
    room_id: str,
    sender_id: str,
    msg_type: str,
    clean_text: str,
    raw_text: str,
    sentinel: SentinelAgent,
    assistant: AssistantAgent,
    wecom: WeComService,
    allow_reply: bool = True,
    allow_alert: bool = True,
    allow_ticket: bool = True,
    replay: bool = False,
    room_name: str | None = None,
    since_msgtime: int | None = None,
    until_msgtime: int | None = None,
) -> dict:
    if not clean_text:
        return {"status": "ignored", "reason": "empty"}
    if DataCleanService.is_noise(clean_text):
        return {"status": "ignored", "reason": "noise"}

    # 获取最近的对话上下文，用于判断问题是否已解决
    recent_chat_lines = data_service.get_recent_chat_text(db, room_id, limit=10)
    if not recent_chat_lines:
        recent_chat_lines = data_service.get_recent_wecom_text(db, room_id, limit=10)

    analysis = await sentinel.check_message(clean_text)
    
    # 调用完整分析获取50字详细总结（用于钉钉推送）
    complete_analysis = await analyze_complete_llm(clean_text)
    summary_50 = complete_analysis.get("summary") or ""  # 50字详细总结
    
    is_hard = is_hard_issue(clean_text, analysis, chat_lines=recent_chat_lines)
    is_resolved = check_resolved_status(clean_text, chat_lines=recent_chat_lines)
    resolved_suffix = "（已解决）" if is_resolved else ""
    reply_text = await generate_reply(clean_text)
    reply_mode = "auto" if (
        settings.AUTO_REPLY_ENABLED and analysis.get("risk_score", 0) <= settings.AUTO_REPLY_MAX_RISK
    ) else "suggest"
    # 优先使用完整分析的 issue_type 和 priority（更准确）
    issue_type = normalize_issue_type(complete_analysis.get("issue_type") or analysis.get("issue_type"))
    priority_from_llm = normalize_priority(complete_analysis.get("priority") or "")
    assignee = _resolve_assignee(db, room_id, issue_type)
    room_name = room_name or _resolve_room_name(db, room_id)

    summary_text = clean_text[: settings.ISSUE_SUMMARY_LEN]
    evidence_id = f"replay:{msg_id}" if replay else str(msg_id)
    issue = Issue(
        room_id=room_id,
        summary=summary_text,
        issue_type=issue_type,
        category=f"{analysis.get('category_l1', 'OTHER')}/{analysis.get('category_l2', 'OTHER')}",
        category_l1=analysis.get("category_l1"),
        category_l2=analysis.get("category_l2"),
        category_short=analysis.get("category_short"),
        labels=analysis.get("labels") or [],
        severity=analysis.get("severity"),
        confidence=int((analysis.get("confidence") or 0) * 100),
        taxonomy_version=analysis.get("taxonomy_version"),
        classification_strategy=analysis.get("classification_strategy"),
        risk_score=analysis.get("risk_score", 0),
        is_bug=bool(analysis.get("is_bug")),
        suggested_reply=reply_text,
        reply_mode=reply_mode,
        evidence=[evidence_id],
        status="alerted" if (is_hard and analysis.get("is_alert")) else "pending",
    )
    db.add(issue)
    db.commit()
    update_issue_aggregation(
        db,
        issue,
        is_hard=is_hard,
        is_alert=bool(analysis.get("is_alert")),
    )

    draft = None
    # 构建带时间窗口的链接，定位到问题消息片段
    # 优先使用 AI 分析确定的时间范围，兜底使用 msg_id 为锚点
    detail_url = _build_detail_url_with_time_window(
        db, room_id, 
        anchor_msg_id=msg_id,
        since_msgtime=since_msgtime,
        until_msgtime=until_msgtime,
    )
    
    # 获取问题发生时间（从 msg_id 反查 ChatRecord.msgtime）
    issue_time = None
    issue_msgtime = None
    anchor_rec = db.query(ChatRecord.msgtime).filter(
        ChatRecord.roomid == room_id,
        ChatRecord.msgid == str(msg_id),
    ).first()
    if anchor_rec and anchor_rec[0]:
        issue_msgtime = int(anchor_rec[0])
        issue_time = _format_issue_time(issue_msgtime)
    
    # 时间校验：跳过当前周期之前的历史问题推送
    cycle_start_ms = _get_current_cycle_start()
    if issue_msgtime and issue_msgtime < cycle_start_ms:
        logger.info(
            f"[时间过滤] 跳过历史问题推送: room={room_id}, "
            f"issue_time={issue_time}, cycle_start={datetime.fromtimestamp(cycle_start_ms/1000)}"
        )
        # 跳过推送但仍记录 Issue（allow_alert 设为 False）
        allow_alert = False
    
    # 草稿阶段就准备好"现象/关键句"，避免后续建单只有一句话
    # 优先使用完整分析结果（更准确），兜底使用 Sentinel 结果
    phenomenon_text = complete_analysis.get("phenomenon") or analysis.get("phenomenon") or (clean_text[:50] if clean_text else "")
    key_sentence_text = analysis.get("key_sentence") or (clean_text.split("\n")[0][:100] if clean_text else "")
    # summary_50 用于钉钉推送（50字详细总结），key_sentence_text 用于TB备注（简短）
    summary_for_alert = summary_50 if summary_50 else key_sentence_text
    if allow_ticket and is_hard:  # is_hard 已包含 severity/is_bug/关键词/RAG 判断
        draft_content = build_ticket_draft(
            room_id=room_id,
            summary=summary_text,
            category=f"{analysis.get('category_l1', 'OTHER')}/{analysis.get('category_l2', 'OTHER')}",
            severity=analysis.get("severity", "S1"),
            risk_score=analysis.get("risk_score", 0),
            raw_text=clean_text,
            room_name=room_name,
            customer=sender_id,
            detail_url=detail_url,
            phenomenon=phenomenon_text,
            key_sentence=key_sentence_text,
            suggested_reply=reply_text,
            platform=complete_analysis.get("platform"),  # 端口分类
        )
        # 使用 LLM 生成详细标题（30-40字），使用50字总结作为输入
        llm_title = await generate_ticket_title_llm(
            phenomenon=phenomenon_text,
            summary=summary_for_alert,  # 使用详细总结生成更好的标题
        )
        if llm_title:
            llm_title = llm_title + resolved_suffix  # 已解决问题添加标记
            draft_content["llm_title"] = llm_title
            draft_content["title"] = llm_title
        elif resolved_suffix:
            # 如果没有 LLM 标题但问题已解决，也要在原始标题上添加标记
            draft_content["title"] = draft_content.get("title", "") + resolved_suffix
        draft = TicketDraft(
            issue_id=issue.issue_id,
            room_id=room_id,  # 添加 room_id 用于去重检查
            title=(llm_title or draft_content.get("title")),
            severity=draft_content.get("severity"),
            category=draft_content.get("category", ""),
            content=draft_content,
            status="draft",
            assigned_to=assignee,
        )
        db.add(draft)
        db.commit()

    if allow_reply and reply_mode == "auto":
        wecom.send_reply(room_id, reply_text)

    send_alert, alert_level, alert_event = should_send_alert(
        db=db,
        room_id=room_id,
        category_l1=analysis.get("category_l1", "OTHER"),
        category_l2=analysis.get("category_l2", "OTHER"),
        severity=analysis.get("severity"),
        risk_score=analysis.get("risk_score", 0),
        is_alert=bool(analysis.get("is_alert")),
        is_bug=bool(analysis.get("is_bug")),
    )
    
    # 调试日志：显示推送判断条件
    if not (allow_alert and send_alert and is_hard):
        logger.debug(
            f"[推送跳过] room={room_id}, "
            f"allow_alert={allow_alert}, send_alert={send_alert}, is_hard={is_hard}, "
            f"severity={analysis.get('severity')}, is_bug={analysis.get('is_bug')}, "
            f"risk_score={analysis.get('risk_score', 0)}, is_alert={analysis.get('is_alert')}"
        )
    
    if allow_alert and send_alert and is_hard:
        # 已移除【🧠 AI 智能辅助】相关逻辑：不再做相似案例检索/深度分析/方案与安抚话术生成
        # 仅基于清洗后的文本，构建“客户原声摘要”
        # 使用 Sentinel AI 生成的简短摘要（50字以内的现象 + 一句关键句）
        aggregate_summary = build_aggregate_summary(
            db=db,
            room_id=room_id,
            category_l1=analysis.get("category_l1", "OTHER"),
            category_l2=analysis.get("category_l2", "OTHER"),
            since_time=alert_event.first_seen_at if alert_event else None,
            limit=settings.ALERT_AGGREGATE_LIMIT,
        )
        if draft:
            content = draft.content or {}
            # 将 risk_score 转换为 priority
            risk_score = analysis.get("risk_score", 0)
            priority = risk_score_to_priority(risk_score)
            # 优先使用 LLM 分析的 priority，兜底使用 risk_score 转换
            priority = priority_from_llm if priority_from_llm else risk_score_to_priority(risk_score)
            content.update(
                {
                    "issue_type": issue_type,
                    "priority": priority,
                    "severity": analysis.get("severity", "-") or "-",
                    "risk_score": risk_score,
                    "category": f"{analysis.get('category_l1', 'OTHER')}/{analysis.get('category_l2', 'OTHER')}",
                    "category_short": analysis.get("category_short"),
                    "phenomenon": phenomenon_text,
                    "summary": summary_for_alert,  # 使用50字详细总结
                    "key_sentence": key_sentence_text,
                    "detail_url": detail_url,
                    "room_name": room_name,
                    "room_id": room_id,
                    "customer": sender_id,
                    "suggested_reply": reply_text,
                    "issue_time": issue_time,  # 问题发生时间（格式化后）
                }
            )
            # 构建新格式的钉钉 markdown（已解决问题在现象后添加标记）
            phenomenon_with_suffix = phenomenon_text + resolved_suffix if resolved_suffix else phenomenon_text
            content["dingtalk_markdown"] = build_ticket_markdown(
                content,
                issue_type=issue_type,
                priority=priority,
                phenomenon=phenomenon_with_suffix,  # 已解决问题添加标记
                summary=summary_for_alert,  # 使用50字详细总结
                room_name=room_name,
                detail_url=detail_url,
                issue_time=issue_time,  # 问题发生时间
            )
            content.pop("customfields_pending", None)
            # 草稿阶段已生成 llm_title；此处兜底
            title_text = content.get("llm_title") or build_ticket_title(content)
            draft.title = title_text
            content["title"] = title_text

        # 发送钉钉推送（使用新的优先级格式）
        dingtalk_markdown = content.get("dingtalk_markdown") if (draft and content) else None
        priority = content.get("priority") if (draft and content) else risk_score_to_priority(analysis.get("risk_score", 0))
        DingTalkService.send_alert(
            summary=aggregate_summary or clean_text,
            risk=analysis.get("risk_score", 0),
            reason=analysis.get("reason", ""),
            room_id=room_id,
            room_name=room_name,
            issue_type=issue_type,
            priority=priority,
            phenomenon=phenomenon_text,
            detail_url=detail_url,
            markdown_text=dingtalk_markdown,
        )

        # TB AI辅助字段：等于钉钉推送内容的纯文本版本（不含【🧠 AI 智能辅助】）
        if draft and content and isinstance(content.get("dingtalk_markdown"), str):
            content["ai_assistant"] = markdown_to_plain_text(content["dingtalk_markdown"])
            content["ai_assistant_text"] = content["ai_assistant"]
            build_customfields_pending(content)
            draft.content = content
            flag_modified(draft, 'content')  # 关键：确保 JSON 字段完整持久化
            db.commit()
            db.refresh(draft)  # 关键：刷新对象，确保后续访问使用最新数据

        if allow_ticket and draft and settings.TEAMBITION_AUTO_CREATE and not draft.teambition_ticket_id:
            # 使用 LLM 生成精炼的问题概括（用于TB备注/标题兜底）
            if isinstance(draft.content, dict) and not draft.content.get("llm_note_summary"):
                draft.content["llm_note_summary"] = await generate_note_summary_llm(clean_text, max_len=30)
                flag_modified(draft, 'content')  # 关键：标记JSON字段已修改，确保SQLAlchemy持久化嵌套更改
                db.commit()
                db.refresh(draft)  # 刷新确保数据同步

            # 新项目字段：客户端版本 / CBS版本 / 镜像ID（从对话中抽取）
            if isinstance(draft.content, dict) and (
                not draft.content.get("client_version")
                or not draft.content.get("cbs_version")
                or not draft.content.get("image_id")
            ):
                extracted = await extract_versions_and_image_llm(clean_text)
                if isinstance(extracted, dict):
                    draft.content["client_version"] = extracted.get("client_version") or "-"
                    draft.content["cbs_version"] = extracted.get("cbs_version") or "-"
                    draft.content["image_id"] = extracted.get("image_id") or "-"
                flag_modified(draft, 'content')  # 关键：标记JSON字段已修改
                db.commit()
                db.refresh(draft)  # 刷新确保数据同步

            if settings.TEAMBITION_MODE == "api":
                ticket_id = create_task(draft.title or "自动工单", (draft.content or {}).get("description", ""))
                if ticket_id:
                    draft.teambition_ticket_id = ticket_id
                    draft.status = "ticketed"
                    db.commit()
            elif settings.TEAMBITION_MODE == "oapi":
                payload = build_task_payload(
                    draft.title or "自动工单",
                    (draft.content or {}).get("description", ""),
                    draft.content,
                )
                if payload:
                    payload["customfields"] = build_customfields_for_create(draft.content or {})
                    ticket_id = create_task_oapi(payload)
                    if ticket_id:
                        draft.teambition_ticket_id = ticket_id
                        draft.status = "ticketed"
                        db.commit()
                        for item in (draft.content or {}).get("customfields_pending") or []:
                            update_task_customfield(ticket_id, item)
            elif settings.TEAMBITION_MODE == "mcp":
                # MCP 模式：自动提交建单请求（与 /mcp_request 逻辑一致）
                payload = build_task_payload(
                    draft.title or "自动工单",
                    (draft.content or {}).get("description", ""),
                    draft.content if isinstance(draft.content, dict) else None,
                )
                if payload:
                    draft.mcp_status = "pending"
                    draft.mcp_payload = payload
                    draft.mcp_requested_at = datetime.utcnow()
                    db.commit()
                    bridge_result = submit_mcp_task(payload)
                    if bridge_result and bridge_result.get("ticket_id"):
                        draft.teambition_ticket_id = bridge_result.get("ticket_id")
                        draft.status = "ticketed"
                        draft.mcp_status = "completed"
                        draft.mcp_completed_at = datetime.utcnow()
                        db.commit()
            else:
                logger.warning("TEAMBITION_MODE 未设置为 mcp/oapi/api，已跳过建单")

            # 建单成功后：补发“工单已创建”通知（带工单链接）
            if draft.teambition_ticket_id:
                ticket_url = get_task_url(draft.teambition_ticket_id)
                if isinstance(draft.content, dict) and ticket_url:
                    content = draft.content
                    # 使用新格式构建 markdown
                    content["dingtalk_markdown"] = build_ticket_markdown(
                        content,
                        issue_type=content.get("issue_type") or "问题反馈",
                        priority=content.get("priority") or risk_score_to_priority(int(content.get("risk_score") or 0)),
                        phenomenon=content.get("phenomenon"),
                        summary=content.get("summary") or content.get("key_sentence"),
                        room_name=content.get("room_name") or room_name,
                        detail_url=content.get("detail_url") or f"{settings.INTERNAL_BASE_URL}/api/ui/rooms/{content.get('room_id')}",
                    )
                    draft.content = content
                    db.commit()
                # 按需求：一个问题只推送一条钉钉消息（不再补发“工单已创建”二次推送）

    return {"status": "alerted" if (is_hard and send_alert) else "saved", "issue_id": issue.issue_id}


async def polling_loop():
    """
    群聊维度轮询循环（每日周期版）：
    1. 每天 DAILY_CYCLE_START_HOUR 点开始新周期，重置所有群聊状态
    2. 只处理当前周期内的消息（忽略历史数据）
    3. 获取活跃魔盒群，按上次处理时间排序（公平轮询）
    4. 对每个群聊单独拉取消息并入库
    5. 累积未分析消息数，达到阈值才触发 LLM 分析
    6. 建单前检查去重（同群同周期内相似问题不重复建单）
    7. 高风险关键词可绕过冷却期
    8. 分析后重置累积计数并更新冷却时间
    9. 群聊状态持久化到数据库，服务重启后从上次位置继续
    """
    if not settings.POLLING_ENABLED:
        logger.warning("[轮询] POLLING_ENABLED=false，轮询未启动")
        return

    cycle_start_ms = _get_current_cycle_start()
    logger.info(
        f"[群聊轮询] 启动轮询服务（每日周期模式），"
        f"周期起点={settings.DAILY_CYCLE_START_HOUR}:00, "
        f"当前周期={datetime.fromtimestamp(cycle_start_ms/1000)}, "
        f"间隔={settings.POLLING_INTERVAL_SECONDS}秒, "
        f"有效消息阈值={settings.ROOM_MIN_MESSAGES_FOR_ANALYZE}, "
        f"原始消息阈值={settings.ROOM_RAW_MIN_MESSAGES_FOR_ANALYZE}, "
        f"高风险最少消息={settings.HIGH_RISK_MIN_MESSAGES}, "
        f"冷却时间={settings.ROOM_COOLDOWN_SECONDS}秒, "
        f"去重阈值={settings.ISSUE_DEDUP_SIMILARITY_THRESHOLD}"
    )

    sentinel = SentinelAgent()
    assistant = AssistantAgent()
    wecom = WeComService()
    faq_service = FaqService()
    
    # 标记是否已从数据库加载状态（仅首次启动时加载）
    state_loaded = False

    while True:
        db = SessionLocal()
        try:
            # ========== 首次启动时从数据库加载状态 ==========
            if not state_loaded:
                loaded_count = _load_all_room_states(db)
                logger.info(f"[群聊轮询] 从数据库恢复了 {loaded_count} 个群聊的轮询状态")
                state_loaded = True
            
            _archive_new_messages(db, wecom)
            
            # ========== 每日周期检测与重置 ==========
            _check_and_reset_cycle(db)  # 传入 db 以支持持久化重置后的状态
            cycle_start_ms = _get_current_cycle_start()
            
            # ========== 优先处理 needs_flush 的群聊（周期重置前未分析的） ==========
            flush_rooms = [
                rid for rid, state in _room_state.items()
                if state.get("needs_flush")
            ]
            if flush_rooms:
                logger.info(f"[周期重置兜底] 优先处理 {len(flush_rooms)} 个 needs_flush 群聊")
                for flush_room_id in flush_rooms:
                    flush_state = _get_room_state(flush_room_id)
                    # 使用上一个周期的起点来获取上下文（因为消息属于上个周期）
                    prev_cycle_ms = cycle_start_ms - 86400000  # 前一天同一时间
                    expanded_context, msg_list = _get_room_history_context(db, flush_room_id, min_msgtime=prev_cycle_ms)
                    if expanded_context:
                        logger.info(
                            f"[周期重置兜底] room={flush_room_id}, "
                            f"pending={flush_state.get('pending_count', 0)}, "
                            f"raw={flush_state.get('raw_pending_count', 0)}, "
                            f"上下文={len(expanded_context)}字符"
                        )
                        pre_analysis = await analyze_complete_llm(expanded_context)
                        phenomenon = pre_analysis.get("phenomenon", "")
                        problem_quote = pre_analysis.get("problem_quote", "")
                        if phenomenon and phenomenon != "暂无":
                            if not await _is_duplicate_issue(db, flush_room_id, phenomenon, prev_cycle_ms):
                                anchor_msg_id = _find_best_anchor_msg(msg_list, problem_quote)
                                if not anchor_msg_id and msg_list:
                                    anchor_msg_id = msg_list[0]["msg_id"]
                                since_msgtime = _find_msg_time_by_quote(db, flush_room_id, msg_list, pre_analysis.get("first_problem_quote", ""))
                                until_msgtime = _find_msg_time_by_quote(db, flush_room_id, msg_list, pre_analysis.get("last_discussion_quote", ""))
                                await process_message(
                                    db,
                                    msg_id=anchor_msg_id or f"flush_{flush_room_id}_{int(time.time()*1000)}",
                                    room_id=flush_room_id,
                                    sender_id="system",
                                    msg_type="text",
                                    clean_text=expanded_context,
                                    raw_text=expanded_context,
                                    sentinel=sentinel,
                                    assistant=assistant,
                                    wecom=wecom,
                                    allow_reply=False,
                                    allow_alert=True,
                                    allow_ticket=True,
                                    replay=False,
                                    since_msgtime=since_msgtime,
                                    until_msgtime=until_msgtime,
                                )
                    # 清除 needs_flush 标记并重置计数
                    flush_state["needs_flush"] = False
                    flush_state["pending_count"] = 0
                    flush_state["raw_pending_count"] = 0
                    _save_room_state(db, flush_room_id)
            
            # ========== 群聊维度轮询 ==========
            # 1. 获取当前周期内活跃的魔盒群
            active_rooms = _get_active_mohe_rooms(db, cycle_start_ms=cycle_start_ms)
            
            # 2. 按上次处理时间排序（最久未处理的优先）
            active_rooms.sort(key=lambda r: _get_room_state(r).get("last_processed_at", 0))
            
            # 3. 限制每轮处理的群聊数
            rooms_to_process = active_rooms[:settings.MAX_ROOMS_PER_ROUND]
            
            if not rooms_to_process:
                logger.debug("[群聊轮询] 本轮无活跃魔盒群")
            else:
                logger.info(f"[群聊轮询] 本轮处理 {len(rooms_to_process)}/{len(active_rooms)} 个魔盒群")
            
            vector_messages = []
            analyzed_count = 0
            
            # 4. 遍历每个群聊
            for room_id in rooms_to_process:
                state = _get_room_state(room_id)
                
                # 5. 拉取该群新消息（仅当前周期内）
                records = _fetch_room_messages(db, room_id, state["last_msgtime"], cycle_start_ms)
                
                # ========== 情况A：没有新消息，检查是否累积触发 ==========
                if not records:
                    pending_count = state["pending_count"]
                    raw_pending = state.get("raw_pending_count", 0)
                    in_cooldown = _is_in_cooldown(room_id)
                    
                    # 双阈值触发：有效消息数>=阈值 OR 原始消息数>=原始阈值
                    should_trigger = (
                        not in_cooldown and (
                            pending_count >= settings.ROOM_MIN_MESSAGES_FOR_ANALYZE
                            or raw_pending >= settings.ROOM_RAW_MIN_MESSAGES_FOR_ANALYZE
                        )
                    )
                    if should_trigger:
                        trigger_detail = (
                            f"有效={pending_count}, 原始={raw_pending}, "
                            f"阈值: 有效>={settings.ROOM_MIN_MESSAGES_FOR_ANALYZE} OR 原始>={settings.ROOM_RAW_MIN_MESSAGES_FOR_ANALYZE}"
                        )
                        logger.info(f"[累积触发] room={room_id}, 无新消息但累积达阈值，{trigger_detail}")
                        expanded_context, msg_list = _get_room_history_context(db, room_id, min_msgtime=cycle_start_ms)
                        
                        if expanded_context:
                            # LLM 预判断（高活跃群强制跳过预判断）
                            if settings.PRE_JUDGE_ENABLED:
                                has_issue, judge_reason = await pre_judge_has_issue(expanded_context)
                                cur_raw = state.get("raw_pending_count", 0)
                                if not has_issue and cur_raw < 30:
                                    logger.info(f"[预判断跳过] room={room_id}, 原因='{judge_reason}', 累积={pending_count}, raw={cur_raw}")
                                    state["pending_count"] = 0
                                    state["raw_pending_count"] = 0
                                    _update_cooldown(room_id)
                                    _save_room_state(db, room_id)
                                    continue
                                elif not has_issue:
                                    logger.info(f"[预判断覆盖] room={room_id}, 预判断=无问题但raw={cur_raw}>=30，强制完整分析")
                            
                            # 完整 LLM 分析
                            pre_analysis = await analyze_complete_llm(expanded_context)
                            if pre_analysis:
                                phenomenon = pre_analysis.get("phenomenon", "")
                                problem_quote = pre_analysis.get("problem_quote", "")
                                first_problem_quote = pre_analysis.get("first_problem_quote", "")
                                last_discussion_quote = pre_analysis.get("last_discussion_quote", "")
                                
                                # 根据 problem_quote 找到最佳锚点消息
                                anchor_msg_id = _find_best_anchor_msg(msg_list, problem_quote)
                                if not anchor_msg_id and msg_list:
                                    mid_idx = len(msg_list) // 2
                                    anchor_msg_id = msg_list[mid_idx]["msg_id"]
                                
                                # 通过 AI 返回的关键句确定时间范围
                                since_msgtime = _find_msg_time_by_quote(db, room_id, msg_list, first_problem_quote)
                                until_msgtime = _find_msg_time_by_quote(db, room_id, msg_list, last_discussion_quote)
                                
                                logger.info(
                                    f"[累积触发] room={room_id}, 问题关键句='{problem_quote[:30] if problem_quote else ''}...', "
                                    f"锚点msg_id={anchor_msg_id}, "
                                    f"时间范围: since={since_msgtime}, until={until_msgtime}"
                                )
                                
                                # 去重检查
                                if await _is_duplicate_issue(db, room_id, phenomenon, cycle_start_ms):
                                    logger.info(f"[去重跳过] room={room_id}, 问题='{phenomenon[:30]}...'")
                                    state["pending_count"] = 0
                                    state["raw_pending_count"] = 0
                                    _update_cooldown(room_id)
                                    _save_room_state(db, room_id)
                                    continue
                                
                                # 调用 process_message（累积触发模式）
                                await process_message(
                                    db,
                                    msg_id=anchor_msg_id or f"accumulated_{room_id}_{int(time.time()*1000)}",
                                    room_id=room_id,
                                    sender_id="system",
                                    msg_type="text",
                                    clean_text=expanded_context,
                                    raw_text=expanded_context,
                                    sentinel=sentinel,
                                    assistant=assistant,
                                    wecom=wecom,
                                    allow_reply=False,
                                    allow_alert=True,
                                    allow_ticket=True,
                                    replay=False,
                                    since_msgtime=since_msgtime,
                                    until_msgtime=until_msgtime,
                                )
                                analyzed_count += 1
                            
                            # 重置累积计数（仅在成功提取到问题时重置）
                            state["pending_count"] = 0
                            state["raw_pending_count"] = 0
                            _update_cooldown(room_id)
                            _save_room_state(db, room_id)
                        else:
                            # LLM 分析失败或无结果，保留 pending 等待重试
                            logger.warning(f"[累积触发] room={room_id} LLM分析无结果，保留pending等待重试")
                            _update_cooldown(room_id)
                            _save_room_state(db, room_id)
                    continue
                
                # ========== 情况B：有新消息，正常处理 ==========
                # 6. 入库并更新累积计数
                stored_messages = []
                raw_text_count = 0  # 所有成功入库的 text 消息计数（含噪音）
                max_msgtime_in_room = state["last_msgtime"]
                
                for r in records:
                    content = _extract_content(r.msgData)
                    if not content:
                        continue
                    
                    clean_text = DataCleanService.sanitize(content)
                    max_msgtime_in_room = max(max_msgtime_in_room, int(r.msgtime))
                    
                    msg_id = str(r.msgid)
                    exists = (
                        db.query(WeComMessage)
                        .filter(WeComMessage.msg_id == msg_id)
                        .first()
                    )
                    if exists:
                        continue
                    
                    # 将 ChatRecord.msgtime（毫秒时间戳）转换为 datetime
                    msg_datetime = datetime.fromtimestamp(int(r.msgtime) / 1000) if r.msgtime else None
                    
                    record = WeComMessage(
                        msg_id=msg_id,
                        seq=int(r.seq or 0),
                        room_id=room_id,
                        sender_id=str(r.sender or ""),
                        msg_type=str(r.msgtype),
                        content_raw=content,
                        content_clean=clean_text,
                        msg_time=msg_datetime,  # 使用消息实际发送时间
                        is_noise=DataCleanService.is_noise(clean_text),
                    )
                    db.add(record)
                    try:
                        db.commit()
                    except IntegrityError:
                        db.rollback()
                        continue
                    
                    # 所有成功入库的 text 消息都计入原始计数
                    if str(r.msgtype) == "text":
                        raw_text_count += 1
                    
                    # 收集非噪音文本消息
                    if not DataCleanService.is_noise(clean_text) and str(r.msgtype) == "text":
                        stored_messages.append({
                            "msg_id": msg_id,
                            "clean_text": clean_text,
                            "content_raw": content,
                            "record": r,
                        })
                        vector_messages.append({
                            "msg_id": msg_id,
                            "room_id": room_id,
                            "sender_id": str(r.sender or ""),
                            "content_raw": content,
                            "content_clean": clean_text,
                            "msg_time": msg_datetime,  # 使用消息实际发送时间
                        })
                
                # 更新群聊状态（双计数：有效消息 + 原始消息）
                state["last_msgtime"] = max_msgtime_in_room
                state["pending_count"] += len(stored_messages)
                state["raw_pending_count"] = state.get("raw_pending_count", 0) + raw_text_count
                state["last_processed_at"] = time.time()
                
                # 持久化状态到数据库
                _save_room_state(db, room_id)
                
                # 7. 判断是否触发分析（基于双阈值：有效消息数 OR 原始消息数）
                pending_count = state["pending_count"]
                raw_pending = state.get("raw_pending_count", 0)
                in_cooldown = _is_in_cooldown(room_id)
                
                # 如果没有新消息且双阈值均不满足，跳过
                if not stored_messages and (
                    pending_count < settings.ROOM_MIN_MESSAGES_FOR_ANALYZE
                    and raw_pending < settings.ROOM_RAW_MIN_MESSAGES_FOR_ANALYZE
                ):
                    continue
                
                # 获取上下文（优先使用本轮消息，如果没有则从历史获取）
                if stored_messages:
                    chat_context = _merge_chat_context(records)
                else:
                    # 没有新消息但累积数达到阈值，从历史记录获取上下文
                    expanded_context, _ = _get_room_history_context(db, room_id, min_msgtime=cycle_start_ms)
                    chat_context = expanded_context or ""
                
                has_high_risk = _contains_high_risk_keyword(chat_context)
                
                should_analyze = False
                trigger_reason = ""
                
                # 高风险关键词绕过冷却，但仍需达到最少消息数
                if has_high_risk and pending_count >= settings.HIGH_RISK_MIN_MESSAGES:
                    should_analyze = True
                    trigger_reason = f"高风险关键词(有效={pending_count}, 原始={raw_pending})"
                elif not in_cooldown and pending_count >= settings.ROOM_MIN_MESSAGES_FOR_ANALYZE:
                    should_analyze = True
                    trigger_reason = f"有效消息数={pending_count}>=阈值{settings.ROOM_MIN_MESSAGES_FOR_ANALYZE}"
                elif not in_cooldown and raw_pending >= settings.ROOM_RAW_MIN_MESSAGES_FOR_ANALYZE:
                    should_analyze = True
                    trigger_reason = f"原始消息数={raw_pending}>=原始阈值{settings.ROOM_RAW_MIN_MESSAGES_FOR_ANALYZE}"
                
                # 8. 触发分析
                if should_analyze:
                    # 获取更丰富的历史上下文（同时返回消息列表用于匹配问题位置）
                    expanded_context, msg_list = _get_room_history_context(db, room_id, min_msgtime=cycle_start_ms)
                    if not expanded_context:
                        expanded_context = chat_context
                        msg_list = [{"msg_id": m["msg_id"], "content": m["clean_text"]} for m in stored_messages]
                    
                    # ========== LLM 预判断（轻量级，节省 tokens） ==========
                    # 在完整分析前先判断对话是否包含有效问题（高活跃群强制跳过预判断）
                    if settings.PRE_JUDGE_ENABLED:
                        has_issue, judge_reason = await pre_judge_has_issue(expanded_context)
                        cur_raw = state.get("raw_pending_count", 0)
                        if not has_issue and cur_raw < 30:
                            logger.info(
                                f"[预判断跳过] room={room_id}, 原因='{judge_reason}', "
                                f"累积消息={pending_count}, raw={cur_raw}, 跳过完整分析"
                            )
                            # 重置累积计数但不触发完整分析
                            state["pending_count"] = 0
                            state["raw_pending_count"] = 0
                            _update_cooldown(room_id)
                            _save_room_state(db, room_id)  # 持久化状态
                            continue
                        elif not has_issue:
                            logger.info(f"[预判断覆盖] room={room_id}, 预判断=无问题但raw={cur_raw}>=30，强制完整分析")
                        else:
                            logger.debug(f"[预判断通过] room={room_id}, 原因='{judge_reason}'")
                    
                    # 先调用 LLM 获取 problem_quote（问题原文关键句），用于定位问题消息
                    pre_analysis = await analyze_complete_llm(expanded_context)
                    problem_quote = pre_analysis.get("problem_quote", "")
                    phenomenon = pre_analysis.get("phenomenon", "")
                    first_problem_quote = pre_analysis.get("first_problem_quote", "")
                    last_discussion_quote = pre_analysis.get("last_discussion_quote", "")
                    
                    # ========== 问题去重检查 ==========
                    if await _is_duplicate_issue(db, room_id, phenomenon, cycle_start_ms):
                        logger.info(
                            f"[去重跳过] room={room_id}, 问题='{phenomenon[:30]}...' "
                            f"在当前周期内已有相似工单，跳过建单"
                        )
                        state["pending_count"] = 0
                        state["raw_pending_count"] = 0
                        _update_cooldown(room_id)
                        _save_room_state(db, room_id)
                        continue
                    
                    # 根据 problem_quote 在消息列表中匹配最相关的消息作为锚点
                    anchor_msg_id = _find_best_anchor_msg(msg_list, problem_quote)
                    if not anchor_msg_id and msg_list:
                        anchor_msg_id = msg_list[0]["msg_id"]
                    
                    # 通过 AI 返回的关键句确定时间范围
                    since_msgtime = _find_msg_time_by_quote(db, room_id, msg_list, first_problem_quote)
                    until_msgtime = _find_msg_time_by_quote(db, room_id, msg_list, last_discussion_quote)
                    
                    logger.info(
                        f"[群聊轮询] room={room_id} 触发LLM分析, "
                        f"原因={trigger_reason}, 本次入库={len(stored_messages)}, "
                        f"上下文长度={len(expanded_context)}字符, "
                        f"问题关键句='{problem_quote[:30] if problem_quote else ''}...', 锚点msg_id={anchor_msg_id}, "
                        f"时间范围: since={since_msgtime}, until={until_msgtime}"
                    )
                    
                    # 获取消息信息（优先用本轮入库的，否则从历史上下文获取）
                    if stored_messages:
                        last_msg = stored_messages[-1]
                        fallback_msg_id = last_msg["msg_id"]
                        sender_id = str(last_msg["record"].sender or "")
                        msg_type = str(last_msg["record"].msgtype)
                    else:
                        fallback_msg_id = msg_list[-1]["msg_id"] if msg_list else f"reset_{room_id}_{int(time.time())}"
                        sender_id = ""
                        msg_type = "text"
                    
                    await process_message(
                        db,
                        msg_id=anchor_msg_id or fallback_msg_id,
                        room_id=room_id,
                        sender_id=sender_id,
                        msg_type=msg_type,
                        clean_text=expanded_context,
                        raw_text=expanded_context,
                        sentinel=sentinel,
                        assistant=assistant,
                        wecom=wecom,
                        allow_reply=True,
                        allow_alert=True,
                        allow_ticket=True,
                        replay=False,
                        since_msgtime=since_msgtime,
                        until_msgtime=until_msgtime,
                    )
                    
                    # 分析后重置累积计数并更新冷却
                    state["pending_count"] = 0
                    state["raw_pending_count"] = 0
                    _update_cooldown(room_id)
                    _save_room_state(db, room_id)  # 持久化状态
                    analyzed_count += 1
                else:
                    logger.debug(
                        f"[群聊轮询] room={room_id}, 本次入库={len(stored_messages)}, "
                        f"有效累积={pending_count}, 原始累积={raw_pending}, "
                        f"冷却中={in_cooldown}, 高风险={has_high_risk}"
                    )
            
            # FAQ 自动生成（每轮末尾执行一次）
            if analyzed_count > 0 and settings.AUTO_FAQ_ENABLED:
                issues = (
                    db.query(Issue)
                    .order_by(Issue.created_at.desc())
                    .limit(200)
                    .all()
                )
                faq_items = await faq_service.generate_from_issues(
                    issues,
                    min_group=settings.AUTO_FAQ_MIN_GROUP,
                    max_groups=settings.AUTO_FAQ_MAX_GROUPS,
                )
                if faq_items:
                    for item in faq_items:
                        db.add(item)
                    db.commit()
            
            # 向量化存储
            if vector_messages:
                vector_kb.add_wecom_messages(vector_messages)
                
        finally:
            db.close()

        await asyncio.sleep(settings.POLLING_INTERVAL_SECONDS)


async def run_end_of_cycle_analysis() -> dict:
    """
    周期结束兜底分析：对未达正常阈值但有累积消息的群聊进行分析
    
    在每日周期结束前（默认 8:30）执行，确保低活跃群聊的问题不被遗漏。
    
    处理范围：
    - pending_count >= END_OF_CYCLE_MIN_MESSAGES（默认 3）
    - pending_count < ROOM_MIN_MESSAGES_FOR_ANALYZE（默认 10）
    
    Returns:
        dict: 包含分析结果统计
            - total_rooms: 满足条件的群聊数
            - analyzed_count: 成功分析的群聊数
            - skipped_pre_judge: 预判断跳过的群聊数
            - skipped_duplicate: 去重跳过的群聊数
            - skipped_no_issue: 无有效问题的群聊数
    """
    logger.info(
        f"[兜底分析] 开始执行周期结束兜底分析，"
        f"阈值范围=[{settings.END_OF_CYCLE_MIN_MESSAGES}, {settings.ROOM_MIN_MESSAGES_FOR_ANALYZE})"
    )
    
    result = {
        "total_rooms": 0,
        "analyzed_count": 0,
        "skipped_pre_judge": 0,
        "skipped_duplicate": 0,
        "skipped_no_issue": 0,
    }
    
    db = SessionLocal()
    try:
        # 初始化 agents（与 polling_loop 类似）
        sentinel = SentinelAgent()
        assistant = AssistantAgent()
        wecom = WeComService()
        
        cycle_start_ms = _get_current_cycle_start()
        
        # 筛选满足条件的群聊
        qualifying_rooms = []
        for room_id, state in _room_state.items():
            pending_count = state.get("pending_count", 0)
            if (
                pending_count >= settings.END_OF_CYCLE_MIN_MESSAGES
                and pending_count < settings.ROOM_MIN_MESSAGES_FOR_ANALYZE
            ):
                qualifying_rooms.append((room_id, pending_count))
        
        result["total_rooms"] = len(qualifying_rooms)
        
        if not qualifying_rooms:
            logger.info("[兜底分析] 无满足条件的群聊，跳过分析")
            return result
        
        logger.info(f"[兜底分析] 找到 {len(qualifying_rooms)} 个群聊需要兜底分析")
        
        for room_id, pending_count in qualifying_rooms:
            state = _get_room_state(room_id)
            
            logger.info(f"[兜底分析] 处理群聊 room={room_id}, pending_count={pending_count}")
            
            # 获取历史上下文
            expanded_context, msg_list = _get_room_history_context(db, room_id, min_msgtime=cycle_start_ms)
            
            if not expanded_context:
                logger.warning(f"[兜底分析] room={room_id} 无法获取历史上下文，跳过")
                result["skipped_no_issue"] += 1
                continue
            
            # LLM 预判断（高活跃群强制跳过预判断）
            if settings.PRE_JUDGE_ENABLED:
                has_issue, judge_reason = await pre_judge_has_issue(expanded_context)
                cur_raw = state.get("raw_pending_count", 0)
                if not has_issue and cur_raw < 30:
                    logger.info(f"[兜底分析] room={room_id} 预判断跳过, 原因='{judge_reason}', raw={cur_raw}")
                    state["pending_count"] = 0
                    state["raw_pending_count"] = 0
                    _save_room_state(db, room_id)
                    result["skipped_pre_judge"] += 1
                    continue
                elif not has_issue:
                    logger.info(f"[兜底分析] room={room_id} 预判断覆盖, raw={cur_raw}>=30，强制分析")
            
            # 完整 LLM 分析
            pre_analysis = await analyze_complete_llm(expanded_context)
            if not pre_analysis:
                logger.warning(f"[兜底分析] room={room_id} LLM分析返回空，保留pending等待下次重试")
                _update_cooldown(room_id)  # 进入冷却避免频繁调用
                # 不清零 pending_count 和 raw_pending_count，下次冷却结束后重新尝试
                _save_room_state(db, room_id)
                result["skipped_no_issue"] += 1
                continue
            
            phenomenon = pre_analysis.get("phenomenon", "")
            problem_quote = pre_analysis.get("problem_quote", "")
            first_problem_quote = pre_analysis.get("first_problem_quote", "")
            last_discussion_quote = pre_analysis.get("last_discussion_quote", "")
            
            # 去重检查
            if await _is_duplicate_issue(db, room_id, phenomenon, cycle_start_ms):
                logger.info(f"[兜底分析] room={room_id} 去重跳过, 问题='{phenomenon[:30]}...'")
                state["pending_count"] = 0
                state["raw_pending_count"] = 0
                _save_room_state(db, room_id)
                result["skipped_duplicate"] += 1
                continue
            
            # 找到最佳锚点消息
            anchor_msg_id = _find_best_anchor_msg(msg_list, problem_quote)
            if not anchor_msg_id and msg_list:
                mid_idx = len(msg_list) // 2
                anchor_msg_id = msg_list[mid_idx]["msg_id"]
            
            # 通过 AI 返回的关键句确定时间范围
            since_msgtime = _find_msg_time_by_quote(db, room_id, msg_list, first_problem_quote)
            until_msgtime = _find_msg_time_by_quote(db, room_id, msg_list, last_discussion_quote)
            
            logger.info(
                f"[兜底分析] room={room_id} 触发分析, "
                f"问题='{phenomenon[:30]}...', 锚点msg_id={anchor_msg_id}"
            )
            
            # 调用 process_message 进行建单和推送
            await process_message(
                db,
                msg_id=anchor_msg_id or f"end_of_cycle_{room_id}_{int(time.time()*1000)}",
                room_id=room_id,
                sender_id="system",
                msg_type="text",
                clean_text=expanded_context,
                raw_text=expanded_context,
                sentinel=sentinel,
                assistant=assistant,
                wecom=wecom,
                allow_reply=False,  # 兜底分析不自动回复
                allow_alert=True,
                allow_ticket=True,
                replay=False,
                since_msgtime=since_msgtime,
                until_msgtime=until_msgtime,
            )
            
            # 重置累积计数并持久化
            state["pending_count"] = 0
            state["raw_pending_count"] = 0
            _update_cooldown(room_id)
            _save_room_state(db, room_id)
            result["analyzed_count"] += 1
        
        logger.info(
            f"[兜底分析] 完成，总计={result['total_rooms']}, "
            f"分析={result['analyzed_count']}, 预判断跳过={result['skipped_pre_judge']}, "
            f"去重跳过={result['skipped_duplicate']}, 无有效问题={result['skipped_no_issue']}"
        )
        
    except Exception as e:
        logger.error(f"[兜底分析] 执行异常: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()
    
    return result
