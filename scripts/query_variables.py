#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""查询剧本变量：列出剧本的全部变量定义（名称/调用名称/类型/是否必填）。

接口：GET .../services/{s}/llm/global_variables?group_id={g}
返回 Result.Variables[]：{id, name, key, IsRequired, VariableType}
（全量无分页；注意抓包中无“变量描述”字段——该控制台弹窗不支持描述）

变量类型：1=String 2=Integer 3=Float 4=Boolean

用法：
    python scripts/query_variables.py llm_xxx

结果文件：result/{时间_账号_查询剧本变量}/variables.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from volc_aibot.client import VolcAIBotClient          # noqa: E402
from volc_aibot.config import VARIABLE_TYPES           # noqa: E402
from volc_aibot.logging_util import setup_logging      # noqa: E402
from volc_aibot.result import new_result_dir, write_json  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="查询剧本变量定义列表")
    ap.add_argument("script_id", help="剧本ID（llm_ 开头，如 llm_xxx）")
    ap.add_argument("--cookie-api", default=None,
                    help="chrome_capture_operate Cookie 服务地址（默认 127.0.0.1:33445）")
    args = ap.parse_args()

    logger = setup_logging()
    client = VolcAIBotClient(cookie_api=args.cookie_api, logger=logger)

    logger.info("查询剧本变量: %s", args.script_id)
    variables = client.query_variables(args.script_id)

    print(f"\n剧本 {args.script_id} 共 {len(variables)} 个变量：")
    print(f"  {'调用名称(key)':<32} {'名称':<16} {'类型':<8} {'必填':<4} ID")
    for v in variables:
        vtype = VARIABLE_TYPES.get(v.get("VariableType"), str(v.get("VariableType")))
        required = "是" if v.get("IsRequired") else "否"
        print(f"  {str(v.get('key')):<32} {str(v.get('name')):<16} "
              f"{vtype:<8} {required:<4} {v.get('id')}")

    out_dir = new_result_dir("查询剧本变量")
    out = write_json(out_dir / "variables.json", {
        "script_id": args.script_id,
        "total": len(variables),
        "variables": variables,
    })
    print(f"\n结果已写入: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
