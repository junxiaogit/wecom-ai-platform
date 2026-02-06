from collections import defaultdict
from typing import List, Dict
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from app.core.llm_factory import get_smart_llm
from app.models.sql_models import Issue, FaqItem
from app.schemas.faq import FaqDraft
from app.services.vector_service import vector_kb


class FaqService:
    def __init__(self):
        self.parser = JsonOutputParser(pydantic_object=FaqDraft)
        self.prompt = ChatPromptTemplate.from_template(
            """
你是企业客服知识库助手。基于问题摘要生成一条 FAQ（问答）。

分类：{category}
问题摘要：
{summaries}

输出 JSON：
{format_instructions}
"""
        )
        self.chain = self.prompt | get_smart_llm() | self.parser

    async def generate_from_issues(
        self,
        issues: List[Issue],
        min_group: int,
        max_groups: int,
    ) -> List[FaqItem]:
        grouped: Dict[str, List[Issue]] = defaultdict(list)
        for issue in issues:
            key = f"{issue.category_l1}/{issue.category_l2}"
            grouped[key].append(issue)

        created_items: List[FaqItem] = []
        groups = [g for g in grouped.items() if len(g[1]) >= min_group]
        groups = sorted(groups, key=lambda x: len(x[1]), reverse=True)[:max_groups]

        for key, group in groups:
            summaries = "\n".join([f"- {i.summary}" for i in group[:10]])
            result = await self.chain.ainvoke(
                {
                    "category": key,
                    "summaries": summaries,
                    "format_instructions": self.parser.get_format_instructions(),
                }
            )
            item = FaqItem(
                category_l1=group[0].category_l1,
                category_l2=group[0].category_l2,
                question=result.get("question"),
                answer=result.get("answer"),
                source_issue_ids=[i.issue_id for i in group],
            )
            created_items.append(item)

        if created_items:
            vector_kb.add_faq_items(
                [
                    {
                        "question": i.question,
                        "answer": i.answer,
                        "category_l1": i.category_l1,
                        "category_l2": i.category_l2,
                    }
                    for i in created_items
                ]
            )
        return created_items
