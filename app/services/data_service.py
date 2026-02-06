# app/services/data_service.py
import json
from sqlalchemy.orm import Session
from app.models.chat_record import ChatRecord
from app.models.sql_models import WeComMessage

def get_recent_chat_text(db: Session, room_id: str = None, limit: int = 50) -> list[str]:
    """从数据库拉取数据，并清洗成纯文本"""
    # 1. 查库
    query = db.query(ChatRecord).filter(ChatRecord.msgtype == 'text')
    
    if room_id:
        query = query.filter(ChatRecord.roomid == room_id)
    
    # 按时间倒序取 limit 条
    records = query.order_by(ChatRecord.msgtime.desc()).limit(limit).all()
    
    cleaned_lines = []
    
    # 2. 清洗数据 (反转顺序，变成从旧到新)
    for record in reversed(records):
        content = _extract_content(record.msgData)
        if content:
            # 简单的脱敏发送者 (取后4位)
            sender = record.sender[-4:] if record.sender else "Unknown"
            cleaned_lines.append(f"用户{sender}: {content}")
            
    return cleaned_lines

def _extract_content(msg_data) -> str:
    """
    智能解析 msgData
    兼容: 字符串 JSON 和 已自动转换的 Dict
    """
    try:
        data = None
        
        # --- 核心修复点 ---
        # 如果数据库驱动已经把它转成了字典，直接用！
        if isinstance(msg_data, dict):
            data = msg_data
        # 如果还是字符串，手动转一下
        elif isinstance(msg_data, str):
            try:
                data = json.loads(msg_data)
            except:
                # 也许它就是一段普通文本？直接返回试试
                return msg_data
        
        if not data:
            return None

        # --- 提取逻辑 (根据你抓到的数据结构适配) ---
        
        # 模式 1: 标准企微格式 {"text": {"content": "你好"}}
        if "text" in data and isinstance(data["text"], dict) and "content" in data["text"]:
            return data["text"]["content"]
            
        # 模式 2: 扁平格式 {"content": "你好"} (你刚才 debug 抓到的就是这种!)
        elif "content" in data:
            return data["content"]
            
        return None
        
    except Exception as e:
        print(f"解析出错: {e}")
        return None
    


def get_raw_records(db: Session, room_id: str = None, limit: int = 50):
    """
    获取原始的数据库对象列表 (用于向量库构建)
    """
    query = db.query(ChatRecord).filter(ChatRecord.msgtype == 'text')
    
    if room_id:
        query = query.filter(ChatRecord.roomid == room_id)
    
    # 获取最近的数据
    return query.order_by(ChatRecord.msgtime.desc()).limit(limit).all()


def get_recent_wecom_text(db: Session, room_id: str = None, limit: int = 50) -> list[str]:
    """从 wecom_messages 拉取数据，并清洗成纯文本"""
    query = db.query(WeComMessage).filter(WeComMessage.msg_type == "text")
    if room_id:
        query = query.filter(WeComMessage.room_id == room_id)

    records = query.order_by(WeComMessage.msg_time.desc()).limit(limit).all()
    cleaned_lines = []
    for record in reversed(records):
        content = record.content_clean or record.content_raw
        if content:
            sender = (record.sender_id or "Unknown")[-4:]
            cleaned_lines.append(f"用户{sender}: {content}")
    return cleaned_lines