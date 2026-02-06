import json
import os
import sys
import webbrowser
from urllib.parse import urlparse, parse_qs, quote

import requests
from dotenv import load_dotenv


load_dotenv()

APP_ID = os.getenv("TB_APP_ID", "").strip()
APP_SECRET = os.getenv("TB_APP_SECRET", "").strip()
TENANT_ID = os.getenv("TB_TENANT_ID", "").strip()
PROJECT_ID = os.getenv("TB_PROJECT_ID", "").strip()
REDIRECT_URI = os.getenv("TB_REDIRECT_URI", "http://localhost").strip()
AUTH_BASE_URL = os.getenv("TB_AUTH_URL", "https://open.teambition.com/oauth/authorize").strip()
SCOPE = os.getenv("TB_SCOPE", "user").strip()
APP_TOKEN_URL = os.getenv("TB_APP_TOKEN_URL", "https://open.teambition.com/api/appToken").strip()
AUTH_CODE = os.getenv("TB_AUTH_CODE", "").strip()
SKIP_USER_TOKEN = os.getenv("TB_SKIP_USER_TOKEN", "false").lower() == "true"


def die(msg: str) -> None:
    print(f"[ERROR] {msg}")
    sys.exit(1)


def build_app_access_token() -> str:
    payload = {"appId": APP_ID, "appSecret": APP_SECRET}
    resp = requests.post(APP_TOKEN_URL, json=payload, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    # Two possible shapes:
    # 1) { code: 200, result: { appToken, expire } }
    # 2) { appToken, expire }
    if "appToken" in data:
        token = data.get("appToken")
        if not token:
            raise RuntimeError(f"appToken missing: {data}")
        return token
    if data.get("code") != 200:
        raise RuntimeError(f"appToken error: {data}")
    result = data.get("result") or {}
    token = result.get("appToken")
    if not token:
        raise RuntimeError(f"appToken missing: {data}")
    return token


def build_auth_url() -> str:
    base = AUTH_BASE_URL.rstrip("?")
    return (
        f"{base}?client_id={APP_ID}"
        f"&redirect_uri={quote(REDIRECT_URI, safe='')}"
        f"&response_type=code"
        f"&scope={SCOPE}"
    )


def parse_code_from_url(url: str) -> str:
    parsed = urlparse(url.strip())
    qs = parse_qs(parsed.query)
    code = (qs.get("code") or [""])[0]
    return code


def exchange_user_token(app_access_token: str, code: str) -> str:
    url = "https://open.teambition.com/api/oauth/userAccessToken"
    payload = {"code": code, "grantType": "authorizationCode", "expires": 86400}
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {app_access_token}"}
    resp = requests.post(url, json=payload, headers=headers, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 200:
        raise RuntimeError(f"oauth error: {data}")
    result = data.get("result") or {}
    token = result.get("accessToken") or result.get("userAccessToken")
    if not token:
        raise RuntimeError(f"oauth response missing token: {data}")
    return token


def fetch_customfields(app_access_token: str) -> list[dict]:
    url = "https://open.teambition.com/api/v3/customfield/search"
    params = {"projectIds": PROJECT_ID, "pageSize": 200}
    headers = {
        "Authorization": f"Bearer {app_access_token}",
        "X-Tenant-Id": TENANT_ID,
        "X-Tenant-Type": "organization",
    }
    resp = requests.get(url, headers=headers, params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 200:
        raise RuntimeError(f"customfield error: {data}")
    return data.get("result") or []


def main() -> None:
    if not APP_ID or not APP_SECRET:
        die("TB_APP_ID / TB_APP_SECRET 未设置")
    if not TENANT_ID:
        die("TB_TENANT_ID 未设置")
    if not PROJECT_ID:
        die("TB_PROJECT_ID 未设置")

    app_token = build_app_access_token()
    print("已获取 appToken（用于 OAPI 调用）")

    code = AUTH_CODE
    if not SKIP_USER_TOKEN and not code:
        auth_url = build_auth_url()
        print("打开授权链接并登录（用于换取 userAccessToken，可选）：")
        print(auth_url)
        try:
            webbrowser.open(auth_url)
        except Exception:
            pass
        redirect_url = input("粘贴回调地址（包含 code=...），或直接回车跳过: ").strip()
        if redirect_url:
            code = parse_code_from_url(redirect_url)

    user_token = None
    if not SKIP_USER_TOKEN and code:
        user_token = exchange_user_token(app_token, code)
        print("已获取 userAccessToken（用于用户相关接口）")
    else:
        print("跳过 userAccessToken 获取（不影响拉字段字典）")

    fields = fetch_customfields(app_token)

    rows = []
    for f in fields:
        rows.append(
            {
                "customfieldId": f.get("id"),
                "name": f.get("name"),
                "type": f.get("type"),
            }
        )

    with open("customfield_dict.json", "w", encoding="utf-8") as fp:
        json.dump(rows, fp, ensure_ascii=False, indent=2)

    for r in rows:
        print(f"{r['customfieldId']}\t{r['name']}\t{r['type']}")
    print("done -> customfield_dict.json")


if __name__ == "__main__":
    main()
