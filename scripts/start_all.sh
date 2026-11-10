#!/usr/bin/env bash
# MindGuard 一键启动:vLLM(复用 mini_vllm)+ FastAPI 后端
# 用法: MINI_VLLM_DIR=/path/to/mini_vllm bash scripts/start_all.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MINI_VLLM_DIR="${MINI_VLLM_DIR:-/mnt/quwenxi/mini_vllm}"
ENV_NAME="vllm_deployment"
VLLM_PORT=8000
BACKEND_PORT=8080
cd "$REPO_ROOT"

if [ -z "${MINDGUARD_ADMIN_PASSWORD:-}" ]; then
  echo "[FAIL] 请先设置管理员密码: export MINDGUARD_ADMIN_PASSWORD=你的密码"
  exit 1
fi

echo "[1/3] 显存预检"
conda run -n "$ENV_NAME" python scripts/check_vram.py --min-gib 16 || {
  echo "[WARN] 显存未达 16 GiB,继续尝试启动(LLM 可能 OOM);见 scripts/check_vram.py 降级建议"
}

mkdir -p logs

echo "[2/3] 启动 vLLM(心理健康微调模型 cpsy-W4A16,仅本机 127.0.0.1:$VLLM_PORT)"
if curl -s -o /dev/null "http://127.0.0.1:$VLLM_PORT/health"; then
  echo "      vLLM 已在运行,跳过"
else
  nohup conda run -n "$ENV_NAME" python "$MINI_VLLM_DIR/scripts/serve.py" \
    --model-path "$MINI_VLLM_DIR/model/Qwen3.5-4B-cpsy-W4A16" \
    --served-model-name "Qwen3.5-4B-cpsy-W4A16" \
    --port "$VLLM_PORT" > logs/vllm.log 2>&1 &
  echo $! > logs/vllm.pid
  echo "      等待引擎就绪(首次启动含 FlashInfer JIT 编译,可能需数分钟)"
  for _ in $(seq 1 240); do
    curl -s -o /dev/null "http://127.0.0.1:$VLLM_PORT/health" && break
    sleep 5
  done
  curl -s -o /dev/null "http://127.0.0.1:$VLLM_PORT/health" \
    || { echo "[FAIL] vLLM 未就绪,见 logs/vllm.log"; exit 1; }
fi

echo "[3/3] 启动 FastAPI 后端(0.0.0.0:$BACKEND_PORT)"
(cd backend && nohup conda run -n "$ENV_NAME" python -m app.main \
  > ../logs/backend.log 2>&1 & echo $! > ../logs/backend.pid)
for _ in $(seq 1 60); do
  curl -s -o /dev/null "http://127.0.0.1:$BACKEND_PORT/api/health" && break
  sleep 2
done
curl -s "http://127.0.0.1:$BACKEND_PORT/api/health" \
  || { echo "[FAIL] 后端未就绪,见 logs/backend.log"; exit 1; }

echo
echo "启动完成。学生端: http://<本机IP>:$BACKEND_PORT/  管理端: /admin.html"
echo "建议立即跑一次高危演练: conda run -n $ENV_NAME python scripts/simulate_crisis.py"
