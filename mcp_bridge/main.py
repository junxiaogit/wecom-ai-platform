import os
import json
import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv


load_dotenv()


class CreateTaskPayload(BaseModel):
    content: str
    projectId: str
    note: str | None = None
    dueDate: str | None = None
    startDate: str | None = None
    executorId: str | None = None
    priorityName: str | None = None
    scenariofieldconfigId: str | None = None


MCP_GATEWAY_URL = os.getenv("MCP_GATEWAY_URL", "").strip()
MCP_TOOL_NAME = os.getenv("MCP_TOOL_NAME", "create_task")
MCP_TIMEOUT = int(os.getenv("MCP_TIMEOUT", "20"))

app = FastAPI(title="MCP Bridge")


def _extract_ticket_id(obj) -> str | None:
    if isinstance(obj, dict):
        for key in ("taskId", "_id", "id", "task_id"):
            val = obj.get(key)
            if isinstance(val, str) and val.strip():
                return val
        for val in obj.values():
            found = _extract_ticket_id(val)
            if found:
                return found
    if isinstance(obj, list):
        for item in obj:
            found = _extract_ticket_id(item)
            if found:
                return found
    return None


def _call_mcp_tool(tool_name: str, arguments: dict) -> dict:
    if not MCP_GATEWAY_URL:
        raise RuntimeError("MCP_GATEWAY_URL 未配置")
    safe_args = dict(arguments)
    if "input" not in safe_args:
        safe_args["input"] = json.dumps(arguments, ensure_ascii=False)

    payload = {"toolName": tool_name, "name": tool_name, "arguments": safe_args}
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    resp = requests.post(
        MCP_GATEWAY_URL, json=payload, headers=headers, timeout=MCP_TIMEOUT, stream=True
    )
    if resp.status_code == 406:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        resp = requests.post(
            MCP_GATEWAY_URL, json=payload, headers=headers, timeout=MCP_TIMEOUT
        )
    if resp.status_code == 400:
        rpc_payload = {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": safe_args},
        }
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        resp = requests.post(
            MCP_GATEWAY_URL, json=rpc_payload, headers=headers, timeout=MCP_TIMEOUT
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"MCP 网关错误: {resp.status_code} {resp.text}")
    content_type = resp.headers.get("Content-Type", "")
    if "text/event-stream" in content_type:
        data_items = []
        for raw in resp.iter_lines():
            if not raw:
                continue
            line = raw.decode("utf-8", errors="ignore").strip()
            if line.startswith("data:"):
                data = line[5:].strip()
                if data and data != "[DONE]":
                    try:
                        data_items.append(json.loads(data))
                    except json.JSONDecodeError:
                        continue
        if data_items:
            return data_items[-1]
        return {}
    return resp.json()


@app.post("/mcp/teambition/create")
def create_teambition_task(payload: CreateTaskPayload):
    try:
        result = _call_mcp_tool(MCP_TOOL_NAME, payload.model_dump(exclude_none=True))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    ticket_id = _extract_ticket_id(result)

    return {"ticket_id": ticket_id, "raw": result}


@app.get("/health")
def health():
    return {"status": "ok"}
