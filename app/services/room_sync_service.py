"""
群名同步服务

从外部 API 同步群聊 roomid 和 display_name 映射到 room_info 表。
"""
import logging
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.sql_models import RoomInfo

logger = logging.getLogger(__name__)


def fetch_groups_from_api(timeout: int = 30) -> list[dict[str, Any]]:
    """
    从外部 API 获取群组列表
    
    API: GET {CHAT_API_BASE_URL}/chat/groups
    
    Returns:
        群组列表，每项包含 roomid, display_name 等字段
    """
    url = f"{settings.CHAT_API_BASE_URL}/chat/groups"
    
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(url)
            response.raise_for_status()
            result = response.json()
            
            if result.get("code") != 200:
                logger.warning(f"[群名同步] API 返回错误: {result.get('message')}")
                return []
            
            data = result.get("data", [])
            logger.info(f"[群名同步] 从 API 获取到 {len(data)} 个群组")
            return data
            
    except httpx.TimeoutException:
        logger.error(f"[群名同步] API 请求超时: {url}")
        return []
    except httpx.HTTPStatusError as e:
        logger.error(f"[群名同步] API 请求失败: {e.response.status_code}")
        return []
    except Exception as e:
        logger.error(f"[群名同步] API 请求异常: {e}")
        return []


def sync_room_names(db: Session) -> dict[str, int]:
    """
    同步群名到 room_info 表
    
    Args:
        db: 数据库会话
    
    Returns:
        同步统计 {"total": N, "created": N, "updated": N, "skipped": N}
    """
    stats = {
        "total": 0,
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "errors": 0,
    }
    
    groups = fetch_groups_from_api()
    if not groups:
        logger.warning("[群名同步] 未获取到群组数据")
        return stats
    
    stats["total"] = len(groups)
    
    for group in groups:
        room_id = group.get("roomid")
        display_name = group.get("display_name")
        
        if not room_id:
            stats["skipped"] += 1
            continue
        
        if not display_name:
            stats["skipped"] += 1
            continue
        
        try:
            # 查询是否已存在
            existing = db.query(RoomInfo).filter(RoomInfo.room_id == room_id).first()
            
            if existing:
                # 如果群名有变化，更新
                if existing.room_name != display_name:
                    existing.room_name = display_name
                    existing.updated_at = datetime.now()
                    stats["updated"] += 1
                    logger.debug(f"[群名同步] 更新: {room_id} -> {display_name}")
                else:
                    stats["skipped"] += 1
            else:
                # 新建记录
                new_room = RoomInfo(
                    room_id=room_id,
                    room_name=display_name,
                )
                db.add(new_room)
                stats["created"] += 1
                logger.debug(f"[群名同步] 新增: {room_id} -> {display_name}")
                
        except Exception as e:
            stats["errors"] += 1
            logger.error(f"[群名同步] 处理 {room_id} 失败: {e}")
    
    try:
        db.commit()
        logger.info(
            f"[群名同步] 完成: 总计={stats['total']}, "
            f"新增={stats['created']}, 更新={stats['updated']}, "
            f"跳过={stats['skipped']}, 错误={stats['errors']}"
        )
    except Exception as e:
        db.rollback()
        logger.error(f"[群名同步] 提交失败: {e}")
        stats["errors"] += 1
    
    return stats


def get_room_info_stats(db: Session) -> dict[str, Any]:
    """
    获取 room_info 表的统计信息
    
    Returns:
        统计信息 {"total_count": N, "sample": [...]}
    """
    total_count = db.query(RoomInfo).count()
    
    # 获取最近更新的 10 条记录作为样例
    recent = (
        db.query(RoomInfo)
        .order_by(RoomInfo.updated_at.desc())
        .limit(10)
        .all()
    )
    
    sample = [
        {
            "room_id": r.room_id,
            "room_name": r.room_name,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in recent
    ]
    
    return {
        "total_count": total_count,
        "api_base_url": settings.CHAT_API_BASE_URL,
        "sample": sample,
    }
