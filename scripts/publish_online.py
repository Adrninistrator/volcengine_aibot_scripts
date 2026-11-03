#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发布上线：将当前测试版本发布到线上（对应控制台"测试与发布"→"发布上线"）。

⚠ 生产变更：请仅在测试项目组（脚本测试项目组）的剧本上使用，避免误改
  线上环境（prompt 约定）。

接口（2026-10-10 抓包 + 直连实测）：
- 提交：POST .../services/{s}/online  （URL 无 group_id）
  body {"env_check_info":"test","golden_case_list":[],
        "script_name":"<剧本ID llm_xxx>","start_time":"<当天 YYYY-MM-DD>"}
  响应 {"code":0,"data":{"status":"Succeeded","version":<上线版本号>,...}}
- 轮询：GET .../services/{s}/release-launch?group_id={g}
  成功判据（直连实测）：online_info.status == "Succeeded"
  （注意与测试版本 train_info 的 "FINISHED" 拼写不同）；
  同版本重复发布幂等（仅 update_time 刷新）。

前置：剧本需已有发布成功的测试版本（版本 0 的未发布剧本会报错提示先
运行 publish_preview.py）。

用法：
    python scripts/publish_online.py llm_xxx
    python scripts/publish_online.py llm_xxx --timeout 900 --interval 5

结果文件：result/{日期}/{时间_账号_发布上线}/publish_online.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from volc_aibot.client import VolcAIBotClient          # noqa: E402
from volc_aibot.logging_util import setup_logging      # noqa: E402
from volc_aibot.result import new_result_dir, write_json  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(
        description="发布上线（生产变更：将当前测试版本发布到线上）")
    ap.add_argument("script_id", help="剧本ID（llm_ 开头，如 llm_xxx）")
    ap.add_argument("--timeout", type=float, default=600.0,
                    help="轮询超时秒数（默认 600）")
    ap.add_argument("--interval", type=float, default=3.0,
                    help="轮询间隔秒数（默认 3）")
    ap.add_argument("--cookie-api", default=None,
                    help="chrome_capture_operate Cookie 服务地址（默认 127.0.0.1:33445）")
    args = ap.parse_args()

    logger = setup_logging()
    client = VolcAIBotClient(cookie_api=args.cookie_api, logger=logger)

    def on_poll(status: dict) -> None:
        print(f"  轮询中: version={status.get('version')} "
              f"status={status.get('status')}")

    logger.info("发布上线: %s（生产变更）", args.script_id)
    result = client.publish_online(
        args.script_id, timeout=args.timeout, interval=args.interval,
        on_poll=on_poll)

    print(f"\n发布上线成功：线上版本 V{result['new_version']}"
          f"（status=Succeeded，测试版本 V{result['before_train_version']}）")
    print(f"  剧本: {result.get('agent_name')}（{args.script_id}）")
    print(f"  提交前线上版本: "
          f"V{result['before_online_version'] or '无'}")
    print(f"  更新时间: {result.get('update_time')}")
    if result.get("note"):
        print(f"  注意: {result['note']}")

    out_dir = new_result_dir("发布上线")
    out = write_json(out_dir / "publish_online.json", result)
    print(f"\n结果已写入: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
