# app/api/endpoints.py
from datetime import datetime
import asyncio
import json
from fastapi import APIRouter, Depends, BackgroundTasks, Query, Body
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from loguru import logger
from app.core.database import get_db
from app.core.config import settings
from app.services import data_service, agent_service
from app.services.vector_service import vector_kb
from app.agents.assistant import AssistantAgent
from app.services.data_clean_service import DataCleanService
from app.services.dingtalk_service import DingTalkService
from app.services.wecom_service import WeComService
from app.services.alert_policy_service import should_send_alert, build_aggregate_summary
from app.services.aggregation_service import update_issue_aggregation
from app.agents.sentinel import SentinelAgent
from app.models.chat_record import ChatRecord
from app.models.sql_models import WeComMessage, Issue, TicketDraft, FaqItem, RoomAssignee, AlertEvent, RoomInfo
from app.services.ticket_service import (
    build_ticket_draft,
    build_ticket_markdown,
    build_ticket_title,
    build_customfields_pending,
    build_customfields_for_create,
    build_ai_assistant_text,
    build_tb_ai_assistant_text,
    normalize_issue_type,
    markdown_to_plain_text,
)
from app.services.teambition_service import create_task, get_task_url, build_task_payload
from app.services.mcp_bridge_service import submit_mcp_task
from app.services.teambition_oapi_service import create_task_oapi, update_task_customfield
from app.services.polling_service import process_message
from app.services.taxonomy_service import load_taxonomy, save_taxonomy
from app.services.faq_service import FaqService
from app.services.issue_filter_service import is_hard_issue
from app.services.teambition_service import generate_note_summary_llm
from app.services.ticket_service import generate_ticket_title_llm
from app.services.room_sync_service import sync_room_names, get_room_info_stats, fetch_groups_from_api
from app.schemas.common import MsgInput
from pydantic import BaseModel
from typing import Optional

router = APIRouter()
sentinel = SentinelAgent()
faq_service = FaqService()
assistant = AssistantAgent()

# 新的请求参数结构
class DeepAnalyzeRequest(BaseModel):
    room_id: Optional[str] = None
    limit: int = 20
    do_vectorize: bool = False # 核心开关：是否将这些数据存入知识库？


class ReplayRequest(BaseModel):
    room_id: Optional[str] = None
    limit: int = 10
    since: Optional[str] = None

@router.post("/deep_analysis")
def deep_analysis(
    request: DeepAnalyzeRequest, 
    background_tasks: BackgroundTasks, # FastAPI 的后台任务神器
    db: Session = Depends(get_db)
):
    """
    全能分析接口：支持 RAG 检索、工单生成、知识库构建
    """
    # 1. 获取纯文本 (给 AI 看)
    chat_lines = data_service.get_recent_chat_text(db, request.room_id, request.limit)
    
    if not chat_lines:
        return {"status": "empty", "message": "无数据"}

    # 2. (后台任务) 向量化存储
    # 如果前端传了 do_vectorize=True，我们在后台默默存入向量库，不阻塞当前请求
    if request.do_vectorize:
        # 获取原始对象
        raw_records = data_service.get_raw_records(db, request.room_id, request.limit)
        background_tasks.add_task(vector_kb.add_chat_records, raw_records)

    # 3. 提取最新的话题 (用于 RAG 搜索)
    # 简单策略：取最近的一条非客套话作为查询意图
    current_topic = chat_lines[-1].split(":")[-1] if chat_lines else ""

    # 4. 调用 Agent 进行深度分析
    full_text = "\n".join(chat_lines)
    analysis_result = agent_service.agent.analyze(full_text, current_topic)

    return {
        "status": "success",
        "message_count": len(chat_lines),
        "knowledge_base_updated": request.do_vectorize,
        "ai_analysis": analysis_result # 这里面就是结构化的 JSON
    }


