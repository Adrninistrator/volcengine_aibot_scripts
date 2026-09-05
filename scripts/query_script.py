#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""根据剧本ID查询剧本：把 llm_xxx 剧本ID解析为详情（含数字坐标）。

接口：GET /console/api/v2/llm/agent/list?Name=<剧本ID>（AgentID 精确匹配）
关键映射：AgentID(llm_xxx) <-> ServiceID(数字剧本标识) / GroupID / ProjectID

用法：
    python scripts/query_script.py llm_xxx

结果文件：result/{时间_根据剧本ID查询剧本}/script.json
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
    ap = argparse.ArgumentParser(description="根据剧本ID（llm_xxx）查询剧本详情")
    ap.add_argument("script_id", help="剧本ID（llm_ 开头，如 llm_xxx）")
    ap.add_argument("--cookie-api", default=None,
                    help="chrome_capture_operate Cookie 服务地址（默认 127.0.0.1:33445）")
    args = ap.parse_args()

    logger = setup_logging()
    client = VolcAIBotClient(cookie_api=args.cookie_api, logger=logger)

    logger.info("根据剧本ID查询剧本: %s", args.script_id)
    agent = client.query_script(args.script_id)

    print(f"\n剧本详情：")
    print(f"  剧本ID(AgentID): {agent.get('AgentID')}")
    print(f"  剧本名称:        {agent.get('AgentName')}")
    print(f"  ServiceID:       {agent.get('ServiceID')}   （数字剧本标识）")
    print(f"  GroupID:         {agent.get('GroupID')}     （项目组ID）")
    print(f"  ProjectID:       {agent.get('ProjectID')}   （业务项目ID）")
    print(f"  测试版本:        V{agent.get('PreviewVersion')} {agent.get('PreviewStatus')}")
    print(f"  线上版本:        V{agent.get('OnlineVersion')} {agent.get('OnlineStatus')}")
    print(f"  创建时间:        {agent.get('CreateTime')}")
    print(f"  更新时间:        {agent.get('UpdateTime')}")

    out_dir = new_result_dir("根据剧本ID查询剧本")
    out = write_json(out_dir / "script.json", agent)
    print(f"\n结果已写入: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
