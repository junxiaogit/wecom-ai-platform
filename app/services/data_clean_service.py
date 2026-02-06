import re
from app.core.config import settings


class DataCleanService:
    # 扩展噪音词库（约50个常见噪音词）
    NOISE_KEYWORDS = [
        # 确认类
        "收到", "好的", "OK", "ok", "Ok", "好", "嗯", "嗯嗯", "对", "是", "是的", "行", "可以",
        "明白", "了解", "知道", "知道了", "懂了", "清楚", "没问题", "可以的", "行的", "好嘞",
        # 感谢类
        "谢谢", "感谢", "谢了", "多谢", "thanks", "thx", "Thanks", "Thx", "3Q", "蟹蟹",
        # 问候类
        "你好", "在吗", "在不", "在", "早", "早上好", "晚上好", "下午好", "Hi", "hi", "hello", "Hello",
        "早安", "晚安", "您好",
        # 简短回复
        "1", "666", "888", "999", "👍", "🙏", "👌", "✅", "哈哈", "哈哈哈", "呵呵", "嘿嘿",
        "哦", "噢", "额", "emmm", "emm", "em", "嗯哼", "啊", "哇", "wow",
        # 测试/占位
        "测试", "test", "Test", "表情", "图片", "语音", "视频", "文件", "链接",
        # 结束语
        "拜拜", "再见", "回见", "下次聊", "先这样",
    ]

    @staticmethod
    def sanitize(text: str) -> str:
        if not settings.SANITIZE_ENABLED:
            return text
        phone_pattern = r"1[3-9]\d{9}"
        return re.sub(phone_pattern, "[PHONE_HIDDEN]", text)

    # 表情符号的 Unicode 范围（常见 emoji）
    EMOJI_PATTERN = re.compile(
        "["
        "\U0001F600-\U0001F64F"  # 表情符号
        "\U0001F300-\U0001F5FF"  # 符号和图案
        "\U0001F680-\U0001F6FF"  # 交通和地图符号
        "\U0001F700-\U0001F77F"  # 炼金术符号
        "\U0001F780-\U0001F7FF"  # 几何形状扩展
        "\U0001F800-\U0001F8FF"  # 补充箭头-C
        "\U0001F900-\U0001F9FF"  # 补充符号和象形文字
        "\U0001FA00-\U0001FA6F"  # 国际象棋符号
        "\U0001FA70-\U0001FAFF"  # 符号和象形文字扩展-A
        "\U00002702-\U000027B0"  # 装饰符号
        "\U000024C2-\U0001F251"  # 圈中字母数字
        "]+", 
        flags=re.UNICODE
    )

    @classmethod
    def is_noise(cls, text: str) -> bool:
        """
        判断文本是否为噪音消息
        
        噪音判断规则：
        1. 空文本或长度<4
        2. 精确匹配噪音词库
        3. 纯表情符号（无有效中英文数字）
        4. 重复字符（如"啊啊啊啊"、"。。。。"）
        5. 仅符号/表情/无有效信息
        """
        if not text:
            return True

        stripped = text.strip()
        if len(stripped) < 4:
            return True

        if stripped in cls.NOISE_KEYWORDS:
            return True

        # 移除表情符号后再检查
        text_without_emoji = cls.EMOJI_PATTERN.sub("", stripped)
        
        # 纯表情符号（移除后为空或只剩空白）
        if not text_without_emoji.strip():
            return True
        
        # 移除表情后长度过短
        if len(text_without_emoji.strip()) < 3:
            return True

        # 仅符号/表情/无有效信息
        if not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", stripped):
            return True
        if re.fullmatch(r"[\W_]+", stripped):
            return True
        
        # 重复字符检测（如"啊啊啊啊"、"。。。。"、"哈哈哈哈哈"）
        # 如果一个字符重复出现超过总长度的70%，视为噪音
        if len(stripped) >= 3:
            char_counts = {}
            for char in stripped:
                char_counts[char] = char_counts.get(char, 0) + 1
            max_count = max(char_counts.values())
            if max_count / len(stripped) >= 0.7:
                return True

        return False

    @staticmethod
    def clean_for_llm(text: str) -> str:
        """
        清理文本中的无关格式，用于 LLM 输入
        - 移除 @用户名
        - 移除「用户名：xxx」格式
        - 移除"这是一条引用/回复消息："
        - 移除设备ID（ACN/ATP开头的长串）
        - 保留核心问题描述
        """
        if not text:
            return ""

        # 移除 @用户名（@后跟非空白字符）
        text = re.sub(r'@\S+\s*', '', text)

        # 移除「用户名：xxx」格式
        text = re.sub(r'「[^」]+：[^」]*」', '', text)

        # 移除"这是一条引用/回复消息：" 及其后面的引用内容
        text = re.sub(r'这是一条引用/回复消息[：:]\s*"[^"]*"', '', text)
        text = re.sub(r'这是一条引用/回复消息[：:].*?(?=\n|$)', '', text)

        # 移除设备ID（ACN/ATP + 数字串，长度超过10位）
        text = re.sub(r'\b(ACN|ATP)\d{10,}\b', '[设备ID]', text)

        # 移除多余空白行和空格
        text = re.sub(r'\n\s*\n', '\n', text)
        text = re.sub(r'[ \t]+', ' ', text)

        return text.strip()
