# app/schemas/analysis.py
from pydantic import BaseModel
from typing import List, Optional

# 请求参数：前端传过来的
class SummaryRequest(BaseModel):
    room_id: Optional[str] = None # 可选，指定群ID
    limit: int = 50               # 默认分析最近 50 条

# 响应结构：我们返回给前端的
class SummaryResponse(BaseModel):
    total_messages: int
    summary_text: str             # AI 总结的内容
    preview_context: List[str]    # 给用户看一眼原文（前几句）