@router.post("/v1/ingest/message")
async def ingest_message(
    msg: MsgInput,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    实时流处理入口：噪音过滤 -> 哨兵分析 -> 钉钉告警
    """
    if DataCleanService.is_noise(msg.content):
        return {"status": "ignored", "reason": "noise"}

    clean_text = DataCleanService.sanitize(msg.content)

    record = WeComMessage(
        msg_id=msg.msg_id or f"{msg.room_id}-{datetime.utcnow().timestamp()}",
        seq=msg.seq or 0,
        room_id=msg.room_id,
        sender_id=msg.sender,
        msg_type=msg.msg_type,
        content_raw=msg.content,
        content_clean=clean_text,
        msg_time=datetime.utcnow(),
        is_noise=False,
    )
    db.add(record)
    db.commit()
    if msg.msg_type == "text":
        background_tasks.add_task(
            vector_kb.add_wecom_messages,
            [
                {
                    "msg_id": record.msg_id,
                    "room_id": record.room_id,
                    "sender_id": record.sender_id,
                    "content_raw": record.content_raw,
                    "content_clean": record.content_clean,
                    "msg_time": record.msg_time,
                }
            ],
        )

    # 获取最近的对话上下文，用于判断问题是否已解决
    recent_chat_lines = data_service.get_recent_chat_text(db, msg.room_id, limit=10)
    if not recent_chat_lines:
        recent_chat_lines = data_service.get_recent_wecom_text(db, msg.room_id, limit=10)

    analysis = await sentinel.check_message(clean_text)
    issue_type = normalize_issue_type(analysis.get("issue_type"))
    assignee = _resolve_assignee(db, msg.room_id, issue_type)
    room_name = _resolve_room_name(db, msg.room_id)
    draft = None
    is_hard = is_hard_issue(clean_text, analysis, chat_lines=recent_chat_lines)
    phenomenon_text = analysis.get("phenomenon") or clean_text[:50]
    key_sentence_text = analysis.get("key_sentence") or (clean_text.split("\n")[0][:100] if clean_text else "")
    summary_text = clean_text[: settings.ISSUE_SUMMARY_LEN]
    issue = Issue(
        room_id=msg.room_id,
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
        evidence=[record.msg_id],
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

    if is_hard:  # is_hard 已包含 severity/is_bug/关键词/RAG 判断
        detail_url = f"{settings.INTERNAL_BASE_URL}/api/ui/rooms/{msg.room_id}"
        draft_content = build_ticket_draft(
            room_id=msg.room_id,
            summary=summary_text,
            category=f"{analysis.get('category_l1', 'OTHER')}/{analysis.get('category_l2', 'OTHER')}",
            severity=analysis.get("severity", "S1"),
            risk_score=analysis.get("risk_score", 0),
            raw_text=clean_text,
            room_name=room_name,
            customer=msg.sender,
            detail_url=detail_url,
            environment=msg.environment,
            version=msg.version,
            repro_steps=msg.repro_steps,
            attachments=msg.attachments,
            phenomenon=phenomenon_text,
            key_sentence=key_sentence_text,
        )
        llm_title = await generate_ticket_title_llm(
            phenomenon=phenomenon_text,
            key_sentence=key_sentence_text,
        )
        if llm_title:
            draft_content["llm_title"] = llm_title
            draft_content["title"] = llm_title
        draft = TicketDraft(
            issue_id=issue.issue_id,
            room_id=msg.room_id,  # 添加 room_id 用于去重检查
            title=(llm_title or draft_content.get("title")),
            severity=draft_content.get("severity"),
            category=draft_content.get("category", ""),
            environment=draft_content.get("environment"),
            version=draft_content.get("version"),
            repro_steps=draft_content.get("repro_steps"),
            attachments=draft_content.get("attachments"),
            content=draft_content,
            status="draft",
            assigned_to=assignee,
        )
        db.add(draft)
        db.commit()

    send_alert, alert_level, alert_event = should_send_alert(
        db=db,
        room_id=msg.room_id,
        category_l1=analysis.get("category_l1", "OTHER"),
        category_l2=analysis.get("category_l2", "OTHER"),
        severity=analysis.get("severity"),
        risk_score=analysis.get("risk_score", 0),
        is_alert=bool(analysis.get("is_alert")),
        is_bug=bool(analysis.get("is_bug")),
    )
    if send_alert and is_hard:
        # 使用 Sentinel AI 生成的简短摘要（50字以内的现象 + 一句关键句）
        # 如果 Sentinel 没有返回，则回退到截取原文
        aggregate_summary = build_aggregate_summary(
            db=db,
            room_id=msg.room_id,
            category_l1=analysis.get("category_l1", "OTHER"),
            category_l2=analysis.get("category_l2", "OTHER"),
            since_time=alert_event.first_seen_at if alert_event else None,
            limit=settings.ALERT_AGGREGATE_LIMIT,
        )
        ticket_url = None
        if draft:
            content = draft.content or {}
            content.update(
                {
                        "issue_type": issue_type,
                        "severity": analysis.get("severity", "-") or "-",
                        "risk_score": analysis.get("risk_score", 0),
                        "category": f"{analysis.get('category_l1', 'OTHER')}/{analysis.get('category_l2', 'OTHER')}",
                        "category_short": analysis.get("category_short"),
                        "phenomenon": phenomenon_text,
                        "key_sentence": key_sentence_text,
                        "detail_url": detail_url,
                        "room_name": room_name,
                        "room_id": msg.room_id,
                        "customer": msg.sender,
                    }
                )
            issue_type_text = issue_type
            category_display = analysis.get("category_short") or f"{analysis.get('category_l1', 'OTHER')}/{analysis.get('category_l2', 'OTHER')}"
            content["dingtalk_markdown"] = build_ticket_markdown(
                content,
                risk_score=analysis.get("risk_score", 0),
                issue_type_text=issue_type_text,
                severity=analysis.get("severity", "-") or "-",
                category_display=category_display,
                assignee=assignee or settings.DEFAULT_ASSIGNEE,
                detail_link=detail_url,
                draft_id=draft.draft_id,
                hit_count=alert_event.hit_count if alert_event else None,
                ticket_url=None,
                include_ticket_line=False,
            )
            # TB AI辅助字段：等于钉钉推送内容的纯文本版本（不含【🧠 AI 智能辅助】）
            dingtalk_md = content.get("dingtalk_markdown") or ""
            content["ai_assistant"] = markdown_to_plain_text(dingtalk_md) if dingtalk_md else ""
            content["ai_assistant_text"] = content["ai_assistant"]
            content.pop("customfields_pending", None)
            build_customfields_pending(content)
            title_text = build_ticket_title(content)
            draft.title = title_text
            content["title"] = title_text
            draft.content = content
            db.commit()

            # 推送钉钉分析结果（不带建单链接），使用已生成的 markdown_text（避免重复拼装）
            background_tasks.add_task(
                DingTalkService.send_alert,
                summary=aggregate_summary or clean_text,
                risk=analysis.get("risk_score", 0),
                reason=analysis.get("reason", ""),
                room_id=msg.room_id,
                room_name=room_name,
                issue_type=issue_type,
                category=f"{analysis.get('category_l1', 'OTHER')}/{analysis.get('category_l2', 'OTHER')}",
                category_short=analysis.get("category_short"),
                severity=analysis.get("severity", ""),
                assignee=assignee,
                hit_count=alert_event.hit_count if alert_event else None,
                phenomenon=phenomenon_text,
                key_sentence=key_sentence_text,
                detail_url=detail_url,
                ticket_url=None,
                draft_id=None,
                include_ticket_line=False,
                suggested_reply=None,
                markdown_text=(content.get("dingtalk_markdown") if content else None),
            )

            if settings.TEAMBITION_AUTO_CREATE and not draft.teambition_ticket_id:
                if isinstance(draft.content, dict) and not draft.content.get("llm_note_summary"):
                    draft.content["llm_note_summary"] = await generate_note_summary_llm(clean_text)
                    db.commit()

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

            ticket_url = get_task_url(draft.teambition_ticket_id)
            if ticket_url and isinstance(draft.content, dict):
                # 建单成功补发通知（带链接）
                content = draft.content
                content["dingtalk_markdown"] = build_ticket_markdown(
                    content,
                    risk_score=int(content.get("risk_score") or 0),
                    issue_type_text=content.get("issue_type") or "问题反馈",
                    severity=content.get("severity") or "-",
                    category_display=content.get("category_short") or content.get("category") or "-",
                    assignee=content.get("assignee") or draft.assigned_to or settings.DEFAULT_ASSIGNEE,
                    detail_link=content.get("detail_url") or f"{settings.INTERNAL_BASE_URL}/api/ui/rooms/{content.get('room_id')}",
                    draft_id=draft.draft_id,
                    hit_count=content.get("hit_count"),
                    ticket_url=ticket_url,
                    include_ticket_line=True,
                )
                draft.content = content
                db.commit()
                background_tasks.add_task(
                    DingTalkService.send_ticket_update,
                    draft_id=draft.draft_id,
                    ticket_url=ticket_url or "",
                    room_label=room_name,
                    markdown_text=content.get("dingtalk_markdown"),
                )

    return {"status": "alerted" if is_hard else "saved", "analysis": analysis}

    return {"status": "saved", "analysis": analysis}


@router.get("/v1/taxonomy")
def get_taxonomy():
    return load_taxonomy()


@router.put("/v1/taxonomy")
def update_taxonomy(payload: dict):
    save_taxonomy(payload)
    return {"status": "ok"}


@router.get("/v1/tickets/{draft_id}/confirm")
def confirm_ticket(draft_id: int, db: Session = Depends(get_db)):
    draft = db.query(TicketDraft).filter(TicketDraft.draft_id == draft_id).first()
    if not draft:
        return HTMLResponse(
            "<html><body style='font-family:Arial,sans-serif;margin:20px;'>未找到工单</body></html>"
        )
    draft.status = "confirmed"
    if not draft.teambition_ticket_id and settings.TEAMBITION_MODE == "api":
        title = draft.title or "自动工单"
        description = (draft.content or {}).get("description", "")
        ticket_id = create_task(title, description)
        draft.teambition_ticket_id = ticket_id
    db.commit()
    html = _render_simple_result_html(
        title="已确认建单",
        message=f"Teambition 工单已创建：{draft.teambition_ticket_id or '已提交'}",
        detail_url=f"/api/ui/tickets/{draft_id}",
    )
    return HTMLResponse(html)


@router.get("/v1/tickets/{draft_id}/mcp_request")
def request_mcp_ticket(draft_id: int, db: Session = Depends(get_db)):
    draft = db.query(TicketDraft).filter(TicketDraft.draft_id == draft_id).first()
    if not draft:
        return HTMLResponse(
            "<html><body style='font-family:Arial,sans-serif;margin:20px;'>未找到工单</body></html>"
        )
    content = draft.content if isinstance(draft.content, dict) else {}
    room_id = content.get("room_id")
    needs_ai = not content.get("phenomenon") or not content.get("key_sentence") or not content.get("ai_solution")
    if needs_ai and room_id:
        chat_lines = data_service.get_recent_chat_text(db, room_id, limit=30)
        if not chat_lines:
            chat_lines = data_service.get_recent_wecom_text(db, room_id, limit=30)
        chat_context = "\n".join(chat_lines) if chat_lines else (content.get("description") or "")
        similar_docs = vector_kb.search_similar_faq(content.get("summary") or "", k=3)
        if not similar_docs:
            similar_docs = vector_kb.search_similar_issues(content.get("summary") or "", k=3)
        similar_context = "\n".join([d.page_content for d in similar_docs]) if similar_docs else "无"
        try:
            ai_insight = asyncio.run(assistant.analyze(chat_context, similar_context))
            content.update(
                {
                    "phenomenon": ai_insight.get("phenomenon"),
                    "key_sentence": ai_insight.get("key_sentence"),
                    "ai_solution": ai_insight.get("ai_solution"),
                    "similar_case_solution": ai_insight.get("similar_case_solution"),
                    "suggested_reply": ai_insight.get("soothing_reply"),
                }
            )
            draft.content = content
            db.commit()
        except Exception:
            pass
    if room_id and not content.get("room_name"):
        content["room_name"] = _resolve_room_name(db, str(room_id))
    if not content.get("issue_type"):
        issue = db.query(Issue).filter(Issue.issue_id == draft.issue_id).first()
        if issue and issue.issue_type:
            content["issue_type"] = issue.issue_type
        if issue and issue.severity:
            content.setdefault("severity", issue.severity)
        if issue and issue.category:
            content.setdefault("category", issue.category)
    issue_type_text = content.get("issue_type") or "问题反馈"
    category_display = content.get("category_short") or content.get("category") or "-"
    severity = content.get("severity") or "-"
    assignee = content.get("assignee") or draft.assigned_to or settings.DEFAULT_ASSIGNEE
    detail_link = content.get("detail_url") or f"{settings.INTERNAL_BASE_URL}/api/ui/rooms/{room_id}"
    content["dingtalk_markdown"] = build_ticket_markdown(
        content,
        risk_score=int(content.get("risk_score") or 0),
        issue_type_text=issue_type_text,
        severity=severity,
        category_display=category_display,
        assignee=assignee,
        detail_link=detail_link,
        draft_id=draft.draft_id,
        hit_count=content.get("hit_count"),
        ticket_url=None,
        include_ticket_line=False,
    )
    draft.content = content
    db.commit()
    title = draft.title or "自动工单"
    description = content.get("description", "")
    payload = build_task_payload(title, description, content)
    if not payload:
        return HTMLResponse(
            "<html><body style='font-family:Arial,sans-serif;margin:20px;'>未配置 Teambition 项目</body></html>"
        )
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
        ticket_url = get_task_url(draft.teambition_ticket_id)
        room_label = None
        if draft.content and isinstance(draft.content, dict):
            room_label = draft.content.get("room_id")
        if room_label:
            room_label = _resolve_room_name(db, str(room_label))
        if draft.content and isinstance(draft.content, dict):
            content = draft.content
            content["dingtalk_markdown"] = build_ticket_markdown(
                content,
                risk_score=int(content.get("risk_score") or 0),
                issue_type_text=content.get("issue_type") or "问题反馈",
                severity=content.get("severity") or "-",
                category_display=content.get("category_short") or content.get("category") or "-",
                assignee=content.get("assignee") or draft.assigned_to or settings.DEFAULT_ASSIGNEE,
                detail_link=content.get("detail_url") or f"{settings.INTERNAL_BASE_URL}/api/ui/rooms/{content.get('room_id')}",
                draft_id=draft.draft_id,
                hit_count=content.get("hit_count"),
                ticket_url=ticket_url,
                include_ticket_line=False,
            )
            draft.content = content
            db.commit()
        # 按需求：一个问题只推送一条钉钉消息（不再补发“工单已创建”二次推送）
        html = _render_simple_result_html(
            title="建单成功",
            message=f"Teambition 工单已创建：{draft.teambition_ticket_id} <br/>"
            f"<a href='{ticket_url}' target='_blank'>查看工单</a>",
            detail_url=f"/api/ui/tickets/{draft_id}",
        )
        return HTMLResponse(html)

    html = _render_simple_result_html(
        title="已提交 MCP 建单请求",
        message="系统已生成 MCP 建单请求，等待协作流程执行。",
        detail_url=f"/api/ui/tickets/{draft_id}",
    )
    return HTMLResponse(html)


@router.get("/v1/tickets/{draft_id}/mcp_payload")
def get_mcp_payload(draft_id: int, db: Session = Depends(get_db)):
    draft = db.query(TicketDraft).filter(TicketDraft.draft_id == draft_id).first()
    if not draft:
        return {"status": "not_found"}
    title = draft.title or "自动工单"
    description = (draft.content or {}).get("description", "")
    payload = build_task_payload(title, description, draft.content if isinstance(draft.content, dict) else None)
    if not payload:
        return {"status": "invalid", "message": "project_id missing"}
    return {"status": "ok", "payload": payload}


@router.post("/v1/tickets/{draft_id}/set_teambition")
def set_teambition_ticket(draft_id: int, payload: dict, db: Session = Depends(get_db)):
    draft = db.query(TicketDraft).filter(TicketDraft.draft_id == draft_id).first()
    if not draft:
        return {"status": "not_found"}
    ticket_id = payload.get("ticket_id")
    if not ticket_id:
        return {"status": "invalid", "message": "ticket_id required"}
    draft.teambition_ticket_id = ticket_id
    draft.status = "ticketed"
    draft.mcp_status = "completed"
    draft.mcp_completed_at = datetime.utcnow()
    db.commit()
    return {
        "status": "ok",
        "ticket_id": ticket_id,
        "ticket_url": get_task_url(ticket_id),
    }


@router.get("/v1/mcp/queue")
def list_mcp_queue(db: Session = Depends(get_db)):
    items = (
        db.query(TicketDraft)
        .filter(TicketDraft.mcp_status == "pending")
        .order_by(TicketDraft.mcp_requested_at.asc())
        .limit(50)
        .all()
    )
    return [
        {
            "draft_id": d.draft_id,
            "issue_id": d.issue_id,
            "title": d.title,
            "payload": d.mcp_payload,
            "requested_at": d.mcp_requested_at,
        }
        for d in items
    ]


@router.get("/v1/tickets")
def list_tickets(
    limit: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    items = (
        db.query(TicketDraft)
        .order_by(TicketDraft.created_at.desc())
        .limit(limit)
        .all()
    )
    changed = False
    response = []
    for d in items:
        content = d.content if isinstance(d.content, dict) else {}
        before = json.dumps(content, ensure_ascii=False, sort_keys=True, default=str)
        build_customfields_pending(content)
        after = json.dumps(content, ensure_ascii=False, sort_keys=True, default=str)
        if after != before:
            d.content = content
            changed = True
        response.append(
            {
                "draft_id": d.draft_id,
                "issue_id": d.issue_id,
                "title": d.title,
                "severity": d.severity,
                "category": d.category,
                "status": d.status,
                "created_at": d.created_at,
                "teambition_ticket_id": d.teambition_ticket_id,
                "room_id": content.get("room_id"),
                "room_name": content.get("room_name"),
                "detail_url": content.get("detail_url"),
                "customfields_pending": content.get("customfields_pending"),
            }
        )
    if changed:
        db.commit()
    return response


@router.get("/v1/tickets/{draft_id}")
def get_ticket(
    draft_id: int,
    format: str = Query("json", pattern="^(json|html)$"),
    db: Session = Depends(get_db),
):
    draft = db.query(TicketDraft).filter(TicketDraft.draft_id == draft_id).first()
    if not draft:
        if format == "html":
            return HTMLResponse(
                "<html><body style='font-family:Arial,sans-serif;margin:20px;'>未找到工单</body></html>"
            )
        return {"status": "not_found"}
    payload = {
        "draft_id": draft.draft_id,
        "issue_id": draft.issue_id,
        "title": draft.title,
        "severity": draft.severity,
        "category": draft.category,
        "environment": draft.environment,
        "version": draft.version,
        "repro_steps": draft.repro_steps,
        "attachments": draft.attachments,
        "status": draft.status,
        "teambition_ticket_id": draft.teambition_ticket_id,
        "content": draft.content,
    }
    if format == "html":
        return HTMLResponse(_render_ticket_html(payload))
    return payload


@router.post("/v1/test/replay")
async def replay_recent_messages(
    payload: ReplayRequest | None = Body(None),
    db: Session = Depends(get_db),
):
    payload = payload or ReplayRequest()
    query = db.query(WeComMessage).order_by(WeComMessage.msg_time.desc())
    if payload.room_id:
        query = query.filter(WeComMessage.room_id == payload.room_id)
    if payload.since:
        try:
            since_text = payload.since.replace("Z", "+00:00")
            since_dt = datetime.fromisoformat(since_text)
            query = query.filter(WeComMessage.msg_time >= since_dt)
        except Exception:
            pass
    records = query.limit(payload.limit).all()
    if not records:
        return {"status": "empty", "count": 0, "results": []}

    sentinel = SentinelAgent()
    assistant = AssistantAgent()
    wecom = WeComService()
    results = []
    for r in reversed(records):
        clean_text = r.content_clean or r.content_raw or ""
        results.append(
            await process_message(
                db,
                msg_id=str(r.msg_id),
                room_id=str(r.room_id),
                sender_id=str(r.sender_id or ""),
                msg_type=str(r.msg_type or ""),
                clean_text=clean_text,
                raw_text=str(r.content_raw or ""),
                sentinel=sentinel,
                assistant=assistant,
                wecom=wecom,
                allow_reply=False,
                allow_alert=True,
                allow_ticket=True,
                replay=True,
            )
        )
    return {"status": "ok", "count": len(results), "results": results}


@router.get("/v1/tickets/{draft_id}/payload")
def get_ticket_payload(draft_id: int, db: Session = Depends(get_db)):
    draft = db.query(TicketDraft).filter(TicketDraft.draft_id == draft_id).first()
    if not draft:
        return {"status": "not_found"}
    content = draft.content if isinstance(draft.content, dict) else {}
    build_customfields_pending(content)
    title = draft.title or content.get("title") or "自动工单"
    description = content.get("description") or ""
    payload = build_task_payload(title, description, content)
    if payload:
        payload["customfields"] = build_customfields_for_create(content)
    return {
        "draft_id": draft.draft_id,
        "mode": settings.TEAMBITION_MODE,
        "payload": payload,
        "customfields_pending": content.get("customfields_pending"),
    }


@router.get("/v1/tickets/{draft_id}/assign")
def assign_ticket(draft_id: int, assignee: Optional[str] = None, db: Session = Depends(get_db)):
    draft = db.query(TicketDraft).filter(TicketDraft.draft_id == draft_id).first()
    if not draft:
        return HTMLResponse(
            "<html><body style='font-family:Arial,sans-serif;margin:20px;'>未找到工单</body></html>"
        )
    draft.assigned_to = assignee or settings.DEFAULT_ASSIGNEE
    db.commit()
    html = _render_simple_result_html(
        title="指派完成",
        message=f"工单已指派给 {draft.assigned_to}",
        detail_url=f"/api/ui/tickets/{draft_id}",
    )
    return HTMLResponse(html)


@router.get("/v1/tickets/{draft_id}/ignore")
def ignore_ticket(draft_id: int, db: Session = Depends(get_db)):
    draft = db.query(TicketDraft).filter(TicketDraft.draft_id == draft_id).first()
    if not draft:
        return HTMLResponse(
            "<html><body style='font-family:Arial,sans-serif;margin:20px;'>未找到工单</body></html>"
        )
    draft.status = "ignored"
    db.commit()
    html = _render_simple_result_html(
        title="已忽略",
        message="该工单已标记为忽略",
        detail_url=f"/api/ui/tickets/{draft_id}",
    )
    return HTMLResponse(html)


@router.get("/ui/tickets/{draft_id}", response_class=HTMLResponse)
def ticket_detail_ui(draft_id: int, db: Session = Depends(get_db)):
    draft = db.query(TicketDraft).filter(TicketDraft.draft_id == draft_id).first()
    if not draft:
        return HTMLResponse(
            "<html><body style='font-family:Arial,sans-serif;margin:20px;'>未找到工单</body></html>"
        )
    payload = {
        "draft_id": draft.draft_id,
        "issue_id": draft.issue_id,
        "title": draft.title,
        "severity": draft.severity,
        "category": draft.category,
        "environment": draft.environment,
        "version": draft.version,
        "repro_steps": draft.repro_steps,
        "attachments": draft.attachments or [],
        "status": draft.status,
        "teambition_ticket_id": draft.teambition_ticket_id,
        "content": draft.content or {},
    }
    return HTMLResponse(_render_ticket_html(payload))


def _render_simple_result_html(title: str, message: str, detail_url: str) -> str:
    return f"""
<!doctype html>
<html>
  <head>
    <meta charset="utf-8"/>
    <title>{title}</title>
  </head>
  <body style="font-family: Arial, sans-serif; margin: 20px; background: #f7f8fa;">
    <div style="max-width: 760px; margin: 0 auto; background: #fff; padding: 18px 22px; border-radius: 10px; box-shadow: 0 2px 8px rgba(0,0,0,0.06);">
      <h2 style="margin: 0 0 12px 0;">{title}</h2>
      <div style="color:#333; margin-bottom: 12px;">{message}</div>
      <a href="{detail_url}" style="display:inline-block; padding:8px 12px; background:#1a73e8; color:#fff; border-radius:6px; text-decoration:none;">查看详情</a>
    </div>
  </body>
</html>
"""


def _render_ticket_html(payload: dict) -> str:
    content = payload.get("content") or {}
    attachments = payload.get("attachments") or content.get("attachments") or []
    attachment_list = "".join(
        [f"<li style='margin:4px 0;'>{a}</li>" for a in attachments]
    ) or "<li style='color:#999;'>无</li>"
    environment = payload.get("environment") or content.get("environment") or "-"
    version = payload.get("version") or content.get("version") or "-"
    repro_steps = payload.get("repro_steps") or content.get("repro_steps") or "未提供"
    summary = content.get("summary") or payload.get("title") or "-"
    description = content.get("description") or "未提供"
    customfields = content.get("customfields_pending") or []
    customfields_html = "".join(
        [
            f"<li style='margin:4px 0;'><code>{i.get('customfieldId')}</code> = {i.get('value') or '-'}</li>"
            for i in customfields
        ]
    ) or "<li style='color:#999;'>无</li>"
    dingtalk_markdown = content.get("dingtalk_markdown") or ""
    return f"""
<!doctype html>
<html>
  <head>
    <meta charset="utf-8"/>
    <title>工单草稿 #{payload.get('draft_id')}</title>
  </head>
  <body style="font-family: Arial, sans-serif; margin: 20px; background: #f7f8fa;">
    <div style="max-width: 900px; margin: 0 auto; background: #fff; padding: 18px 22px; border-radius: 10px; box-shadow: 0 2px 8px rgba(0,0,0,0.06);">
      <div style="display:flex; align-items:center; justify-content:space-between; gap:12px;">
        <div>
          <h2 style="margin:0 0 6px 0;">{payload.get('title') or '工单草稿'}</h2>
          <div style="color:#666;">草稿编号 #{payload.get('draft_id')} · 关联问题 {payload.get('issue_id') or '-'}</div>
        </div>
        <span style="padding:6px 10px; border-radius:6px; background:#f1f3f4; color:#444; font-size:12px;">
          {payload.get('status') or 'draft'}
        </span>
      </div>

      <div style="margin-top:16px; display:flex; gap:10px; flex-wrap:wrap;">
        <span style="padding:6px 10px; border-radius:6px; background:#fdecea; color:#c5221f;">严重度: {payload.get('severity') or '-'}</span>
        <span style="padding:6px 10px; border-radius:6px; background:#e8f0fe; color:#1a73e8;">分类: {payload.get('category') or '-'}</span>
        <span style="padding:6px 10px; border-radius:6px; background:#f1f3f4; color:#444;">环境: {environment}</span>
        <span style="padding:6px 10px; border-radius:6px; background:#f1f3f4; color:#444;">版本: {version}</span>
      </div>

      <div style="margin-top:18px;">
        <h3 style="margin:0 0 8px 0;">问题摘要</h3>
        <div style="white-space:pre-wrap; color:#333; background:#fafafa; padding:10px 12px; border-radius:8px;">{summary}</div>
      </div>

      <div style="margin-top:16px;">
        <h3 style="margin:0 0 8px 0;">问题描述</h3>
        <div style="white-space:pre-wrap; color:#333; background:#fafafa; padding:10px 12px; border-radius:8px;">{description}</div>
      </div>

      <div style="margin-top:16px;">
        <h3 style="margin:0 0 8px 0;">复现步骤</h3>
        <div style="white-space:pre-wrap; color:#333; background:#fafafa; padding:10px 12px; border-radius:8px;">{repro_steps}</div>
      </div>

      <div style="margin-top:16px;">
        <h3 style="margin:0 0 8px 0;">附件</h3>
        <ul style="margin:0; padding-left:18px;">{attachment_list}</ul>
      </div>

      <div style="margin-top:16px;">
        <h3 style="margin:0 0 8px 0;">自定义字段预填</h3>
        <ul style="margin:0; padding-left:18px;">{customfields_html}</ul>
      </div>

      <div style="margin-top:16px;">
        <h3 style="margin:0 0 8px 0;">钉钉推送正文</h3>
        <div style="white-space:pre-wrap; color:#333; background:#fafafa; padding:10px 12px; border-radius:8px;">{dingtalk_markdown or "未生成"}</div>
      </div>

      <div style="margin-top:16px; color:#666;">
        Teambition 工单 ID: {payload.get('teambition_ticket_id') or "未生成"}
      </div>
    </div>
  </body>
</html>
"""


@router.get("/admin/taxonomy", response_class=HTMLResponse)
def taxonomy_admin_page():
    html = """
<!doctype html>
<html>
  <head>
    <meta charset="utf-8"/>
    <title>Taxonomy 管理</title>
  </head>
  <body style="font-family: Arial, sans-serif; margin: 20px;">
    <h2>Taxonomy 管理</h2>
    <p>直接编辑 JSON 并保存。</p>
    <textarea id="json" style="width: 100%; height: 70vh;"></textarea>
    <div style="margin-top: 10px;">
      <button onclick="save()">保存</button>
      <span id="status" style="margin-left: 12px;"></span>
    </div>
    <script>
      async function load() {
        const res = await fetch('/api/v1/taxonomy');
        const data = await res.json();
        document.getElementById('json').value = JSON.stringify(data, null, 2);
      }
      async function save() {
        const text = document.getElementById('json').value;
        const payload = JSON.parse(text);
        const res = await fetch('/api/v1/taxonomy', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const result = await res.json();
        document.getElementById('status').innerText = result.status || 'ok';
      }
      load();
    </script>
  </body>
</html>
"""
    return HTMLResponse(html)


@router.get("/ui/rooms", response_class=HTMLResponse)
def list_rooms_ui(db: Session = Depends(get_db)):
    rooms = (
        db.query(ChatRecord.roomid, func.max(ChatRecord.msgtime).label("last_msgtime"))
        .filter(ChatRecord.roomid.isnot(None))
        .group_by(ChatRecord.roomid)
        .order_by(func.max(ChatRecord.msgtime).desc())
        .limit(200)
        .all()
    )
    room_list = "\n".join(
        [
            (
                "<div style='padding:12px 14px; border-bottom:1px solid #f0f0f0;'>"
                f"<a href='/api/ui/rooms/{r.roomid}' style='text-decoration:none; color:#1a73e8;'>"
                f"{_resolve_room_name(db, r.roomid) or '群'} ({r.roomid})"
                "</a>"
                f"<div style='color:#999; font-size:12px; margin-top:6px;'>最新消息时间: {_format_msgtime(r.last_msgtime)}</div>"
                "</div>"
            )
            for r in rooms
            if r.roomid
        ]
    )
    html = f"""
<!doctype html>
<html>
  <head>
    <meta charset="utf-8"/>
    <title>客户原声 - 群列表</title>
  </head>
  <body style="font-family: Arial, sans-serif; margin: 20px; background:#f7f8fa;">
    <div style="max-width: 1100px; margin: 0 auto;">
      <h2 style="margin: 0 0 12px 0;">客户原声 - 群列表</h2>
      <div style="background:#fff; border-radius:10px; box-shadow:0 2px 8px rgba(0,0,0,0.06); overflow:hidden;">
        {room_list if room_list else "<div style='padding:14px;color:#999;'>暂无群记录</div>"}
      </div>
    </div>
  </body>
</html>
"""
    return HTMLResponse(html)


@router.get("/ui/entry", response_class=HTMLResponse)
def teambition_entry_ui(project: str | None = None, projectId: str | None = None):
    """
    Teambition 项目「更多」菜单入口页。
    入口 URL 里可用 project=$_id$ / projectId=$_id$ 透传当前项目 ID。
    """
    pid = projectId or project or ""
    html = f"""
<!doctype html>
<html>
  <head>
    <meta charset="utf-8"/>
    <title>会话AI分析 - 项目入口</title>
  </head>
  <body style="font-family: Arial, sans-serif; margin: 20px; background:#f7f8fa;">
    <div style="max-width: 1100px; margin: 0 auto;">
      <h2 style="margin: 0 0 12px 0;">会话AI分析 - 项目入口</h2>
      <div style="background:#fff; border-radius:10px; padding:14px 18px; box-shadow:0 2px 8px rgba(0,0,0,0.06);">
        <div style="color:#666; margin-bottom:10px;">
          <b>projectId</b>: {pid if pid else "<i>未传入</i>"}
        </div>
        <div style="display:flex; gap:12px; flex-wrap:wrap;">
          <a href="/api/ui/rooms" style="text-decoration:none; color:#fff; background:#1a73e8; padding:10px 14px; border-radius:8px;">客户原声（群列表）</a>
          <a href="/api/docs" style="text-decoration:none; color:#1a73e8; background:#eef2ff; padding:10px 14px; border-radius:8px;">接口文档</a>
        </div>
        <div style="margin-top:12px; color:#999; font-size:12px;">
          说明：Teambition 入口配置示例：<code>{settings.INTERNAL_BASE_URL}/api/ui/entry?project=$_id$</code>
        </div>
      </div>
    </div>
  </body>
</html>
"""
    return HTMLResponse(html)


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


@router.get("/ui/rooms/{room_id}", response_class=HTMLResponse)
def room_messages_ui(
    room_id: str,
    since: int = Query(None, description="起始时间戳(msgtime)，只显示该时间之后的消息"),
    until: int = Query(None, description="结束时间戳(msgtime)，只显示该时间之前的消息"),
    db: Session = Depends(get_db),
):
    """
    查看群聊原声内容
    
    支持时间窗口过滤：
    - since: 只显示 msgtime >= since 的消息
    - until: 只显示 msgtime <= until 的消息
    - 不传参数时显示最近一段消息（默认由 UI_MESSAGES_LIMIT_DEFAULT 控制）
    """
    # 获取友好群名（从 room_info 表查找）
    room_name = _resolve_room_name(db, room_id)
    
    # 构建查询
    query = db.query(ChatRecord).filter(
        ChatRecord.roomid == room_id,
        ChatRecord.msgtype == "text",
    )
    
    # 应用时间窗口过滤
    has_time_filter = since is not None or until is not None
    if since is not None:
        query = query.filter(ChatRecord.msgtime >= since)
    if until is not None:
        query = query.filter(ChatRecord.msgtime <= until)
    
    # 有时间窗口过滤时，优先展示该窗口内的对话（设一个安全上限，避免极端情况下页面过重）
    # 无过滤时仅展示最近一段对话，减少加载压力
    limit = (
        settings.UI_MESSAGES_LIMIT_WITH_FILTER
        if has_time_filter
        else settings.UI_MESSAGES_LIMIT_DEFAULT
    )
    records = query.order_by(ChatRecord.msgtime.desc()).limit(limit).all()
    
    messages = []
    for r in reversed(records):
        content = data_service._extract_content(r.msgData)
        if not content:
            continue
        sender = r.sender[-6:] if r.sender else "Unknown"
        time_text = _format_msgtime(r.msgtime)
        side = _sender_side(sender)
        bubble_bg = "#e6f4ea" if side == "right" else "#ffffff"
        bubble_border = "#ccebd6" if side == "right" else "#e6e6e6"
        align_style = "flex-end" if side == "right" else "flex-start"
        avatar_bg = "#1a73e8" if side == "right" else "#6b7280"
        avatar_text = sender[:2] if sender and sender != "Unknown" else "?"
        time_align = "right" if side == "right" else "left"
        messages.append(
            f"<div style='display:flex; justify-content:{align_style}; margin:12px 0;'>"
            f"<div style='display:flex; gap:10px; max-width:70%; align-items:flex-end;'>"
            f"<div style='width:32px; height:32px; border-radius:50%; background:{avatar_bg}; color:#fff; display:flex; align-items:center; justify-content:center; font-size:12px; flex-shrink:0;'>{avatar_text}</div>"
            f"<div>"
            f"<div style='color:#666; font-size:12px; margin-bottom:4px; text-align:{time_align};'>{sender} · {time_text}</div>"
            f"<div style='background:{bubble_bg}; border:1px solid {bubble_border}; padding:10px 12px; border-radius:10px; box-shadow:0 1px 2px rgba(0,0,0,0.04); white-space:pre-wrap;'>{content}</div>"
            f"</div>"
            f"</div>"
            f"</div>"
        )
    
    # 页面提示信息
    if has_time_filter:
        time_hint = f"<div style='background:#fff3cd; color:#856404; padding:10px 14px; border-radius:6px; margin-bottom:12px; font-size:14px;'>📍 以下为问题发生时段的核心对话（共 {len(messages)} 条）&nbsp;&nbsp;<a href='/api/ui/rooms/{room_id}' style='color:#1a73e8;'>查看完整对话</a></div>"
    else:
        time_hint = f"<div style='color:#666; font-size:13px; margin-bottom:12px;'>显示最近 {len(messages)} 条消息</div>"
    
    html = f"""
<!doctype html>
<html>
  <head>
    <meta charset="utf-8"/>
    <title>客户原声 - {room_name}</title>
  </head>
  <body style="font-family: Arial, sans-serif; margin: 20px; background:#f7f8fa;">
    <div style="max-width: 1100px; margin: 0 auto;">
      <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:12px;">
        <h2 style="margin:0;">客户原声 - 群 {room_name}</h2>
        <a href="/api/ui/rooms" style="text-decoration:none; color:#1a73e8;">返回群列表</a>
      </div>
      {time_hint}
      <div style="background:#fff; border-radius:10px; padding:14px 18px; box-shadow:0 2px 8px rgba(0,0,0,0.06);">
        {''.join(messages) if messages else '暂无内容'}
      </div>
    </div>
  </body>
</html>
"""
    return HTMLResponse(html)


def _format_msgtime(ts: int | None) -> str:
    if not ts:
        return "-"
    try:
        value = int(ts)
    except Exception:
        return "-"
    if value > 10**12:
        value = int(value / 1000)
    return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M")


def _sender_side(sender: str) -> str:
    if not sender:
        return "left"
    score = sum(ord(ch) for ch in sender)
    return "right" if score % 2 == 0 else "left"


@router.get("/v1/room_assignments")
def list_room_assignments(db: Session = Depends(get_db)):
    mappings = db.query(RoomAssignee).all()
    return [{"room_id": m.room_id, "assignee": m.assignee} for m in mappings]


@router.put("/v1/room_assignments/{room_id}")
def set_room_assignment(room_id: str, payload: dict, db: Session = Depends(get_db)):
    assignee = payload.get("assignee")
    if not assignee:
        return {"status": "invalid", "message": "assignee required"}
    mapping = db.query(RoomAssignee).filter(RoomAssignee.room_id == room_id).first()
    if not mapping:
        mapping = RoomAssignee(room_id=room_id, assignee=assignee)
        db.add(mapping)
    else:
        mapping.assignee = assignee
    db.commit()
    return {"status": "ok", "room_id": room_id, "assignee": assignee}


@router.get("/v1/room_names")
def list_room_names(db: Session = Depends(get_db)):
    mappings = db.query(RoomInfo).all()
    return [{"room_id": m.room_id, "room_name": m.room_name} for m in mappings]


@router.put("/v1/room_names/{room_id}")
def set_room_name(room_id: str, payload: dict, db: Session = Depends(get_db)):
    room_name = payload.get("room_name")
    if not room_name:
        return {"status": "invalid", "message": "room_name required"}
    mapping = db.query(RoomInfo).filter(RoomInfo.room_id == room_id).first()
    if not mapping:
        mapping = RoomInfo(room_id=room_id, room_name=room_name)
        db.add(mapping)
    else:
        mapping.room_name = room_name
    db.commit()
    return {"status": "ok", "room_id": room_id, "room_name": room_name}


@router.post("/v1/faq/generate")
async def generate_faq(db: Session = Depends(get_db)):
    issues = db.query(Issue).order_by(Issue.created_at.desc()).limit(200).all()
    items = await faq_service.generate_from_issues(
        issues, min_group=settings.AUTO_FAQ_MIN_GROUP, max_groups=settings.AUTO_FAQ_MAX_GROUPS
    )
    for item in items:
        db.add(item)
    db.commit()
    return {"created": len(items)}


@router.get("/v1/faq")
def list_faq(db: Session = Depends(get_db)):
    faqs = db.query(FaqItem).order_by(FaqItem.created_at.desc()).limit(50).all()
    return [
        {
            "faq_id": f.faq_id,
            "category_l1": f.category_l1,
            "category_l2": f.category_l2,
            "question": f.question,
            "answer": f.answer,
            "source_issue_ids": f.source_issue_ids,
            "created_at": f.created_at,
        }
        for f in faqs
    ]


# ============================================================
# 群名同步 API
# ============================================================

@router.get("/v1/sync/rooms/status")
def get_room_sync_status(db: Session = Depends(get_db)):
    """
    查看 room_info 表当前状态
    
    返回：
    - total_count: 已映射的群聊数量
    - api_base_url: 外部 API 地址
    - sample: 最近更新的 10 条记录
    """
    stats = get_room_info_stats(db)
    return {"status": "ok", **stats}


@router.post("/v1/sync/rooms")
def trigger_room_sync(db: Session = Depends(get_db)):
    """
    手动触发群名同步
    
    从外部 API 获取群组列表，同步到 room_info 表
    """
    stats = sync_room_names(db)
    return {
        "status": "ok",
        "message": f"同步完成: 新增 {stats['created']} 个，更新 {stats['updated']} 个",
        "stats": stats,
    }


@router.get("/v1/sync/rooms/preview")
def preview_room_sync():
    """
    预览从外部 API 获取的群组数据（不写入数据库）
    
    用于调试和验证 API 连通性
    """
    groups = fetch_groups_from_api()
    return {
        "status": "ok",
        "count": len(groups),
        "sample": groups[:20],  # 只返回前 20 条作为预览
    }