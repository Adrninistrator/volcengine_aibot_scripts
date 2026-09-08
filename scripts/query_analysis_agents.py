#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""查询分析Agent列表：名称/ID/状态（已发布/未发布）/更新时间。

接口（2026-09-06 抓包）：CloudLadder（igh.bytedance.com）
- GET /console/api/v2/cloud_ladder/token（Cookie 鉴权）-> JWTToken；
- GET /Sca/CloudLadder/Agent/List?TypeIdentifiers=...（x-jwt-token 头，
  自动翻页收集全部；Status: 0=未发布 1=已发布）。

类型过滤（--type）：通话总结/信息抽取/线索定级，为空查全部。

用法：
    python scripts/query_analysis_agents.py
    python scripts/query_analysis_agents.py --type 信息抽取
    python scripts/query_analysis_agents.py --type 通话总结 --type 线索定级

结果文件：result/{日期}/{时间_账号_查询分析Agent}/agents.json + agents.md
"""

from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from volc_aibot.client import (VolcAIBotClient,      # noqa: E402
                               ladder_type_alias_map)
from volc_aibot.logging_util import setup_logging      # noqa: E402
from volc_aibot.result import new_result_dir, write_json  # noqa: E402

# 类型名 -> TypeIdentifier：全名（外呼-信息抽取）/短名（信息抽取）/标识（BDE）
# 均可（与用法示例、docs/使用说明.md 中的写法一致）
TYPE_NAME_TO_ID = ladder_type_alias_map()


def format_ts(ts) -> str:
    if not ts:
        return ""
    try:
        return datetime.datetime.fromtimestamp(int(ts) / 1000).strftime(
            "%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError, OverflowError):
        return str(ts)


def main() -> int:
    ap = argparse.ArgumentParser(description="查询分析Agent列表")
    ap.add_argument("--type", action="append", default=None,
                    help="类型过滤：通话总结/信息抽取/线索定级"
                         "（可多次；缺省查全部）")
    ap.add_argument("--cookie-api", default=None,
                    help="chrome_capture_operate Cookie 服务地址")
    args = ap.parse_args()

    type_ids: list[str] = []
    if args.type:
        unknown = [t for t in args.type if t not in TYPE_NAME_TO_ID]
        if unknown:
            print(f"错误：未知类型 {unknown}；可选: 通话总结/信息抽取/线索定级"
                  "（或全名 外呼-xxx、标识 DSA/BDE/BLG）")
            return 1
        type_ids = sorted({TYPE_NAME_TO_ID[t] for t in args.type})

    logger = setup_logging()
    client = VolcAIBotClient(cookie_api=args.cookie_api, logger=logger)

    agents, total = client.list_cloud_ladder_agents(
        type_identifiers=type_ids or None)
    rows = []
    for a in agents:
        rows.append({
            "name": a.get("Name") or "",
            "id": a.get("AgentId") or "",
            "type": a.get("Type") or "",
            "status": client.ladder_agent_status(a),
            "update_time": a.get("UpdateTime"),
            "update_time_text": format_ts(a.get("UpdateTime")),
        })
    rows.sort(key=lambda r: (r["type"], r["name"]))

    print(f"分析Agent 共 {len(rows)} 个（Total={total}）"
          f"{'，类型过滤: ' + '; '.join(args.type) if args.type else ''}：")
    print(f"  {'ID':<14} {'名称':<28} {'类型':<12} {'状态':<6} 更新时间")
    for r in rows:
        print(f"  {r['id']:<14} {r['name']:<28} {r['type']:<12} "
              f"{r['status']:<6} {r['update_time_text']}")

    out_dir = new_result_dir("查询分析Agent")
    write_json(out_dir / "agents.json", {
        "type_filter": args.type, "total": len(rows), "agents": rows,
    })
    md = ["# 分析Agent列表", "",
          f"- 类型过滤：{'; '.join(args.type) if args.type else '全部'}",
          f"- 总数：{len(rows)}", "",
          "名称\tID\t状态\t更新时间\t类型"]
    for r in rows:
        md.append("\t".join([r["name"], r["id"], r["status"],
                             r["update_time_text"], r["type"]]))
    (out_dir / "agents.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"\n结果已写入: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
