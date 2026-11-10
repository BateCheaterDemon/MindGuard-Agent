# MindGuard v1 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建学生自助心理聊天网站第一版:文字流式对话 + 三层管线(意图/RAG/风险)+ Excel 存档 + 高危邮件预警辅导员,可在 16 GB 显存机器复现部署。

**Architecture:** 四个进程一个 conda 环境:vLLM 服务 cpsy 模型(仅本机)、FastAPI 编排器(托管前端 + SSE)、MCP 工具服务器(Excel/邮件,stdio)、内嵌 Chroma(bge-m3 向量)。后端对每条消息执行 意图判断 → RAG 决策/检索 → 流式生成 → 风险判断 → MCP 副作用。

**Tech Stack:** Python 3.11 / FastAPI / vLLM 0.29.0(OpenAI 兼容 HTTP)/ chromadb / FastMCP / openpyxl / smtplib / 原生 HTML+CSS+JS(无构建链)/ SQLite / pytest。

**Spec:** `docs/superpowers/specs/2026-11-10-mindguard-v1-design.md`(计划从其取数,执行者两个都读)

## Global Constraints

- conda 环境 `vllm_deployment`(Python 3.11);新增依赖不得与现有 pin 冲突(torch 2.13.0 cu130、transformers 5.10.4);安装后必须验证 `import transformers, vllm, chromadb` 三者共存。
- 模型固定 `Qwen3.5-4B-cpsy-W4A16`,由 mini_vllm `scripts/serve.py` 启动(`--reasoning-parser qwen3`,默认 127.0.0.1:8000,预算 8 GiB);LLM 调用用 requests 手写 HTTP+SSE,不引入 LLM SDK。
- 模型下载走 ModelScope(bge-m3 id 为 `BAAI/bge-m3`),不走 HuggingFace。
- 显存目标 ≤16 GiB:LLM 8 + bge-m3 ~2.3 + 余量;`check_vram.py` 负责预检与降级提示。
- 风险分级 0-3,只有 3 级发预警邮件;判定失败进复核队列,不自动发邮件;高危词表只强制升级评估,不直接触发。
- RAG:top-k=4,相似度阈值 0.35(低于不引用);块 400-800 字、重叠 80-120 字、标题感知。
- Excel 按日分文件 `data/archives/YYYY-MM-DD.xlsx`,列固定:会话ID|开始时间|更新时间|学号|姓名|消息数|意图|最高风险等级|风险摘要|完整对话|是否已预警|预警时间。
- 会话超时 30 分钟;风险上下文 = 当前消息 + 回复 + 最近 6 轮。
- SSE 事件协议:`delta`{text} / `risk_done`{risk_level,archived,alerted} / `done`{archived} / `error`{message}。
- 代码注释与 UI 文案用中文;提交信息结尾附 `Co-Authored-By: Claude Code <noreply@anthropic.com>`。
- 不提交 `data/`、`logs/`、模型权重;.gitignore 覆盖。

## Review Focus

1. 高危词表命中但意图判定为 casual_chat → 必须仍进入风险层(Task 6:`test_keyword_forces_risk_eval`)
2. 风险层判定失败(超时/解析失败)→ 存档 + 复核队列,绝不静默、绝不自动发邮件(Task 6:`test_risk_failure_goes_to_review_queue`)
3. 纯闲聊消息 → 不写 Excel、不发邮件、不评估风险(Task 6:`test_casual_chat_skips_archive_and_risk`)
4. 检索相似度低于阈值 → 不注入上下文,生成仍正常(Task 3:`test_search_below_threshold_returns_empty`)
5. 同日重复写入 Excel → 追加行,不覆盖历史;并发写不损坏文件(Task 7:`test_append_twice_keeps_both_rows`、`test_concurrent_appends`)
6. SMTP 发送失败 → 重试 3 次仍失败则复核队列标记"邮件发送失败"(Task 6:`test_alert_email_failure_flags_review`)
7. 生成阶段 LLM 失败 → 流式 error 事件 + 风险层仍按用户消息评估(Task 6:`test_generation_failure_still_risks`)

---

### Task 1: 项目骨架与配置

**Files:**
- Create: `.gitignore`, `requirements.txt`, `backend/app/__init__.py`, `backend/app/config.py`, `backend/app/schemas.py`, `backend/app/main.py`, `backend/tests/test_config.py`, `backend/tests/test_main.py`, `backend/conftest.py`
- 目录:`backend/app/`, `backend/tests/`, `mcp_server/`, `knowledge/raw/`, `frontend/`, `scripts/`, `data/`, `logs/`

