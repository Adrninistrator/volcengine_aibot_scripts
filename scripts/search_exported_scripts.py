#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""搜索已导出剧本内容关键字：在「批量导出剧本」目录的导出文件中按行搜索。

数据链路：result/ 下以「_批量导出剧本」结尾的目录（含 清单.json 项目组/
剧本ID/剧本名称 对应关系）-> 逐个导出文件按行读取 -> 判定关键字是否出现/
出现次数。

输出字段：项目组/剧本ID/剧本名称/剧本类型/关键字/是否出现/出现次数
（剧本类型取自批量导出清单；仅当 --count 时输出出现次数具体值，否则为空）。

用法：
    python scripts/search_exported_scripts.py --dir "result\\20260906_120000_2105888584_批量导出剧本" --keyword 优惠券
    python scripts/search_exported_scripts.py --list          # 列出可选目录
    python scripts/search_exported_scripts.py --dir ... --keyword 优惠券 --count

结果文件：result/{日期}/{时间_账号_搜索已导出剧本内容关键字}/结果.json + 结果.md
（搜索本身不依赖网络/Cookie；账号为 best-effort 获取——Cookie 服务可用时
写入目录名，不可用时目录名退化为 {时间_功能描述}；--dir 可只给目录名，
自动在 result/ 下定位）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from volc_aibot.logging_util import setup_logging  # noqa: E402
from volc_aibot.result import (EXPORT_DIR_SUFFIX,      # noqa: E402
                               new_result_dir, write_json)


def find_export_dirs(result_root: Path) -> list[Path]:
    """列出 result/（含日期子层）下以 _批量导出剧本 结尾的目录（时间倒序）。

    目录结构（prompt 2026-09-07）：result/{日期}/{时间_账号_批量导出剧本}/，
    兼容旧的 result/ 一级结构。
    """
    if not result_root.is_dir():
        return []
    dirs = []
    for d in result_root.iterdir():
        if not d.is_dir():
            continue
        if d.name.endswith(EXPORT_DIR_SUFFIX):
            dirs.append(d)
        elif d.name.isdigit():        # 日期层（YYYYMMDD）
            dirs.extend(x for x in d.iterdir()
                        if x.is_dir()
                        and x.name.endswith(EXPORT_DIR_SUFFIX))
    return sorted(dirs, reverse=True)


def resolve_target_dir(arg: str | None, result_root: Path) -> Path | None:
    """--dir 支持完整路径或目录名（兼容 日期层/目录名）；None 取最新。"""
    if arg:
        p = Path(arg)
        if not p.is_absolute():
            # 依次尝试：result/{arg}（旧结构/目录名）、result/日期/{arg}
            for cand in (result_root / arg,
                         *[result_root / day / arg
                           for day in _day_dirs(result_root)]):
                if cand.is_dir():
                    p = cand
                    break
        return p if p.is_dir() else None
    dirs = find_export_dirs(result_root)
    return dirs[0] if dirs else None


def _day_dirs(result_root: Path) -> list[str]:
    """result/ 下的日期目录名（倒序）。"""
    if not result_root.is_dir():
        return []
    return sorted((d.name for d in result_root.iterdir()
                   if d.is_dir() and d.name.isdigit()), reverse=True)


def load_manifest(export_dir: Path) -> dict[str, dict]:
    """读 清单.json -> {导出文件名: {项目组/剧本ID/剧本名称}}。"""
    mf = export_dir / "清单.json"
    if not mf.is_file():
        return {}
    import json
    data = json.loads(mf.read_text(encoding="utf-8"))
    return {row.get("export_file", ""): row
            for row in data.get("scripts", [])}


def search_file(path: Path, keyword: str,
                with_count: bool) -> tuple[bool, int]:
    """按行读取单个导出文件，判定关键字是否出现/出现次数。

    prompt 约定：按行读取，再在每行中判断出现或计数
    （一行内多次出现按行内出现次数累计）。
    """
    found = False
    count = 0
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if keyword in line:
                    found = True
                    if with_count:
                        count += line.count(keyword)
    except OSError:
        pass
    return found, count


