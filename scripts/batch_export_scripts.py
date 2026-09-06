#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量导出剧本：按项目组（或全部项目组）导出其下所有剧本。

数据链路：casbin 项目组 -> agent/list（GroupID 翻页收集全部剧本）
-> 逐个 dialogue_flow_script/export 下载 -> result 目录落盘。

目录约定（prompt）：
- 本次新目录名含当前火山引擎账号（区分环境）且以「_批量导出剧本」结尾
  （其他功能不会含该关键字，供「搜索已导出剧本内容关键字」按目录名定位）；
- 目录内 清单.json / 清单.md 写 项目组/剧本ID/剧本名称 对应关系（清单
  亦记录账号）。

并发（prompt 2026-09-06）：获得剧本清单后并行导出，--concurrency 控制
（默认 5，范围 1~10；实测并发 5 提速约 3.7x 且无错误，见
volc_aibot/concurrency.py 说明）。

用法：
    python scripts/batch_export_scripts.py                       # 全部项目组
    python scripts/batch_export_scripts.py --group 脚本测试项目组
    python scripts/batch_export_scripts.py --group A --group B   # 多个项目组
    python scripts/batch_export_scripts.py --concurrency 8       # 并发导出

结果文件：result/{时间_账号_批量导出剧本}/
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from volc_aibot.client import (VolcAIBotClient,      # noqa: E402
                                agent_mode_name)
from volc_aibot.concurrency import (DEFAULT_CONCURRENCY,  # noqa: E402
                                    MAX_CONCURRENCY,
                                    clamp_concurrency, run_parallel)
from volc_aibot.logging_util import setup_logging      # noqa: E402
from volc_aibot.result import (EXPORT_DIR_SUFFIX,      # noqa: E402
                               new_result_dir, safe_filename,
                               write_json)


def main() -> int:
    ap = argparse.ArgumentParser(description="批量导出剧本（按项目组/全部）")
    ap.add_argument("--group", action="append", default=None,
                    help="项目组名称（可多次指定；不指定=全部项目组）")
    ap.add_argument("--concurrency", type=int, default=None,
                    help=f"导出并发数（默认 {DEFAULT_CONCURRENCY}，"
                         f"范围 1~{MAX_CONCURRENCY}）")
    ap.add_argument("--cookie-api", default=None,
                    help="chrome_capture_operate Cookie 服务地址")
    args = ap.parse_args()

    logger = setup_logging()
    client = VolcAIBotClient(cookie_api=args.cookie_api, logger=logger)
    concurrency = clamp_concurrency(args.concurrency)

    # 1. 确定项目组范围
    groups = client.query_project_groups()
    if args.group:
        wanted = set(args.group)
        selected = [g for g in groups if g.get("group_name") in wanted]
        missing = wanted - {g.get("group_name") for g in selected}
        if missing:
            print(f"错误：以下项目组不存在: {sorted(missing)}")
            return 1
    else:
        selected = groups

    if not selected:
        print("错误：未找到任何项目组（检查账号权限）")
        return 1

    # 2. 账号（写入结果目录名，区分环境；清单.json 中亦记录）
    account = client.get_current_account()

    # 3. 导出（目录名含账号且固定以 _批量导出剧本 结尾）
    out_dir = new_result_dir(EXPORT_DIR_SUFFIX)

    manifest: list[dict] = []
    exported = failed = 0
    group_names = [g.get("group_name") for g in selected]
    print(f"批量导出：{len(selected)} 个项目组（{'; '.join(group_names)}），"
          f"账号 {account}，并发 {concurrency}")
    for g in selected:
        gid = g["id"]
        gname = g.get("group_name") or str(gid)
        scripts = client.list_group_all_scripts(gid)
        print(f"  项目组 {gname}：{len(scripts)} 个剧本")

        # 并发导出（prompt 2026-09-06：获得剧本后并行执行导出操作）
        from volc_aibot import progress

        def export_one(s: dict):
            sid = s.get("AgentID") or ""
            filename, content = client.export_script(sid)
            rel = f"{safe_filename(gname)}_{safe_filename(filename)}"
            path = out_dir / safe_filename(rel)
            path.write_bytes(content)
            return {"export_file": path.name, "size_bytes": len(content)}

        def on_progress(s: dict, index: int, total: int) -> None:
            progress.report_stage(
                f"导出剧本 {s.get('AgentID') or ''}", group=gname,
                script_id=s.get("AgentID") or "",
                script_name=s.get("AgentName") or "",
                index=index, total=total)

        results = run_parallel(scripts, export_one,
                               concurrency=concurrency,
                               progress=on_progress)
        for s, r in zip(scripts, results):
            sid = s.get("AgentID") or ""
            sname = s.get("AgentName") or ""
            row = {"project_group": gname, "script_id": sid,
                   "script_name": sname,
                   "agent_mode_name": agent_mode_name(
                       s.get("AgentMode"))}
            if isinstance(r, tuple) and r and r[0] == "error":
                row["error"] = str(r[1])[:200]
                failed += 1
                print(f"    ✗ {sid} {sname}: {str(r[1])[:120]}")
            else:
                row.update(r)
                exported += 1
                print(f"    ✓ {sid} {sname} -> {row['export_file']}")
            manifest.append(row)

    write_json(out_dir / "清单.json", {
        "account": str(account), "total": len(manifest),
        "exported": exported, "failed": failed, "scripts": manifest,
    })
    md = ["# 批量导出剧本清单", "",
          f"- 账号：{account}",
          f"- 项目组：{'; '.join(group_names)}",
          f"- 总数：{len(manifest)}（成功 {exported}，失败 {failed}）", "",
          "项目组\t剧本ID\t剧本名称\t导出文件\t失败原因"]
    for row in manifest:
        md.append("\t".join([row["project_group"], row["script_id"],
                             row["script_name"],
                             row.get("export_file", ""),
                             row.get("error", "")]))
    (out_dir / "清单.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    print(f"\n导出完成：成功 {exported}/{len(manifest)}，失败 {failed}")
    print(f"导出目录: {out_dir}")
    return 0 if failed == 0 and exported > 0 else (1 if exported == 0 else 0)


if __name__ == "__main__":
    sys.exit(main())
