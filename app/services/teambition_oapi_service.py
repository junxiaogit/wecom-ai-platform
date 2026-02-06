import time
import json
from pathlib import Path
import requests
from loguru import logger
import jwt

from app.core.config import settings


_APP_TOKEN_CACHE: dict = {"token": None, "expire_at": 0}
_CUSTOMFIELD_DICT_CACHE: dict | None = None
_CUSTOMFIELD_CHOICES_CACHE: dict[str, dict[str, str]] = {}
_CUSTOMFIELD_CHOICES_FILE_CACHE: dict[str, dict[str, str]] | None = None


def _build_jwt_app_token(app_id: str, app_secret: str, expires_in_seconds: int = 3600) -> str | None:
    try:
        now = int(time.time())
        token = jwt.encode(
            {"_appId": app_id, "iat": now, "exp": now + int(expires_in_seconds)},
            app_secret,
            algorithm="HS256",
        )
        if isinstance(token, bytes):
            token = token.decode("utf-8")
        return token
    except Exception as exc:
        logger.error(f"本地生成 appToken 失败: {exc}")
        return None


def _persist_app_token(token: str) -> None:
    """Persist TB_APP_TOKEN into .env for visibility/debugging."""
    try:
        env_path = Path(".env")
        if not env_path.exists():
            env_path.write_text(f"TB_APP_TOKEN={token}\n", encoding="utf-8")
            return
        content = env_path.read_text(encoding="utf-8")
        if "TB_APP_TOKEN=" in content:
            content = __replace_env_line(content, "TB_APP_TOKEN", token)
        else:
            if not content.endswith("\n"):
                content += "\n"
            content += f"TB_APP_TOKEN={token}\n"
        env_path.write_text(content, encoding="utf-8")
    except Exception as exc:
        logger.warning(f"写入 TB_APP_TOKEN 失败: {exc}")


def __replace_env_line(content: str, key: str, value: str) -> str:
    lines = content.splitlines()
    replaced = False
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key}={value}")
    return "\n".join(lines) + ("\n" if content.endswith("\n") else "")


def _get_app_token() -> str | None:
    now = int(time.time())
    cached = _APP_TOKEN_CACHE.get("token")
    expire_at = int(_APP_TOKEN_CACHE.get("expire_at") or 0)
    if cached and now < expire_at - 30:
        return cached

    # Prefer user-provided token if still valid
    if settings.TB_APP_TOKEN:
        try:
            payload = jwt.decode(settings.TB_APP_TOKEN, options={"verify_signature": False})
            exp = int(payload.get("exp") or 0)
            if exp and now < exp - 30:
                _APP_TOKEN_CACHE["token"] = settings.TB_APP_TOKEN
                _APP_TOKEN_CACHE["expire_at"] = exp
                return settings.TB_APP_TOKEN
            logger.warning("TB_APP_TOKEN 已过期，尝试自动刷新")
        except Exception:
            logger.warning("TB_APP_TOKEN 解析失败，尝试自动刷新")

    if not settings.TB_APP_ID or not settings.TB_APP_SECRET:
        logger.warning("TB_APP_ID / TB_APP_SECRET 未配置")
        return None

    url = settings.TB_APP_TOKEN_URL or "https://open.teambition.com/api/appToken"
    try:
        resp = requests.post(
            url,
            json={"appId": settings.TB_APP_ID, "appSecret": settings.TB_APP_SECRET},
            timeout=20,
        )
        if resp.status_code < 400:
            data = resp.json()
            token = data.get("appToken") or (data.get("result") or {}).get("appToken")
            expire = int(data.get("expire") or (data.get("result") or {}).get("expire") or 3600)
            if token:
                _APP_TOKEN_CACHE["token"] = token
                _APP_TOKEN_CACHE["expire_at"] = now + expire
                return token
            logger.error(f"appToken 响应未包含 token: {data}")
        else:
            logger.warning(f"appToken 远程获取失败({resp.status_code})，尝试本地生成: {resp.text}")
    except Exception as exc:
        logger.warning(f"appToken 请求异常，尝试本地生成: {exc}")

    # Fallback: generate JWT token locally (works when app is installed/authorized)
    token = _build_jwt_app_token(settings.TB_APP_ID, settings.TB_APP_SECRET, 3600)
    if token:
        _APP_TOKEN_CACHE["token"] = token
        _APP_TOKEN_CACHE["expire_at"] = now + 3600
        _persist_app_token(token)
        return token
    return None


def _headers(app_token: str) -> dict:
    return {
        "Authorization": f"Bearer {app_token}",
        "X-Tenant-Id": settings.TB_TENANT_ID or "",
        "X-Tenant-Type": "organization",
        "x-operator-id": settings.TB_OPERATOR_ID or "",
        "Content-Type": "application/json",
    }


