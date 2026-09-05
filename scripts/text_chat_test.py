#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文本对话测试：以固定话术文件驱动与火山引擎机器人对话（模式一）。

⚠ 资源提示：本测试占用生产实际外呼资源（每次对话走真实 LLM 推理）——
本脚本为单会话串行对话，属低资源占用；请勿同时启动大量实例，
建议在晚上测试。

前置条件：剧本需已发布测试版本（train_info.status=FINISHED），
未发布会直接报错提示先运行 scripts/publish_preview.py。

协议（抓包验证，同步 HTTP 非流式）：
- POST .../talk?group_id={g}，round 0 时 query="" 触发机器人开场白
  （首轮输入由剧本配置 LLMFirstRoundInput 提供）；
- 之后每轮 query=客户话术，round_index 从 1 递增；
- session_id 客户端生成（UUIDv4）整段复用，uuid 每轮新生成；
- 机器人回复：data.skill_results[0].tts_data[0].text；
- 机器人结束对话：data.rsp_ctx.session_completed == true（挂机，停止发送剩余话术）；
- 结束后 POST dialog_analysis 做对话分析（意向评级 + 摘要）；
  注意 2026-09-03 抓包该接口返回空 Result（疑点），为空时保存对话全文并提示。
  机器人挂机那轮的结束语不计入分析报文（与页面行为一致）。

用法（模式一：固定话术文件，每行客户说一句，空行跳过）：
    python scripts/text_chat_test.py llm_xxx queries.txt
    python scripts/text_chat_test.py llm_xxx queries.txt \
        --var coupon_a_lock_term=3 --encoding gbk

不带话术文件时进入交互模式：逐句输入，空行结束。

任意轮数自由对话（模式二）请使用 MCP 服务（mcp_server.py）。

结果文件：result/{时间_文本对话测试}/
- rounds/round_001.md ... **每轮对话结束后单独生成一个 md 文件**
  （含“# 变量”段（本轮使用的变量，名称\t值）与“# 对话内容”段
  （对话截至本轮，每行 机器人:xxx / 客户:xxx）；进程中断也已保留已完成轮次）
- rounds/round_all.md  全部轮次的汇总 md（最终一次写入）
- transcript.json  逐轮详情（节点/挂机标记等）
- analysis.json    对话分析结果（意向评级+摘要）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from uuid import uuid4

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from volc_aibot.client import VolcAIBotClient          # noqa: E402
from volc_aibot.logging_util import setup_logging      # noqa: E402
from volc_aibot.result import (new_result_dir,         # noqa: E402
                               write_dialog_round_md, write_json)


