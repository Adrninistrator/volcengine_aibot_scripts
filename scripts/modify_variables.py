#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""剧本变量修改：对变量执行新增/修改/删除（先查询当前变量，再提交 diff）。

接口：POST .../services/{s}/llm/global_variables?group_id={g}
body {"Variables":{"delete":[变量ID...],"add":[{...}],"update":[...全量回传]}}
- 新增：add 元素不带 id（name/key/IsRequired/VariableType 四字段）
- 修改：必须带数字变量ID（先查询获得），存量变量全量回传到 update
- 删除：delete 传变量数字ID数组（不是调用名称）
注意：POST 响应为空 Result，成功以回查 GET 为准。

⚠ 变量修改后，需要发布剧本测试版本才能生效（prompt 约定）。
   加 --publish 可在修改成功后自动发布（描述自动生成）。

流程：查询当前变量 -> 修改前值写入结果文件(before.md) -> 提交 ->
回查验证 -> 修改后值写入结果文件(after.md)。

before/after 为 md 文件，\t 分隔各列，列含：名称、调用名称、
是否必填、变量类型数值、变量类型（字符串形式），如：
    座席工号\tagent_id\t是\t1\tString

变量类型：1=String 2=Integer 3=Float 4=Boolean

用法：
    python scripts/modify_variables.py llm_xxx --op add \
        --key test_vvv --name "测试变量" --type 1 --required true
    python scripts/modify_variables.py llm_xxx --op update \
        --key test_vvv --required false --type 2
    python scripts/modify_variables.py llm_xxx --op delete --key test_vvv
    （加 --publish 修改成功后自动发布测试版本）
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from volc_aibot.client import VolcAIBotClient          # noqa: E402
from volc_aibot.config import VARIABLE_TYPES           # noqa: E402
from volc_aibot.logging_util import setup_logging      # noqa: E402
from volc_aibot.result import new_result_dir, safe_filename, write_variables_md, write_json  # noqa: E402

VARIABLE_COLUMNS = [
    ("name", "名称"), ("key", "调用名称"), ("IsRequired", "是否必填"),
    ("VariableType", "变量类型数值"), ("VariableTypeDesc", "变量类型"),
]


def _with_type_desc(variables: list[dict]) -> list[dict]:
    """给变量列表补充 VariableTypeDesc（类型数值 -> 字符串形式）。"""
    out = []
    for v in variables or []:
        item = dict(v)
        t = item.get("VariableType")
        item["VariableTypeDesc"] = VARIABLE_TYPES.get(t, str(t or ""))
        out.append(item)
    return out

# 变量调用名称只支持英文与下划线（prompt 背景知识）
KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def parse_bool(text: str) -> bool:
    t = str(text).strip().lower()
    if t in ("true", "1", "yes", "y", "是"):
        return True
    if t in ("false", "0", "no", "n", "否"):
        return False
    raise argparse.ArgumentTypeError(f"无法解析布尔值: {text}（用 true/false）")


