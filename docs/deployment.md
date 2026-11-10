# MindGuard 部署文档(16 GiB 目标机器)

从零部署清单,按顺序执行。全程使用 conda 环境 `vllm_deployment`。

## 1. 环境(约 2 分钟)

```bash
# 若无 vllm_deployment 环境,先按 mini_vllm/README.md 创建:
#   conda create -n vllm_deployment python=3.11 -y
#   conda activate vllm_deployment
#   pip install -r mini_vllm/requirements.txt

# 安装 MindGuard 新增依赖
cd MindGuard
conda run -n vllm_deployment pip install -r requirements.txt
# 验证依赖共存(出现 ImportError 说明 pin 冲突,停止并反馈)
conda run -n vllm_deployment python -c \
  "import transformers, vllm, chromadb, fastapi, fastmcp, openpyxl; print('ok')"
```

## 2. 模型(约 15 分钟 + 下载时间)

```bash
# 2.1 心理健康微调模型 Qwen3.5-4B-cpsy-W4A16(按 mini_vllm/README.md 流程):
#   下载 bf16 权重 → 量化为 W4A16。若 mini_vllm/model/ 已有该模型则跳过。
cd mini_vllm && conda run -n vllm_deployment python scripts/download_model.py --model 4b
conda run -n vllm_deployment python scripts/quantize.py --model 4b --samples 256
cd ../MindGuard

# 2.2 embedding 模型(经 ModelScope)
# 默认 bge-small-zh-v1.5(~100 MB,数分钟);带宽充足可改 bge-m3(~5 GB,数小时)
conda run -n vllm_deployment python knowledge/build_chroma.py \
  --docs-dir knowledge/raw --persist-dir data/chroma --embed bge-small-zh-v1.5 --device auto
```

## 3. 配置

```bash
export MINDGUARD_ADMIN_PASSWORD='你的管理员密码'        # 必填
export MINDGUARD_ALERT_EMAIL_TO='counselor@example.edu' # 预警收件邮箱
export MINDGUARD_SMTP_HOST='smtp.example.com'           # SMTP 服务器
export MINDGUARD_SMTP_PORT='465'
export MINDGUARD_SMTP_USERNAME='sender@example.com'
export MINDGUARD_SMTP_PASSWORD='发件密码'
export MINDGUARD_SMTP_FROM='MindGuard <sender@example.com>'
export MINDGUARD_SMTP_SSL='true'                        # 465 用 true;25/587 用 false
```

`MINDGUARD_` 前缀环境变量即全部配置项(参见 `backend/app/config.py` 的 Config
字段名);也可放在启动脚本的系统环境/服务文件中。

## 4. 启动与验证

```bash
conda run -n vllm_deployment python scripts/check_vram.py --min-gib 16
bash scripts/start_all.sh
# 学生端 http://<本机IP>:8080/  管理端 http://<本机IP>:8080/admin.html

# 上线前必跑:高危演练(验证 高危→3级判定→预警邮件→Excel 存档 全链路)
conda run -n vllm_deployment python scripts/simulate_crisis.py
bash scripts/stop_all.sh
```

## 5. 显存账本(16 GiB)

| 组件 | 显存 |
|---|---|
| LLM(Qwen3.5-4B-cpsy-W4A16,8 GiB 预算) | ~8 GiB |
| bge-small-zh-v1.5(fp16) | ~0.1 GiB |
| CUDA 上下文/余量 | ~1 GiB |
| **合计** | **~9.1 GiB** |

16 GiB 目标留有充足余量。若改用 bge-m3,embedding 占 ~2.4 GiB,合计 ~11.4 GiB,
仍在预算内;显存不足(如 12 GiB 卡)时 bge-small-zh 可转 CPU,
见 `scripts/check_vram.py` 输出的建议;LLM 预算由 mini_vllm 的 planner 自动收紧。

## 6. 常见故障

| 现象 | 处理 |
|---|---|
| vLLM 起不来 / FlashInfer JIT 报错 | 本机 PATH 上的 nvcc 版本不匹配,mini_vllm 的 config.py 会自动改指 /usr/local/cuda;确认 `$CUDA_HOME/bin/nvcc` 存在且 ninja 已装 |
| 显存 OOM("No available memory for the cache blocks") | 用 mini_vllm `scripts/plan.py --model 4b --budget 8` 重新规划;或降级 bge-small-zh CPU 模式 |
| 预警邮件失败 | 复核队列可见"邮件发送失败";检查 SMTP 配置与端口(465=SSL,587=STARTTLS) |
| 管理页打不开 | 后端绑定 0.0.0.0:8080,确认防火墙放行;管理密码即 MINDGUARD_ADMIN_PASSWORD |
| Chroma 检索无命中 | 重跑 build_chroma.py;或调低 rag_threshold(管理页可在线调整) |

## 7. 安全与合规(上线前必读)

- **HTTPS**:本服务默认走 HTTP(校园内网)。若经公网或反向代理对外,
  务必启用 TLS,并设置环境变量 `MINDGUARD_COOKIE_SECURE=true`
  (管理员 Cookie 加 Secure 标志),同时用 `--api-key` 保护 vLLM 端口。
- **端口暴露**:vLLM(8000)仅监听本机;后端(8080)按需绑定校园网。
- **数据**:`data/`(对话存档 Excel、SQLite、向量库)与 `logs/` 均不入版本库;
  包含学生身份与对话内容,按敏感数据管理,定期备份、限制访问。
- **告知同意**:学生端首屏已内置记录与预警政策告知;若学校另有合规要求
  (如未成年人保护),需在告知文案中补充。
- **人工闭环**:预警邮件注明"系统自动判定,请人工核实";辅导员处置流程
  需校方另行建立(系统是辅助工具,不是决策者)。
