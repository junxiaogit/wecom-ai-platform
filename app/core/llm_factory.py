from langchain_openai import ChatOpenAI
from app.core.config import settings


def get_fast_llm() -> ChatOpenAI:
    return ChatOpenAI(
        base_url=settings.AI_API_URL,
        api_key=settings.AI_API_KEY,
        model=settings.AI_MODEL_FAST,
        temperature=0.1,
    )


def get_smart_llm() -> ChatOpenAI:
    return ChatOpenAI(
        base_url=settings.AI_API_URL,
        api_key=settings.AI_API_KEY,
        model=settings.AI_MODEL_SMART,
        temperature=0.2,
    )


def get_classifier_llm() -> ChatOpenAI:
    """
    分类专用：尽量稳定输出，降低抖动。
    """
    return ChatOpenAI(
        base_url=settings.AI_API_URL,
        api_key=settings.AI_API_KEY,
        model=settings.AI_MODEL_SMART,
        temperature=0.0,
    )
