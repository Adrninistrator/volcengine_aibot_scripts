#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修改本地剧本 json 文件 HASH（checksum）字段值。

用途（prompt 2026-09-07）：修改剧本 json 内容后（如 meta.name 加
「_由AI修改」后缀），checksum 会失配导致导入被拒（Code=101 "文件已被
修改"）。本脚本按平台规则重算并写回 checksum：
    checksum = sha256(GoMarshal(data))
序列化规则参考 prompt/剧本json文件HASH字段计算规则.md（Go
encoding/json 默认输出：键排序、紧凑分隔、中文原样、特定字符转义）。

功能：
1. 校验模式（默认）：重算并与文件内 checksum 比对，输出是否一致；
2. --refresh：重算并写回文件（修复失配）；
3. --add-suffix：把 data.meta.name 加「_由AI修改」后缀并重算写回
   （不带后缀时才加；已带则不重复加）。

用法：
    python scripts/update_script_hash.py "result/xxx/剧本.json"           # 校验
    python scripts/update_script_hash.py 剧本.json --refresh              # 重算写回
    python scripts/update_script_hash.py 剧本.json --add-suffix           # 加后缀+重算

验证：对未修改的导出原样文件，重算值与文件内 checksum 完全一致（实测）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from volc_aibot.client import AI_MODIFIED_SUFFIX  # noqa: E402
from volc_aibot.client import has_ai_modified_suffix  # noqa: E402
from volc_aibot.gojson import (calc_checksum,  # noqa: E402
                               dump_json_text, load_json_text)
from volc_aibot.logging_util import setup_logging  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(
        description="剧本 json HASH（checksum）校验/重算")
    ap.add_argument("file", help="剧本 json 文件路径")
    ap.add_argument("--refresh", action="store_true",
                    help="重算 checksum 并写回文件（修复失配）")
    ap.add_argument("--add-suffix", action="store_true",
                    help=f"data.meta.name 加「{AI_MODIFIED_SUFFIX}」后缀"
                         "并重算写回（已带则不重复加）")
    args = ap.parse_args()

    setup_logging()
    path = Path(args.file)
    if not path.is_file():
        print(f"错误：文件不存在: {path}")
        return 1

    try:
        doc = load_json_text(path.read_bytes())
    except ValueError as e:
        print(f"错误：文件不是合法 JSON: {e}")
        return 1
    data = doc.get("data") or {}
    if not data:
        print("错误：文件缺少 data 字段（不是剧本导出格式）")
        return 1
    declared = doc.get("checksum") or ""

    name = str((data.get("meta") or {}).get("name") or "")
    changed = False
    # 兼容时间戳形态：xxx_由AI修改(20260907...) 也算已带后缀，不重复加
    if args.add_suffix and not has_ai_modified_suffix(name):
        data.setdefault("meta", {})["name"] = \
            (name or "剧本") + AI_MODIFIED_SUFFIX
        changed = True
        print(f"名称加后缀: {name} -> {data['meta']['name']}")

    local = calc_checksum(data)
    if not (changed or args.refresh):
        # 纯校验模式
        ok = declared == local
        print(f"文件声明: {declared}")
        print(f"本地重算: {local}")
        print("结论: " + ("一致（未修改过，可直接导入）" if ok
                        else "不一致（文件被改过，需 --refresh 重算后再导入）"))
        return 0 if ok else 2

    # 写回（--refresh 或 --add-suffix 触发的修改）
    new_text = dump_json_text({"data": data, "checksum": local})
    path.write_text(new_text, encoding="utf-8")
    print(f"已写回: {path}")
    print(f"新 checksum: {local}")
    if declared != local:
        print(f"原 checksum: {declared}")
    print("说明: 导入用 import_script（会自动处理名称后缀与 HASH）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
