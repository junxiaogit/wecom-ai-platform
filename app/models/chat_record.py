# app/models/chat_record.py
from sqlalchemy import Column, Integer, String, BigInteger, Text
from app.core.database import Base

class ChatRecord(Base):
    __tablename__ = "chat_records"

    # 这些字段必须和你数据库里的真实字段一一对应
    id = Column(Integer, primary_key=True, index=True)
    msgid = Column(String, unique=True)
    action = Column(String)
    sender = Column("from", String)  # 因为 from 是 python 关键字，必须重命名映射
    tolist = Column(Text)
    roomid = Column(String, index=True)
    msgtime = Column(BigInteger)
    msgtype = Column(String)
    msgData = Column(Text)           # 核心内容 JSON
    seq = Column(BigInteger)