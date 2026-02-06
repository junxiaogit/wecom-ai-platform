import os
import json
import requests
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from dotenv import load_dotenv


load_dotenv()

CUSTOM_FIELDS_URL_TEMPLATE = os.getenv(
    "CUSTOM_FIELDS_URL_TEMPLATE",
    "https://www.teambition.com/api/projects/{project_id}/customfields",
)
CUSTOM_FIELDS_QUERY = os.getenv("CUSTOM_FIELDS_QUERY", "")
TB_ACCESS_TOKEN = os.getenv("TB_ACCESS_TOKEN", "").strip()
TB_COOKIE = os.getenv("TB_COOKIE", "").strip()
TB_COOKIE_AUTO = os.getenv("TB_COOKIE_AUTO", "true").lower() == "true"
BROWSER_COOKIE_SOURCE = os.getenv("BROWSER_COOKIE_SOURCE", "edge").strip().lower()
BROWSER_PROFILE_PATH = os.getenv("BROWSER_PROFILE_PATH", "").strip()

app = FastAPI(title="Teambition Custom Fields MCP")


class ToolCallRequest(BaseModel):
    toolName: str | None = None
    name: str | None = None
    arguments: dict | None = None
    method: str | None = None
    params: dict | None = None
    id: str | int | None = None


def _build_headers() -> dict:
    headers = {"Content-Type": "application/json"}
    # Re-read env each request (so restart not strictly required)
    token = (os.getenv("TB_ACCESS_TOKEN") or "").strip()
    cookie = (os.getenv("TB_COOKIE") or "").strip()
    if token:
        # Some Teambition endpoints require cookie token rather than bearer.
        headers["Authorization"] = f"Bearer {token}"
        headers["Cookie"] = f"TB_ACCESS_TOKEN={token}"
    if cookie:
        # Allow users to paste full Cookie header if needed.
        headers["Cookie"] = cookie
    if "Cookie" not in headers and TB_COOKIE_AUTO:
        cookie_auto = _load_browser_cookie()
        if cookie_auto:
            headers["Cookie"] = cookie_auto
    return headers


def _load_browser_cookie() -> str | None:
    try:
        import browser_cookie3  # type: ignore
    except Exception:
        return None

    loaders = {
        "edge": getattr(browser_cookie3, "edge", None),
        "chrome": getattr(browser_cookie3, "chrome", None),
    }
    loader = loaders.get(BROWSER_COOKIE_SOURCE)
    if not loader:
        loader = loaders.get("edge") or loaders.get("chrome")
    if not loader:
        return None

    try:
        domains = ["teambition.com", "account.teambition.com"]
        kwargs = {"domain_name": domains[0]}
        if BROWSER_PROFILE_PATH:
            kwargs["cookie_file"] = BROWSER_PROFILE_PATH
        pairs = []
        for domain in domains:
            kwargs["domain_name"] = domain
            jar = loader(**kwargs)
            for c in jar:
                if c.domain and ("teambition.com" not in c.domain):
                    continue
                pairs.append(f"{c.name}={c.value}")
        return "; ".join(pairs) if pairs else None
    except Exception:
        return None


def _fetch_custom_fields(project_id: str) -> dict:
    url = CUSTOM_FIELDS_URL_TEMPLATE.format(project_id=project_id)
    if CUSTOM_FIELDS_QUERY:
        joiner = "&" if "?" in url else "?"
        url = f"{url}{joiner}{CUSTOM_FIELDS_QUERY}"
    resp = requests.get(url, headers=_build_headers(), timeout=20)
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return resp.json()


def _tool_spec() -> dict:
    return {
        "tools": [
            {
                "name": "get_project_custom_fields",
                "description": "获取项目自定义字段列表",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "projectId": {
                            "type": "string",
                            "title": "projectId",
                            "description": "项目ID",
                        }
                    },
                    "required": ["projectId"],
                },
            }
        ]
    }


def _handle_call(tool: str | None, args: dict | None) -> dict:
    if tool == "get_project_custom_fields":
        project_id = str((args or {}).get("projectId") or "").strip()
        if not project_id:
            raise HTTPException(status_code=400, detail="projectId required")
        result = _fetch_custom_fields(project_id)
        return {"result": result}
    raise HTTPException(status_code=404, detail=f"Unknown tool: {tool}")


@app.post("/mcp/tools/call")
def call_tool(payload: ToolCallRequest):
    tool = payload.toolName or payload.name
    args = payload.arguments or {}
    return _handle_call(tool, args)


@app.post("/mcp")
async def call_tool_alias(request: Request):
    payload = await request.json()
    if isinstance(payload, dict):
        method = payload.get("method")
        req_id = payload.get("id")
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": req_id, "result": _tool_spec()}
        if method == "tools/call":
            params = payload.get("params") or {}
            tool = params.get("name") or params.get("toolName")
            args = params.get("arguments") or {}
            result = _handle_call(tool, args)
            return {"jsonrpc": "2.0", "id": req_id, "result": result}
        tool = payload.get("toolName") or payload.get("name")
        args = payload.get("arguments") or {}
        return _handle_call(tool, args)
    raise HTTPException(status_code=400, detail="Invalid payload")


@app.get("/health")
def health():
    return {"status": "ok"}

