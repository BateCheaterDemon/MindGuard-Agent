#!/usr/bin/env python
"""显存预检:16 GiB 账本(spec §4)。

账本:LLM 8 GiB + bge-m3 ~2.4 GiB + CUDA 上下文/余量 ~1 GiB ≈ 11.4 GiB。
不足时打印账本与降级建议(bge-small-zh CPU 模式)。
用法: python scripts/check_vram.py [--min-gib 16]
"""
from __future__ import annotations

import argparse
import sys

BUDGET = {"llm_gib": 8.0, "embed_gib": 0.1, "overhead_gib": 1.0}  # embed 按 bge-small-zh 计


def budget_ok(min_gib: float = 16.0) -> tuple[bool, dict]:
    """返回 (是否达标, 明细);pynvml 不可用时视为 False。"""
    try:
        import pynvml
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        total_bytes = pynvml.nvmlDeviceGetMemoryInfo(handle).total
        pynvml.nvmlShutdown()
    except Exception as exc:  # noqa: BLE001
        return False, {"error": f"pynvml 读取失败: {exc}"}
    total_gib = total_bytes / (1024 ** 3)
    needed = sum(BUDGET.values())
    return (total_gib >= min_gib and total_gib >= needed), {
        "total_gib": round(total_gib, 1),
        "min_gib": min_gib,
        "needed_gib": needed,
        **BUDGET,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--min-gib", type=float, default=16.0)
    args = ap.parse_args()
    ok, detail = budget_ok(args.min_gib)
    if "error" in detail:
        print(f"[FAIL] {detail['error']}")
        return 1
    print(f"GPU 总显存: {detail['total_gib']} GiB")
    print(f"账本需求: LLM {detail['llm_gib']} + embedding {detail['embed_gib']}"
          f" + 余量 {detail['overhead_gib']} ≈ {detail['needed_gib']} GiB")
    if not ok:
        print("[FAIL] 显存不足。降级建议:embedding 改用 bge-small-zh-v1.5 并走 CPU"
              "(config: embedding_model=bge-small-zh-v1.5, embedding_device=cpu),"
              "约占 0.1 GiB 显存。")
        return 1
    print("[OK] 显存达标,可部署 MindGuard。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
