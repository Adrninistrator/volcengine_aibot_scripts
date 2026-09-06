#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""查询剧本基本信息：剧本类型/对话控制/LLM模型/ASR设置/分析Agents/发布状态。

接口组合（2026-09-06 抓包）：
- agent/list        剧本名称/项目组/测试/线上版本
- services/{s}/config     DialogControlCfg（最大轮次/最大模型出错次数/挂机词）、
                    AsrHotwordID、AsrContextCfg.Enabled、DialogAnalysisCfg
- llm/prompt_config AgentMode（1=纯PE 2=Multi Agents 3=对话流程编排）+ ModelType
- bigasr/hotword_tables  热词表ID -> 名称
- release-launch    测试/线上版本发布详情（版本号/更新时间/状态）
- CloudLadder Agent/List  挂载的分析Agent 反查名称/状态/更新时间

用法：
    python scripts/query_script_info.py llm_xxx
    python scripts/query_script_info.py llm_xxx --group 脚本测试项目组

结果文件：result/{时间_账号_查询剧本基本信息}/{剧本ID}.json + {剧本ID}.md
"""

from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from volc_aibot.client import VolcAIBotClient          # noqa: E402
from volc_aibot.logging_util import setup_logging      # noqa: E402
from volc_aibot.result import (new_result_dir,         # noqa: E402
                               safe_filename, write_json)


def render_info_md(info: dict) -> str:
    """把 get_script_info 结果渲染为 md 报告（\t 分隔列的清单区）。"""
    lines = [
        f"# 剧本基本信息 {info.get('script_id')}", "",
        f"- 项目组：{info.get('project_group')}",
        f"- 剧本ID：{info.get('script_id')}",
        f"- 剧本名称：{info.get('script_name')}",
        f"- 剧本类型：{info.get('agent_mode_name')}"
        f"（AgentMode={info.get('agent_mode')}）",
        f"- 最大对话轮次：{info.get('max_dialogue_rounds')}",
        f"- 最大模型出错次数：{info.get('max_model_error_count')}",
        "- Agent 回复自动挂机关键词："
        + ";".join(str(k) for k in info.get("hangup_keywords") or []),
        f"- LLM 模型：{info.get('llm_model')}",
        f"- 语音识别（ASR）设置-引用热词表：{info.get('asr_hotword_table')}",
        f"- 语音识别（ASR）设置-上传上下文：{info.get('asr_context_enabled')}",
        f"- 非真人接听识别开关：{info.get('answer_recognize_enabled')}",
        f"- 非真人接听识别后播报内容：{info.get('answer_recognize_text')}",
        "",
    ]
    lines.append("## 分析Agents（挂载）")
    if info.get("analysis_agents"):
        lines.append("")
        lines.append("类型\tID\t名称\t状态\t更新时间")
        for t, a in (info.get("analysis_agents") or {}).items():
            lines.append("\t".join([
                t, a.get("id", ""), a.get("name", ""),
                a.get("status", ""),
                a.get("update_time_text", "")]))
    else:
        lines.append("（未挂载）")
    lines.append("")
    lines.append("## 发布状态")
    lines.append("")
    lines.append("环境\t版本号\t状态\t更新时间")
    for key, label in (("preview_publish", "测试版本"),
                       ("online_publish", "线上版本")):
        p = info.get(key) or {}
        lines.append("\t".join([label, str(p.get("version") or ""),
                                str(p.get("status") or ""),
                                str(p.get("update_time") or "")]))
    return "\n".join(lines) + "\n"


def format_ts(ts) -> str:
    """毫秒时间戳 -> 'YYYY-MM-DD HH:MM:SS'；空值返回空串。"""
    if not ts:
        return ""
    try:
        return datetime.datetime.fromtimestamp(int(ts) / 1000).strftime(
            "%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError, OverflowError):
        return str(ts)


def main() -> int:
    ap = argparse.ArgumentParser(description="查询剧本基本信息")
    ap.add_argument("script_id", help="剧本ID（llm_ 开头，如 llm_xxx）")
    ap.add_argument("--group", default=None,
                    help="可选：限定项目组名称（校验剧本属于该项目组）")
    ap.add_argument("--cookie-api", default=None,
                    help="chrome_capture_operate Cookie 服务地址")
    args = ap.parse_args()

    logger = setup_logging()
    client = VolcAIBotClient(cookie_api=args.cookie_api, logger=logger)

    if args.group:
        group = client.find_group(args.group)
        info = client.get_script_info(args.script_id)
        gid = group["id"]
        # resolve 已含 group；此处仅校验一致性
        coords = client.resolve_script(args.script_id)
        if coords["group"] != gid:
            print(f"错误：剧本 {args.script_id} 不在项目组 {args.group} 中"
                  f"（实际项目组ID {coords['group']}）")
            return 1
    else:
        info = client.get_script_info(args.script_id)

    # 时间戳转可读文本（分析Agent 更新时间为 ms 时间戳），先转换再渲染
    for a in (info.get("analysis_agents") or {}).values():
        a["update_time_text"] = format_ts(a.get("update_time"))

    print(render_info_md(info))

    out_dir = new_result_dir("查询剧本基本信息")
    out_json = write_json(out_dir / f"{safe_filename(args.script_id)}.json",
                          info)
    md_path = out_dir / f"{safe_filename(args.script_id)}.md"
    md_path.write_text(render_info_md(info), encoding="utf-8")
    print(f"结果已写入: {out_json}\n           {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
