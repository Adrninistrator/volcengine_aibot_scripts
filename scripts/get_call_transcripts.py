#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导出通话文本：按通话ID或通话明细筛选条件，逐条写入 <目录>/<通话ID>.md。

- `--call-id`：按通话ID（或 SessionID）定位单条；建议同时传
  `--script`（服务端 Schema 只接受具体剧本ID；省略时按项目组枚举剧本）；
- 或 `--group` + `--script` + 筛选条件：导出满足条件的全部记录
  （`--start/--end` 省略时默认最近 30 天）；
- `--output-dir` 省略时默认
  `result/{日期}/{时间_账号_查询通话文本}/{HHMMSS_短uuid}/`；
- 默认每行 `[第N轮 开始→结束 时长 打断] 机器人:/客户:`；
  `--no-include-line-meta` 输出纯 `机器人:/客户:`；
  `--no-include-call-info` 只输出对话，不输出头部通话信息；
- 页面「操作」列为「数据已清除」（IsDataPruned=true）的记录没有
  对话文本，跳过并在输出中列出；
- 导出目录写 `manifest.json` + `query.json`；`--analyze` 额外写
  `summary.json/summary.md`。
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from volc_aibot.call_transcript import export_transcripts  # noqa: E402
from volc_aibot.client import VolcAIBotClient               # noqa: E402
from volc_aibot.logging_util import setup_logging           # noqa: E402
from volc_aibot.result import new_result_dir                # noqa: E402


def _is_connected_arg(value: str | None) -> bool | None:
    if value is None:
        return None
    return str(value).strip().lower() in ("1", "true", "yes", "y", "是")