**Interfaces:**
- Produces(后续任务全部依赖):
  - `config.load_config(path: Path | None = None) -> Config`;`Config` dataclass 字段:host="0.0.0.0", port=8080, vllm_base_url="http://127.0.0.1:8000/v1", model_name="Qwen3.5-4B-cpsy-W4A16", chroma_dir, archives_dir, db_path, log_dir(均 Path,默认 data/ 下), embedding_model="bge-m3", embedding_device="auto", rag_top_k=4, rag_threshold=0.35, session_timeout_minutes=30, risk_window_turns=6, rate_limit_per_minute=20, admin_password(必填,读 env `MINDGUARD_ADMIN_PASSWORD`), alert_email_to, smtp: SmtpConfig, high_risk_keywords: list[str]。
  - `SmtpConfig` dataclass:host, port, username, password, from_addr, use_ssl。
  - `schemas.py`:INTENT_SCHEMA / RAG_SCHEMA / RISK_SCHEMA(guided_json 用的 JSON Schema dict,属性名与类型照 spec §5.2);`IntentResult(intent:str, emotion:str, brief:str)`、`RagDecision(need_kb:bool, query:str)`、`RiskResult(risk_level:int, risk_type:str, evidence:str, summary:str)`、`Message(role:str, content:str, created_at:str)`、`ArchiveRecord(session_id, started_at, updated_at, student_id, name, message_count, intent, max_risk_level, risk_summary, conversation, alerted, alerted_at)`、`AlertPayload(session_id, student_id, name, risk_level, risk_type, evidence, summary, conversation, created_at)`、`Archiver` Protocol(两个异步方法:`write_archive(record: ArchiveRecord) -> str`、`send_alert_email(alert: AlertPayload) -> bool`)。
  - `main.py`:`create_app() -> FastAPI`,仅含 `GET /api/health` 返回 `{"ok": true}`。

- [ ] **Step 1: 写失败测试**

`backend/tests/test_config.py::test_load_config_defaults` — env 里设 `MINDGUARD_ADMIN_PASSWORD`,断言 Config 默认值(model_name、rag_top_k、rag_threshold、session_timeout_minutes、risk_window_turns 等与 Global Constraints 一致);`test_missing_admin_password_raises` — 未设 env 时 `load_config()` 抛 RuntimeError。
`backend/tests/test_main.py::test_health_returns_ok` — `TestClient(create_app()).get("/api/health")` 断言 200 且 json `{"ok": true}`。

- [ ] **Step 2: 运行测试确认失败**

Run: `conda run -n vllm_deployment python -m pytest backend/tests -x -q`
Expected: FAIL(ImportError: no module named app/config)

- [ ] **Step 3: 实现骨架**

`config.py`:dataclass + `load_config()`,env 覆盖 + 可选 YAML 合并(仅一层,无外部依赖);`schemas.py` 三个 JSON Schema 与数据类按 Interfaces 定义;`main.py` 最小 FastAPI 应用。目录与 `.gitignore`(data/、logs/、*.xlsx、__pycache__/)按 Files 建齐。

- [ ] **Step 4: 安装依赖并跑测试**

```bash
conda run -n vllm_deployment pip install -r requirements.txt
conda run -n vllm_deployment python -c "import transformers, vllm, chromadb, fastapi, fastmcp, openpyxl; print('ok')"
conda run -n vllm_deployment python -m pytest backend/tests -q
```
Expected: 三者共存导入 ok;测试 PASS。若 chromadb 与 transformers 的 tokenizers pin 冲突(导入报错),停止并报告——按 spec §7 走 Chroma 独立进程降级方案,先不硬装。

- [ ] **Step 5: 提交**

```bash
cd /mnt/quwenxi/MindGuard && git init && git add -A && git commit -m "chore: MindGuard 项目骨架与配置"
```

---

### Task 2: 知识切分器

**Files:**
- Create: `knowledge/chunker.py`, `knowledge/tests/test_chunker.py`

**Interfaces:**
- Produces:
  - `Chunk(text: str, section: str, source: str, category: str)` dataclass
  - `chunk_markdown(text: str, *, source: str, category: str, max_chars: int = 600, overlap_chars: int = 100) -> list[Chunk]`
  - `parse_frontmatter(text: str) -> dict`(手写解析 `---` 块,键 title/source/category)
  - `iter_md_files(docs_dir: Path) -> Iterator[tuple[Path, dict]]`(frontmatter 缺失抛 ValueError)

