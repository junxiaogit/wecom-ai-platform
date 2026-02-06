# app/services/vector_service.py
import os
import shutil
from typing import List, Dict, Any, Tuple
from loguru import logger
# 新版 langchain-chroma 的引用方式
from langchain_chroma import Chroma
from langchain_core.documents import Document
from app.core.llm import get_embeddings
from app.services.data_service import _extract_content # 复用之前的清洗函数

# 向量库/知识库独立存储在项目根目录
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
VECTOR_DB_DIR = os.path.join(BASE_DIR, "vector_store")
KNOWLEDGE_DB_DIR = os.path.join(BASE_DIR, "knowledge_base")

# 高风险关键词（用于历史风险评估）
HIGH_RISK_KEYWORDS = ["崩溃", "闪退", "白屏", "无法使用", "数据丢失", "投诉", "报警", "退款"]
# 严重程度映射
SEVERITY_SCORES = {"S4": 100, "S3": 80, "S2": 60, "S1": 40, "S0": 20}

class VectorKnowledgeBase:
    def __init__(self):
        self.embeddings = get_embeddings()
        # 初始化 Chroma 客户端（向量库 & 知识库）
        self.chat_store = Chroma(
            collection_name="wecom_chat_history",
            embedding_function=self.embeddings,
            persist_directory=VECTOR_DB_DIR
        )
        self.faq_store = Chroma(
            collection_name="wecom_faq_kb",
            embedding_function=self.embeddings,
            persist_directory=KNOWLEDGE_DB_DIR
        )

    def add_chat_records(self, records: list):
        """
        核心功能：将 PostgreSQL 的原始记录 -> 清洗 -> 向量化 -> 存入 Chroma
        """
        documents = []
        print(f"📥 准备处理 {len(records)} 条原始记录...")

        for r in records:
            # 1. 清洗数据 (提取纯文本)
            content = _extract_content(r.msgData)
            
            # 2. 过滤无效数据 (太短的或者没提取出来的)
            if not content or len(str(content)) < 4:
                continue

            # 3. 构造元数据 (Metadata) - 方便以后溯源是哪个群、谁说的
            meta = {
                "msgid": str(r.msgid),
                "sender": str(r.sender)[-6:] if r.sender else "Unknown", # 简单脱敏
                "roomid": str(r.roomid),
                "time": int(r.msgtime)
            }
            
            # 4. 封装成 Document 对象
            doc = Document(page_content=content, metadata=meta)
            documents.append(doc)
        
        if documents:
            print(f"🚀 正在向量化 {len(documents)} 条有效数据 (这可能需要一点时间)...")
            # 批量写入
            self.chat_store.add_documents(documents)
            print(f"✅ 成功存入向量库！当前库中总数: {self._get_count(self.chat_store)}")
        else:
            print("⚠️ 没有有效数据需要存储。")

    def add_wecom_messages(self, messages: list):
        """
        将 wecom_messages -> 向量化 -> 存入 Chroma
        支持 ORM 对象或 dict 输入。
        """
        documents = []
        for msg in messages:
            if isinstance(msg, dict):
                content = msg.get("content_clean") or msg.get("content_raw") or msg.get("content")
                msg_id = msg.get("msg_id")
                room_id = msg.get("room_id")
                sender_id = msg.get("sender_id")
                msg_time = msg.get("msg_time")
            else:
                content = getattr(msg, "content_clean", None) or getattr(msg, "content_raw", None)
                msg_id = getattr(msg, "msg_id", None)
                room_id = getattr(msg, "room_id", None)
                sender_id = getattr(msg, "sender_id", None)
                msg_time = getattr(msg, "msg_time", None)

            if not content or len(str(content)) < 4:
                continue

            meta = {
                "msgid": str(msg_id) if msg_id is not None else "",
                "sender": str(sender_id)[-6:] if sender_id else "Unknown",
                "roomid": str(room_id) if room_id is not None else "",
                "time": int(getattr(msg_time, "timestamp", lambda: 0)()) if msg_time else 0,
            }
            documents.append(Document(page_content=str(content), metadata=meta))

        if documents:
            self.chat_store.add_documents(documents)

    def search_similar_issues(self, query: str, k=3):
        """
        RAG 核心：给一个问题，找出历史上最相似的 k 个对话
        """
        print(f"🔍 RAG 检索中: '{query}'")
        try:
            results = self.chat_store.similarity_search(query, k=k)
            return results
        except Exception as e:
            print(f"检索失败 (可能是库为空): {e}")
            return []

    def search_similar_faq(self, query: str, k=3):
        """
        知识库检索：优先从 FAQ 中找相似问题
        """
        try:
            results = self.faq_store.similarity_search(query, k=k)
            return results
        except Exception as e:
            print(f"FAQ 检索失败 (可能是库为空): {e}")
            return []

    def add_faq_items(self, items: list[dict]):
        documents = []
        for item in items:
            content = f"Q: {item.get('question')}\nA: {item.get('answer')}"
            meta = {
                "type": "faq",
                "category_l1": item.get("category_l1"),
                "category_l2": item.get("category_l2"),
            }
            documents.append(Document(page_content=content, metadata=meta))
        if documents:
            self.faq_store.add_documents(documents)

    def search_with_metadata(self, query: str, k: int = 3) -> List[Tuple[Document, float]]:
        """
        RAG 检索带元数据和相似度分数的结果
        返回: [(Document, score), ...]
        """
        try:
            results = self.chat_store.similarity_search_with_score(query, k=k)
            return results
        except Exception as e:
            logger.warning(f"RAG 检索失败 (可能是库为空): {e}")
            return []

    def get_historical_severity(self, query: str, k: int = 2) -> Dict[str, Any]:
        """
        根据历史相似案例评估风险等级
        
        Returns:
            {
                "has_similar": bool,           # 是否找到相似案例
                "max_severity_score": int,     # 历史最高严重程度分数 (0-100)
                "has_high_risk": bool,         # 是否包含高风险关键词
                "similar_categories": list,    # 历史相似案例的分类
                "similar_docs": list,          # 相似文档列表
            }
        """
        result = {
            "has_similar": False,
            "max_severity_score": 0,
            "has_high_risk": False,
            "similar_categories": [],
            "similar_docs": [],
        }
        
        try:
            docs = self.chat_store.similarity_search(query, k=k)
            if not docs:
                return result
            
            result["has_similar"] = True
            result["similar_docs"] = docs
            
            for doc in docs:
                content = doc.page_content
                meta = doc.metadata or {}
                
                # 检查是否包含高风险关键词
                for keyword in HIGH_RISK_KEYWORDS:
                    if keyword in content:
                        result["has_high_risk"] = True
                        break
                
                # 提取分类信息
                if meta.get("category_l1"):
                    cat = f"{meta.get('category_l1')}/{meta.get('category_l2', '')}"
                    if cat not in result["similar_categories"]:
                        result["similar_categories"].append(cat)
                
                # 提取严重程度
                severity = meta.get("severity")
                if severity and severity in SEVERITY_SCORES:
                    score = SEVERITY_SCORES[severity]
                    if score > result["max_severity_score"]:
                        result["max_severity_score"] = score
            
            return result
        except Exception as e:
            logger.warning(f"历史风险评估失败: {e}")
            return result

    def get_historical_categories(self, query: str, k: int = 2) -> List[Dict[str, str]]:
        """
        获取历史相似案例的分类信息，用于辅助分类
        
        Returns:
            [{"category_l1": ..., "category_l2": ..., "severity": ..., "issue_type": ...}, ...]
        """
        categories = []
        try:
            docs = self.chat_store.similarity_search(query, k=k)
            for doc in docs:
                meta = doc.metadata or {}
                if meta.get("category_l1") or meta.get("issue_type"):
                    categories.append({
                        "category_l1": meta.get("category_l1", ""),
                        "category_l2": meta.get("category_l2", ""),
                        "severity": meta.get("severity", ""),
                        "issue_type": meta.get("issue_type", ""),
                        "content_preview": doc.page_content[:50] if doc.page_content else "",
                    })
        except Exception as e:
            logger.warning(f"历史分类检索失败: {e}")
        return categories

    def has_similar_hard_issue(self, query: str, k: int = 2) -> bool:
        """
        检查历史中是否有类似的"硬问题"
        用于辅助 is_hard_issue 判断
        """
        try:
            docs = self.chat_store.similarity_search(query, k=k)
            for doc in docs:
                content = doc.page_content
                meta = doc.metadata or {}
                
                # 检查是否包含高风险关键词
                for keyword in HIGH_RISK_KEYWORDS:
                    if keyword in content:
                        return True
                
                # 检查历史严重程度
                severity = meta.get("severity")
                if severity in ["S0", "S1", "S2"]:
                    return True
                
                # 检查是否被标记为 bug
                if meta.get("is_bug"):
                    return True
            
            return False
        except Exception as e:
            logger.warning(f"硬问题检索失败: {e}")
            return False

    def add_issue_with_metadata(self, content: str, metadata: Dict[str, Any]):
        """
        添加带有完整元数据的问题到向量库
        用于记录处理过的问题，便于后续 RAG 检索
        """
        if not content or len(content) < 4:
            return
        
        doc = Document(page_content=content, metadata=metadata)
        self.chat_store.add_documents([doc])
        logger.debug(f"已添加问题到向量库: {content[:50]}...")

    def _get_count(self, store):
        # 这是一个内部辅助方法，查看库里有多少条
        return store._collection.count()

# 单例模式：全局使用这个实例
vector_kb = VectorKnowledgeBase()