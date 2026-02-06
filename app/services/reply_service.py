from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from app.core.llm_factory import get_smart_llm
from app.services.vector_service import vector_kb


async def generate_reply(text: str) -> str:
    history_context = "无历史相似案例"
    faq_docs = vector_kb.search_similar_faq(text, k=3)
    if faq_docs:
        history_context = "\n".join([f"- {d.page_content}" for d in faq_docs])
    else:
        docs = vector_kb.search_similar_issues(text, k=3)
        if docs:
            history_context = "\n".join([f"- {d.page_content}" for d in docs])

    prompt = ChatPromptTemplate.from_template(
        """
你是企业客服助手。基于历史案例和当前问题，给出一条简洁可直接发送的回复建议。

【历史相似案例】
{history}

【当前问题】
{text}

输出一条中文建议回复，不要加引号。
"""
    )
    chain = prompt | get_smart_llm() | StrOutputParser()
    return await chain.ainvoke({"history": history_context, "text": text})