- [ ] **Step 1: 写失败测试**

`test_chunker.py`:
- `test_heading_split`:带两个 `##` 小节的 md → 至少 2 块,块的 `section` 等于各自小节标题
- `test_long_block_splits_at_sentence_boundary`:单个 `##` 下 2000 字 → 每块 ≤ max_chars,断点在句号/换行处(断言相邻块衔接文本无截断词)
- `test_overlap`:相邻两块的后缀/前缀按 overlap_chars 重叠(断言第二块开头包含第一块结尾的 overlap 文本)
- `test_metadata`:每块 source/category 与传入值一致
- `test_frontmatter_parse`:标准 frontmatter 解析出 title/source/category;缺 frontmatter 抛 ValueError

- [ ] **Step 2: 运行测试确认失败**

Run: `conda run -n vllm_deployment python -m pytest knowledge/tests -q`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 实现 `chunker.py`**

标题感知切分:按 `##` 行切成节;节内超 max_chars 时按句边界(。!?;换行)续切;每块前补 overlap_chars 的前块尾文本(仅当前节内);section 取小节标题(无标题用"开头"或文件标题)。

- [ ] **Step 4: 运行测试确认通过**

Run: `conda run -n vllm_deployment python -m pytest knowledge/tests -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add knowledge/chunker.py knowledge/tests && git commit -m "feat: 标题感知的 markdown 知识切分器"
```

---

### Task 3: 知识库内容与 Chroma 建库

**Files:**
- Create: `knowledge/raw/*.md`(≥15 篇,见 Step 3), `knowledge/validate_docs.py`, `knowledge/build_chroma.py`, `backend/app/rag.py`, `backend/tests/test_rag.py`

**Interfaces:**
- Consumes: Task 2 的 `Chunk/chunk_markdown/iter_md_files`
- Produces:
  - `backend/app/rag.py`:
    - `Embedder` Protocol:`embed(texts: list[str]) -> list[list[float]]`
    - `TransformersEmbedder(model_name: str, device: str = "auto")`,含 `embed()`(mean pooling + L2 归一化,经 ModelScope 下载/加载本地权重)
    - `FakeEmbedder(dim: int = 128)`(确定性:同文本同向量,不同文本不同向量,供测试)
    - `RetrievedChunk(source, section, category, text, score)` dataclass
    - `RagIndex(persist_dir: Path, embedder: Embedder, collection: str = "psych_kb")`,方法 `add_documents(chunks: list[Chunk]) -> int`(按 source+section 幂等 upsert)、`search(query: str, k: int = 4, threshold: float = 0.35) -> list[RetrievedChunk]`(score = 1 - cosine 距离,低于阈值过滤,同 source 去重)

- [ ] **Step 1: 写失败测试(RagIndex,用 FakeEmbedder + chromadb EphemeralClient)**

