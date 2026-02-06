# wecom-ai-platform
这是一个会话内容AI分析平台
## 会话内容 AI 分析平台（wecom-ai-platform）设计文档

## 1. 目标、范围与核心原则

### 1.1 建设背景
企业微信并非人人都在群，消息经“传话”后容易产生理解偏差；同时业务需要对客户对话做结构化分析，用于：
- 故障/事故追溯
- 跨部门沟通问题定位
- 售后沟通质检
- 运维异常关联分析
- 项目推进过程沉淀
- 团队知识沉淀（FAQ/流程提取）

### 1.2 平台目标（以当前工程可运行形态为准）
平台以 **“采集归档 → 噪音/难题过滤 → 分类与风险评估 → 去重/聚合推送 → 人机协作建单 → 知识沉淀（RAG/FAQ）”** 为主线，满足：
- **只抓“没解决/难解决/影响业务”的对话**，避免告警噪音
- **问题分类**：业务分发四类（使用咨询/问题反馈/产品需求/产品缺陷）+ 技术分类（L1/L2）+ 短标签（2–6 字）
- **严重问题及时提醒**：钉钉 actionCard 推送 + 去重/升级策略
- **客户原声可追溯**：内网页面查看群列表与对话气泡（类微信样式）
- **工单标准化**：点击钉钉按钮后自动建单（MCP）并回写工单链接
- **知识沉淀**：向量库/FAQ 知识库可持续增长，为建议回复与后续自动回复提供依据

### 1.3 核心原则（上线后最稳）
- **规则优先，模型兜底**：分类/过滤尽量规则化；模型负责补全“现象/关键句/方案/话术”
- **低噪音优先**：宁可少推送，也不要“hi/嗯/怎么换”这种无意义告警
- **人机协作**：建单必须通过“确认建单”按钮触发（避免垃圾工单）
- **内网可用**：所有按钮跳转使用 `INTERNAL_BASE_URL`（示例 `http://192.168.10.188:8000`）

---

## 2. 总体架构（L0–L3）

### 2.1 分层
- **L0 数据与存储**
  - PostgreSQL：保存原始消息、分析结果、告警事件、草稿、FAQ、游标、群名映射等
  - Chroma（本地持久化）：向量库（历史对话/问题）+ 知识库（FAQ）
- **L1 业务服务**
  - 采集轮询、数据清洗、难题过滤、告警策略（去重/聚合/升级）、RAG 检索、FAQ 生成、工单草稿生成
- **L2 智能体（LLM）**
  - `SentinelAgent`：风险与分类（规则优先 + LLM 兜底），输出结构化字段
  - `AssistantAgent`：生成“现象/关键句/历史原因&方案/AI解决建议/安抚话术”，用于钉钉与工单备注
- **L3 接口与交互**
  - FastAPI：采集入口、UI、草稿操作、Taxonomy 管理、群名/负责人映射
  - 钉钉机器人：告警卡片（actionCard）+ 按钮（查看详情/指派/忽略/确认建单）
  - MCP Bridge：接收建单 payload → 调用 DingTalk MCP → 创建 Teambition 任务

### 2.2 服务部署形态（推荐）
- 主服务：`uvicorn main:app --host 0.0.0.0 --port 8000`
- MCP Bridge：`uvicorn mcp_bridge.main:app --host 0.0.0.0 --port 8010`

---

## 3. 关键工作流（重点）

### 3.1 工作流总览（端到端）
以“生产轮询”模式为例（每 2 分钟刷新一次）：

1. **采集/归档（L0）**  
   从 `chat_records` 增量拉取新消息（基于 `ingest_state` 游标），标准化落表到 `wecom_messages`。

2. **噪音过滤（L1）**  
   `DataCleanService.is_noise()` 过滤短句、无意义回复、纯符号等（降低向量库与告警噪音）。

3. **哨兵分析：风险 + 分类（L2）**  
   `SentinelAgent.check_message()` 输出结构化结果（JSON），包含：
   - 风险分 `risk_score`
   - 严重度 `severity`（S1–S4）
   - 技术分类 `category_l1/category_l2`
   - 短标签 `category_short`（2–6 字）
   - 是否疑似缺陷/故障 `is_bug`
   - **问题类型（四选一）** `issue_type`（用于分发）

4. **难题过滤（Hard Issue Gate，L1）**  
   `is_hard_issue()`：只保留“未解决/反复/影响业务/高严重度”等问题；普通咨询直接丢弃，不进入告警与建单。

5. **记录问题（L0）**  
   将问题写入 `issues`（作为资产沉淀与统计基础）。

6. **告警去重/聚合/升级（L1）**  
   `should_send_alert()`：同房间同类问题进入同一 dedup_key，短时间内只推一次；累计 hit_count 达阈值才推送，推送内容使用聚合摘要（减少报告数量）。

