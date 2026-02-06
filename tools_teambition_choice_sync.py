import json
import os
import time
import requests
import jwt
from dotenv import load_dotenv


load_dotenv()

APP_ID = os.getenv("TB_APP_ID", "").strip()
APP_SECRET = os.getenv("TB_APP_SECRET", "").strip()
TENANT_ID = os.getenv("TB_TENANT_ID", "").strip()
OPERATOR_ID = os.getenv("TB_OPERATOR_ID", "").strip()
PROJECT_ID = (os.getenv("TB_PROJECT_ID") or os.getenv("TEAMBITION_PROJECT_ID") or "").strip()
APP_TOKEN = (os.getenv("TB_APP_TOKEN") or os.getenv("appToken") or "").strip()
OAPI_BASE_URL = os.getenv("TB_OAPI_BASE_URL", "https://open.teambition.com/api").strip().rstrip("/")
MAPPING_PATH = os.getenv("CUSTOM_FIELDS_MAPPING_PATH", "customfield_mapping.json")
OUTPUT_PATH = os.getenv("CUSTOM_FIELDS_CHOICE_MAP_PATH", "customfield_choice_map.json")


def die(msg: str) -> None:
    raise SystemExit(f"[ERROR] {msg}")


def _normalize_oapi_v3_base(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/api/v3"):
        return base
    if base.endswith("/api"):
        return f"{base}/v3"
    return f"{base}/api/v3"


def _build_jwt_app_token(app_id: str, app_secret: str, expires_in_seconds: int = 3600) -> str:
    now = int(time.time())
    token = jwt.encode(
        {"_appId": app_id, "iat": now, "exp": now + int(expires_in_seconds)},
        app_secret,
        algorithm="HS256",
    )
    if isinstance(token, bytes):
        token = token.decode("utf-8")
    return token


def get_app_token() -> str:
    if APP_TOKEN:
        try:
            payload = jwt.decode(APP_TOKEN, options={"verify_signature": False})
            exp = int(payload.get("exp") or 0)
            now = int(time.time())
            if exp and now < exp - 30:
                return APP_TOKEN
        except Exception:
            pass
    if not APP_ID or not APP_SECRET:
        die("TB_APP_ID / TB_APP_SECRET 未配置")
    return _build_jwt_app_token(APP_ID, APP_SECRET, 3600)


def _headers(app_token: str) -> dict:
    return {
        "Authorization": f"Bearer {app_token}",
        "X-Tenant-Id": TENANT_ID,
        "X-Tenant-Type": "organization",
        "x-operator-id": OPERATOR_ID,
        "Content-Type": "application/json",
    }


def _load_customfield_ids() -> list[str]:
    if not os.path.exists(MAPPING_PATH):
        die(f"未找到映射文件: {MAPPING_PATH}")
    with open(MAPPING_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return [k for k in data.keys() if k]
    if isinstance(data, list):
        ids = []
        for item in data:
            cid = item.get("customfieldId") or item.get("id")
            if cid:
                ids.append(cid)
        return ids
    die("customfield_mapping.json 格式不正确")


def _load_customfield_dict() -> dict[str, dict]:
    path = os.getenv("CUSTOM_FIELDS_DICT_PATH", "customfield_dict.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return {str(i.get("customfieldId")): i for i in data if i.get("customfieldId")}
    except Exception:
        return {}
    return {}


def fetch_choices(app_token: str, cf_id: str) -> dict[str, str]:
    base = _normalize_oapi_v3_base(OAPI_BASE_URL)
    urls = [
        f"{base}/project/{PROJECT_ID}/customfield/{cf_id}/choice/search",
        f"{base}/customfield/{cf_id}/choice/search",
    ]
    last_error = None
    for url in urls:
        resp = requests.get(url, headers=_headers(app_token), timeout=20)
        if resp.status_code >= 400:
            last_error = f"{resp.status_code} {resp.text}"
            continue
        data = resp.json()
        if data.get("code") not in (None, 200):
            last_error = json.dumps(data, ensure_ascii=False)
            continue
        items = data.get("result") or []
        mapping: dict[str, str] = {}
        for item in items:
            cid = item.get("id") or item.get("_id") or item.get("value")
            title = item.get("title") or item.get("name") or item.get("label")
            if cid and title:
                mapping[str(title)] = str(cid)
        return mapping
    return {"__error__": str(last_error)}


def fetch_project_customfields(app_token: str) -> list[dict]:
    base = _normalize_oapi_v3_base(OAPI_BASE_URL)
    url = f"{base}/project/{PROJECT_ID}/customfield/search"
    resp = requests.get(url, headers=_headers(app_token), timeout=20)
    if resp.status_code >= 400:
        raise SystemExit(f"[ERROR] fetch project customfields failed: {resp.status_code} {resp.text}")
    data = resp.json()
    if data.get("code") not in (None, 200):
        raise SystemExit(f"[ERROR] fetch project customfields error: {data}")
    return data.get("result") or []


def main() -> None:
    if not TENANT_ID:
        die("TB_TENANT_ID 未配置")
    if not PROJECT_ID:
        die("TB_PROJECT_ID / TEAMBITION_PROJECT_ID 未配置")
    if not OPERATOR_ID:
        die("TB_OPERATOR_ID 未配置")

    app_token = get_app_token()
    ids = _load_customfield_ids()
    cf_dict = _load_customfield_dict()
    project_fields = fetch_project_customfields(app_token)
    project_index = {
        str(item.get("customfieldId") or item.get("id") or item.get("_id")): item
        for item in project_fields
        if item.get("customfieldId") or item.get("id") or item.get("_id")
    }
    result: dict[str, dict[str, str]] = {}
    for cid in ids:
        field_type = (cf_dict.get(str(cid)) or {}).get("type")
        if field_type not in ("dropDown", "multipleChoice"):
            continue
        item = project_index.get(str(cid))
        choices = (item or {}).get("choices") or []
        if choices:
            mapping: dict[str, str] = {}
            for choice in choices:
                title = choice.get("title") or choice.get("name") or choice.get("label") or choice.get("value")
                value = choice.get("id") or choice.get("_id") or choice.get("value")
                if title and value:
                    mapping[str(title)] = str(value)
            result[cid] = mapping
            continue
        result[cid] = fetch_choices(app_token, cid)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"choice map saved: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
