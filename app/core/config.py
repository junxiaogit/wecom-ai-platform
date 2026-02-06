# app/core/config.py
import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # App
    PROJECT_NAME: str = "WeCom AI Platform"

    # Database
    DB_USER: str | None = os.getenv("DB_USER")
    DB_PASS: str | None = os.getenv("DB_PASS")
    DB_HOST: str | None = os.getenv("DB_HOST")
    DB_PORT: str | None = os.getenv("DB_PORT")
    DB_NAME: str | None = os.getenv("DB_NAME")
    DB_URL: str | None = os.getenv("DB_URL")

    # AI - Aliyun DashScope compatible mode
    AI_API_URL: str = os.getenv("AI_API_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    AI_API_KEY: str | None = os.getenv("AI_API_KEY")
    AI_MODEL_NAME: str = os.getenv("AI_MODEL_NAME", "qwen-plus")
    AI_MODEL_FAST: str = os.getenv("AI_MODEL_FAST", AI_MODEL_NAME)
    AI_MODEL_SMART: str = os.getenv("AI_MODEL_SMART", AI_MODEL_NAME)

    # DingTalk
    DINGTALK_WEBHOOK: str | None = os.getenv("DINGTALK_WEBHOOK")
    DINGTALK_SECRET: str | None = os.getenv("DINGTALK_SECRET")
    DINGTALK_AT_ALL: bool = os.getenv("DINGTALK_AT_ALL", "false").lower() == "true"
    INTERNAL_BASE_URL: str = os.getenv("INTERNAL_BASE_URL", "http://127.0.0.1:8000")

    # UI（客户原声页面）
    # 不带 since/until 时展示的最近消息条数
    UI_MESSAGES_LIMIT_DEFAULT: int = int(os.getenv("UI_MESSAGES_LIMIT_DEFAULT", "100"))
    # 带 since/until 时间窗口时展示的最大消息条数（安全上限，避免极端情况下页面过重）
    UI_MESSAGES_LIMIT_WITH_FILTER: int = int(os.getenv("UI_MESSAGES_LIMIT_WITH_FILTER", "500"))

    # WeCom archive (SDK proxy)
    WECOM_ARCHIVE_ENABLED: bool = os.getenv("WECOM_ARCHIVE_ENABLED", "false").lower() == "true"
    WECOM_ARCHIVE_URL: str | None = os.getenv("WECOM_ARCHIVE_URL")
    WECOM_ARCHIVE_TOKEN: str | None = os.getenv("WECOM_ARCHIVE_TOKEN")
    WECOM_ARCHIVE_LIMIT: int = int(os.getenv("WECOM_ARCHIVE_LIMIT", "200"))

    # Chat API (外部群组信息 API)
    CHAT_API_BASE_URL: str = os.getenv("CHAT_API_BASE_URL", "http://192.168.230.160:19000/api")

    # Teambition
    TEAMBITION_API_BASE: str = os.getenv("TEAMBITION_API_BASE", "https://www.teambition.com/api")
    TEAMBITION_TOKEN: str | None = os.getenv("TEAMBITION_TOKEN")
    TEAMBITION_PROJECT_ID: str | None = os.getenv("TEAMBITION_PROJECT_ID")
    TEAMBITION_TASK_URL_BASE: str = os.getenv("TEAMBITION_TASK_URL_BASE", "https://www.teambition.com/task")
    TEAMBITION_PROJECT_URL: str | None = os.getenv("TEAMBITION_PROJECT_URL")
    TEAMBITION_TASK_TYPE_ID: str | None = os.getenv("TEAMBITION_TASK_TYPE_ID")
    TEAMBITION_MODE: str = os.getenv("TEAMBITION_MODE", "mcp").lower()
    TEAMBITION_AUTO_CREATE: bool = os.getenv("TEAMBITION_AUTO_CREATE", "false").lower() == "true"
    MCP_BRIDGE_URL: str | None = os.getenv("MCP_BRIDGE_URL")
    MCP_BRIDGE_TIMEOUT: int = int(os.getenv("MCP_BRIDGE_TIMEOUT", "15"))
    MCP_GATEWAY_URL: str | None = os.getenv("MCP_GATEWAY_URL")
    MCP_TOOL_NAME: str = os.getenv("MCP_TOOL_NAME", "create_task")
    MCP_TIMEOUT: int = int(os.getenv("MCP_TIMEOUT", "20"))

    # Ticket defaults
    DEFAULT_ASSIGNEE: str = os.getenv("DEFAULT_ASSIGNEE", "oncall")

    # Issue type assignees
    ISSUE_TYPE_ASSIGNEE_USAGE: str | None = os.getenv("ISSUE_TYPE_ASSIGNEE_USAGE")
    ISSUE_TYPE_ASSIGNEE_FEEDBACK: str | None = os.getenv("ISSUE_TYPE_ASSIGNEE_FEEDBACK")
    ISSUE_TYPE_ASSIGNEE_REQUIREMENT: str | None = os.getenv("ISSUE_TYPE_ASSIGNEE_REQUIREMENT")
    ISSUE_TYPE_ASSIGNEE_DEFECT: str | None = os.getenv("ISSUE_TYPE_ASSIGNEE_DEFECT")

    # Polling
    POLLING_ENABLED: bool = os.getenv("POLLING_ENABLED", "true").lower() == "true"
    POLL_INTERVAL_SECONDS: int = int(os.getenv("POLL_INTERVAL_SECONDS", "120"))

    # Polling - 批处理模式
    POLLING_INTERVAL_SECONDS: int = int(os.getenv("POLLING_INTERVAL_SECONDS", "60"))
    ROOM_BATCH_WINDOW_SECONDS: int = int(os.getenv("ROOM_BATCH_WINDOW_SECONDS", "300"))
    ROOM_COOLDOWN_SECONDS: int = int(os.getenv("ROOM_COOLDOWN_SECONDS", "600"))
    ROOM_MIN_MESSAGES_FOR_ANALYZE: int = int(os.getenv("ROOM_MIN_MESSAGES_FOR_ANALYZE", "5"))
    # 原始消息阈值（所有 text 消息，含噪音），解决高过滤率群聊无法触发分析的问题
    ROOM_RAW_MIN_MESSAGES_FOR_ANALYZE: int = int(os.getenv("ROOM_RAW_MIN_MESSAGES_FOR_ANALYZE", "15"))
    HIGH_RISK_KEYWORDS: str = os.getenv(
        "HIGH_RISK_KEYWORDS",
        "崩溃,白屏,闪退,数据丢失,投诉,退款,卡死,黑屏,死机,紧急,完全不能用",
    )
    # 高风险关键词触发的最少消息数（避免单条消息就触发分析）
    HIGH_RISK_MIN_MESSAGES: int = int(os.getenv("HIGH_RISK_MIN_MESSAGES", "3"))
    # 上下文聚合时查询的历史消息数量
    CONTEXT_HISTORY_COUNT: int = int(os.getenv("CONTEXT_HISTORY_COUNT", "50"))
    # 群组过滤：包含这些 sender 的群组将被整体排除（逗号分隔）
    EXCLUDE_SENDERS: str = os.getenv("EXCLUDE_SENDERS", "XiaoSuanYunZhuShou1")
    # 排除群列表缓存 TTL（秒），避免每分钟全表扫描
    EXCLUDE_ROOMS_CACHE_TTL_SECONDS: int = int(os.getenv("EXCLUDE_ROOMS_CACHE_TTL_SECONDS", "600"))
    # 每个群聊单次拉取的最大消息数
    ROOM_FETCH_LIMIT: int = int(os.getenv("ROOM_FETCH_LIMIT", "50"))
    # 每轮最多处理的群聊数（避免单轮耗时过长）
    MAX_ROOMS_PER_ROUND: int = int(os.getenv("MAX_ROOMS_PER_ROUND", "20"))
    # 每日周期起始小时（24小时制），默认9点开始新周期
    DAILY_CYCLE_START_HOUR: int = int(os.getenv("DAILY_CYCLE_START_HOUR", "9"))
    # 问题去重相似度阈值（0-1），超过此阈值认为是重复问题
    ISSUE_DEDUP_SIMILARITY_THRESHOLD: float = float(os.getenv("ISSUE_DEDUP_SIMILARITY_THRESHOLD", "0.7"))
    # 是否开启全局去重（跨群聊），适合小规模场景
    ISSUE_DEDUP_GLOBAL: bool = os.getenv("ISSUE_DEDUP_GLOBAL", "true").lower() == "true"
    # 全局去重时间窗口（天），在此天数内相同问题不重复建单
    ISSUE_DEDUP_DAYS: int = int(os.getenv("ISSUE_DEDUP_DAYS", "7"))
    # 是否启用 LLM 预判断（在完整分析前判断消息是否包含有效问题）
    PRE_JUDGE_ENABLED: bool = os.getenv("PRE_JUDGE_ENABLED", "true").lower() == "true"

    # 周期结束兜底分析配置（在每日周期结束前对未达阈值的群聊进行分析）
    END_OF_CYCLE_ANALYSIS_ENABLED: bool = os.getenv("END_OF_CYCLE_ANALYSIS_ENABLED", "true").lower() == "true"
    END_OF_CYCLE_ANALYSIS_HOUR: int = int(os.getenv("END_OF_CYCLE_ANALYSIS_HOUR", "8"))
    END_OF_CYCLE_ANALYSIS_MINUTE: int = int(os.getenv("END_OF_CYCLE_ANALYSIS_MINUTE", "30"))
    END_OF_CYCLE_MIN_MESSAGES: int = int(os.getenv("END_OF_CYCLE_MIN_MESSAGES", "2"))

    # Reply strategy
    AUTO_REPLY_ENABLED: bool = os.getenv("AUTO_REPLY_ENABLED", "true").lower() == "true"
    AUTO_REPLY_MAX_RISK: int = int(os.getenv("AUTO_REPLY_MAX_RISK", "50"))

    # Issue filtering
    PROCESS_ONLY_HARD: bool = os.getenv("PROCESS_ONLY_HARD", "true").lower() == "true"
    HARD_MIN_SEVERITY: str = os.getenv("HARD_MIN_SEVERITY", "S2")
    HARD_KEYWORDS: str = os.getenv(
        "HARD_KEYWORDS",
        "没解决,无法解决,解决不了,反复,一直,多次,长期,升级,影响业务,阻塞,卡住,紧急,严重",
    )

    # FAQ auto generation
    AUTO_FAQ_ENABLED: bool = os.getenv("AUTO_FAQ_ENABLED", "true").lower() == "true"
    AUTO_FAQ_MIN_GROUP: int = int(os.getenv("AUTO_FAQ_MIN_GROUP", "3"))
    AUTO_FAQ_MAX_GROUPS: int = int(os.getenv("AUTO_FAQ_MAX_GROUPS", "5"))

    # Alert dedup/escalation（默认7天，让工单去重主导，适合小规模场景）
    ALERT_DEDUP_P0_SECONDS: int = int(os.getenv("ALERT_DEDUP_P0_SECONDS", "604800"))  # 7天
    ALERT_DEDUP_P1_SECONDS: int = int(os.getenv("ALERT_DEDUP_P1_SECONDS", "604800"))  # 7天
    ALERT_DEDUP_P2_SECONDS: int = int(os.getenv("ALERT_DEDUP_P2_SECONDS", "604800"))  # 7天
    ALERT_SUMMARY_LEN: int = int(os.getenv("ALERT_SUMMARY_LEN", "200"))
    ISSUE_SUMMARY_LEN: int = int(os.getenv("ISSUE_SUMMARY_LEN", "300"))
    ALERT_MIN_HITS_TO_SEND: int = int(os.getenv("ALERT_MIN_HITS_TO_SEND", "1"))
    ALERT_AGGREGATE_LIMIT: int = int(os.getenv("ALERT_AGGREGATE_LIMIT", "5"))

    # Data clean
    SANITIZE_ENABLED: bool = os.getenv("SANITIZE_ENABLED", "false").lower() == "true"

    # 半日高频复盘模式配置
    HALF_DAY_REVIEW_ENABLED: bool = os.getenv("HALF_DAY_REVIEW_ENABLED", "true").lower() == "true"
    HALF_DAY_REVIEW_INTERVAL_HOURS: int = int(os.getenv("HALF_DAY_REVIEW_INTERVAL_HOURS", "12"))
    HALF_DAY_REVIEW_SCHEDULE: str = os.getenv("HALF_DAY_REVIEW_SCHEDULE", "08:00,20:00")  # 每日执行时间点
    HALF_DAY_REVIEW_MIN_MESSAGES: int = int(os.getenv("HALF_DAY_REVIEW_MIN_MESSAGES", "1"))  # 最少消息数才触发
    HALF_DAY_REVIEW_HIGH_RISK_THRESHOLD: int = int(os.getenv("HALF_DAY_REVIEW_HIGH_RISK_THRESHOLD", "70"))  # 高风险阈值
    HALF_DAY_REVIEW_SUMMARY_MAX_LEN: int = int(os.getenv("HALF_DAY_REVIEW_SUMMARY_MAX_LEN", "100"))  # 摘要最大长度

    # 定时报表配置（日报/周报/月报）
    REPORT_ENABLED: bool = os.getenv("REPORT_ENABLED", "true").lower() == "true"
    DAILY_REPORT_ENABLED: bool = os.getenv("DAILY_REPORT_ENABLED", "true").lower() == "true"
    WEEKLY_REPORT_ENABLED: bool = os.getenv("WEEKLY_REPORT_ENABLED", "true").lower() == "true"
    MONTHLY_REPORT_ENABLED: bool = os.getenv("MONTHLY_REPORT_ENABLED", "true").lower() == "true"
    REPORT_SEND_HOUR: int = int(os.getenv("REPORT_SEND_HOUR", "9"))  # 报表发送时间（小时）
    REPORT_DINGTALK_WEBHOOK: str = os.getenv("REPORT_DINGTALK_WEBHOOK", "")  # 可选：独立的报表钉钉 Webhook

    # Custom fields (comma-separated customfieldId list)
    CUSTOM_FIELDS_IDS: str = os.getenv("CUSTOM_FIELDS_IDS", "")
    CUSTOM_FIELDS_URL_TEMPLATE: str | None = os.getenv("CUSTOM_FIELDS_URL_TEMPLATE")
    CUSTOM_FIELDS_MAPPING_PATH: str = os.getenv("CUSTOM_FIELDS_MAPPING_PATH", "customfield_mapping.json")
    CUSTOM_FIELDS_DICT_PATH: str = os.getenv("CUSTOM_FIELDS_DICT_PATH", "customfield_dict.json")
    CUSTOM_FIELDS_CHOICE_MAP_PATH: str = os.getenv("CUSTOM_FIELDS_CHOICE_MAP_PATH", "customfield_choice_map.json")
    TB_ACCESS_TOKEN: str | None = os.getenv("TB_ACCESS_TOKEN")
    TB_COOKIE: str | None = os.getenv("TB_COOKIE")

    # OAPI (appToken-based)
    TB_APP_ID: str | None = os.getenv("TB_APP_ID")
    TB_APP_SECRET: str | None = os.getenv("TB_APP_SECRET")
    TB_TENANT_ID: str | None = os.getenv("TB_TENANT_ID")
    TB_OPERATOR_ID: str | None = os.getenv("TB_OPERATOR_ID")
    TB_APP_TOKEN_URL: str | None = os.getenv("TB_APP_TOKEN_URL")
    TB_APP_TOKEN: str | None = os.getenv("TB_APP_TOKEN") or os.getenv("appToken")
    TB_OAPI_BASE_URL: str = os.getenv("TB_OAPI_BASE_URL", "https://open.teambition.com/api")
    TB_PROJECT_ID: str | None = os.getenv("TB_PROJECT_ID")
    TB_REDIRECT_URI: str | None = os.getenv("TB_REDIRECT_URI")
    TB_SFC_ID: str | None = os.getenv("TB_SFC_ID")
    TEAMBITION_DEFAULT_TASKFLOWSTATUS_ID: str | None = os.getenv("TEAMBITION_DEFAULT_TASKFLOWSTATUS_ID")

    @property
    def DATABASE_URL(self) -> str:
        if self.DB_URL:
            return self.DB_URL
        return f"postgresql://{self.DB_USER}:{self.DB_PASS}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"

    class Config:
        env_file = ".env"


settings = Settings()