def load_lines(file: str | None, encoding: str) -> list[str]:
    """读取话术文件（每行一句）；无文件时进入交互输入（空行结束）。"""
    if file:
        p = Path(file)
        if not p.is_file():
            raise SystemExit(f"错误：话术文件不存在: {p}")
        text = p.read_text(encoding=encoding)
        return [ln.strip() for ln in text.splitlines() if ln.strip()]

    print("未指定话术文件，进入交互模式：逐句输入客户话术，空行结束。")
    lines: list[str] = []
    while True:
        try:
            line = input("客户> ").strip()
        except EOFError:
            break
        if not line:
            break
        lines.append(line)
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description="文本对话测试（固定话术文件驱动）")
    ap.add_argument("script_id", help="剧本ID（llm_ 开头，如 llm_xxx）")
    ap.add_argument("queries_file", nargs="?", default=None,
                    help="客户话术文件（每行一句，空行跳过；缺省进入交互模式）")
    ap.add_argument("--var", action="append", default=[], metavar="KEY=VALUE",
                    help="会话级对话变量 KEY=VALUE（--var 可重复传入以一次指定多个，"
                         "如 --var coupon_a_lock_term=3 --var bce_jiangjia=8）；"
                         "仅在对话首次请求时生效且整段会话不变，覆盖剧本测试版本"
                         "全局变量的同名变量（不改测试版本全局变量）；"
                         "未指定时使用测试版本全局变量")
    ap.add_argument("--encoding", default="utf-8",
                    help="话术文件编码（默认 utf-8，可传 gbk）")
    ap.add_argument("--no-analysis", action="store_true",
                    help="跳过结束后的对话分析")
    ap.add_argument("--cookie-api", default=None,
                    help="chrome_capture_operate Cookie 服务地址（默认 127.0.0.1:33445）")
    args = ap.parse_args()

    lines = load_lines(args.queries_file, args.encoding)

    overrides: dict[str, str] = {}
    for item in args.var:
        if "=" not in item:
            ap.error(f"--var 参数格式应为 KEY=VALUE，收到: {item}")
        k, v = item.split("=", 1)
        overrides[k.strip()] = v

    logger = setup_logging()
    client = VolcAIBotClient(cookie_api=args.cookie_api, logger=logger)
    out_dir = new_result_dir("文本对话测试")

    # ---- 前置：解析剧本 + 检查测试版本已发布 ----
    coords = client.resolve_script(args.script_id)
    logger.info("剧本 %s -> ServiceID=%s GroupID=%s",
                args.script_id, coords["service"], coords["group"])
    check = client.check_preview_published(coords)
    if not check["published"]:
        ti = check.get("train_info") or {}
        raise SystemExit(
            f"错误：剧本测试版本未发布完成（train_info: version="
            f"{ti.get('version')} status={ti.get('status')}），"
            f"无法进行文本对话测试。请先运行 scripts/publish_preview.py。")
    logger.info("测试版本已发布：V%s（FINISHED），开始对话",
                (check.get("train_info") or {}).get("version"))

    # ---- 对话变量（context） ----
    variables = client.fetch_preview_variable_values(coords=coords)
    variables.update(overrides)
    context_str = json.dumps(variables, ensure_ascii=False, sort_keys=True)
    logger.info("对话变量 %d 个（覆盖 %d 个）", len(variables), len(overrides))

    session_id = str(uuid4())
    rounds: list[dict] = []   # 每轮记录
    md_items: list[dict] = []  # 对话记录条目（Speaker 1=机器人 2=客户）
    robot_ended = False

    rounds_dir = out_dir / "rounds"
    # 本轮使用的变量（名称, 值），\t 分隔写入每轮 md
    var_pairs = sorted(variables.items())

    def md_header(index: int) -> list[str]:
        return [
            f"# 文本对话测试 第 {index} 轮",
            f"- 剧本: {args.script_id}（{coords.get('agent_name')}）",
            f"- 会话ID: {session_id}",
        ]

    def write_round_md(index: int) -> Path:
        """每轮结束后单独生成一个 md（含变量段与截至本轮的对话内容）。"""
        return write_dialog_round_md(
            rounds_dir / f"round_{index:03d}.md",
            md_header(index), var_pairs, md_items)

    def talk_round(query: str, index: int) -> None:
        nonlocal robot_ended
        data = client.talk(coords, context_str, query, session_id, index)
        texts, node, completed = VolcAIBotClient.extract_reply(data)
        if query:
            print(f"\n客户> {query}")
            md_items.append({"Speaker": 2, "Content": query})
        print(f"机器人> " + "\n".join(texts))
        if node:
            print(f"  [节点: {node}]")
        if texts:
            md_items.append({"Speaker": 1, "Content": "\n".join(texts)})
        rounds.append({
            "round_index": index, "query": query,
            "robot_texts": texts, "node": node,
            "session_completed": completed,
        })
        robot_ended = robot_ended or completed
        write_round_md(index)   # 每轮结束后单独生成该轮 md 文件

    # ---- 第 0 轮：开场白（query 为空串） ----
    print(f"\n=== 开始对话（剧本 {args.script_id}，会话 {session_id[:8]}...）===")
    talk_round("", 0)

    # ---- 后续轮次：逐行发送客户话术 ----
    for i, line in enumerate(lines, start=1):
        if robot_ended:
            print("\n机器人已结束对话（挂机），剩余话术不再发送。")
            break
        talk_round(line, i)

    if robot_ended:
        print("\n=== 机器人已结束对话（session_completed=true）===")
    else:
        print("\n=== 话术发送完毕（人工结束测试）===")

    # ---- 对话分析（挂机结束语不计入，与页面行为一致） ----
    analysis: dict = {}
    analysis_available = False
    if not args.no_analysis:
        # 组装分析报文：机器人开场白/回复为 Speaker=1，客户话术为 Speaker=2
        items = []
        for r in rounds:
            if r["query"] == "":
                # 机器人开场白（若挂机开场则不计入分析）
                if not r["session_completed"]:
                    items.append({"Speaker": 1, "Content": "\n".join(r["robot_texts"])})
            else:
                items.append({"Speaker": 2, "Content": r["query"]})
                if not r["session_completed"]:
                    items.append({"Speaker": 1, "Content": "\n".join(r["robot_texts"])})
        if len(items) >= 2:
            logger.info("提交对话分析（%d 条对话）...", len(items))
            result = client.dialog_analysis(coords, items)
            analysis = result or {}
            analysis_available = bool(analysis.get("LeadsGrading")
                                      or analysis.get("DialogSummary"))
        else:
            logger.info("对话内容不足一问一答，跳过分析")

    # ---- 结果落盘 ----
    transcript = {
        "script_id": args.script_id,
        "agent_name": coords.get("agent_name"),
        "service_id": coords["service"],
        "group_id": coords["group"],
        "session_id": session_id,
        "variables": variables,
        "rounds": rounds,
        "robot_ended": robot_ended,
        "total_rounds": len(rounds),
    }
    write_json(out_dir / "transcript.json", transcript)
    # 汇总 md（全部轮次一次写入；各轮单独 md 已在每轮实时生成）
    all_md = write_dialog_round_md(
        rounds_dir / "round_all.md",
        [f"# 文本对话测试 全部轮次（共 {len(rounds)} 轮）",
         f"- 剧本: {args.script_id}（{coords.get('agent_name')}）",
         f"- 会话ID: {session_id}"],
        var_pairs, md_items)

    if not args.no_analysis:
        write_json(out_dir / "analysis.json", {
            "script_id": args.script_id,
            "session_id": session_id,
            "available": analysis_available,
            "result": analysis,
        })
        if analysis_available:
            lg = analysis.get("LeadsGrading") or {}
            ds = analysis.get("DialogSummary") or {}
            print("\n=== 对话分析 ===")
            if lg:
                print(f"  意向评级: {lg.get('Level')}（{lg.get('Description')}）")
                print(f"  评级原因: {lg.get('Reason')}")
            if ds:
                print(f"  对话摘要: {ds.get('Summary')}")
        else:
            print("\n注意：对话分析接口返回空结果（2026-09-03 抓包同现象，"
                  "疑点待人工确认）；对话全文已保存（rounds/round_all.md），"
                  "可人工评估。")

    print(f"\n每轮对话记录: {rounds_dir}/round_001.md ~ round_{len(rounds):03d}.md")
    print(f"全部轮次汇总: {all_md}")
    print(f"详细数据: {out_dir / 'transcript.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
