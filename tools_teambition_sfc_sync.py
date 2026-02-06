import os
import re
import time
import requests
from dotenv import load_dotenv
import jwt


load_dotenv()

APP_ID = os.getenv("TB_APP_ID", "").strip()
APP_SECRET = os.getenv("TB_APP_SECRET", "").strip()
TENANT_ID = os.getenv("TB_TENANT_ID", "").strip()
OPERATOR_ID = os.getenv("TB_OPERATOR_ID", "").strip()
PROJECT_ID = (os.getenv("TB_PROJECT_ID") or os.getenv("TEAMBITION_PROJECT_ID") or "").strip()
SFC_ID = (os.getenv("TB_SFC_ID") or os.getenv("TEAMBITION_TASK_TYPE_ID") or "").strip()
ENV_PATH = os.getenv("TB_ENV_PATH", ".env")
APP_TOKEN_URL = os.getenv("TB_APP_TOKEN_URL", "https://open.teambition.com/api/appToken").strip()
OAPI_BASE_URL = os.getenv("TB_OAPI_BASE_URL", "https://open.teambition.com/api").strip().rstrip("/")
APP_TOKEN_OVERRIDE = (os.getenv("TB_APP_TOKEN") or os.getenv("appToken") or "").strip()


def die(msg: str) -> None:
    raise SystemExit(f"[ERROR] {msg}")


def get_app_token() -> str:
    if APP_TOKEN_OVERRIDE:
        try:
            payload = jwt.decode(APP_TOKEN_OVERRIDE, options={"verify_signature": False})
            exp = int(payload.get("exp") or 0)
            now = int(time.time())
            if exp and now < exp - 30:
                return APP_TOKEN_OVERRIDE
        except Exception:
            pass

    payload = {"appId": APP_ID, "appSecret": APP_SECRET}
    try:
        resp = requests.post(APP_TOKEN_URL, json=payload, timeout=20)
        if resp.status_code < 400:
            data = resp.json()
            token = data.get("appToken") or (data.get("result") or {}).get("appToken")
            if token:
                return token
            die(f"appToken missing: {data}")
    except Exception:
        pass

    # Fallback: generate JWT app token locally
    try:
        now = int(time.time())
        token = jwt.encode({"_appId": APP_ID, "iat": now, "exp": now + 3600}, APP_SECRET, algorithm="HS256")
        if isinstance(token, bytes):
            token = token.decode("utf-8")
        return token
    except Exception as exc:
        die(f"appToken generate failed: {exc}")


def _get_json(url: str, headers: dict) -> dict:
    resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    return resp.json()


def _normalize_oapi_v3_base(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/api/v3"):
        return base
    if base.endswith("/api"):
        return f"{base}/v3"
    return f"{base}/api/v3"


def _fetch_sfc_result(headers: dict) -> dict:
    api_v3_base = _normalize_oapi_v3_base(OAPI_BASE_URL)
    candidates = [
        f"{api_v3_base}/sfc/{SFC_ID}",
        f"{api_v3_base}/sfc/{SFC_ID}/sf",
        f"{api_v3_base}/scenariofieldconfig/{SFC_ID}",
        f"{api_v3_base}/scenariofieldconfig/{SFC_ID}/sf",
    ]
    last_error = None
    for url in candidates:
        try:
            data = _get_json(url, headers)
            if data.get("code") in (None, 200):
                return data
            last_error = data
        except Exception as exc:
            last_error = {"error": str(exc), "url": url}
    die(f"sfc api error: {last_error}")


def _fetch_project_customfields(headers: dict) -> tuple[list[str], list[dict]]:
    if not PROJECT_ID:
        return [], []
    api_v3_base = _normalize_oapi_v3_base(OAPI_BASE_URL)
    url = f"{api_v3_base}/project/{PROJECT_ID}/customfield/search"
    params = []
    if SFC_ID:
        params.append(f"sfcId={SFC_ID}")
    if params:
        url = f"{url}?{'&'.join(params)}"
    try:
        data = _get_json(url, headers)
        if data.get("code") not in (None, 200):
            return [], []
        result = data.get("result") if isinstance(data, dict) else None
        items = result or []
        ids = []
        for item in items:
            cf_id = item.get("customfieldId") or item.get("_id") or item.get("id")
            if cf_id:
                ids.append(cf_id)
        return sorted(set(ids)), items
    except Exception:
        return [], []


def get_sfc_fields(app_token: str) -> tuple[list[str], list[dict]]:
    headers = {
        "Authorization": f"Bearer {app_token}",
        "X-Tenant-Id": TENANT_ID,
        "X-Tenant-Type": "organization",
        "x-operator-id": OPERATOR_ID,
    }
    # Prefer project customfield search; it is more stable across environments.
    project_ids, project_items = _fetch_project_customfields(headers)
    if project_ids:
        return project_ids, project_items

    data = _fetch_sfc_result(headers)
    result = data.get("result") if isinstance(data, dict) else None
    scenariofields = (result or {}).get("scenariofields") or (result or [])
    ids = []
    for item in scenariofields:
        if item.get("fieldType") != "customfield":
            continue
        cf_id = item.get("customfieldId")
        if cf_id:
            ids.append(cf_id)
    return sorted(set(ids)), []


def update_env(custom_ids: list[str]) -> None:
    if not custom_ids:
        die("no customfieldId found in sfc")
    line_value = "CUSTOM_FIELDS_IDS=" + ",".join(custom_ids)
    if not os.path.exists(ENV_PATH):
        with open(ENV_PATH, "w", encoding="utf-8") as f:
            f.write(line_value + "\n")
        return
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    if re.search(r"^CUSTOM_FIELDS_IDS=.*$", content, flags=re.M):
        content = re.sub(r"^CUSTOM_FIELDS_IDS=.*$", line_value, content, flags=re.M)
    else:
        if not content.endswith("\n"):
            content += "\n"
        content += line_value + "\n"
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.write(content)


def write_customfield_dict(items: list[dict]) -> None:
    if not items:
        return
    path = os.getenv("CUSTOM_FIELDS_DICT_PATH") or "customfield_dict.json"
    data = []
    for item in items:
        cid = item.get("customfieldId") or item.get("_id") or item.get("id")
        if not cid:
            continue
        data.append(
            {
                "customfieldId": cid,
                "name": item.get("name") or item.get("title") or "",
                "type": item.get("type") or item.get("fieldType") or "",
            }
        )
    if not data:
        return
    with open(path, "w", encoding="utf-8") as f:
        f.write("[\n")
        for idx, row in enumerate(data):
            line = (
                "  {"
                + f"\"customfieldId\": \"{row['customfieldId']}\", "
                + f"\"name\": \"{row['name']}\", "
                + f"\"type\": \"{row['type']}\""
                + "}"
            )
            if idx < len(data) - 1:
                line += ","
            f.write(line + "\n")
        f.write("]\n")

def main() -> None:
    if not APP_ID or not APP_SECRET:
        die("TB_APP_ID / TB_APP_SECRET 未配置")
    if not TENANT_ID:
        die("TB_TENANT_ID 未配置")
    if not OPERATOR_ID:
        die("TB_OPERATOR_ID 未配置")
    if not SFC_ID:
        die("TB_SFC_ID 或 TEAMBITION_TASK_TYPE_ID 未配置")

    app_token = get_app_token()
    custom_ids, items = get_sfc_fields(app_token)
    update_env(custom_ids)
    write_customfield_dict(items)
    print("CUSTOM_FIELDS_IDS updated:")
    for cid in custom_ids:
        print(cid)


if __name__ == "__main__":
    main()
