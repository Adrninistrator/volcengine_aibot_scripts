#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""删除剧本（修改操作）：仅允许删除名称以「_由AI修改」为后缀的剧本。

安全约束（prompt 2026-09-07）：
- 名称/ID 双入口：传剧本ID（llm_xxx）或剧本名称均可（名称经
  agent/list 精确解析为 ServiceID 坐标后删除）；
- **双重后缀校验**：本地解析后先检查名称后缀；再调火山接口反查该剧本
  最新名称确认后缀（以后端为准，防本地缓存过期）；不以「_由AI修改」
  结尾一律拒绝删除；
- 修改操作：执行前走账号守卫（仅允许配置的账号）；
- 删除后回查列表确认已移除。

接口（2026-09-07 17:39 抓包实证）：
    DELETE /console/api/v2/projects/{p}/products/9/services/{ServiceID}?group_id={g}
    body {}  ->  {"code":0,"data":{},"msg":"ok"}

用法：
    python scripts/delete_script.py llm_xxx
    python scripts/delete_script.py "剧本名称_由AI修改"
    python scripts/delete_script.py llm_xxx --group 脚本测试项目组

结果文件：result/{日期}/{时间_账号_删除剧本}/delete.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from volc_aibot.client import (AI_MODIFIED_SUFFIX,        # noqa: E402
                               VolcAIBotClient,
                               has_ai_modified_suffix)
from volc_aibot.logging_util import setup_logging      # noqa: E402
from volc_aibot.result import new_result_dir, write_json  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="删除剧本（仅 _由AI修改 后缀）")
    ap.add_argument("script", help="剧本ID（llm_xxx）或剧本名称（精确匹配）")
    ap.add_argument("--group", default=None,
                    help="可选：限定项目组（校验剧本在该组）")
    ap.add_argument("--cookie-api", default=None,
                    help="chrome_capture_operate Cookie 服务地址")
    args = ap.parse_args()

    logger = setup_logging()
    client = VolcAIBotClient(cookie_api=args.cookie_api, logger=logger)

    if not has_ai_modified_suffix(args.script) \
            and not args.script.startswith("llm_"):
        print(f"提示：传入剧本「{args.script}」名称不以"
              f"「{AI_MODIFIED_SUFFIX}」为后缀，将被拒绝删除")

    try:
        r = client.delete_script(args.script, group_name=args.group)
    except Exception as e:  # noqa: BLE001 - 业务拒绝/接口错误统一退出码
        print(f"删除失败: {e}")
        return 1

    print(f"已删除: {r['script_id']}（{r['script_name']}），"
          f"项目组 GroupID={r['group_id']}"
          + (f"（{r['group_name']}）" if r["group_name"] else ""))
    if not r["deleted"]:
        print(f"⚠ 删除后回查仍发现该剧本（remain={r['remain_count']}），"
              f"请到控制台人工确认")
    out_dir = new_result_dir("删除剧本")
    write_json(out_dir / "delete.json", r)
    print(f"结果已写入: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
