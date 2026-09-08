#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""查询项目组：列出火山引擎智能外呼控制台的全部项目组（含组ID与层级）。

接口：GET /console/api/v2/casbin/permission/groups?resource=llm_dialog_config
（注意区分：项目组=权限分组，不是 volcano/project 返回的 AICS 业务项目）

用法：
    python scripts/query_project_groups.py
    python scripts/query_project_groups.py --cookie-api http://127.0.0.1:33445

结果文件：result/{日期}/{时间_账号_查询项目组}/groups.json
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


def print_tree(groups: list[dict]) -> None:
    """按 parent_group_id 组装树打印（level 字段语义反直觉，不使用）。"""
    by_parent: dict[int, list[dict]] = {}
    for g in groups:
        by_parent.setdefault(g.get("parent_group_id") or 0, []).append(g)

    def render(pid: int, indent: str = "") -> None:
        for g in sorted(by_parent.get(pid, []), key=lambda x: x.get("id") or 0):
            print(f"{indent}- {g.get('group_name')} (id={g.get('id')})")
            render(g.get("id") or 0, indent + "    ")

    roots = sorted(by_parent.get(0, []), key=lambda x: x.get("id") or 0)
    if not roots:  # 兜底：无根节点时全部平铺
        for g in groups:
            print(f"- {g.get('group_name')} (id={g.get('id')})")
        return
    render(0)


def main() -> int:
    ap = argparse.ArgumentParser(description="查询火山引擎智能外呼项目组列表")
    ap.add_argument("--cookie-api", default=None,
                    help="chrome_capture_operate Cookie 服务地址（默认 127.0.0.1:33445）")
    args = ap.parse_args()

    logger = setup_logging()
    client = VolcAIBotClient(cookie_api=args.cookie_api, logger=logger)

    logger.info("查询项目组 ...")
    groups = client.query_project_groups()
    print(f"\n共 {len(groups)} 个项目组（树形结构，括号内为组ID）：")
    print_tree(groups)

    out_dir = new_result_dir("查询项目组")
    out = write_json(out_dir / "groups.json", {
        "total": len(groups),
        "groups": groups,
    })
    print(f"\n结果已写入: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
