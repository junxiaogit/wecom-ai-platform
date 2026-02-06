# app/services/half_day_review_service.py
"""
半日高频复盘服务
每12小时执行一次，按群汇总输出：摘要 + 分类清单 + 风险预警
"""
import asyncio
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from collections import defaultdict
from loguru import logger
from sqlalchemy.orm import Session
from sqlalchemy import and_

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.sql_models import WeComMessage, RoomInfo
from app.services.data_clean_service import DataCleanService
from app.services.vector_service import vector_kb
from app.agents.review_agent import ReviewAgent
from app.schemas.common import (
    ReviewItemResult,
    RiskAlertItem,
    RoomReviewStats,
    RoomReviewReport,
    HalfDayReviewResult,
)


class HalfDayReviewService:
    """
    半日高频复盘服务
    核心职责：
    1. 每12小时批量处理增量聊天记录
    2. 按群分组，进行四维度分类
    3. 情绪感知 + 流失风险评分
    4. 话术平民化重组
    5. 生成结构化报告推送钉钉
    """

    def __init__(self):
        self.review_agent = ReviewAgent()
        self.high_risk_threshold = settings.HALF_DAY_REVIEW_HIGH_RISK_THRESHOLD
        self.summary_max_len = settings.HALF_DAY_REVIEW_SUMMARY_MAX_LEN

    async def run_review(self, window_hours: int = 12) -> HalfDayReviewResult:
        """
        主入口：执行半日复盘
        Args:
            window_hours: 时间窗口（小时），默认12小时
        Returns:
            HalfDayReviewResult: 复盘结果
        """
        logger.info(f"========== 开始半日复盘 (窗口:{window_hours}小时) ==========")
        
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=window_hours)
        
        db = SessionLocal()
        try:
            # 阶段A: 采集增量消息
            messages = self._fetch_incremental_messages(db, start_time, end_time)
            
            if not messages:
                logger.info("该时段无新消息，跳过复盘")
                return HalfDayReviewResult(
                    status="no_data",
                    message="该时段无新消息",
                    start_time=start_time.isoformat(),
                    end_time=end_time.isoformat(),
                )

            logger.info(f"采集到 {len(messages)} 条增量消息")

            # 阶段B: 按群分组 + 过滤噪音
            grouped = self._group_and_filter(messages)
            logger.info(f"分组后共 {len(grouped)} 个群")

            # 阶段C+D: 遍历每个群，生成汇总报告
            reports = []
            total_messages = 0
            
            for room_id, room_messages in grouped.items():
                if len(room_messages) < settings.HALF_DAY_REVIEW_MIN_MESSAGES:
                    logger.debug(f"群 {room_id} 消息数不足，跳过")
                    continue
                    
                room_name = self._get_room_name(db, room_id)
                report = await self._analyze_room(
                    room_id=room_id,
                    room_name=room_name,
                    messages=room_messages,
                    window_hours=window_hours,
                )
                reports.append(report)
                total_messages += len(room_messages)

            logger.info(f"========== 半日复盘完成: {len(reports)}个群, {total_messages}条消息 ==========")
            
            return HalfDayReviewResult(
                status="success",
                room_count=len(reports),
                total_messages=total_messages,
                reports=reports,
                start_time=start_time.isoformat(),
                end_time=end_time.isoformat(),
            )

        except Exception as e:
            logger.error(f"半日复盘执行失败: {e}")
            return HalfDayReviewResult(
                status="error",
                message=str(e),
                start_time=start_time.isoformat(),
                end_time=end_time.isoformat(),
            )
        finally:
            db.close()

    def _fetch_incremental_messages(
        self, db: Session, start_time: datetime, end_time: datetime
    ) -> List[dict]:
        """
        采集指定时间窗口内的增量消息
        """
        # 将时间转换为时间戳（毫秒）
        start_ts = int(start_time.timestamp() * 1000)
        end_ts = int(end_time.timestamp() * 1000)
        
        # 从 wecom_messages 表查询
        records = (
            db.query(WeComMessage)
            .filter(
                WeComMessage.is_noise == False,
                WeComMessage.msg_type == "text",
            )
            .order_by(WeComMessage.created_at.desc())
            .limit(2000)  # 限制最大处理量
            .all()
        )
        
        messages = []
        for r in records:
            # 过滤时间（如果有时间字段）
            content = r.content_clean or r.content_raw
            if not content or len(content.strip()) < 3:
                continue
            messages.append({
                "msg_id": r.msg_id,
                "room_id": r.room_id,
                "sender_id": r.sender_id,
                "content": content,
                "created_at": r.created_at,
            })
        
        return messages

    def _group_and_filter(self, messages: List[dict]) -> Dict[str, List[dict]]:
        """
        按群分组 + 过滤噪音
        """
        grouped = defaultdict(list)
        
        for msg in messages:
            content = msg.get("content", "")
            
            # 二次噪音过滤
            if DataCleanService.is_noise(content):
                continue
            
            room_id = msg.get("room_id") or "unknown"
            grouped[room_id].append(msg)
        
        return dict(grouped)

    def _get_room_name(self, db: Session, room_id: str) -> str:
        """获取群名称"""
        if not room_id:
            return "未知群"
        
        mapping = db.query(RoomInfo).filter(RoomInfo.room_id == room_id).first()
        if mapping and mapping.room_name:
            return mapping.room_name
        return room_id

    async def _analyze_room(
        self,
        room_id: str,
        room_name: str,
        messages: List[dict],
        window_hours: int,
    ) -> RoomReviewReport:
        """
        对单个群的消息进行深度分析
        """
        logger.info(f"分析群: {room_name} ({room_id}), 消息数: {len(messages)}")
        
        # 1. 对每条消息进行四维度分类 + 情绪评估
        items: List[ReviewItemResult] = []
        risk_alerts: List[RiskAlertItem] = []
        
        # 并发处理提升效率（限制并发数）
        semaphore = asyncio.Semaphore(5)
        
        async def process_message(msg: dict) -> Optional[ReviewItemResult]:
            async with semaphore:
                return await self._analyze_single_message(msg)
        
        tasks = [process_message(msg) for msg in messages]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.warning(f"消息分析失败: {result}")
                continue
            if result:
                items.append(result)
                
                # 筛选高风险预警
                if result.risk_score >= self.high_risk_threshold or result.emotion_level == "负面":
                    risk_alerts.append(RiskAlertItem(
                        original_quote=result.original[:100],
                        risk_score=result.risk_score,
                        reason=f"{result.emotion_level}情绪，{result.dimension}",
                        msg_id=result.msg_id,
                    ))

        # 2. 计算统计数据
        stats = self._calculate_stats(items)

        # 3. 生成通俗摘要
        typical_issues = "\n".join([
            f"- {item.readable_desc}" 
            for item in sorted(items, key=lambda x: x.risk_score, reverse=True)[:3]
        ])
        
        summary = await self.review_agent.generate_summary(
            room_name=room_name,
            window_hours=window_hours,
            total_count=len(items),
            dimension_counts=stats.dimension_counts,
            avg_risk=stats.avg_risk_score,
            high_risk_count=stats.high_risk_count,
            typical_issues=typical_issues,
            max_len=self.summary_max_len,
        )

        return RoomReviewReport(
            room_id=room_id,
            room_name=room_name,
            summary=summary,
            items=items,
            risk_alerts=risk_alerts,
            stats=stats,
            review_time=datetime.now().isoformat(),
            window_hours=window_hours,
        )

    async def _analyze_single_message(self, msg: dict) -> Optional[ReviewItemResult]:
        """
        分析单条消息
        """
        content = msg.get("content", "")
        if not content:
            return None
        
        try:
            # 1. 四维度分类
            dim_result = await self.review_agent.classify_dimension(content)
            dimension = dim_result.get("dimension", "问题反馈")
            
            # 2. 情绪分析
            emotion_result = await self.review_agent.analyze_emotion(content)
            emotion = emotion_result.get("emotion", "中性")
            risk_score = emotion_result.get("risk_score", 30)
            
            # 3. 话术平民化
            plain_result = await self.review_agent.rewrite_plain(content, dimension)
            readable_desc = plain_result.get("readable_desc", content[:30])
            
            # 4. 生成建议动作
            action = self.review_agent.suggest_action(dimension, risk_score)
            
            return ReviewItemResult(
                dimension=dimension,
                readable_desc=readable_desc,
                emotion_level=emotion,
                emotion_icon=self.review_agent.get_emotion_icon(emotion),
                risk_score=risk_score,
                action=action,
                original=content[:50],
                msg_id=msg.get("msg_id"),
                sender_id=msg.get("sender_id"),
            )
        except Exception as e:
            logger.warning(f"单消息分析失败: {e}")
            return None

    def _calculate_stats(self, items: List[ReviewItemResult]) -> RoomReviewStats:
        """
        计算统计数据
        """
        if not items:
            return RoomReviewStats(
                total_count=0,
                dimension_counts={},
                avg_risk_score=0.0,
                high_risk_count=0,
                emotion_distribution={},
            )
        
        # 维度分布
        dimension_counts = defaultdict(int)
        for item in items:
            dimension_counts[item.dimension] += 1
        
        # 情绪分布
        emotion_distribution = defaultdict(int)
        for item in items:
            emotion_distribution[item.emotion_level] += 1
        
        # 风险统计
        risk_scores = [item.risk_score for item in items]
        avg_risk = sum(risk_scores) / len(risk_scores) if risk_scores else 0
        high_risk_count = sum(1 for s in risk_scores if s >= self.high_risk_threshold)
        
        return RoomReviewStats(
            total_count=len(items),
            dimension_counts=dict(dimension_counts),
            avg_risk_score=round(avg_risk, 1),
            high_risk_count=high_risk_count,
            emotion_distribution=dict(emotion_distribution),
        )