7. **生成“可读推送内容”（L2 + L1）**  
   - 从向量库检索历史相似 FAQ/问题（RAG）
   - `AssistantAgent` 生成“现象/关键句/历史方案/AI解决建议/安抚话术”
   - 按产品模板排版为钉钉 actionCard（包含按钮）

8. **钉钉推送（L3）**  
   发送 actionCard，包含：
   - 查看详情（客户原声）
   - 指派处理
   - 忽略
   - 确认建单（仅在需要建单的场景出现）

9. **建单协作闭环（点击按钮触发，L3→MCP→Teambition）**  
   用户在钉钉点击“确认建单”：
   - 主服务 `/api/v1/tickets/{draft_id}/mcp_request` 生成建单 payload，并**确保 AI 字段完整**
   - 主服务调用 `MCP_BRIDGE_URL`（Bridge）
   - Bridge 调用 DingTalk MCP 网关创建任务（Teambition）
   - 主服务回写 `ticket_drafts.teambition_ticket_id` 并推送“工单已创建”消息（含链接）

10. **知识沉淀（L0 + 向量库）**  
   - `issues` 持久化为问题资产
   - `faq_service` 可从历史 `issues` 聚合生成 FAQ（`faq_items`），并写入知识库向量库

---

### 3.2 轮询采集工作流（polling_service）
触发点：服务启动后台任务，每 `POLL_INTERVAL_SECONDS`（默认 120s）执行一次。

**处理链路（简化版）**：
- 拉取增量 chat_records（可选：wecom archive 代理）
- 写 `wecom_messages`
- 噪音过滤
- Sentinel 分析（风险/分类/问题类型）
- Hard Issue Gate
- 写 `issues`
- 创建 `ticket_drafts`（仅故障/高严重度）
- 告警策略判断
- RAG + Assistant 生成推送内容
- 钉钉推送 actionCard

### 3.3 API 注入工作流（ingest_message）
`POST /api/v1/ingest/message`：用于测试/模拟输入，逻辑与轮询一致（主要差异：数据来源是 API，而非 chat_records）。

---

## 4. 分类（Taxonomy）设计（字段/枚举/策略）

### 4.1 业务分发分类（四选一：issue_type）
用于“推给哪类人处理”：
- 使用咨询
- 问题反馈
- 产品需求
- 产品缺陷

**策略**：
- 规则优先：关键词、句式、意图触发
- LLM 兜底：规则无法命中时输出四选一

### 4.2 技术分类（category_l1 / category_l2）
用于治理聚合与统计（例如 STREAMING/PUSH_FAIL、PLAYBACK/BLACK_SCREEN 等）。

**策略**：
- taxonomy.yaml 规则优先（可控、稳定）
- LLM 兜底（输出必须落在 taxonomy tree，否则回落 OTHER/OTHER）

### 4.3 短标签（category_short，2–6 字）
用于钉钉/报表“人类可读”，示例：推流失败/白屏/鉴权/掉线/黑屏。

**规范**：
- 2–6 个中文字符，避免英文/机器码
- 不能是“其他/未知”（除非真的无法判断）

---

## 5. 告警策略（阈值、去重、聚合、升级）

### 5.1 推送前置条件
必须同时满足：
- `is_hard_issue == True`（难题/未解决/高严重）
- `should_send_alert == True`（去重/聚合策略允许）

### 5.2 去重与同类命中
- 同类 key：`room_id + category_l1 + category_l2 (+ alert_level)`
- 窗口：`ALERT_DEDUP_P0_SECONDS / P1 / P2`
- 触发：`hit_count >= ALERT_MIN_HITS_TO_SEND` 或更高风险/严重度直接推送

### 5.3 聚合摘要
推送不展示单条碎片，使用聚合摘要（最近 N 条同类关键摘要）提升“凝聚性”，减少推送数量。

---

## 6. 钉钉机器人（字段、卡片、按钮、状态机）

### 6.1 卡片字段（推送模板）
推送结构固定为：
- `### 问题类型：{issue_type}`（醒目标题行）
- **风险评估**：评分/等级/短分类/负责人
- **客户原声摘要**：现象 + 关键句（关键句必须换行）
- **AI 智能辅助**：
  - 历史类似案例（次数 + 原因 + 方案）
  - AI 辅助（AI解决方法 + 建议安抚话术）
- **底部**（换行显示）：
  - `🏠 客户群 + 查看详情链接`（客户原声）
  - `🧾 提单`（未建单/已建单 + 链接）
  - `🧾 草稿ID`

### 6.2 按钮（actionCard）
- 查看详情：打开客户原声页面（不要删除）
- 指派处理：更新草稿 assigned_to
- 忽略：标记草稿 ignored
- 确认建单：触发 MCP 建单（建单成功后按钮显示“已完成”并跳工单链接）

### 6.3 草稿状态机（ticket_drafts.status）
- `draft`：草稿生成
- `ignored`：忽略
- `ticketed`：已建单（teambition_ticket_id 有值）

---

## 7. Teambition 建单（MCP 模式）与“列表视图”

