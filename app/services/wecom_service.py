import json
import requests
from loguru import logger
from app.core.config import settings


class WeComService:
    """
    企微 SDK 访问封装。
    生产可通过内网“消息存档 SDK 服务”拉取并解密后的记录。
    """

    def fetch_messages(self, after_seq: int, limit: int | None = None) -> list[dict]:
        """
        从消息存档服务拉取增量记录，返回标准化字段:
        msgid, action, from, tolist, roomid, msgtime, msgtype, msgData, seq
        """
        if not settings.WECOM_ARCHIVE_URL:
            return []

        params = {"seq": after_seq, "limit": limit or settings.WECOM_ARCHIVE_LIMIT}
        headers = {}
        if settings.WECOM_ARCHIVE_TOKEN:
            headers["Authorization"] = f"Bearer {settings.WECOM_ARCHIVE_TOKEN}"

        try:
            resp = requests.get(
                settings.WECOM_ARCHIVE_URL, params=params, headers=headers, timeout=10
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            logger.error(f"WeCom archive fetch failed: {exc}")
            return []

        records = payload.get("records") or payload.get("data") or payload.get("messages") or []
        normalized: list[dict] = []
        for item in records:
            msgdata = (
                item.get("msgData")
                or item.get("msgdata")
                or item.get("content")
                or item.get("payload")
            )
            if isinstance(msgdata, dict):
                msgdata = json.dumps(msgdata, ensure_ascii=False)
            tolist = item.get("tolist") or item.get("to_list")
            if isinstance(tolist, list):
                tolist = json.dumps(tolist, ensure_ascii=False)
            normalized.append(
                {
                    "msgid": item.get("msgid") or item.get("msg_id"),
                    "action": item.get("action"),
                    "from": item.get("from") or item.get("sender") or item.get("sender_id"),
                    "tolist": tolist,
                    "roomid": item.get("roomid") or item.get("room_id") or item.get("chatid"),
                    "msgtime": item.get("msgtime") or item.get("msg_time"),
                    "msgtype": item.get("msgtype") or item.get("msg_type") or item.get("type"),
                    "msgData": msgdata,
                    "seq": item.get("seq") or item.get("msgseq") or item.get("msg_seq"),
                }
            )
        return [r for r in normalized if r.get("msgid")]

    def send_reply(self, room_id: str, text: str):
        """
        自动回复占位：上线后对接企微 API。
        """
        print(f"[AUTO_REPLY] room={room_id} text={text}")
