#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""查询通话明细：按项目组+剧本+时间段查询外呼通话记录（含过滤条件）。

接口（2026-09-06 22:51 抓包实证）：POST /console/api/v2/llm/call_list
- 必填：项目组、剧本、开始/结束时间；
- 过滤（与通话明细页一致）：意向等级（非固定值）/人工标记等级/通话时长
  （大于等于|小于|介于，秒）/命中语音助手（是|否|未启用识别能力）/
  通话id或被叫号码/任务ID/环境类型/接通状态/排序；
- 输出（13 字段）：被叫号码/主叫号码/接通状态/SIP状态码/通话时长秒/
  交互轮次/环境类型/命中语音助手/意向等级/信息抽取/短信状态/创建时间/通话ID；
- 默认只查第一页；--all-pages 才自动翻页收集全部（受约 1 万条上限）。

用法：
    python scripts/query_call_records.py --group 电销项目组_测试 --script llm_yjzw_bjfhd \
        --start "2026-08-08 00:00:00" --end "2026-09-06 23:59:59"
    python scripts/query_call_records.py ... --grading B
    python scripts/query_call_records.py ... --duration-op 介于 --duration-min 5 --duration-max 15
    python scripts/query_call_records.py ... --answer-recognize 否
    python scripts/query_call_records.py ... --page-index 0          # 第一页（默认）
    python scripts/query_call_records.py ... --page-index 1          # 第二页
    python scripts/query_call_records.py ... --all-pages             # 全量

分页：默认 --page-index 0（单页）；--all-pages 自动翻页收集全部。
命中语音助手输出三态：是/否/未启用识别能力（IsNonhumanAnswer
true/false/键缺失）。

结果文件：result/{日期}/{时间_账号_查询通话明细}/records.json + records.md
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

# 输出列（prompt 约定 13 字段，\t 分隔）
COLUMNS = ["被叫号码", "主叫号码", "接通状态", "SIP状态码", "通话时长/秒",
           "交互轮次", "环境类型", "命中语音助手", "意向等级", "信息抽取",
           "短信状态", "创建时间", "通话ID"]


