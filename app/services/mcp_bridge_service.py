import requests
from loguru import logger
from app.core.config import settings


def submit_mcp_task(payload: dict) -> dict | None:
    if not settings.MCP_BRIDGE_URL:
        return None
    try:
        resp = requests.post(
            settings.MCP_BRIDGE_URL,
            json=payload,
            timeout=settings.MCP_BRIDGE_TIMEOUT,
        )
        if resp.status_code >= 300:
            logger.error(f"MCP Bridge 失败: {resp.status_code} {resp.text}")
            return None
        return resp.json()
    except Exception as exc:
        logger.error(f"MCP Bridge 请求异常: {exc}")
        return None