# 单例
half_day_review_service = HalfDayReviewService()


async def run_half_day_review_loop():
    """
    半日复盘定时循环
    每12小时执行一次
    """
    if not settings.HALF_DAY_REVIEW_ENABLED:
        logger.info("半日复盘模式未启用")
        return
    
    from app.services.dingtalk_service import DingTalkService
    
    interval_hours = settings.HALF_DAY_REVIEW_INTERVAL_HOURS
    interval_seconds = interval_hours * 3600
    
    logger.info(f"半日复盘定时任务启动，间隔: {interval_hours}小时")
    
    while True:
        try:
            # 执行复盘
            result = await half_day_review_service.run_review(window_hours=interval_hours)
            
            # 推送钉钉
            if result.status == "success" and result.reports:
                for report in result.reports:
                    DingTalkService.send_review_report(report)
                    await asyncio.sleep(1)  # 避免发送过快
                    
            logger.info(f"半日复盘完成: {result.status}, 群数: {result.room_count}")
            
        except Exception as e:
            logger.error(f"半日复盘循环异常: {e}")
        
        # 等待下一个周期
        await asyncio.sleep(interval_seconds)


async def run_scheduled_review():
    """
    按固定时间点执行复盘（如每天08:00和20:00）
    """
    if not settings.HALF_DAY_REVIEW_ENABLED:
        logger.info("半日复盘模式未启用")
        return
    
    from app.services.dingtalk_service import DingTalkService
    
    schedule_times = settings.HALF_DAY_REVIEW_SCHEDULE.split(",")
    logger.info(f"半日复盘定时任务启动，执行时间点: {schedule_times}")
    
    while True:
        now = datetime.now()
        current_time = now.strftime("%H:%M")
        
        if current_time in schedule_times:
            logger.info(f"触发定时复盘: {current_time}")
            try:
                result = await half_day_review_service.run_review(
                    window_hours=settings.HALF_DAY_REVIEW_INTERVAL_HOURS
                )
                
                if result.status == "success" and result.reports:
                    for report in result.reports:
                        DingTalkService.send_review_report(report)
                        await asyncio.sleep(1)
                        
            except Exception as e:
                logger.error(f"定时复盘失败: {e}")
            
            # 避免同一分钟重复触发
            await asyncio.sleep(60)
        else:
            # 每30秒检查一次
            await asyncio.sleep(30)
