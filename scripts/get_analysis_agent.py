#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""获取分析Agent内容：系统提示词/用户提示词/状态/更新时间/模型参数。

接口（2026-09-06 抓包）：CloudLadder（igh.bytedance.com）
- GET /Sca/CloudLadder/Agent/Config?AgentId={id}（x-jwt-token 头）：
  AgentConfig.GeneralAgentConfig.SummaryAgentConfig.InputTmpls
  —— Role=1 系统提示词、Role=2 用户提示词（如 "{{.Input}}"）；
  ModelParam 模型参数（ModelName/Endpoint/Temperature/TopP 等）；
- 状态/更新时间经 Agent/List?AgentIds={id} 反查（Status 0/1）。

提示词全文（实测约 1KB~60KB）落盘到结果文件，终端只输出摘要，
供 AI 后续按需读取文件分析。

用法：
    python scripts/get_analysis_agent.py BDE44205003

结果文件：result/{时间_账号_获取分析Agent内容}/{AgentID}.json + {AgentID}.md
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


def format_ts(ts) -> str:
    if not ts:
        return ""
    try:
        return datetime.datetime.fromtimestamp(int(ts) / 1000).strftime(
            "%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError, OverflowError):
        return str(ts)


def main() -> int:
    ap = argparse.ArgumentParser(description="获取分析Agent内容")
    ap.add_argument("agent_id", help="分析AgentID（如 BDE44205003）")
    ap.add_argument("--cookie-api", default=None,
                    help="chrome_capture_operate Cookie 服务地址")
    args = ap.parse_args()

    logger = setup_logging()
    client = VolcAIBotClient(cookie_api=args.cookie_api, logger=logger)

    # 1. 详情（提示词/模型参数）
    agent_config = client.get_cloud_ladder_agent_config(args.agent_id)
    summary = ((agent_config.get("GeneralAgentConfig") or {})
               .get("SummaryAgentConfig") or {})
    tmpl_sys = ""
    tmpl_user = ""
    for t in summary.get("InputTmpls") or []:
        if t.get("Role") == 1:
            tmpl_sys = t.get("Text") or ""
        elif t.get("Role") == 2:
            tmpl_user = t.get("Text") or ""
    model = summary.get("ModelParam") or {}

    # 2. 状态/更新时间（列表接口反查）
    agents, _ = client.list_cloud_ladder_agents(agent_ids=[args.agent_id])
    info = agents[0] if agents else {}
    status = client.ladder_agent_status(info) if info else "未知"
    name = info.get("Name") or ""
    type_zh = info.get("Type") or ""
    update_time = format_ts(info.get("UpdateTime"))

    print(f"分析Agent {args.agent_id}（{name}，{type_zh}）：")
    print(f"  状态：{status}")
    print(f"  更新时间：{update_time}")
    print(f"  模型：{model.get('ModelName', '')}"
          f"（Endpoint: {model.get('Endpoint', '')}，"
          f"Temperature: {model.get('Temperature', '')}）")
    print(f"  系统提示词：{len(tmpl_sys)} 字"
          f"（前80字: {tmpl_sys[:80]!r}）")
    print(f"  用户提示词：{len(tmpl_user)} 字（{tmpl_user!r}）")

    out_dir = new_result_dir("获取分析Agent内容")
    write_json(out_dir / f"{safe_filename(args.agent_id)}.json", {
        "agent_id": args.agent_id, "name": name, "type": type_zh,
        "status": status, "update_time": info.get("UpdateTime"),
        "update_time_text": update_time,
        "model": model,
        "system_prompt": tmpl_sys, "user_prompt": tmpl_user,
        "agent_config": agent_config,
    })
    md = [f"# 分析Agent内容 {args.agent_id}", "",
          f"- 名称：{name}（{type_zh}）",
          f"- 状态：{status}",
          f"- 更新时间：{update_time}",
          f"- 模型：{model.get('ModelName', '')}"
          f" Endpoint={model.get('Endpoint', '')}"
          f" Temperature={model.get('Temperature', '')}", "",
          "## 系统提示词", "", tmpl_sys, "",
          "## 用户提示词", "", tmpl_user, ""]
    (out_dir / f"{safe_filename(args.agent_id)}.md").write_text(
        "\n".join(md) + "\n", encoding="utf-8")
    print(f"\n结果已写入: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
