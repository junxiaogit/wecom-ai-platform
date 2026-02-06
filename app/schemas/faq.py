from pydantic import BaseModel
from typing import List


class FaqDraft(BaseModel):
    question: str
    answer: str


class FaqItemResponse(BaseModel):
    faq_id: int
    category_l1: str
    category_l2: str
    question: str
    answer: str
    source_issue_ids: List[int]
