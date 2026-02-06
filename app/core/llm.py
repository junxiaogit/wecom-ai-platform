# app/core/llm.py
import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
# 注意：使用 community 版本的 embeddings，兼容性最好
from langchain_community.embeddings import HuggingFaceEmbeddings

load_dotenv()

def get_llm():
    """
    配置 SiliconFlow (通义千问) 作为推理大脑
    """
    # 获取环境变量
    api_key = os.getenv("AI_API_KEY")
    base_url = os.getenv("AI_API_URL")
    model_name = os.getenv("AI_MODEL_NAME")

    if not api_key:
        raise ValueError("未找到 AI_API_KEY，请检查 .env 文件")

    print(f"初始化 LLM: {model_name}...")
    
    return ChatOpenAI(
        model=model_name,
        openai_api_base=base_url, 
        openai_api_key=api_key,
        temperature=0.1, # 0.1 意味着非常严谨，适合做工单分析
        max_tokens=2048
    )

def get_embeddings():
    """
    配置本地 Embedding 模型 (用于将中文转为向量)
    第一次运行会自动下载 shibing624/text2vec-base-chinese (约 400MB)
    """
    model_name = "shibing624/text2vec-base-chinese"
    print(f"加载 Embedding 模型: {model_name} (首次运行需下载)...")
    
    # 使用 CPU 运行即可，速度很快
    return HuggingFaceEmbeddings(
        model_name=model_name,
        model_kwargs={'device': 'cpu'},
        encode_kwargs={'normalize_embeddings': True}
    )