def _normalize_oapi_v3_base(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/api/v3"):
        return base
    if base.endswith("/api"):
        return f"{base}/v3"
    return f"{base}/api/v3"


def _load_customfield_dict() -> dict[str, dict]:
    global _CUSTOMFIELD_DICT_CACHE
    if _CUSTOMFIELD_DICT_CACHE is not None:
        return _CUSTOMFIELD_DICT_CACHE
    path = settings.CUSTOM_FIELDS_DICT_PATH or "customfield_dict.json"
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            _CUSTOMFIELD_DICT_CACHE = {
                str(item.get("customfieldId")): item for item in data if item.get("customfieldId")
            }
            return _CUSTOMFIELD_DICT_CACHE
    except Exception:
        pass
    _CUSTOMFIELD_DICT_CACHE = {}
    return _CUSTOMFIELD_DICT_CACHE


def _get_customfield_type(cf_id: str) -> str | None:
    info = _load_customfield_dict().get(cf_id)
    if not info:
        return None
    return info.get("type") or info.get("fieldType")


def _get_customfield_choices(app_token: str, cf_id: str) -> dict[str, str]:
    file_choices = _load_choice_map_from_file()
    if cf_id in file_choices:
        return file_choices[cf_id]
    if cf_id in _CUSTOMFIELD_CHOICES_CACHE:
        return _CUSTOMFIELD_CHOICES_CACHE[cf_id]
    base = _normalize_oapi_v3_base(settings.TB_OAPI_BASE_URL or "https://open.teambition.com/api")
    project_id = settings.TB_PROJECT_ID or settings.TEAMBITION_PROJECT_ID
    urls = []
    if project_id:
        urls.append(f"{base}/project/{project_id}/customfield/{cf_id}/choice/search")
    urls.append(f"{base}/customfield/{cf_id}/choice/search")
    for url in urls:
        try:
            resp = requests.get(url, headers=_headers(app_token), timeout=20)
            if resp.status_code >= 400:
                logger.warning(f"获取选项失败: {resp.status_code} {resp.text}")
                continue
            data = resp.json()
            if data.get("code") not in (None, 200):
                logger.warning(f"获取选项返回异常: {data}")
                continue
            items = data.get("result") or []
            mapping: dict[str, str] = {}
            for item in items:
                cid = item.get("id") or item.get("_id") or item.get("value")
                title = item.get("title") or item.get("name") or item.get("label")
                if cid and title:
                    mapping[str(title)] = str(cid)
            if mapping:
                _CUSTOMFIELD_CHOICES_CACHE[cf_id] = mapping
                return mapping
        except Exception as exc:
            logger.warning(f"获取选项异常: {exc}")
            continue
    return {}


def _load_choice_map_from_file() -> dict[str, dict[str, str]]:
    global _CUSTOMFIELD_CHOICES_FILE_CACHE
    if _CUSTOMFIELD_CHOICES_FILE_CACHE is not None:
        return _CUSTOMFIELD_CHOICES_FILE_CACHE
    path = settings.CUSTOM_FIELDS_CHOICE_MAP_PATH or "customfield_choice_map.json"
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            normalized: dict[str, dict[str, str]] = {}
            for cf_id, mapping in data.items():
                if isinstance(mapping, dict):
                    # Skip error placeholders so we can fallback to API.
                    if "__error__" in mapping:
                        continue
                    normalized[str(cf_id)] = {str(k): str(v) for k, v in mapping.items()}
            _CUSTOMFIELD_CHOICES_FILE_CACHE = normalized
            return normalized
    except Exception:
        pass
    _CUSTOMFIELD_CHOICES_FILE_CACHE = {}
    return {}


def _map_choice_value(app_token: str, cf_id: str, value) -> list[dict] | None:
    if value is None:
        return None
    choices = _get_customfield_choices(app_token, cf_id)
    if not choices:
        return None
    text = str(value).strip()
    # Map severity S1/S2/S3 to 高/中/低 if applicable
    if text.upper() in ("S1", "S2", "S3"):
        severity_map = {"S1": "高", "S2": "中", "S3": "低"}
        target = severity_map.get(text.upper())
        if target in choices:
            return [{"id": choices[target], "title": target}]
        # New project may use 致命/严重/一般/轻微
        legacy_to_new = {"S1": "致命", "S2": "严重", "S3": "一般"}
        target2 = legacy_to_new.get(text.upper())
        if target2 in choices:
            return [{"id": choices[target2], "title": target2}]
        # Fallback mapping if only 部分选项存在
        if text.upper() == "S1" and "严重" in choices:
            return [{"id": choices["严重"], "title": "严重"}]
        if text.upper() == "S2" and "一般" in choices:
            return [{"id": choices["一般"], "title": "一般"}]
        if text.upper() == "S3" and "轻微" in choices:
            return [{"id": choices["轻微"], "title": "轻微"}]
    # Direct match by title
    if text in choices:
        return [{"id": choices[text], "title": text}]
    # Try numeric match (e.g., 10 -> "10")
    if text.isdigit() and text in choices:
        return [{"id": choices[text], "title": text}]
    # Map numeric risk to 大/中/小 if applicable
    if text.isdigit():
        score = int(text)
        # Default thresholds: >=70 大, >=40 中, else 小
        if any(k in choices for k in ("大", "中", "小")):
            if score >= 70 and "大" in choices:
                return [{"id": choices["大"], "title": "大"}]
            if score >= 40 and "中" in choices:
                return [{"id": choices["中"], "title": "中"}]
            if "小" in choices:
                return [{"id": choices["小"], "title": "小"}]
    # Fuzzy contains match
    for title, cid in choices.items():
        if text in title or title in text:
            return [{"id": cid, "title": title}]
    return None


def _normalize_customfield_value(app_token: str, cf_id: str, value):
    field_type = _get_customfield_type(cf_id) or ""
    if field_type == "work":
        # Work type expects workId objects; skip plain strings to avoid API errors.
        if isinstance(value, dict) and (value.get("id") or value.get("workId")):
            work_id = value.get("id") or value.get("workId")
            return [{"id": str(work_id)}]
        logger.warning("customfield work type requires workId, skip cfId={}", cf_id)
        return None
    if field_type in ("dropDown", "multipleChoice"):
        mapped = _map_choice_value(app_token, cf_id, value)
        if mapped:
            return mapped
    return [
        {
            "title": str(value) if value is not None else "-",
            "description": "",
            "meta": "",
            "metaString": "",
        }
    ]


def create_task_oapi(payload: dict) -> str | None:
    app_token = _get_app_token()
    if not app_token:
        return None
    if not settings.TB_TENANT_ID or not settings.TB_OPERATOR_ID:
        logger.warning("TB_TENANT_ID / TB_OPERATOR_ID 未配置")
        return None

    url = "https://open.teambition.com/api/v3/task/create"
    if isinstance(payload.get("customfields"), list):
        normalized = []
        for item in payload.get("customfields") or []:
            cf_id = item.get("cfId") or item.get("customfieldId")
            value = item.get("value")
            if isinstance(value, list) and value:
                raw_value = value[0].get("title") if isinstance(value[0], dict) else value[0]
            else:
                raw_value = value
            if cf_id:
                normalized_value = _normalize_customfield_value(app_token, str(cf_id), raw_value)
                if normalized_value is None:
                    continue
                normalized.append({"cfId": cf_id, "value": normalized_value})
        payload["customfields"] = normalized
    logger.info(
        "Teambition create payload: title={}, tags={}, note_len={}, customfields={}",
        payload.get("content"),
        payload.get("tagNames"),
        len(payload.get("note") or ""),
        [
            {"cfId": i.get("cfId"), "value": (i.get("value") or [])[:1]}
            for i in (payload.get("customfields") or [])
        ],
    )
    try:
        resp = requests.post(url, json=payload, headers=_headers(app_token), timeout=20)
        if resp.status_code >= 400:
            logger.error(f"创建任务失败: {resp.status_code} {resp.text}")
            return None
        data = resp.json()
        if data.get("code") != 200:
            logger.error(f"创建任务返回异常: {data}")
            return None
        result = data.get("result") or {}
        return result.get("taskId") or result.get("id")
    except Exception as exc:
        logger.error(f"创建任务请求异常: {exc}")
        return None


def update_task_customfield(task_id: str, item: dict) -> bool:
    app_token = _get_app_token()
    if not app_token:
        return False
    if not settings.TB_TENANT_ID or not settings.TB_OPERATOR_ID:
        logger.warning("TB_TENANT_ID / TB_OPERATOR_ID 未配置")
        return False
    url = f"https://open.teambition.com/api/v3/task/{task_id}/customfield/update"
    cf_id = item.get("customfieldId") or item.get("cfId")
    value = item.get("value")
    normalized_value = _normalize_customfield_value(app_token, str(cf_id), value)
    if normalized_value is None:
        return False
    body = {
        "customfieldId": cf_id,
        "value": normalized_value,
    }
    logger.info("Teambition update customfield: task={}, cfId={}, value={}", task_id, cf_id, value)
    try:
        resp = requests.post(url, json=body, headers=_headers(app_token), timeout=20)
        if resp.status_code >= 400:
            logger.error(f"更新自定义字段失败: {resp.status_code} {resp.text}")
            return False
        data = resp.json()
        if data.get("code") != 200:
            logger.error(f"更新自定义字段异常: {data}")
            return False
        return True
    except Exception as exc:
        logger.error(f"更新自定义字段请求异常: {exc}")
        return False
