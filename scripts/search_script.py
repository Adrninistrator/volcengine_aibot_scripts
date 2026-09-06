#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""根据剧本名称搜索剧本：模糊匹配剧本名称或剧本ID，自动翻页收集全部。

接口：GET /console/api/v2/llm/agent/list?Name=<关键字>
（Name 同时匹配剧本名称与 AgentID；Total 是命中总数，单页仅 12 条）

用法：
    python scripts/search_script.py "【存客】multi-agent v2"
    python scripts/search_script.py "multi-agent" --max-pages 10

结果文件：result/{时间_账号_根据剧本名称搜索剧本}/scripts.json
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
    ap = argparse.ArgumentParser(description="根据剧本名称/关键字搜索剧本")
    ap.add_argument("name", help="剧本名称（或片段），也可传剧本ID")
    ap.add_argument("--max-pages", type=int, default=20,
                    help="最多翻页数（每页 12 条，默认 20 页）")
    ap.add_argument("--cookie-api", default=None,
                    help="chrome_capture_operate Cookie 服务地址（默认 127.0.0.1:33445）")
    args = ap.parse_args()

    logger = setup_logging()
    client = VolcAIBotClient(cookie_api=args.cookie_api, logger=logger)

    logger.info("根据剧本名称搜索剧本: %s", args.name)
    agents = client.search_scripts(args.name, max_pages=args.max_pages)
    if not agents:
        print(f"未搜索到匹配剧本: {args.name}")
        return 1

    print(f"\n共命中 {len(agents)} 个剧本：")
    for a in agents:
        print(f"  - {a.get('AgentID')}  {a.get('AgentName')}"
              f"  [ServiceID={a.get('ServiceID')} GroupID={a.get('GroupID')}"
              f" 测试版V{a.get('PreviewVersion')} 线上版V{a.get('OnlineVersion')}]")

    out_dir = new_result_dir("根据剧本名称搜索剧本")
    out = write_json(out_dir / "scripts.json", {
        "keyword": args.name,
        "total": len(agents),
        "scripts": agents,
    })
    print(f"\n结果已写入: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
