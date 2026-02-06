# -*- coding: utf-8 -*-
"""
回填历史工单的 platform 字段

遍历所有已建单的 TicketDraft，对于缺少 platform 字段或为"其他"的记录，
使用 LLM 重新分析并更新。
"""

import sys
import os
import asyncio
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from loguru import logger
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.database import SessionLocal
from app.models.sql_models import TicketDraft
from app.services.ticket_service import analyze_complete_llm, normalize_platform


async def backfill_single_ticket(db: Session, ticket: TicketDraft) -> bool:
    """
    回填单个工单的 platform 字段
    
    Args:
        db: 数据库会话
        ticket: 工单对象
    
    Returns:
        是否成功更新
    """
    content = ticket.content or {}
    
    # 获取用于分析的文本
    text = (
        content.get("description")
        or content.get("summary")
        or content.get("phenomenon")
        or content.get("key_sentence")
        or ""
    )
    
    if not text or len(text) < 10:
        logger.warning(f"工单 {ticket.draft_id} 文本内容过短，跳过")
        return False
    
    try:
        # 调用 LLM 分析获取 platform
        analysis = await analyze_complete_llm(text)
        platform = normalize_platform(analysis.get("platform"))
        
        # 更新 content
        content["platform"] = platform
        ticket.content = content
        flag_modified(ticket, 'content')
        db.commit()
        db.refresh(ticket)
        
        logger.info(f"工单 {ticket.draft_id} 更新 platform: {platform}")
        return True
        
    except Exception as e:
        logger.error(f"工单 {ticket.draft_id} 回填失败: {e}")
        db.rollback()
        return False


async def backfill_all_tickets(
    limit: int = 0,
    force: bool = False,
) -> dict:
    """
    回填所有历史工单的 platform 字段
    
    Args:
        limit: 处理数量限制，0 表示不限制
        force: 是否强制重新分析（即使已有 platform）
    
    Returns:
        统计结果
    """
    db = SessionLocal()
    stats = {
        "total": 0,
        "need_update": 0,
        "updated": 0,
        "skipped": 0,
        "failed": 0,
    }
    
    try:
        # 查询所有已建单的工单
        query = db.query(TicketDraft).filter(
            TicketDraft.teambition_ticket_id != None
        )
        
        tickets = query.all()
        stats["total"] = len(tickets)
        logger.info(f"共找到 {stats['total']} 个已建单的工单")
        
        # 筛选需要更新的工单
        to_update = []
        for ticket in tickets:
            content = ticket.content or {}
            current_platform = content.get("platform", "")
            
            # 判断是否需要更新
            if force or not current_platform or current_platform == "其他":
                to_update.append(ticket)
            else:
                stats["skipped"] += 1
        
        stats["need_update"] = len(to_update)
        logger.info(f"需要更新 {stats['need_update']} 个工单，跳过 {stats['skipped']} 个")
        
        # 应用 limit
        if limit > 0:
            to_update = to_update[:limit]
            logger.info(f"限制处理数量为 {limit}")
        
        # 逐个处理
        for i, ticket in enumerate(to_update, 1):
            logger.info(f"处理 {i}/{len(to_update)}: 工单 {ticket.draft_id}")
            
            success = await backfill_single_ticket(db, ticket)
            if success:
                stats["updated"] += 1
            else:
                stats["failed"] += 1
            
            # 每 10 个工单暂停一下，避免 LLM 调用过于频繁
            if i % 10 == 0:
                await asyncio.sleep(1)
        
        logger.info(f"回填完成: {stats}")
        return stats
        
    except Exception as e:
        logger.error(f"回填过程出错: {e}")
        raise
    finally:
        db.close()


def get_platform_stats() -> dict:
    """
    获取当前 platform 字段的分布统计
    """
    db = SessionLocal()
    try:
        tickets = db.query(TicketDraft).filter(
            TicketDraft.teambition_ticket_id != None
        ).all()
        
        stats = {
            "total": len(tickets),
            "has_platform": 0,
            "missing_platform": 0,
            "platform_distribution": {},
        }
        
        for ticket in tickets:
            content = ticket.content or {}
            platform = content.get("platform", "")
            
            if platform and platform != "其他":
                stats["has_platform"] += 1
            else:
                stats["missing_platform"] += 1
            
            # 统计分布
            key = platform if platform else "(空)"
            stats["platform_distribution"][key] = stats["platform_distribution"].get(key, 0) + 1
        
        return stats
    finally:
        db.close()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="回填历史工单的 platform 字段")
    parser.add_argument("--limit", type=int, default=0, help="处理数量限制，0 表示不限制")
    parser.add_argument("--force", action="store_true", help="强制重新分析所有工单")
    parser.add_argument("--stats", action="store_true", help="只显示统计信息，不执行回填")
    
    args = parser.parse_args()
    
    if args.stats:
        stats = get_platform_stats()
        print(f"\n当前 platform 统计:")
        print(f"  总工单数: {stats['total']}")
        print(f"  已有 platform: {stats['has_platform']}")
        print(f"  缺少 platform: {stats['missing_platform']}")
        print(f"\n分布:")
        for k, v in sorted(stats['platform_distribution'].items(), key=lambda x: -x[1]):
            print(f"  {k}: {v}")
    else:
        result = asyncio.run(backfill_all_tickets(limit=args.limit, force=args.force))
        print(f"\n回填结果:")
        print(f"  总工单数: {result['total']}")
        print(f"  需要更新: {result['need_update']}")
        print(f"  成功更新: {result['updated']}")
        print(f"  跳过: {result['skipped']}")
        print(f"  失败: {result['failed']}")