### 7.1 为什么用 MCP
Teambition token 方式易过期且权限复杂；MCP 可直接复用钉钉体系授权，稳定性更好。

### 7.2 建单内容来源
建单备注（note）直接使用“钉钉推送 Markdown”（即人类可读的完整字段），避免出现：
- 问题概括=原始短句
- 现象/关键句/方案为空

### 7.3 如何进入你想要的“需求列表”
Teambition 是“项目 + 任务类型（scenariofieldconfigId）”决定落在哪个列表。

配置项：
- `TEAMBITION_PROJECT_ID`：目标项目
- `TEAMBITION_TASK_TYPE_ID`：任务类型 ID（需求/缺陷/用户反馈等）

---

## 8. 向量库与知识库：是否有用、为什么存在

### 8.1 现在在流程中起作用的点
- 告警推送前会先做 RAG：优先检索 FAQ，再检索历史问题，提供“历史方案/建议回复/解决建议”的依据
- FAQ 自动生成会把高频问题沉淀为结构化 Q/A，未来可直接用于低风险自动回复

### 8.2 落盘目录（项目根目录）
- `D:\Coze\wecom-ai-platform\vector_store`：历史问题/对话向量
- `D:\Coze\wecom-ai-platform\knowledge_base`：FAQ 知识库向量

---

## 9. 数据模型（表结构）

### 9.1 外部表（chat_records）
用于承接企微归档/代理落库的数据源。

### 9.2 平台核心表（自建）
- `wecom_messages`：标准化原始消息
- `issues`：问题记录（分类、风险、证据链）
- `alert_events`：告警去重/命中/窗口
- `ticket_drafts`：工单草稿（字段完整、支持按钮操作、回写 teambition_ticket_id）
- `faq_items`：FAQ（问/答/来源 issue_id 列表）
- `ingest_state`：采集游标（source → last_msgtime/seq）
- `room_info`：群名映射
- `room_assignees`：群→负责人映射

---

## 10. 接口设计（关键 API）

### 10.1 采集与分析
- `POST /api/v1/ingest/message`：手动注入消息（测试）

### 10.2 客户原声（内网 UI）
- `GET /api/ui/rooms`：群列表
- `GET /api/ui/rooms/{room_id}`：对话气泡页面（客户原声）

### 10.3 草稿操作
- `GET /api/v1/tickets/{draft_id}/ignore`
- `GET /api/v1/tickets/{draft_id}/assign`
- `GET /api/v1/tickets/{draft_id}/mcp_request`：点击按钮后建单（MCP）

### 10.4 Taxonomy 管理
- `GET /api/v1/taxonomy`
- `PUT /api/v1/taxonomy`

---

## 11. 配置（.env）说明（关键项）

### 11.1 内网跳转
- `INTERNAL_BASE_URL=http://192.168.10.188:8000`

### 11.2 钉钉机器人
- `DINGTALK_WEBHOOK`
- `DINGTALK_SECRET`

### 11.3 MCP Bridge
- 主服务：`MCP_BRIDGE_URL=http://127.0.0.1:8010/mcp/teambition/create`
- Bridge：`MCP_GATEWAY_URL` / `MCP_TOOL_NAME=create_task` / `MCP_TIMEOUT`

### 11.4 Teambition（列表视图决定项）
- `TEAMBITION_PROJECT_ID`
- `TEAMBITION_TASK_TYPE_ID`（需求/缺陷/用户反馈等）

---

## 12. 已实现 / 待补充（以“上线可用”为准）

### 12.1 已实现
- ✅ 轮询采集链路（chat_records 增量 → 分析 → 推送）
- ✅ 噪音过滤 + Hard Issue Gate（只抓难题）
- ✅ taxonomy：规则优先 + LLM 兜底 + 短标签
- ✅ 告警去重/聚合/命中阈值（稳住生产噪音）
- ✅ 钉钉 actionCard 模板与按钮（查看详情保留）
- ✅ 内网客户原声页面（群列表 + 气泡）
- ✅ MCP Bridge 建单（点击确认建单 → 自动建单 → 回写 ID/链接）
- ✅ 向量库/知识库落盘 + RAG 检索 + FAQ 生成接口

### 12.2 待补充（建议按优先级）
- ⏳ 真正接入企微消息存档 SDK（解密与合规字段）
- ⏳ 企微自动回复（低风险自动回，高风险给建议）
- ⏳ 发送者角色识别（客户/内部）用于左右对话更精准
- ⏳ FAQ 人工审核（human-in-the-loop）
- ⏳ 更细的升级策略（跨群聚合、hit_count 阶梯升级）

---

## 13. 测试与验收（推荐路径）
- 注入测试：`POST /api/v1/ingest/message`
- 告警噪音测试：同类问题重复多条，验证只在阈值触发后推送
- 建单测试：钉钉点“确认建单”，验证 Teambition 任务类型落在正确列表（需求/缺陷等）
- 原声测试：钉钉“查看详情”必须可打开（客户原声页面）

