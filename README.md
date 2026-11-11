# MindGuard

学生自助心理聊天网站:面向校园的心理陪伴与危机预警系统。

学生在网页上与心理健康微调模型对话,系统对每条消息执行三层处理——**意图判断 →
Agentic RAG(按需检索知识库)→ 风险判断**——对话归档到 Excel,高危情况
(自伤/自杀意念、伤人意图等)自动向辅导员发送预警邮件,由辅导员线下联系学生。

## 核心能力(v1)

| 能力 | 说明 |
|---|---|
| 心理微调模型 | Qwen3.5-4B-cpsy-W4A16,经 vLLM 0.29.0 以 OpenAI 兼容接口服务 |
| 三层管线 | 意图判断(闲聊不存档不预警)→ RAG 决策与检索(Chroma + bge 向量)→ 风险分级(0-3) |
| 保守降级 | 意图失败按心理处理;风险失败进管理员复核队列;生成失败仍评估风险——宁可多报,不可漏报 |
| 高危预警 | 3 级风险 → 邮件预警辅导员(带学生身份、证据摘录、对话摘要、人工核实声明) |
| 对话存档 | MCP 工具服务器写 Excel(按日分文件、并发锁、公式注入防护),管理员可见学生身份 |
| 管理端 | 存档浏览、复核队列、预警记录、RAG 参数配置(密码登录、限流、HttpOnly Cookie) |
| 前端 | 原生单页(无构建链):告知同意 → 身份填写 → SSE 流式聊天;移动端可用 |
| 部署 | 16 GiB 显存机器可复现部署;一键启停、显存预检、高危演练脚本 |

## 架构

```
浏览器(学生/管理端)
   │ HTTP + SSE
   ▼
FastAPI 后端(编排器:意图 → RAG → 风险 → 副作用)
   ├── LLMClient ──────► vLLM(127.0.0.1:8000,Qwen3.5-4B-cpsy-W4A16,8 GiB 预算)
   ├── RagIndex ───────► Chroma 持久化向量库(bge 中文向量,18 篇知识文档/62 块)
   ├── SessionStore ───► SQLite(会话/消息/风险/复核队列/预警)
   └── McpTools(stdio) ► MCP 工具服务器:Excel 按日归档 + SMTP 预警邮件
```

## 快速开始

```bash
# 环境(复用 mini_vllm 的 conda 环境,见 docs/deployment.md 完整清单)
conda create -n vllm_deployment python=3.11 -y
conda run -n vllm_deployment pip install -r requirements.txt
export MINDGUARD_ADMIN_PASSWORD='你的管理员密码'
export MINDGUARD_ALERT_EMAIL_TO='counselor@example.edu'

# 启动(vLLM + 后端,需 mini_vllm 的 cpsy 模型就绪)
bash scripts/start_all.sh

# 上线前必跑高危演练
conda run -n vllm_deployment python scripts/simulate_crisis.py
```

- 学生端:`http://<本机IP>:8080/` · 管理端:`http://<本机IP>:8080/admin.html`
- 完整部署文档:[docs/deployment.md](docs/deployment.md)
- 架构设计:[docs/superpowers/specs/2026-11-10-mindguard-v1-design.md](docs/superpowers/specs/2026-11-10-mindguard-v1-design.md)
- 实施计划:[docs/superpowers/plans/2026-11-10-mindguard-v1.md](docs/superpowers/plans/2026-11-10-mindguard-v1.md)

## 测试

```bash
conda run -n vllm_deployment python -m pytest backend/tests knowledge/tests -q
```

## 目录结构

```
backend/   FastAPI 后端(配置/管线/LLM 客户端/RAG/存储/API/安全)
mcp_server/ MCP 工具服务器(Excel 归档 + 预警邮件)
frontend/  原生单页(学生聊天页 + 管理页)
knowledge/ 知识文档(18 篇 md)+ 切分器 + Chroma 建库脚本
scripts/   启停脚本、显存预检、高危演练
docs/      部署文档、设计规格、实施计划
```

## 安全与合规

- 学生首次使用需同意"记录存档 + 高危预警辅导员"告知
- 预警邮件注明"身份为学生自填,请核实;系统自动判定,请人工核实"
- 对话存档与身份信息仅管理员可见,按敏感数据管理
- 本系统是辅助工具,不提供专业诊疗;紧急情况拨打全国心理援助热线 **12356**

## License

待定。
