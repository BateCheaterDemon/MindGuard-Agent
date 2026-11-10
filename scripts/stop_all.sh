#!/usr/bin/env bash
# MindGuard 停止:按 PID 文件终止后端与 vLLM
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

for pidfile in logs/backend.pid logs/vllm.pid; do
  if [ -f "$pidfile" ]; then
    pid="$(cat "$pidfile")"
    if kill -0 "$pid" 2>/dev/null; then
      # conda run 包装进程:先杀进程组,再兜底 kill
      kill -- -"$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
      echo "已停止 $pidfile ($pid)"
    fi
    rm -f "$pidfile"
  fi
done

# 兜底:按命令行特征清理残留进程
pkill -f "scripts/serve.py --model 4b" 2>/dev/null || true
pkill -f "python -m app.main" 2>/dev/null || true
echo "停止完成。"
