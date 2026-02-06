from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from loguru import logger
from app.core.llm_factory import get_smart_llm
from app.schemas.common import AssistantInsight, CustomFieldInsight, TbFieldsInsight
from app.prompts import (
    ISSUE_ANALYSIS_PROMPT,
    TB_FIELDS_GENERATION_PROMPT,
    TB_CUSTOM_FIELD_PROMPT,
)


class AssistantAgent:
    def __init__(self):
        self.llm = get_smart_llm()
        self.parser = JsonOutputParser(pydantic_object=AssistantInsight)
        # 从 prompts 模块导入问题分析提示词
        self.prompt = ChatPromptTemplate.from_template(ISSUE_ANALYSIS_PROMPT)
        self.chain = self.prompt | self.llm | self.parser

        # 自定义字段生成
        self.custom_field_parser = JsonOutputParser(pydantic_object=CustomFieldInsight)
        self.custom_field_prompt = ChatPromptTemplate.from_template(TB_CUSTOM_FIELD_PROMPT)
        self.custom_field_chain = self.custom_field_prompt | self.llm | self.custom_field_parser

        # TB 全字段生成
        self.tb_fields_parser = JsonOutputParser(pydantic_object=TbFieldsInsight)
        self.tb_fields_prompt = ChatPromptTemplate.from_template(TB_FIELDS_GENERATION_PROMPT)
        self.tb_fields_chain = self.tb_fields_prompt | self.llm | self.tb_fields_parser

    async def analyze(self, chat_context: str, similar_context: str) -> dict:
        logger.debug(f"LLM analyze input: chat_len={len(chat_context)}, similar_len={len(similar_context)}")
        result = await self.chain.ainvoke(
            {
                "chat_context": chat_context,
                "similar_context": similar_context,
                "format_instructions": self.parser.get_format_instructions(),
            }
        )
        # 检查关键字段是否为空值
        ai_sol = result.get("ai_solution", "")
        soothing = result.get("soothing_reply", "")
        logger.info(f"LLM analyze result: ai_solution='{ai_sol[:50] if ai_sol else '-'}', soothing='{soothing[:30] if soothing else '-'}'")
        return result

    async def analyze_custom_fields(
        self,
        chat_context: str,
        similar_context: str,
        ai_insight: dict,
        dingtalk_markdown: str,
    ) -> dict:
        result = await self.custom_field_chain.ainvoke(
            {
                "chat_context": chat_context,
                "similar_context": similar_context,
                "ai_insight": ai_insight or {},
                "dingtalk_markdown": dingtalk_markdown or "",
                "format_instructions": self.custom_field_parser.get_format_instructions(),
            }
        )
        return result

    async def analyze_tb_fields(
        self,
        *,
        chat_context: str,
        similar_context: str,
        tag_text: str,
        assignee: str,
        detail_url: str,
        hit_count: int = 0,
    ) -> dict:
        logger.debug(f"LLM tb_fields input: chat_len={len(chat_context)}, similar_len={len(similar_context)}")
        result = await self.tb_fields_chain.ainvoke(
            {
                "chat_context": chat_context,
                "similar_context": similar_context,
                "tag_text": tag_text or "-",
                "assignee": assignee or "-",
                "detail_url": detail_url or "-",
                "hit_count": int(hit_count or 0),
                "format_instructions": self.tb_fields_parser.get_format_instructions(),
            }
        )
        # 检查关键字段是否为空值
        ai_sol = result.get("ai_solution", "")
        soothing = result.get("soothing_reply", "")
        logger.info(f"LLM tb_fields result: ai_solution='{ai_sol[:50] if ai_sol else '-'}', soothing='{soothing[:30] if soothing else '-'}'")
        return result
