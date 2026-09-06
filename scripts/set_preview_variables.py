#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试版本全局变量赋值：修改剧本测试版本（Preview）变量的取值。

接口：GET/POST .../services/{s}/llm/script_variables?group_id={g}
- GET：返回 PreviewVariables / OnlineVariables 两套变量及取值
- POST：全量提交 PreviewVariables（仅改目标 key 的 value，其余原样回传），
  值一律为字符串（如 "3"、"无"）；Online 不受影响（抓包验证）

⚠ 必填变量（is_required=true）赋值时值不能为空：传入空值（空串/None）会被拦截报错。

流程：查询当前值 -> 修改前值写入结果文件(before.md) -> 全量提交 ->
回查验证 -> 修改后值写入结果文件(after.md)。

before/after 为 md 文件，\t 分隔各列，列含：名称、调用名称、值、
是否必填、变量类型数值、变量类型（字符串形式），如：
    优惠券a锁期天数\tcoupon_a_lock_term\t无\t否\t1\tString

用法：
    python scripts/set_preview_variables.py llm_xxx \
        --set coupon_a_lock_term=3 --set agent_id=10086
    python scripts/set_preview_variables.py llm_xxx \
        --file vars.json          # vars.json 为 {"调用名称": "值", ...}

结果文件：result/{时间_账号_测试版本全局变量赋值}/before.md + after.md + summary.json
说明：文本对话测试会直接使用这些值（作为对话 context 默认值）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from volc_aibot.client import VolcAIBotClient          # noqa: E402
from volc_aibot.config import VARIABLE_TYPES           # noqa: E402
from volc_aibot.logging_util import setup_logging      # noqa: E402
from volc_aibot.result import new_result_dir, write_variables_md, write_json  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="测试版本全局变量赋值")
    ap.add_argument("script_id", help="剧本ID（llm_ 开头，如 llm_xxx）")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="变量赋值，可多次（KEY 为变量调用名称，VALUE 为新值，"
                         "如 coupon_a_lock_term=3）")
    ap.add_argument("--file", default=None,
                    help="JSON 文件路径，内容为 {调用名称: 值}（与 --set 可并用）")
    ap.add_argument("--cookie-api", default=None,
                    help="chrome_capture_operate Cookie 服务地址（默认 127.0.0.1:33445）")
    args = ap.parse_args()

    values: dict = {}
    if args.file:
        p = Path(args.file)
        if not p.is_file():
            raise SystemExit(f"错误：文件不存在: {p}")
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except ValueError as e:
            raise SystemExit(f"错误：JSON 解析失败: {e}")
        if not isinstance(data, dict):
            raise SystemExit("错误：--file 内容必须是 JSON 对象 {调用名称: 值}")
        values.update(data)
    for item in args.set:
        if "=" not in item:
            ap.error(f"--set 参数格式应为 KEY=VALUE，收到: {item}")
        k, v = item.split("=", 1)
        values[k.strip()] = v
    if not values:
        ap.error("至少提供 --set KEY=VALUE 或 --file 之一")

    logger = setup_logging()
    client = VolcAIBotClient(cookie_api=args.cookie_api, logger=logger)
    out_dir = new_result_dir("测试版本全局变量赋值")

    logger.info("测试版本全局变量赋值: %s，共 %d 个变量", args.script_id, len(values))
    result = client.set_preview_variables(args.script_id, values)

    # 修改前/后值写入结果文件（md，\t 分隔，含类型数值与字符串形式）
    sv_columns = [("name", "名称"), ("key", "调用名称"), ("value", "值"),
                  ("is_required", "是否必填"), ("variable_type", "变量类型数值"),
                  ("variable_type_desc", "变量类型")]

    def _desc(items: list[dict]) -> list[dict]:
        out = []
        for v in items or []:
            item = dict(v)
            item["is_required"] = "是" if item.get("is_required") else "否"
            t = item.get("variable_type")
            item["variable_type_desc"] = VARIABLE_TYPES.get(t, str(t or ""))
            out.append(item)
        return out

    write_variables_md(out_dir / "before.md", "修改前测试版本变量",
                       _desc(result["before"]), sv_columns)
    write_variables_md(out_dir / "after.md", "修改后测试版本变量",
                       _desc(result["after"]), sv_columns)
    write_json(out_dir / "before.json", {
        "script_id": args.script_id,
        "preview_variables": result["before"],
    })
    write_json(out_dir / "after.json", {
        "script_id": args.script_id,
        "preview_variables": result["after"],
    })

    before_map = {v.get("key"): v.get("value") for v in result["before"]}
    print(f"\n赋值完成（剧本 {args.script_id}）：")
    for k, v in result["values"].items():
        print(f"  {k}: {before_map.get(k)!r} -> {v!r}")
    if result["verify_failed"]:
        print(f"\n警告：{len(result['verify_failed'])} 个变量回读后未生效：")
        for k, info in result["verify_failed"].items():
            print(f"  {k}: 期望 {info['expect']!r}，实际 {info['actual']!r}")
        print("请人工核查（POST 响应为空，以回查 GET 为准）。")
        return 1

    write_json(out_dir / "summary.json", {
        "script_id": args.script_id,
        "values": result["values"],
        "success": True,
    })
    print(f"  修改前值: {out_dir / 'before.md'}")
    print(f"  修改后值: {out_dir / 'after.md'}")
    print("\n说明：文本对话测试（text_chat_test.py）会直接使用这些值"
          "作为对话变量默认值。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