`backend/tests/test_rag.py`:
- `test_add_and_search_returns_chunk`:入 5 块,查相关 query → 命中含元数据且 score ≥ threshold
- `test_search_below_threshold_returns_empty`(Review Focus #4):embedder 用 FakeEmbedder,查询与库内容无关的文本 → 返回 []
- `test_dedup_by_source`:同一 source 多块命中 → 每个 source 至多 1 条

- [ ] **Step 2: 运行测试确认失败**

Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 整理知识内容并实现 rag.py**

内容:用 WebSearch 从公开权威资源(国家/高校心理援助与危机干预指南、高校心理健康科普、大学生常见困扰科普)整理 markdown 文档,`knowledge/raw/` 下,类别限 5 类:情绪管理、人际交往、学业压力、常见心理困扰、危机干预与求助;每类 ≥3 篇、每篇 300-1500 字、frontmatter(title/source/category)、正文带 `##` 小节。写 `validate_docs.py`(检查上述全部约束,退出码 0/1)。
实现 `rag.py`:`TransformersEmbedder` 用 transformers `AutoModel/AutoTokenizer`(模型经 ModelScope 缓存,`embedding_device="auto"` 时 GPU 可用用 GPU);`RagIndex` 用 chromadb `PersistentClient`/`EphemeralClient`(测试时注入 ephemeral)。
写 `build_chroma.py`:`--docs-dir knowledge/raw --persist-dir data/chroma --embed bge-m3 --device auto`;流程 = iter_md_files → chunk_markdown → RagIndex.add_documents → 打印入库块数与耗时;幂等可重跑。

- [ ] **Step 4: 跑单测 + 建库 + 真实检索冒烟**

```bash
conda run -n vllm_deployment python -m pytest backend/tests/test_rag.py -q
conda run -n vllm_deployment python knowledge/validate_docs.py
conda run -n vllm_deployment python knowledge/build_chroma.py
conda run -n vllm_deployment python -c "从 RagIndex 检索 '我最近压力很大睡不着怎么办'、'觉得自己没有价值'、'和室友关系很差',打印 top 命中"
```
Expected:单测 PASS;validate 退出码 0;建库输出块数 > 50;三个样例查询各有 ≥1 条 score > 0.35 的命中(低于则扩内容或调阈值,如实记录)。

- [ ] **Step 5: 提交**

```bash
git add knowledge backend/app/rag.py backend/tests/test_rag.py && git commit -m "feat: 公开资源知识库整理 + Chroma 建库与检索"
```

---

### Task 4: SQLite 会话存储

**Files:**
- Create: `backend/app/session_store.py`, `backend/tests/test_session_store.py`

**Interfaces:**
- Consumes: Task 1 的 `Message/RiskResult/ArchiveRecord`
- Produces:
  - `SessionStore(db_path: Path)` 方法:
    - `create_session(student_id: str, name: str) -> str`(空值抛 ValueError,返回 uuid session_id)
    - `get_session(session_id) -> dict | None`、`touch_session(session_id) -> None`
    - `add_message(session_id: str, role: str, content: str) -> None`
    - `recent_messages(session_id: str, turns: int = 6) -> list[Message]`(取最后 turns 条)
    - `full_conversation(session_id: str) -> list[Message]`
    - `close_session(session_id: str) -> ArchiveRecord`(组装 Excel 行字段;conversation 为完整对话文本)
    - `expired_sessions(now: datetime) -> list[str]`(updated_at 超过 30 分钟且未关闭)
    - `record_risk(session_id, risk: RiskResult) -> None`(更新会话 max_risk_level/risk_summary + risk_records 表)
    - `add_review_item(session_id, reason: str) -> None`、`list_review_items(pending_only=True) -> list[dict]`、`resolve_review_item(item_id: int, resolution: str) -> None`
  - 表:sessions / messages / risk_records / review_queue / alerts(字段照 spec §5.6)

- [ ] **Step 1: 写失败测试**

`test_session_store.py`(tmp_path 建库):
- `test_create_and_get_session` / `test_empty_identity_raises`
- `test_recent_messages_returns_last_n`(写 10 条,recent_messages(turns=3) 长度 3 且为最后 3 条)
- `test_close_session_builds_archive_record`(断言 ArchiveRecord 各字段:message_count、conversation 含全部消息、started_at/updated_at)
- `test_expired_sessions`:手动把 updated_at 改为 31 分钟前 → 出现在 expired_sessions,未关闭的 5 分钟前会话不出现;已关闭的 40 分钟前会话不出现
- `test_record_risk_updates_max`:先 2 级后 3 级 → max_risk_level=3;先 3 后 1 → 仍 3
- `test_review_queue_flow`:add → list(pending) 含之 → resolve → list 不含

- [ ] **Step 2: 运行测试确认失败**

Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 实现 `session_store.py`**

标准库 sqlite3,连接每次请求短开(线程安全),建表用 `CREATE TABLE IF NOT EXISTS`;时间统一 ISO 字符串。

- [ ] **Step 4: 运行测试确认通过**

Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/app/session_store.py backend/tests/test_session_store.py && git commit -m "feat: SQLite 会话/消息/风险/复核队列存储"
```

---

### Task 5: LLM 客户端(vLLM HTTP+SSE)

**Files:**
- Create: `backend/app/llm_client.py`, `backend/tests/test_llm_client.py`

**Interfaces:**
- Consumes: Task 1 的 Config
- Produces:
  - `LLMError(Exception)`
  - `LLMClient(base_url: str, model: str, api_key: str | None = None, timeout: float = 120.0)`:
    - `chat_stream(messages: list[dict], temperature: float = 0.7) -> Iterator[str]`(POST `/chat/completions`,stream=True,手解析 SSE `data:` 行,取 `choices[0].delta.content`,遇非 2xx/连接错误抛 LLMError)
    - `structured(messages: list[dict], schema: dict, temperature: float = 0.0) -> dict`(stream=False,`extra_body={"guided_json": schema}`,返回解析后的 dict,解析失败抛 LLMError)
    - `health() -> bool`(GET `/models`,2xx 即 True)

- [ ] **Step 1: 写失败测试(用 httpx MockTransport 模拟 vLLM 响应,不连真实服务)**

`test_llm_client.py`:
- `test_chat_stream_yields_deltas`:mock SSE 响应两段 content + `[DONE]` → 迭代器产出两段文本
- `test_chat_stream_http_error_raises_llmerror`:mock 500 → 抛 LLMError
- `test_structured_sends_guided_json_and_parses`:断言请求体含 `guided_json` 与传入 schema;mock 返回合法 JSON → 解析出 dict
- `test_structured_invalid_json_raises_llmerror`:mock 返回非 JSON → 抛 LLMError
- `test_health_ok`:mock `/models` 200 → True;连接失败 → False

- [ ] **Step 2: 运行测试确认失败**

Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 实现 `llm_client.py`**

requests + 手写 SSE 行解析(参考 mini_vllm `scripts/client.py` 的写法);httpx 用于测试注入传输层(构造参数 `transport` 透传,生产默认 None)。

- [ ] **Step 4: 运行测试确认通过**

Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/app/llm_client.py backend/tests/test_llm_client.py && git commit -m "feat: vLLM OpenAI 兼容客户端(流式 + guided_json 结构化输出)"
```

---

### Task 6: 三层管线(核心编排)

**Files:**
- Create: `backend/app/prompts.py`, `backend/app/pipeline.py`, `backend/tests/test_pipeline.py`(含 FakeLLM/FakeArchiver 测试替身)

**Interfaces:**
- Consumes:Task 1 的 schemas/Config;Task 4 的 SessionStore;Task 5 的 LLMClient;Task 3 的 RagIndex
- Produces:
  - `prompts.py`:
    - `DEFAULT_HIGH_RISK_KEYWORDS: list[str]`(固定小词表:如 "不想活了","自杀","割腕","跳楼","安眠药" 等)
    - `contains_high_risk_keyword(text: str, keywords: list[str]) -> bool`
    - `INTENT_SYSTEM / RAG_SYSTEM / RISK_SYSTEM: str`(RISK_SYSTEM 含 spec §5.2 的 0-3 级判据与"只依据对话证据,不臆测"要求)
    - `build_intent_messages(history: list[Message], user_msg: str) -> list[dict]`
    - `build_rag_messages(history, user_msg) -> list[dict]`
    - `build_reply_messages(history, user_msg, kb_context: list[RetrievedChunk] | None) -> list[dict]`(kb_context 非空时要求只依据资料、不足明说、标注 [n];无资料时要求自然陪伴式回复 + 免责声明)
    - `build_risk_messages(window: list[Message], reply: str) -> list[dict]`
  - `pipeline.py`:
    - `Pipeline(llm: LLMClient, rag: RagIndex | None, store: SessionStore, archiver: Archiver, config: Config)`
    - `async def process(session_id: str, content: str) -> AsyncIterator[dict]` — 事件序:若干 `{"event":"delta","text":…}`;心理流程末尾 `{"event":"risk_done","risk_level":N,"archived":True,"alerted":bool}`;最后 `{"event":"done","archived":bool}`;出错时 `{"event":"error","message":…}` 后再 `done`。
      流程:add_message → 词表扫描(forced)→ intent(structured;失败按 counseling)→ 若 casual_chat 且非 forced:直接流式生成(kb_context=None),不评估风险、不存档,done(archived=False)→ counseling:rag 决策(structured;失败 need_kb=False)→ need_kb 时 `rag.search`(top_k/threshold 取 config)→ 流式生成 → 风险(structured;失败走复核)→ record_risk → `await archiver.write_archive(close_session 的结果)`;3 级再 `await archiver.send_alert_email(...)`(失败 → `add_review_item(session_id,"邮件发送失败")`)。生成阶段 LLMError → yield error 事件,风险层仍按 user_msg + reply="" 评估。

- [ ] **Step 1: 写失败测试(FakeLLM 按调用序返回预置结果;FakeArchiver 记录调用)**

`test_pipeline.py`:
- `test_casual_chat_skips_archive_and_risk`(Review Focus #3):intent=casual_chat → 事件仅 delta+done(archived=False);FakeArchiver 零调用
- `test_keyword_forces_risk_eval`(Review Focus #1):intent=casual_chat 但消息含词表词 → 走风险层,出现 risk_done,FakeArchiver.write_archive 被调用
- `test_counseling_archives_with_risk_done`:intent=counseling,risk=2 → risk_done{risk_level:2, archived:True, alerted:False} + write_archive 一次、send_alert_email 零次
- `test_high_risk_sends_alert`:risk=3 → alerted:True,send_alert_email 一次,AlertPayload 含 student_id/evidence/summary
- `test_risk_failure_goes_to_review_queue`(Review Focus #2):风险 structured 抛 LLMError → write_archive 一次、send_alert_email 零次、store.add_review_item 一次
- `test_intent_failure_treated_as_counseling`:intent 抛 LLMError → 仍走风险层并存档
- `test_alert_email_failure_flags_review`(Review Focus #6):send_alert_email 返回 False → add_review_item("邮件发送失败")
- `test_generation_failure_still_risks`(Review Focus #7):chat_stream 抛 LLMError → 出现 error 事件,且风险层仍执行(risk_done 或复核)
- `test_rag_need_kb_injects_context`:rag 决策 need_kb=True,FakeRag.search 返回 2 块 → build_reply_messages 收到的上下文含 [1][2] 引用格式(通过 FakeLLM 记录的消息断言)

- [ ] **Step 2: 运行测试确认失败**

Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 实现 `prompts.py` 与 `pipeline.py`**

按 Interfaces 的流程与降级规则实现;所有 LLM 调用失败重试一次再降级;`process` 为 async generator,事件 dict 按 Global Constraints 的协议。

- [ ] **Step 4: 运行测试确认通过**

Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/app/prompts.py backend/app/pipeline.py backend/tests/test_pipeline.py && git commit -m "feat: 三层管线编排(意图/RAG/风险)+ 保守降级与词表兜底"
```

---

### Task 7: MCP 工具服务器(Excel + 邮件)

**Files:**
- Create: `mcp_server/excel_writer.py`, `mcp_server/email_sender.py`, `mcp_server/server.py`, `backend/app/mcp_client.py`, `backend/tests/test_mcp_server.py`, `backend/tests/test_mcp_client.py`

**Interfaces:**
- Consumes:Task 1 的 ArchiveRecord/AlertPayload/Archiver Protocol/SmtpConfig
- Produces:
  - `excel_writer.append_archive_row(record: ArchiveRecord, archives_dir: Path) -> str`(目标 `archives_dir/YYYY-MM-DD.xlsx`,无则建表头,有则追加;fcntl 文件锁;返回落盘路径)
  - `email_sender.render_alert_email(alert: AlertPayload) -> tuple[str, str]`(subject 形如 `【MindGuard 高风险预警】学号 {student_id} {name}`;body 含:身份(注明"身份为学生自填,请核实")、时间、风险等级、风险类型、证据摘录、对话摘要、处置建议"请及时核实并联系该学生"、声明"本邮件由系统自动判定生成,请人工核实"、紧急热线)
  - `email_sender.send_alert(smtp: SmtpConfig, alert: AlertPayload) -> None`(重试 3 次退避,仍失败抛 `SmtpError`)
  - `mcp_server/server.py`:FastMCP("mindguard-tools") 两个 tool:`write_archive(record: dict) -> str`、`send_alert_email(payload: dict) -> str`(dict→dataclass 映射后调上面两个模块;返回状态串)
  - `backend/app/mcp_client.py`:`McpTools(server_script: Path, config: Config)` 实现 Archiver 协议(async,stdio transport,启动失败抛 RuntimeError)

- [ ] **Step 1: 写失败测试**

`test_mcp_server.py`:
- `test_new_file_writes_header_and_row`:tmp_path 下写入 → 表头为 Global Constraints 的 12 列,数据行字段对应
- `test_append_twice_keeps_both_rows`(Review Focus #5):同日写两次 → 3 行(表头+2),行序保留
- `test_concurrent_appends`(Review Focus #5):8 线程同写 → 不抛异常,9 行全在
- `test_render_alert_email_contains_required_fields`:subject/body 含学号、姓名、"请人工核实"、"身份为学生自填"、证据摘录
- `test_send_alert_retries_then_raises`:monkeypatch smtplib.SMTP_SSL 持续抛错 → 恰 3 次尝试且抛 SmtpError;一次失败两次成功 → 成功返回
`test_mcp_client.py`:
- `test_mcp_tools_write_archive_roundtrip`:启动测试 MCP server(子进程,stdout 重定向),调 `write_archive` → 返回路径且文件存在;`close()` 干净退出

- [ ] **Step 2: 运行测试确认失败**

Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 实现四个模块**

openpyxl 写 Excel(fcntl 锁);smtplib + SMTP_SSL/SMTP 按 use_ssl 选择;邮件模板字符串放 `email_sender.py`;FastMCP server 脚本入口 `python mcp_server/server.py`(stdio);`McpTools` 用 fastmcp Client + stdio_client 上下文,方法内部 `asyncio.to_thread` 包裹同步调用。

- [ ] **Step 4: 运行测试确认通过**

Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add mcp_server backend/app/mcp_client.py backend/tests/test_mcp_server.py backend/tests/test_mcp_client.py && git commit -m "feat: MCP 工具服务器(Excel 按日归档 + SMTP 预警邮件)与后端客户端"
```

---

### Task 8: API 层与管理端安全

**Files:**
- Create: `backend/app/api.py`, `backend/app/security.py`, `backend/tests/test_api.py`;Modify: `backend/app/main.py`

**Interfaces:**
- Consumes:Task 1/4/5/6/7 的全部产物
- Produces:
  - `api.create_app(config: Config, pipeline: Pipeline, store: SessionStore) -> FastAPI` 挂载:
    - `POST /api/sessions` `{student_id, name}` → 200 `{session_id}`(空值 400)
    - `POST /api/chat` `{session_id, message}` → `text/event-stream`(透传 Pipeline.process 事件;未知 session 400)
    - `POST /api/admin/login` `{password}` → Set-Cookie 管理员 token;`GET/POST /api/admin/*`(archives/review/alerts/config)依赖 require_admin,未登录 401
    - `GET /api/health` → `{ok, vllm, chroma, mcp}`(vllm 用 llm.health();chroma/mcp 状态由 main 注入的探针函数返回)
  - `security.py`:`check_admin_password(plain: str, cfg: Config) -> bool`(hmac.compare_digest)、`issue_admin_token() -> str`(secrets.token_urlsafe(32),内存存储 + 12h 过期)、`require_admin(request)` FastAPI dependency、`RateLimiterMiddleware`(每 IP 每分钟 rate_limit_per_minute 次,超出 429,仅作用于 /api/chat 与 /api/sessions)
  - `main.py` 扩展:`create_app(config)` 真实装配(Pipeline/LLMClient/RagIndex/SessionStore/McpTools/管理员探针)+ 启动时后台任务每 60s 关过期会话并归档 + 挂载 `frontend/` 静态目录(根路径 index.html)

- [ ] **Step 1: 写失败测试(TestClient + FakePipeline/FakeStore)**

`test_api.py`:
- `test_create_session_ok_and_empty_rejected`:正常 200 返回 session_id;空学号 400
- `test_chat_sse_protocol`:FakePipeline 依序 yield delta/done → 响应 `text/event-stream`,事件名与 data 字段符合协议;未知 session_id 400
- `test_chat_rate_limited`:连发 > rate_limit_per_minute 条 → 429
- `test_admin_login_and_guard`:错密码 401;对密码 Set-Cookie;带 cookie 访问 `/api/admin/archives` 200;无 cookie 401
- `test_health_reports_probes`:probe 注入假状态 → json 各字段一致

- [ ] **Step 2: 运行测试确认失败**

Expected: FAIL(ImportError)

- [ ] **Step 3: 实现 `api.py` / `security.py` / 扩展 `main.py`**

SSE 用 `StreamingResponse`(media_type="text/event-stream"),每事件 `event: <name>\ndata: <json>\n\n`;后台归档任务 `asyncio.create_task` 于 lifespan 启动/取消;logging 输出到 `logs/backend.log`,大小轮转(2 MB × 3),预警链路日志含 session_id。

- [ ] **Step 4: 运行测试确认通过**

Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/app/api.py backend/app/security.py backend/app/main.py backend/tests/test_api.py && git commit -m "feat: FastAPI 路由(会话/聊天SSE/管理端)+ 限流与管理员认证"
```

---

### Task 9: 前端(学生端 + 管理端)

**Files:**
- Create: `frontend/index.html`, `frontend/admin.html`, `frontend/css/style.css`, `frontend/js/chat.js`, `frontend/js/admin.js`

**Interfaces:**
- Consumes:Task 8 的 API 与 SSE 协议
- Produces:无代码接口;交付两个页面,由 Task 8 的静态挂载托管

- [ ] **Step 1: 写失败测试**

`backend/tests/test_frontend_serving.py`:`GET /` 200 且内容含 `<title>MindGuard`;`GET /admin.html` 200;`GET /css/style.css` 200 text/css;不存在的路径 404。

- [ ] **Step 2: 运行测试确认失败**

Expected: FAIL(AssertionError: / 非 200,静态目录未挂载)

- [ ] **Step 3: 实现两个页面**

学生端 `index.html`:首次弹窗(告知:"对话将被记录存档;若检测到高风险,辅导员将收到预警并可能联系你" + 勾选同意)→ 学号姓名表单 → 聊天页(消息气泡、SSE 流式渲染、`risk_done` 时显示"对话已记录"、`error` 显示错误提示、发送中禁用输入)。管理端 `admin.html`:密码登录 → 存档浏览(按日期/学号筛选表格)、复核队列(可标记已处理)、预警记录、配置页(收件邮箱/SMTP/风险阈值)。原生 JS + fetch,`EventSource` 不适用 POST,用 fetch 读流解析 SSE。视觉:温暖平静的配色,移动端可用(窄屏单列)。

- [ ] **Step 4: 跑测试 + 手动验证清单**

```bash
conda run -n vllm_deployment python -m pytest backend/tests/test_frontend_serving.py -q
```
Expected: PASS。手动清单(FakePipeline 下起服务):① 首访见同意弹窗,不勾选不能进聊天 ② 发消息见流式输出 ③ 收 delta 后收 done ④ 窗口缩到手机宽度无横向滚动。

- [ ] **Step 5: 提交**

```bash
git add frontend backend/tests/test_frontend_serving.py && git commit -m "feat: 学生聊天页与管理页(原生单页,SSE 流式)"
```

---

### Task 10: 部署脚本与高危演练

**Files:**
- Create: `scripts/start_all.sh`, `scripts/stop_all.sh`, `scripts/check_vram.py`, `scripts/simulate_crisis.py`, `docs/deployment.md`, `backend/tests/test_check_vram.py`

**Interfaces:**
- Consumes:Task 1 配置;Task 8 的 main;mini_vllm `scripts/serve.py`(路径经 env `MINI_VLLM_DIR` 定位,默认 `/mnt/quwenxi/mini_vllm`)

- [ ] **Step 1: 写失败测试**

`test_check_vram.py`:`budget_ok(min_gib=16.0) -> tuple[bool, dict]`,monkeypatch pynvml 读数为 8 GiB → (False, 明细);为 32 GiB → (True, 明细)。

- [ ] **Step 2: 运行测试确认失败**

Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: 实现脚本与文档**

`check_vram.py`:pynvml 读总显存,`budget_ok(16.0)`;不足时打印账本与降级建议(bge-small-zh CPU 模式)。
`start_all.sh`:检查 `MINI_VLLM_DIR` → 用 `conda run -n vllm_deployment` 依次:vLLM serve.py 后台(日志 `logs/vllm.log`,PID 文件)→ 等 `/api/health` → uvicorn 后端前台(日志 `logs/backend.log`)。
`stop_all.sh`:按 PID 文件杀 vLLM 与后端。
`simulate_crisis.py`:剧本式高危对话(≥4 轮,含"不想活了"类词)→ 逐条 POST /api/chat 收集事件 → 断言收到 `risk_done` 且 risk_level==3 → 检查当日 `data/archives/YYYY-MM-DD.xlsx` 存在且含该会话行 → 打印结果,退出码 0/1。
`deployment.md`:从零部署清单(16 GB 机器):conda 环境 → 依赖 → mini_vllm 模型(README 流程)→ bge-m3(ModelScope)→ start_all → 演练 → 常见故障(显存不足/邮件失败/端口占用)。

- [ ] **Step 4: 单测 + 真实全链路演练(需要 GPU 与测试邮箱)**

```bash
conda run -n vllm_deployment python -m pytest backend/tests/test_check_vram.py -q
bash scripts/start_all.sh
conda run -n vllm_deployment python scripts/simulate_crisis.py
bash scripts/stop_all.sh
```
Expected:单测 PASS;演练退出码 0、Excel 落行、测试邮箱收到预警邮件(此步为上线前必跑项,失败即修)。

- [ ] **Step 5: 提交**

```bash
git add scripts docs/deployment.md backend/tests/test_check_vram.py && git commit -m "feat: 一键启停、显存预检与高危演练脚本 + 16GB 部署文档"
```
