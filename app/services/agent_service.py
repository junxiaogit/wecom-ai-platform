# app/services/agent_service.py
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field
from langchain_core.output_parsers import JsonOutputParser
from app.core.llm import get_llm
from app.services.vector_service import vector_kb
from typing import List

# --- 1. 定义数据结构 (Pydantic) ---
# 这就是我们要求 AI 填写的“表格”
class TicketInfo(BaseModel):
    issue_summary: str = Field(description="一句话总结问题现象，用于工单标题")
    category: str = Field(description="问题分类，例如：推流失败/截图失败/卡顿/网络/其他")
    risk_score: int = Field(description="客户情绪风险得分 0-100，分数越高越愤怒")
    is_bug: bool = Field(description="是否疑似系统故障")
    suggested_reply: str = Field(description="建议客服回复的话术")

class AnalysisResult(BaseModel):
    # 允许一次提取多个问题
    tickets: List[TicketInfo] = Field(description="分析出的工单列表")
    qa_comment: str = Field(description="对客服服务的简短质检点评")

# --- 2. Agent 逻辑 ---
class ChatAnalysisAgent:
    def __init__(self):
        self.llm = get_llm()
        self.parser = JsonOutputParser(pydantic_object=AnalysisResult)

# app/services/agent_service.py (只修改 analyze 方法)

    def analyze(self, chat_context: str, current_query: str = ""):
        # ... (前面的 RAG 逻辑保持不变) ...
        # Step 1: RAG 检索 (如果有明确查询意图)
        history_context = "无历史相似记录"
        if current_query and len(current_query) > 2:
            docs = vector_kb.search_similar_issues(current_query, k=3)
            if docs:
                history_context = "\n".join([f"- {d.page_content}" for d in docs])

        # Step 2: 编写超级 Prompt (保持不变)
        system_prompt = """你是一个企业级会话分析 AI。请分析以下客服聊天记录。

【历史相似案例参考 (RAG)】:
{history_context}

【当前待分析对话】:
{chat_context}

【任务要求】:
1. 识别客户是否遇到了技术故障，如果是，提取为工单。
2. 评估客户情绪风险 (0-100)。
3. 如果历史案例中有类似问题，请参考其解决方案生成建议回复。
4. 严格只输出 JSON 格式。

{format_instructions}
"""
        prompt = ChatPromptTemplate.from_template(system_prompt)

        # Step 3: 组装链 (Chain)
        chain = prompt | self.llm | self.parser

        print(f"🤖 Agent 正在思考中... (Context长度: {len(chat_context)})")
        try:
            # --- 🔴 修改点：增加详细的错误捕获 ---
            import traceback # 引入这个库
            
            result = chain.invoke({
                "history_context": history_context,
                "chat_context": chat_context,
                "format_instructions": self.parser.get_format_instructions()
            })
            return result
            
        except Exception as e:
            print("\n❌❌❌ 严重错误发生 ❌❌❌")
            print(f"错误类型: {type(e).__name__}")
            print(f"错误详情: {str(e)}")
            print("--- 完整堆栈 ---")
            traceback.print_exc() # 打印出错的哪一行代码
            print("----------------")
            
            return {"tickets": [], "qa_comment": f"分析失败: {str(e)}"}

# 初始化单例
agent = ChatAnalysisAgent()