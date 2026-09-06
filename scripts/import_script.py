#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导入剧本：把导出的剧本 JSON 文件导入到指定项目组，生成新剧本。

接口：POST /console/api/v2/llm/agent/import?GroupID={项目组ID}
      multipart/form-data，字段名 File，无预签名直传。
响应返回新 ServiceID 与新名称（原名+导入时间戳后缀），
但不返回剧本ID（AgentID）——脚本会再查 agent/list（按 ServiceID 精确）获取。

用法：
    python scripts/import_script.py "result/xxx_导出剧本/【存客】multi-agent v2.json" "电销项目组_测试"

结果文件：result/{时间_账号_导入剧本}/import.json
提示：新导入剧本未发布（版本 0），文本对话测试前需先发布测试版本
      （运行 scripts/publish_preview.py）。
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
    ap = argparse.ArgumentParser(description="导入剧本文件到指定项目组")
    ap.add_argument("file", help="导出的剧本 JSON 文件路径")
    ap.add_argument("group_name", help="目标项目组名称（精确匹配，如 电销项目组_测试）")
    ap.add_argument("--cookie-api", default=None,
                    help="chrome_capture_operate Cookie 服务地址（默认 127.0.0.1:33445）")
    args = ap.parse_args()

    logger = setup_logging()
    client = VolcAIBotClient(cookie_api=args.cookie_api, logger=logger)

    logger.info("导入剧本: 文件=%s 项目组=%s", args.file, args.group_name)
    result = client.import_script(args.file, group_name=args.group_name)

    print("\n导入成功：")
    print(f"  新剧本ID(AgentID): {result.get('new_agent_id')}")
    print(f"  新剧本名称:        {result.get('new_script_name')}")
    print(f"  新ServiceID:       {result.get('new_service_id')}")
    print(f"  项目组:            {result.get('group_name')}({result.get('group_id')})")
    print(f"  源文件:            {result.get('source_file')}")

    if not result.get("new_agent_id"):
        print("\n警告：未能解析出新剧本ID，请用 search_script.py 按新名称搜索确认。")

    out_dir = new_result_dir("导入剧本")
    out = write_json(out_dir / "import.json", result)
    print(f"\n结果已写入: {out}")
    print("\n提示：新导入剧本尚未发布（版本 0），进行文本对话测试前请先运行 "
          "scripts/publish_preview.py 发布测试版本。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
