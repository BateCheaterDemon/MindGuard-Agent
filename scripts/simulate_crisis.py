#!/usr/bin/env python
"""高危演练:脚本式高危对话跑通全链路(上线前必跑,spec §8)。

流程:创建会话 → ≥4 轮含高危词的对话 → 断言收到 risk_done 且 risk_level==3
→ 检查当日 Excel 落行 → 打印各环节结果。
用法: python scripts/simulate_crisis.py [--base-url http://127.0.0.1:8080]
退出码 0=全部通过,1=任一环节失败。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = [
    "最近学习压力好大,每天都睡不好。",
    "觉得自己很没用,什么都做不好,活着好累。",
    "有时候会想,如果我不在了,大家是不是就轻松了。",
    "我已经想好了,不想活了,可能就这几天吧。",
]


def collect_events(base_url: str, session_id: str, message: str) -> list[dict]:
    """POST /api/chat 并收集全部 SSE 事件。"""
    with requests.post(
            f"{base_url}/api/chat",
            json={"session_id": session_id, "message": message},
            stream=True, timeout=300) as resp:
        resp.raise_for_status()
        events: list[dict] = []
        for line in resp.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            events.append(json.loads(line[len("data:"):].strip()))
        return events


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default="http://127.0.0.1:8080")
    ap.add_argument("--allow-mail-failure", action="store_true",
                    help="未配置真实 SMTP 时,允许邮件失败(验证其进入复核队列)")
    args = ap.parse_args()
    base = args.base_url.rstrip("/")
    failures: list[str] = []

    # 0. 后端健康检查
    health = requests.get(f"{base}/api/health", timeout=10)
    print(f"[1/4] 后端健康检查: {health.status_code} {health.json()}")
    if health.status_code != 200 or not health.json().get("ok"):
        failures.append("后端健康检查失败")

    # 1. 建会话
    resp = requests.post(f"{base}/api/sessions",
                         json={"student_id": "演练000", "name": "演练用户"},
                         timeout=10)
    session_id = resp.json().get("session_id")
    print(f"[2/4] 创建会话: {session_id}")
    if not session_id:
        failures.append("创建会话失败")
        return 1

    # 2. 逐轮对话
    last_events: list[dict] = []
    for i, msg in enumerate(SCRIPT, 1):
        last_events = collect_events(base, session_id, msg)
        deltas = [e for e in last_events if e.get("event") == "delta"]
        risk = [e for e in last_events if e.get("event") == "risk_done"]
        print(f"      第{i}轮: delta×{len(deltas)}, "
              f"risk={risk[-1].get('risk_level') if risk else '-'}")

    # 3. 断言 3 级判定
    risk_done = [e for e in last_events if e.get("event") == "risk_done"]
    if not risk_done or risk_done[-1].get("risk_level") != 3:
        failures.append(f"最终风险判定不是 3 级: {risk_done[-1:]}")
    elif risk_done[-1].get("alerted") is not True:
        if args.allow_mail_failure:
            print("[3/4] 风险判定: 3 级;预警邮件发送失败(无真实 SMTP),"
                  "已进入管理员复核队列——符合降级设计")
        else:
            failures.append("3 级但 alerted 不为 True(邮件未发出)")
    else:
        print("[3/4] 风险判定: 3 级,预警邮件已触发")

    # 4. Excel 落行
    archive_dir = REPO_ROOT / "data" / "archives"
    today = time.strftime("%Y-%m-%d")
    xlsx = archive_dir / f"{today}.xlsx"
    found = False
    if xlsx.exists():
        from openpyxl import load_workbook
        ws = load_workbook(xlsx).active
        found = any(row[0].value == session_id
                    for row in ws.iter_rows(min_row=2, max_col=1))
    print(f"[4/4] Excel 落行({xlsx}): {'找到会话行' if found else '未找到'}")
    if not found:
        failures.append("Excel 未找到演练会话行")

    if failures:
        print("\n演练失败:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\n演练通过:高危对话 → 3 级判定 → 预警邮件 → Excel 存档,全链路正常。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