def main() -> int:
    ap = argparse.ArgumentParser(description="剧本变量新增/修改/删除")
    ap.add_argument("script_id", help="剧本ID（llm_ 开头，如 llm_xxx）")
    ap.add_argument("--op", required=True, choices=["add", "update", "delete"],
                    help="操作类型：新增/修改/删除")
    ap.add_argument("--key", required=True,
                    help="变量调用名称（只支持英文与下划线，如 test_vvv）")
    ap.add_argument("--name", help="变量名称（add 必填；update 可选修改）")
    ap.add_argument("--type", type=int, choices=[1, 2, 3, 4],
                    help="变量类型：1=String 2=Integer 3=Float 4=Boolean")
    ap.add_argument("--required", type=parse_bool,
                    help="是否必填：true/false")
    ap.add_argument("--publish", action="store_true",
                    help="修改成功后自动发布剧本测试版本（使其生效）")
    ap.add_argument("--publish-description", default=None,
                    help="自动发布时的描述（默认：变量修改-{时间戳}）")
    ap.add_argument("--cookie-api", default=None,
                    help="chrome_capture_operate Cookie 服务地址（默认 127.0.0.1:33445）")
    args = ap.parse_args()

    # ---- 参数校验 ----
    if args.op == "add":
        if not (args.name and args.type is not None):
            ap.error("--op add 需要 --name 与 --type（--required 可选，默认否）")
        if not KEY_PATTERN.fullmatch(args.key):
            ap.error("变量调用名称只支持英文与下划线（如 test_vvv）")
    if args.op == "update":
        if args.name is None and args.type is None and args.required is None:
            ap.error("--op update 至少需要 --name / --type / --required 之一")
    if args.op == "delete" and (args.name or args.type is not None
                                or args.required is not None):
        ap.error("--op delete 只需要 --key")

    logger = setup_logging()
    client = VolcAIBotClient(cookie_api=args.cookie_api, logger=logger)
    out_dir = new_result_dir(f"剧本变量修改_{args.op}")

    # ---- 1. 查询当前变量（修改/删除需要变量ID；新增也按抓包原样全量回传） ----
    logger.info("查询剧本当前变量: %s", args.script_id)
    variables = client.query_variables(args.script_id)
    by_key = {v.get("key"): v for v in variables}
    write_variables_md(out_dir / "before.md", "修改前变量", _with_type_desc(variables),
                       VARIABLE_COLUMNS)
    write_json(out_dir / "before.json", {
        "script_id": args.script_id, "op": args.op,
        "total": len(variables), "variables": variables,
    })
    logger.info("当前共 %d 个变量，修改前值已写入 %s",
                len(variables), out_dir / "before.md")

    # ---- 1b. 修改前先导出剧本备份到同一结果目录（prompt 要求） ----
    try:
        backup_name, backup_content = client.export_script(args.script_id)
        backup_path = out_dir / ("backup_" + safe_filename(backup_name))
        backup_path.write_bytes(backup_content)
        logger.info("剧本已备份: %s（%d 字节，可原样用于导入回滚）",
                    backup_path.name, len(backup_content))
        print(f"  剧本备份: {backup_path}")
    except Exception as e:  # noqa: BLE001 - 备份失败不阻塞（继续执行修改）
        logger.warning("剧本备份失败（不影响本次修改）: %s", e)
        print(f"  剧本备份失败（不影响本次修改）: {e}")

    target = by_key.get(args.key)

    # ---- 2. 构造 diff 并提交 ----
    add: list[dict] = []
    update: list[dict] = []
    delete_ids: list[int] = []

    if args.op == "add":
        if target:
            raise SystemExit(
                f"错误：调用名称 {args.key} 已存在（id={target.get('id')}），"
                f"如需变更请用 --op update")
        add = [{"name": args.name, "key": args.key,
                "IsRequired": bool(args.required), "VariableType": args.type}]
        action_desc = (f"新增变量 {args.key}（{args.name}，类型 "
                       f"{VARIABLE_TYPES.get(args.type)}，"
                       f"必填={bool(args.required)}）")
    elif args.op == "update":
        if not target:
            raise SystemExit(
                f"错误：调用名称 {args.key} 不存在，无法修改（可先运行 "
                f"query_variables.py 查看现有变量）")
        item = {"id": target["id"], "name": target.get("name"),
                "key": target.get("key"),
                "IsRequired": target.get("IsRequired"),
                "VariableType": target.get("VariableType")}
        changes = []
        if args.name is not None and args.name != item["name"]:
            item["name"] = args.name
            changes.append(f"name -> {args.name}")
        if args.type is not None and args.type != item["VariableType"]:
            item["VariableType"] = args.type
            changes.append(f"VariableType -> {args.type}"
                           f"({VARIABLE_TYPES.get(args.type)})")
        if args.required is not None and args.required != item["IsRequired"]:
            item["IsRequired"] = args.required
            changes.append(f"IsRequired -> {args.required}")
        if not changes:
            raise SystemExit("没有任何字段发生变化（新值与当前值相同），未提交")
        update = [item]
        action_desc = f"修改变量 {args.key}(id={target['id']})：{'; '.join(changes)}"
    else:  # delete
        if not target:
            raise SystemExit(f"错误：调用名称 {args.key} 不存在，无法删除")
        delete_ids = [target["id"]]
        action_desc = f"删除变量 {args.key}(id={target['id']})"

    logger.info("提交剧本变量修改：%s", action_desc)
    result = client.modify_variables(args.script_id, add=add, update=update,
                                     delete_ids=delete_ids)

    # ---- 3. 回查验证并写 after.md ----
    after = result["after"]
    write_variables_md(out_dir / "after.md", "修改后变量", _with_type_desc(after),
                       VARIABLE_COLUMNS)
    write_json(out_dir / "after.json", {
        "script_id": args.script_id, "op": args.op,
        "total": len(after), "variables": after,
    })

    after_by_key = {v.get("key"): v for v in after}
    ok = True
    if args.op == "add":
        ok = args.key in after_by_key
        new_id = after_by_key.get(args.key, {}).get("id")
        print(f"\n新增{'成功' if ok else '失败'}：{args.key}"
              f"{'（新变量ID: ' + str(new_id) + '）' if ok else ''}")
    elif args.op == "update":
        cur = after_by_key.get(args.key) or {}
        ok = (cur.get("id") == target["id"]
              and (args.name is None or cur.get("name") == args.name)
              and (args.type is None or cur.get("VariableType") == args.type)
              and (args.required is None
                   or bool(cur.get("IsRequired")) == args.required))
        print(f"\n修改{'成功' if ok else '失败'}：{action_desc}")
    else:
        ok = args.key not in after_by_key
        print(f"\n删除{'成功' if ok else '失败'}：{args.key} 已"
              f"{'从变量列表消失' if ok else '仍存在'}")

    if not ok:
        print("请对照 before.md / after.md 人工核查（POST 响应为空，"
              "以回查 GET 为准）。")
        return 1

    print(f"  变量数: {len(variables)} -> {len(after)}")
    print(f"  修改前值: {out_dir / 'before.md'}")
    print(f"  修改后值: {out_dir / 'after.md'}")

    # ---- 4. 发布（可选） / 提醒 ----
    if args.publish:
        desc = args.publish_description or (
            "变量修改-" + datetime.now().strftime("%Y%m%d%H%M%S"))
        logger.info("自动发布剧本测试版本: %s", desc)
        pub = client.publish_preview(args.script_id, desc)
        print(f"\n已发布测试版本：V{pub['before_version']} -> "
              f"V{pub['new_version']}（FINISHED），变量修改已生效")
        write_json(out_dir / "publish.json", pub)
    else:
        print("\n提醒：剧本变量修改后，需要发布剧本测试版本才能生效"
              "（运行 scripts/publish_preview.py，或本次加 --publish）。")

    write_json(out_dir / "summary.json", {
        "script_id": args.script_id, "op": args.op,
        "action": action_desc, "success": ok,
        "payload": result["payload"],
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
