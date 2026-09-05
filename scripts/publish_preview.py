#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发布剧本测试版本：提交发布并轮询至完成（对应控制台"发布测试版本"）。

接口：
- 提交：POST .../services/{s}/training  body {"description": "..."}
  （剧本ID靠 URL 定位，版本号不传、服务端自动 +1，响应 data 为空）
- 轮询：GET .../services/{s}/release-launch?group_id={g}
  成功判据（prompt 约定）：train_info.version 相比提交前 +1 且
  status == "FINISHED"。

用法：
    python scripts/publish_preview.py llm_xxx
    python scripts/publish_preview.py llm_xxx --description "zzz-剧本发布测试版本-标志"
    python scripts/publish_preview.py llm_xxx --timeout 900 --interval 5

结果文件：result/{时间_发布剧本测试版本}/publish.json
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from volc_aibot.client import VolcAIBotClient          # noqa: E402
from volc_aibot.logging_util import setup_logging      # noqa: E402
from volc_aibot.result import new_result_dir, write_json  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="发布剧本测试版本并轮询至完成")
    ap.add_argument("script_id", help="剧本ID（llm_ 开头，如 llm_xxx）")
    ap.add_argument("--description", default=None,
                    help="发布描述信息（默认自动生成：发布测试版本-<时间戳>）")
    ap.add_argument("--timeout", type=float, default=600.0,
                    help="轮询超时秒数（默认 600）")
    ap.add_argument("--interval", type=float, default=3.0,
                    help="轮询间隔秒数（默认 3）")
    ap.add_argument("--cookie-api", default=None,
                    help="chrome_capture_operate Cookie 服务地址（默认 127.0.0.1:33445）")
    args = ap.parse_args()

    description = args.description or (
        "发布测试版本-" + datetime.now().strftime("%Y%m%d%H%M%S"))

    logger = setup_logging()
    client = VolcAIBotClient(cookie_api=args.cookie_api, logger=logger)

    def on_poll(status: dict) -> None:
        print(f"  轮询中: version={status.get('version')} "
              f"status={status.get('status')}")

    logger.info("发布剧本测试版本: %s 描述: %s", args.script_id, description)
    result = client.publish_preview(
        args.script_id, description,
        timeout=args.timeout, interval=args.interval, on_poll=on_poll)

    print(f"\n发布成功：V{result['before_version']} -> V{result['new_version']}"
          f"（status=FINISHED）")
    print(f"  剧本: {result.get('agent_name')}（{args.script_id}）")
    print(f"  描述: {result['description']}")
    print(f"  更新时间: {result.get('update_time')}")
    if result.get("note"):
        print(f"  注意: {result['note']}")

    out_dir = new_result_dir("发布剧本测试版本")
    out = write_json(out_dir / "publish.json", result)
    print(f"\n结果已写入: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