def main() -> int:
    ap = argparse.ArgumentParser(description="搜索已导出剧本内容关键字")
    ap.add_argument("--dir", default=None,
                    help="批量导出剧本目录（完整路径或目录名；缺省取最新）")
    ap.add_argument("--keyword", default=None, help="要搜索的关键字")
    ap.add_argument("--count", action="store_true",
                    help="统计出现次数（默认只判定是否出现）")
    ap.add_argument("--list", action="store_true",
                    help="列出 result/ 下全部「批量导出剧本」目录后退出")
    ap.add_argument("--cookie-api", default=None,
                    help="可选：chrome_capture_operate Cookie 服务地址"
                         "（仅用于结果目录名的账号，获取失败不影响搜索）")
    args = ap.parse_args()

    logger = setup_logging()

    # 账号写入结果目录名（prompt 约定）：注册账号提供者；搜索本身离线，
    # Cookie 服务不可用时目录名自动退化为 {时间_功能描述}（不影响搜索）
    try:
        from volc_aibot.client import VolcAIBotClient
        VolcAIBotClient(cookie_api=args.cookie_api, logger=logger)
    except Exception:  # noqa: BLE001 - 离线场景正常
        pass

    from volc_aibot.result import RESULT_DIR
    if args.list:
        dirs = find_export_dirs(RESULT_DIR)
        if not dirs:
            print("未找到任何「批量导出剧本」目录（先运行 "
                  "batch_export_scripts.py）")
            return 1
        print("可选的批量导出剧本目录（新→旧）：")
        for d in dirs:
            print(f"  {d}")
        return 0

    if not args.keyword:
        print("错误：缺少 --keyword（要搜索的关键字）")
        return 1

    target = resolve_target_dir(args.dir, RESULT_DIR)
    if target is None:
        print(f"错误：导出目录不存在（{args.dir or 'result/ 下无导出目录'}）；"
              "先运行 batch_export_scripts.py，或用 --list 查看")
        return 1

    manifest = load_manifest(target)
    files = sorted(p for p in target.iterdir()
                   if p.is_file() and p.suffix == ".json"
                   and p.name not in ("清单.json",))

    results: list[dict] = []
    for p in files:
        meta = manifest.get(p.name) or {}
        found, count = search_file(p, args.keyword, args.count)
        results.append({
            "project_group": meta.get("project_group", ""),
            "script_id": meta.get("script_id", ""),
            "script_name": meta.get("script_name", ""),
            "agent_mode_name": meta.get("agent_mode_name", ""),
            "keyword": args.keyword,
            "found": found,
            "found_text": "是" if found else "否",
            "count": count if args.count else "",
        })
        mark = f"是({count}次)" if found and args.count else \
            ("是" if found else "否")
        print(f"  {mark}  {meta.get('script_id', p.stem):<18} "
              f"{meta.get('script_name', '')}")

    hits = [r for r in results if r["found"]]
    print(f"\n共 {len(results)} 个导出剧本，命中 {len(hits)} 个"
          f"（关键字：{args.keyword}）")

    out_dir = new_result_dir("搜索已导出剧本内容关键字")
    write_json(out_dir / "结果.json", {
        "export_dir": str(target), "keyword": args.keyword,
        "with_count": args.count, "total": len(results),
        "hits": len(hits), "results": results,
    })
    md = ["# 搜索已导出剧本内容关键字", "",
          f"- 导出目录：{target}",
          f"- 关键字：{args.keyword}",
          f"- 是否统计出现次数：{'是' if args.count else '否'}",
          f"- 命中：{len(hits)}/{len(results)}", "",
          "项目组\t剧本ID\t剧本名称\t剧本类型\t关键字\t是否出现\t出现次数"]
    for r in results:
        md.append("\t".join([r["project_group"], r["script_id"],
                             r["script_name"], r["agent_mode_name"],
                             r["keyword"],
                             r["found_text"], str(r["count"])]))
    (out_dir / "结果.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"结果已写入: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
