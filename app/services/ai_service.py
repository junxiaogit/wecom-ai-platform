# app/services/ai_service.py
import requests
import json
from app.core.config import settings

def summarize_chat(chat_lines: list[str]) -> str:
    """
    将聊天记录发送给大模型 (阿里云 DashScope) 进行总结
    """
    if not chat_lines:
        return "没有找到可供分析的聊天记录。"

    # 1. 拼接聊天记录
    full_text = "\n".join(chat_lines)
    
    # 2. 定义 Prompt (提示词) - 这是 AI 的指令
    system_prompt = """你是一个专业的企业会话分析助手。请阅读提供的群聊记录，输出一份结构化的分析报告。

要求输出格式如下（不要包含Markdown代码块符号）：
【核心议题】：(用一句话概括讨论的主题)
【故障/风险】：(如果有报错、异常或客户抱怨，请详细列出；如果没有，请写"无")
【关键结论】：(讨论的最终结果或下一步行动计划)
    """

    # 3. 构造请求 Payload (OpenAI 兼容格式)
    payload = {
        "model": settings.AI_MODEL_NAME,  # 读取 .env 里的 qwen-plus
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": full_text}
        ],
        "temperature": 0.3, # 温度越低，回答越严谨
        "stream": False
    }

    # 4. 构造 Header
    headers = {
        "Authorization": f"Bearer {settings.AI_API_KEY}",
        "Content-Type": "application/json"
    }

    # 5. 发起真实请求
    try:
        # 注意：DashScope 兼容模式的完整 URL 需要拼接 /chat/completions
        # 如果 .env 里填的是 .../v1，这里就要加 /chat/completions
        api_endpoint = f"{settings.AI_API_URL.rstrip('/')}/chat/completions"
        
        print(f"🤖 正在调用 AI 模型: {settings.AI_MODEL_NAME} ...")
        
        response = requests.post(
            api_endpoint, 
            json=payload, 
            headers=headers,
            timeout=60 # 设置超时时间，防止 AI 思考太久卡住
        )
        
        # 检查 HTTP 状态码
        if response.status_code != 200:
            return f"AI 接口报错 (Code {response.status_code}): {response.text}"
            
        # 解析返回结果
        result_json = response.json()
        
        # 提取 AI 回复的内容
        ai_content = result_json['choices'][0]['message']['content']
        return ai_content
        
    except requests.exceptions.Timeout:
        return "AI 分析超时，请稍后重试。"
    except Exception as e:
        print(f"调用异常详情: {str(e)}")
        return f"AI 分析服务发生内部错误: {str(e)}"