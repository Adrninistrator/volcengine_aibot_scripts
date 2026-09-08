#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""查询项目组下的剧本：列出指定项目组内的全部剧本。

接口：GET /console/api/v2/llm/agent/list?GroupID={项目组ID}
（GroupID 来自 casbin/permission/groups 按项目组名称精确匹配）

用法：
    python scripts/list_group_scripts.py 脚本测试项目组
    python scripts/list_group_scripts.py "电销项目组_测试"

结果文件：result/{日期}/{时间_账号_查询项目组下的剧本}/scripts.json
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
    ap = argparse.ArgumentParser(description="查询项目组下的剧本列表")
    ap.add_argument("group_name", help="项目组名称（精确匹配，如 脚本测试项目组）")
    ap.add_argument("--page-size", type=int, default=100,
                    help="每页条数（默认 100，一次取全）")
    ap.add_argument("--cookie-api", default=None,
                    help="chrome_capture_operate Cookie 服务地址（默认 127.0.0.1:33445）")
    args = ap.parse_args()

    logger = setup_logging()
    client = VolcAIBotClient(cookie_api=args.cookie_api, logger=logger)

    group = client.find_group(args.group_name)
    group_id = group["id"]
    logger.info("项目组 %s -> GroupID=%s", args.group_name, group_id)

    # 翻页收集该组全部剧本
    collected: list[dict] = []
    page = 1
    while True:
        agents, total = client.list_agents(group_id=group_id, page=page,
                                           page_size=args.page_size)
        if not agents:
            break
        collected.extend(agents)
        if len(collected) >= total:
            break
        page += 1

    print(f"\n项目组「{args.group_name}」（{group_id}）共 {len(collected)} 个剧本：")
    print(f"  {'剧本ID(AgentID)':<18} {'剧本名称':<44} ServiceID  测试版  线上版")
    for a in collected:
        print(f"  {str(a.get('AgentID')):<18} {str(a.get('AgentName')):<44} "
              f"{str(a.get('ServiceID')):<9} V{a.get('PreviewVersion'):<5} "
              f"V{a.get('OnlineVersion')}")

    out_dir = new_result_dir("查询项目组下的剧本")
    out = write_json(out_dir / "scripts.json", {
        "group_name": args.group_name,
        "group_id": group_id,
        "total": len(collected),
        "scripts": collected,
    })
    print(f"\n结果已写入: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