def main() -> int:
    ap = argparse.ArgumentParser(description="查询通话明细")
    ap.add_argument("--group", required=True, help="项目组名称（必填）")
    ap.add_argument("--script", required=True,
                    help="剧本ID（必填，llm_xxx）")
    ap.add_argument("--start", required=True,
                    help='开始时间（必填，如 "2026-08-08 00:00:00"）')
    ap.add_argument("--end", required=True,
                    help='结束时间（必填，如 "2026-09-06 23:59:59"）')
    ap.add_argument("--grading", default=None,
                    help="可选：意向等级（非固定值，如 A/B/高意愿）")
    ap.add_argument("--duration-op", default=None,
                    choices=["大于等于", "小于", "介于"],
                    help="可选：通话时长判断方式（秒）")
    ap.add_argument("--duration-value", type=int, default=None,
                    help="通话时长数值（秒，对应 大于等于/小于）")
    ap.add_argument("--duration-min", type=int, default=None,
                    help="通话时长最小值（秒，对应 介于）")
    ap.add_argument("--duration-max", type=int, default=None,
                    help="通话时长最大值（秒，对应 介于）")
    ap.add_argument("--answer-recognize", default=None,
                    choices=["是", "否", "未启用识别能力"],
                    help="可选：命中语音助手过滤")
    ap.add_argument("--labeled-grading", default=None,
                    help="可选：人工标记等级（LabeledGrading）")
    ap.add_argument("--search-id", default=None,
                    help="可选：页面搜索值（通话ID或手机号）")
    ap.add_argument("--search-type", default=None,
                    choices=["parent_or_session_id", "phone_number"],
                    help="可选：搜索类型；通话id=parent_or_session_id（默认），"
                         "被叫号码=phone_number")
    ap.add_argument("--env", default=None, choices=["online", "preview"],
                    help="可选：环境类型（online=线上，preview=测试）")
    ap.add_argument("--is-connected", default=None,
                    help="可选：接通状态（true=接通，false=未接通）")
    ap.add_argument("--task-ids", default=None,
                    help="可选：任务ID（页面「任务名称」对应 TaskIDs）")
    ap.add_argument("--order-by", default=None,
                    choices=["Duration", "Rounds"],
                    help="可选：排序字段（Duration/Rounds）")
    ap.add_argument("--sort-type", type=int, default=None, choices=[1, 2],
                    help="可选：排序方向（1=升序，2=降序）")
    ap.add_argument("--page-size", type=int, default=100,
                    help="分页大小（默认 100，页面为 20）")
    ap.add_argument("--page-index", type=int, default=0,
                    help="分页页号（0 基，默认 0=只查第一页；"
                         "传 1=第二页）")
    ap.add_argument("--all-pages", action="store_true",
                    help="自动翻页收集全部（受服务端约 1 万条上限约束）")
    ap.add_argument("--cookie-api", default=None,
                    help="chrome_capture_operate Cookie 服务地址")
    args = ap.parse_args()

    logger = setup_logging()
    client = VolcAIBotClient(cookie_api=args.cookie_api, logger=logger)

    if args.duration_op in ("大于等于", "小于") and \
            args.duration_value is None:
        print("错误：duration-op 为 大于等于/小于 时需提供 --duration-value")
        return 1
    if args.duration_op == "介于" and (
            args.duration_min is None or args.duration_max is None):
        print("错误：duration-op 为 介于 时需提供 --duration-min 与 --duration-max")
        return 1

    is_connected = None
    if args.is_connected is not None:
        is_connected = str(args.is_connected).strip().lower() in (
            "1", "true", "yes", "y", "是")
    r = client.query_call_records(
        group_name=args.group, script_id=args.script,
        date_start=args.start, date_end=args.end,
        grading=args.grading, labeled_grading=args.labeled_grading,
        duration_op=args.duration_op,
        duration_value=args.duration_value,
        duration_min=args.duration_min,
        duration_max=args.duration_max,
        answer_recognize=args.answer_recognize,
        search_id=args.search_id, search_type=args.search_type,
        env=args.env, is_connected=is_connected, task_ids=args.task_ids,
        order_by=args.order_by, sort_type=args.sort_type,
        page_size=args.page_size,
        page_index=args.page_index, all_pages=args.all_pages)

    if not args.all_pages:
        print(f"查询通话明细：共 {r['total']} 条（{r['total_pages']} 页），"
              f"本次取第 {r['page_index']} 页 {r['count']} 条")
    else:
        print(f"查询通话明细：共 {r['total']} 条，本次收集 {r['count']} 条")
        if r.get("truncated"):
            print(f"⚠ {r.get('warning') or r.get('note')}")
    conds = []
    if args.grading:
        conds.append(f"意向等级={args.grading}")
    if args.duration_op:
        conds.append(f"时长{args.duration_op}"
                     + (f"{args.duration_value}s" if args.duration_value
                        else f"{args.duration_min}~{args.duration_max}s"))
    if args.answer_recognize:
        conds.append(f"命中语音助手={args.answer_recognize}")
    if args.labeled_grading:
        conds.append(f"人工标记等级={args.labeled_grading}")
    if args.search_id:
        conds.append(f"搜索={args.search_type or 'parent_or_session_id'}"
                     f":{args.search_id}")
    if args.env:
        conds.append(f"环境={args.env}")
    if is_connected is not None:
        conds.append(f"接通状态={'接通' if is_connected else '未接通'}")
    if args.task_ids:
        conds.append(f"任务ID={args.task_ids}")
    if args.order_by:
        conds.append(f"排序={args.order_by}:{args.sort_type}")
    if conds:
        print("  过滤条件：" + "；".join(conds))
    for rec in r["records"][:20]:
        print("  " + "\t".join(str(rec.get(c, "")) for c in COLUMNS))
    if r["count"] > 20:
        print(f"  …（其余 {r['count'] - 20} 条见结果文件）")

    out_dir = new_result_dir("查询通话明细")
    # 请求参数全量记录（prompt 2026-09-07：有指定的请求参数都要记录；
    # 未指定的可选项标注"未指定"，便于复核本次查询口径）
    def _v(x):
        return "未指定" if x is None else x

    params = {
        "项目组": args.group,
        "剧本": args.script,
        "开始时间": args.start,
        "结束时间": args.end,
        "意向等级": _v(args.grading),
        "人工标记等级": _v(args.labeled_grading),
        "搜索类型": _v(args.search_type),
        "搜索值": _v(args.search_id),
        "环境类型": _v(args.env),
        "接通状态": _v(is_connected),
        "任务ID": _v(args.task_ids),
        "排序字段": _v(args.order_by),
        "排序方向": _v(args.sort_type),
        "通话时长-判断方式": _v(args.duration_op),
        "通话时长-数值": _v(args.duration_value),
        "通话时长-数值-最小": _v(args.duration_min),
        "通话时长-数值-最大": _v(args.duration_max),
        "命中语音助手": _v(args.answer_recognize),
        "分页-页号": ("全部收集" if args.all_pages
                      else f"第 {args.page_index} 页（0 基）"),
        "分页-页大小": args.page_size,
        "是否收集全部": args.all_pages,
    }
    r = dict(r, request_params=params)
    write_json(out_dir / "records.json", r)
    md = [f"# 查询通话明细 {args.script}", "",
          "## 请求参数", ""]
    md += [f"- {k}：{v}" for k, v in params.items()]
    md += ["", f"- 总数：{r['total']}",
           f"- 本次收集：{r['count']} 条"]
    md += ["", "## 通话记录", "", "\t".join(COLUMNS)]
    for rec in r["records"]:
        md.append("\t".join(
            " ".join(str(rec.get(c, "") or "").split()) for c in COLUMNS))
    (out_dir / "records.md").write_text(
        "\n".join(md) + "\n", encoding="utf-8")
    print(f"结果已写入: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