def main() -> int:
    ap = argparse.ArgumentParser(description="导出通话文本")
    ap.add_argument("--call-id", default=None,
                    help="按通话ID（或 SessionID）导出单条")
    ap.add_argument("--group", default=None, help="项目组名称（筛选模式必填）")
    ap.add_argument("--script", default=None,
                    help="剧本ID（筛选模式必填；--call-id 模式建议传）")
    ap.add_argument("--start", default=None,
                    help="开始时间（省略=最近 30 天）")
    ap.add_argument("--end", default=None,
                    help="结束时间（省略=最近 30 天）")
    ap.add_argument("--grading", default=None,
                    help="意向等级（非固定值；多个用半角逗号）")
    ap.add_argument("--labeled-grading", default=None,
                    help="人工标记等级")
    ap.add_argument("--duration-op", default=None,
                    choices=["大于等于", "小于", "介于"])
    ap.add_argument("--duration-value", type=int, default=None)
    ap.add_argument("--duration-min", type=int, default=None)
    ap.add_argument("--duration-max", type=int, default=None)
    ap.add_argument("--answer-recognize", default=None,
                    choices=["是", "否", "未启用识别能力"])
    ap.add_argument("--search-id", default=None,
                    help="页面搜索值（通话ID或手机号）")
    ap.add_argument("--search-type", default=None,
                    choices=["parent_or_session_id", "phone_number"])
    ap.add_argument("--env", default=None, choices=["online", "preview"])
    ap.add_argument("--is-connected", default=None,
                    help="接通状态（true/false）")
    ap.add_argument("--task-ids", default=None, help="任务ID")
    ap.add_argument("--order-by", default="create_time",
                    choices=["create_time", "duration", "rounds"])
    ap.add_argument("--sort-type", type=int, default=2, choices=[1, 2])
    ap.add_argument("--limit", type=int, default=None,
                    help="最多导出条数（默认不限）")
    ap.add_argument("--skip-existing", action=argparse.BooleanOptionalAction,
                    default=True, help="目标 md 已存在则跳过（默认开启）")
    ap.add_argument("--workers", type=int, default=4,
                    help="并发导出线程数（默认 4，1=串行）")
    ap.add_argument("--include-call-info",
                    action=argparse.BooleanOptionalAction, default=True,
                    help="是否输出头部通话信息（默认输出）")
    ap.add_argument("--include-line-meta",
                    action=argparse.BooleanOptionalAction, default=True,
                    help="对话行是否带轮次/时间/打断前缀（默认输出）")
    ap.add_argument("--analyze", action="store_true",
                    help="额外输出 summary.json/summary.md 统计")
    ap.add_argument("--keywords", default=None,
                    help="统计关键词（半角逗号分隔；省略用默认表）")
    ap.add_argument("--page-size", type=int, default=100)
    ap.add_argument("--output-dir", default=None,
                    help="通话文本写入目录（省略=result/.../查询通话文本/...）")
    ap.add_argument("--cookie-api", default=None,
                    help="chrome_capture_operate Cookie 服务地址")
    args = ap.parse_args()

    if not args.call_id and not (args.group and args.script):
        print("错误：需指定 --call-id，或同时指定 --group 与 --script")
        return 1

    logger = setup_logging()
    client = VolcAIBotClient(cookie_api=args.cookie_api, logger=logger)

    matched_total = 1
    truncated = False
    warning = ""
    if args.call_id:
        records = [client.find_call_record(args.call_id, args.group,
                                           args.script)]
    else:
        ds, de = args.start, args.end
        if not ds or not de:
            end = datetime.datetime.now()
            start = end - datetime.timedelta(days=29)
            ds = ds or start.strftime("%Y-%m-%d 00:00:00")
            de = de or end.strftime("%Y-%m-%d 23:59:59")
        args.start, args.end = ds, de   # query.json 记录实际生效时间
        ob = {"create_time": None, "duration": "Duration",
              "rounds": "Rounds"}.get(args.order_by)
        r = client.query_call_records(
            group_name=args.group, script_id=args.script,
            date_start=ds, date_end=de,
            grading=args.grading, labeled_grading=args.labeled_grading,
            duration_op=args.duration_op,
            duration_value=args.duration_value,
            duration_min=args.duration_min,
            duration_max=args.duration_max,
            answer_recognize=args.answer_recognize,
            search_id=args.search_id, search_type=args.search_type,
            env=args.env,
            is_connected=_is_connected_arg(args.is_connected),
            task_ids=args.task_ids, order_by=ob,
            sort_type=args.sort_type if ob else None,
            raw=True,
            page_size=(min(args.page_size, int(args.limit))
                       if args.limit is not None and args.limit > 0
                       else args.page_size),
            max_pages=200, all_pages=True,
            max_records=(int(args.limit) if args.limit is not None else None))
        records = r.get("records") or []
        matched_total = r.get("total") or len(records)
        truncated = bool(r.get("truncated"))
        warning = r.get("warning") or r.get("note") or ""

    if args.output_dir:
        out_dir = Path(args.output_dir).expanduser().resolve()
    else:
        base = new_result_dir("查询通话文本")
        out_dir = base / (datetime.datetime.now().strftime("%H%M%S")
                          + "_" + uuid.uuid4().hex[:6])
    out_dir.mkdir(parents=True, exist_ok=True)
    keywords = ([k.strip() for k in args.keywords.split(",") if k.strip()]
                if args.keywords else None)
    query = {
        "call_id": args.call_id, "group": args.group, "script": args.script,
        "start": args.start, "end": args.end, "grading": args.grading,
        "labeled_grading": args.labeled_grading,
        "duration_op": args.duration_op,
        "duration_value": args.duration_value,
        "duration_min": args.duration_min,
        "duration_max": args.duration_max,
        "answer_recognize": args.answer_recognize,
        "search_id": args.search_id, "search_type": args.search_type,
        "env": args.env, "is_connected": args.is_connected,
        "task_ids": args.task_ids, "order_by": args.order_by,
        "sort_type": args.sort_type, "limit": args.limit,
        "skip_existing": args.skip_existing, "workers": args.workers,
        "include_call_info": args.include_call_info,
        "include_line_meta": args.include_line_meta,
        "analyze": args.analyze,
        "keywords": keywords, "output_dir": str(out_dir),
        "exported_at": datetime.datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"),
    }
    (out_dir / "query.json").write_text(
        json.dumps(query, ensure_ascii=False, indent=2),
        encoding="utf-8")

    result = export_transcripts(
        client, records, out_dir, skip_existing=args.skip_existing,
        workers=args.workers, include_call_info=args.include_call_info,
        include_line_meta=args.include_line_meta,
        analyze=args.analyze, keywords=keywords, limit=args.limit,
        matched_total=matched_total, manifest_extra=query)
    print(f"匹配 {matched_total} 条，处理 {result['fetched']} 条："
          f"导出 {result['exported']} 条，已存在跳过 "
          f"{result['skipped_existing']} 条，数据已清除跳过 "
          f"{result['skipped_pruned']} 条，失败 {result['failed_count']} 条")
    if truncated:
        print(f"⚠ 结果不完整：{warning}")
    for item in result["files"]:
        print(f"  {item['call_id']} -> {item['file']}")
    for item in result["skipped"]:
        print(f"  跳过 {item['call_id']}：{item['reason']}")
    for item in result["failed"]:
        print(f"  失败 {item['call_id']}：{item['error']}")
    if result.get("summary_file"):
        print(f"统计: {result['summary_file']}")
    print(f"输出目录: {out_dir}")
    return 0 if result["failed_count"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())