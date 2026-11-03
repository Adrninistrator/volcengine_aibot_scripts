# -*- coding: utf-8 -*-
"""通话文本解析、渲染、导出与统计（2026-11-02 / 2026-11-03 优化）。

数据来源：通话明细页「查看详细」加载的对话 JSON
（`SessionURL`/`LogURL` 指向的 TOS 对象，经
`/console/api/v2/intranet/proxy/tos` 读取）。

优先使用 `dialog_series_by_time`（role/display_text/时间/轮次/打断信息），
缺失时回退 `NlpParserWorker`（每轮 inputText=客户、outputText=机器人）。

输出：
- 默认每行 `[第N轮 00:12.4→00:19.2 6.8s 打断成功] 机器人:...`；
- `include_line_meta=False` -> `机器人:...`；
- `include_call_info=False` -> 只输出对话行，不输出头部通话信息；
- **不提供自动脱敏**（2026-11-03 下线；准确率不可控），导出物含客户
  信息，需人工控制目录与使用范围。

导出采用临时文件 + os.replace 原子写入；支持 limit / skip_existing /
并发 workers / manifest.json / 关键词统计 summary。
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

from .result import safe_filename

ROLE_AGENT = "机器人"
ROLE_USER = "客户"
ROLE_OTHER = "系统"

DEFAULT_KEYWORDS = ["利率", "额度", "还款", "利息", "不需要", "骗子",
                    "正规", "续贷", "身份", "忙"]



def is_data_pruned(record: dict) -> bool:
    """通话数据是否已清除（页面「操作」列为「数据已清除」、不可查看详细）。"""
    value = (record or {}).get("IsDataPruned")
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return bool(value)


def _clean(text: Any) -> str:
    """单行化：内部换行/连续空白压成单个空格。"""
    return " ".join(str(text or "").split())


def _int_or_none(value: Any) -> int | None:
    try:
        n = int(value)
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def _is_event_text(text: str) -> bool:
    """平台事件行（如 (用户侧挂机)）：不参与轮次编号/时长展示（V4）。

    只识别明确的挂机/系统事件，避免把客户正常说的“(好的)”误判为事件。
    """
    if not (text.startswith("(") and text.endswith(")")):
        return False
    return any(k in text for k in ("挂机", "系统", "转人工"))


def _interrupt_label(qi: Any) -> str:
    if not isinstance(qi, dict) or not qi:
        return ""
    if qi.get("interrupt_success") is True:
        return "打断成功"
    if qi.get("interrupt_attempted") is True:
        return "尝试打断"
    if qi.get("interrupt_evaluated") is True:
        return "未打断"
    return ""


def transcript_items(detail: dict) -> list[dict]:
    """从对话 JSON 提取逐句条目（说话人/文本/轮次/起止/打断）。"""
    detail = detail or {}
    series = detail.get("dialog_series_by_time")
    if isinstance(series, list) and series:
        out: list[dict] = []
        for item in series:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip().lower()
            speaker = (ROLE_AGENT if role == "agent"
                       else ROLE_USER if role == "user" else ROLE_OTHER)
            text = _clean(item.get("display_text"))
            if not text:
                continue
            node_info = (item.get("output_info") or {}).get("node_info") or {}
            node = (node_info.get("node_name")
                    or (item.get("platformParams") or {}).get("node_name")
                    or "")
            out.append({
                "speaker": speaker,
                "text": text,
                "round": item.get("logic_round"),
                "start_ms": _int_or_none(item.get("start_ts_ms")),
                "end_ms": _int_or_none(item.get("end_ts_ms")),
                "interrupt": _interrupt_label(item.get("query_info")),
                "is_event": _is_event_text(text),
                "node": node,
            })
        if out:
            return out

    # 回退：NlpParserWorker = [[序号, {messageData:{inputText,outputText}}], ...]
    out = []
    worker = detail.get("NlpParserWorker")
    if isinstance(worker, list):
        for item in worker:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            data = item[1] if isinstance(item[1], dict) else {}
            md = data.get("messageData") or {}
            round_no = (md.get("dmState") or {}).get("round")
            in_ms = _int_or_none((data.get("audioInData") or {}).get(
                "audioInStartMs"))
            out_ms = _int_or_none((data.get("audioOutData") or {}).get(
                "audioOutEndMs")) or _int_or_none(
                    (data.get("audioOutData") or {}).get("audioOutStartMs"))
            node = (data.get("platformParams") or {}).get("node_name") or ""
            user_text = _clean(md.get("inputText"))
            agent_text = _clean(md.get("outputText"))
            if user_text:
                out.append({"speaker": ROLE_USER, "text": user_text,
                            "round": round_no, "start_ms": in_ms,
                            "end_ms": None, "interrupt": "",
                            "is_event": _is_event_text(user_text),
                            "node": ""})
            if agent_text:
                out.append({"speaker": ROLE_AGENT, "text": agent_text,
                            "round": round_no, "start_ms": out_ms,
                            "end_ms": None, "interrupt": "",
                            "is_event": _is_event_text(agent_text),
                            "node": node})
    return out


def transcript_lines(detail: dict) -> list[tuple[str, str]]:
    """兼容旧调用：[(说话人, 文本)]。"""
    return [(it["speaker"], it["text"]) for it in transcript_items(detail)]




def _rel_start(items: list[dict]) -> int | None:
    starts = [it["start_ms"] for it in items
              if isinstance(it.get("start_ms"), int)]
    return min(starts) if starts else None


def _fmt_rel(ms: int | None, t0: int | None) -> str:
    if ms is None or t0 is None:
        return "时间未采集"
    offset = max(0, ms - t0)
    return f"{offset // 60000:02d}:{(offset % 60000) / 1000:04.1f}"


def _item_duration(item: dict) -> float | None:
    start, end = item.get("start_ms"), item.get("end_ms")
    if isinstance(start, int) and isinstance(end, int) and end >= start:
        return (end - start) / 1000
    return None


def _line_prefix(item: dict, t0: int | None) -> str:
    if item.get("is_event"):
        return "[事件] "
    parts = []
    if item.get("round") is not None:
        parts.append(f"第{item['round']}轮")
    start = _fmt_rel(item.get("start_ms"), t0)
    if start == "时间未采集":
        parts.append(start)
    elif item.get("end_ms"):
        parts.append(f"{start}→{_fmt_rel(item['end_ms'], t0)}")
    else:
        # 无结束时间时不输出 "→?"，保持列稳定（V4）
        parts.append(start)
    duration = _item_duration(item)
    if duration is not None:
        parts.append(f"{duration:.1f}s")
    if item.get("interrupt"):
        parts.append(item["interrupt"])
    if item.get("node"):
        parts.append(f"节点:{item['node']}")
    return "[" + " ".join(parts) + "] "


def _call_summary_lines(record: dict, detail: dict,
                        items: list[dict]) -> list[str]:
    record = record or {}
    detail = detail or {}
    grading = (record.get("GradingInfo") or {}).get("RobotGrading") or ""
    sms = (record.get("SmsInfo") or {}).get("Status") or ""
    connected = "接通" if record.get("IsConnected") else "未接通"
    call_id = record.get("CallID") or record.get("SessionID") or ""

    info_extraction = _clean(record.get("InfoExtraction"))
    summary = _clean(record.get("DialogSummary"))
    lines = [
        f"# 通话文本 {call_id}",
        "",
        f"- 通话ID：{call_id}",
        f"- SessionID：{record.get('SessionID') or ''}",
        f"- 项目组ID：{record.get('GroupID') or ''}",
        f"- 剧本：{record.get('SchemaName') or ''}",
        f"- 创建时间：{record.get('CreateDate') or ''}",
        f"- 通话时长：{_duration_sec(record)} 秒",
        f"- 交互轮次：{record.get('Rounds') if record.get('Rounds') is not None else ''}",
        f"- 环境类型：{_env_label(record.get('Env'))}",
        f"- 接通状态：{connected}",
        f"- SIP状态码：{record.get('SIPCode') or ''}",
        f"- 意向等级：{grading}",
        f"- 信息抽取：{info_extraction}",
        f"- 短信状态：{sms}",
        f"- 数据状态：{'数据已清除' if is_data_pruned(record) else '正常'}",
    ]
    if summary:
        lines.append(f"- 通话总结：{summary}")

    success_rounds = [it.get("round") for it in items
                      if it.get("interrupt") == "打断成功"]
    if success_rounds:
        rounds = "、".join(str(r) for r in success_rounds if r is not None)
        lines.append(f"- 是否被打断：是（第 {rounds} 轮）")
    elif any(it.get("interrupt") for it in items):
        lines.append("- 是否被打断：否")
    else:
        lines.append("- 是否被打断：未采集")

    agent_items = [it for it in items if it["speaker"] == ROLE_AGENT]
    durations = [d for d in (_item_duration(it) for it in agent_items)
                 if d is not None]
    if durations:
        lines.append(
            f"- 机器人播报：平均 {sum(durations) / len(durations):.1f}s / "
            f"最长 {max(durations):.1f}s / 共 {len(durations)} 句")
    else:
        lines.append("- 机器人播报：未采集")

    hang = (detail.get("summary") or {}).get("isUserHangup")
    if hang is True:
        lines.append("- 挂机侧：客户侧")
    elif hang is False:
        lines.append("- 挂机侧：机器人侧")
    else:
        lines.append("- 挂机侧：未采集")
    return lines


def render_transcript_md(record: dict, detail: dict, *,
                         include_call_info: bool = True,
                         include_line_meta: bool = True) -> str:
    """渲染单条通话 Markdown。

    include_call_info=False -> 只输出对话行；
    include_line_meta=False -> 对话行不带轮次/时间/打断前缀。
    不提供自动脱敏；导出物含客户信息，需人工控制使用范围。
    """
    record = record or {}
    items = transcript_items(detail)
    t0 = _rel_start(items)
    if include_call_info:
        lines = _call_summary_lines(record, detail, items)
        lines.insert(2, "- 安全提示：导出物含客户信息，禁止外传"
                        "（当前版本未提供自动脱敏）")
        lines += ["", "## 对话", ""]
    else:
        lines = []
    if not items:
        lines.append("（无对话文本）")
    else:
        for it in items:
            prefix = _line_prefix(it, t0) if include_line_meta else ""
            lines.append(f"{prefix}{it['speaker']}:{it['text']}")
    return "\n".join(lines).rstrip() + "\n"


def _duration_sec(record: dict) -> str:
    try:
        return f"{int(record.get('Duration') or 0) / 1000:.1f}"
    except (TypeError, ValueError):
        return ""


def _env_label(env: str) -> str:
    return {"preview": "测试", "online": "线上"}.get(env or "", env or "")


def _record_meta(record: dict) -> dict:
    """manifest.items 的公共元数据（V3）。"""
    record = record or {}
    return {
        "create_time": record.get("CreateDate") or "",
        "duration_sec": round(int(record.get("Duration") or 0) / 1000, 1),
        "rounds": record.get("Rounds"),
        "grading": (record.get("GradingInfo") or {}).get(
            "RobotGrading") or "",
        "is_nonhuman": record.get("IsNonhumanAnswer"),
        "env": record.get("Env") or "",
    }


def analyze_items(record: dict, detail: dict, items: list[dict],
                  keywords: list[str]) -> dict:
    """单条通话统计（B5）。"""
    record = record or {}
    detail = detail or {}
    text = " ".join(it["text"] for it in items)
    bot = [it for it in items if it["speaker"] == ROLE_AGENT]
    user = [it for it in items if it["speaker"] == ROLE_USER]
    bot_lens = [len(it["text"]) for it in bot]
    durations = [d for d in (_item_duration(it) for it in items)
                 if d is not None]
    hang = (detail.get("summary") or {}).get("isUserHangup")
    nodes = [it.get("node") for it in bot if it.get("node")]
    return {
        "call_id": record.get("CallID") or record.get("SessionID") or "",
        "rounds": record.get("Rounds"),
        "duration_sec": round(int(record.get("Duration") or 0) / 1000, 1),
        "env": record.get("Env") or "",
        "connected": bool(record.get("IsConnected")),
        "grading": (record.get("GradingInfo") or {}).get("RobotGrading") or "",
        "nonhuman": record.get("IsNonhumanAnswer"),
        "bot_turns": len(bot),
        "user_turns": len(user),
        "bot_avg_chars": round(sum(bot_lens) / len(bot_lens), 1)
                         if bot_lens else 0,
        "bot_max_chars": max(bot_lens) if bot_lens else 0,
        "bot_over_25": sum(1 for n in bot_lens if n > 25),
        "interrupt_success": sum(
            1 for it in items if it.get("interrupt") == "打断成功"),
        "interrupt_attempted": sum(
            1 for it in items if it.get("interrupt") == "尝试打断"),
        "hangup_side": ("客户侧" if hang is True
                        else "机器人侧" if hang is False else "未采集"),
        "last_node": nodes[-1] if nodes else "",
        "nodes": nodes,
        "keyword_hits": {k: text.count(k) for k in keywords if k},
        "speech_seconds": round(sum(durations), 1) if durations else 0,
    }


def _atomic_write_text(path: Path, text: str) -> None:
    """临时文件 + os.replace，避免中断留下半截文件（B2）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent),
                               prefix=f".{path.stem}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _write_json(path: Path, data: dict) -> None:
    _atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))


def _aggregate_summary(stats: list[dict], keywords: list[str]) -> dict:
    n = len(stats)
    grading = Counter(s["grading"] or "无定级" for s in stats)
    env = Counter(s["env"] or "未知" for s in stats)
    hang = Counter(s["hangup_side"] for s in stats)
    kw = Counter()
    for s in stats:
        for k, v in s["keyword_hits"].items():
            kw[k] += v
    bot_over = sum(s["bot_over_25"] for s in stats)
    bot_total = sum(s["bot_turns"] for s in stats)
    node_reach = Counter()
    node_last = Counter()
    for s in stats:
        for name in set(s.get("nodes") or []):
            node_reach[name] += 1
        if s.get("last_node"):
            node_last[s["last_node"]] += 1
    node_stats = {}
    for name, reached in node_reach.most_common():
        last = node_last.get(name, 0)
        node_stats[name] = {
            "reached_calls": reached,
            "last_node_calls": last,
            # 末节点/到达该节点（节点挂机概率）；另附占全部通话比例
            "last_node_ratio": round(last / reached, 3) if reached else 0,
            "last_node_share": round(last / n, 3) if n else 0,
        }
    return {
        "calls": n,
        "avg_rounds": round(sum(float(s["rounds"] or 0) for s in stats) / n, 2)
                      if n else 0,
        "avg_duration_sec": round(
            sum(float(s["duration_sec"] or 0) for s in stats) / n, 1)
            if n else 0,
        "avg_speech_seconds": round(
            sum(float(s["speech_seconds"] or 0) for s in stats) / n, 1)
            if n else 0,
        "grading": dict(grading),
        "env": dict(env),
        "hangup_side": dict(hang),
        "bot_turns": bot_total,
        "bot_over_25": bot_over,
        "bot_over_25_ratio": round(bot_over / bot_total, 3)
                             if bot_total else 0,
        "interrupt_success": sum(s["interrupt_success"] for s in stats),
        "interrupt_attempted": sum(s["interrupt_attempted"] for s in stats),
        "keyword_hits": {k: kw.get(k, 0) for k in keywords},
        "node_stats": node_stats,
    }


def _render_summary_md(summary: dict) -> str:
    lines = [
        "# 通话文本统计",
        "",
        f"- 分析通话数：{summary['calls']}",
        f"- 平均交互轮次：{summary['avg_rounds']}",
        f"- 平均通话时长：{summary['avg_duration_sec']} 秒",
        f"- 平均语音时长：{summary['avg_speech_seconds']} 秒",
        f"- 机器人句数：{summary['bot_turns']}",
        f"- 机器人超 25 字句数：{summary['bot_over_25']}"
        f"（{summary['bot_over_25_ratio']:.1%}）",
        f"- 打断成功：{summary['interrupt_success']} 次；"
        f"尝试打断：{summary['interrupt_attempted']} 次",
        "",
        "## 意向等级分布",
    ]
    lines += [f"- {k}：{v}" for k, v in summary["grading"].items()] or ["- 无"]
    lines += ["", "## 环境分布"]
    lines += [f"- {_env_label(k)}：{v}" for k, v in summary["env"].items()] \
        or ["- 无"]
    lines += ["", "## 挂机侧"]
    lines += [f"- {k}：{v}" for k, v in summary["hangup_side"].items()] \
        or ["- 无"]
    lines += ["", "## 节点分布（到达/末节点）"]
    node_stats = summary.get("node_stats") or {}
    if node_stats:
        lines.append("| 节点 | 到达通话数 | 作为末节点通话数 | "
                     "末节点/到达 | 占全部通话 |")
        lines.append("|---|---|---|---|---|")
        for name, st in node_stats.items():
            lines.append(f"| {name} | {st['reached_calls']} | "
                         f"{st['last_node_calls']} | "
                         f"{st['last_node_ratio']:.1%} | "
                         f"{st.get('last_node_share', 0):.1%} |")
    else:
        lines.append("- 未采集")
    lines += ["", "## 关键词命中"]
    lines += [f"- {k}：{v}" for k, v in summary["keyword_hits"].items()] \
        or ["- 无"]
    return "\n".join(lines) + "\n"


def export_transcripts(
        client, records: list[dict], out_dir: Path, *,
        skip_existing: bool = True, workers: int = 4,
        include_call_info: bool = True, include_line_meta: bool = True,
        analyze: bool = False,
        keywords: list[str] | None = None, limit: int | None = None,
        matched_total: int | None = None,
        manifest_extra: dict | None = None) -> dict:
    """逐条导出 <out_dir>/<通话ID>.md（B1/B2/B5/B8）。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    all_records = list(records or [])
    if limit is not None:
        all_records = all_records[:max(0, int(limit))]
    keyword_list = [str(k).strip() for k in (keywords or DEFAULT_KEYWORDS)
                    if str(k).strip()]

    local = threading.local()

    def _worker_client():
        c = getattr(local, "client", None)
        if c is None:
            clone = getattr(client, "clone", None)
            c = clone() if callable(clone) else client
            local.client = c
        return c

    def _one(rec: dict) -> dict:
        cid = str(rec.get("CallID") or rec.get("SessionID") or "").strip()
        meta = _record_meta(rec)
        if not cid:
            return {"status": "failed", "call_id": "", "meta": meta,
                    "error": "记录缺少 CallID/SessionID"}
        path = out_dir / (safe_filename(cid) + ".md")
        if skip_existing and path.exists():
            return {"status": "existing", "call_id": cid, "meta": meta,
                    "file": str(path)}
        if is_data_pruned(rec):
            return {"status": "pruned", "call_id": cid, "meta": meta,
                    "reason": "数据已清除（页面「操作」列为“数据已清除”，"
                              "无法查看详细/获取通话文本）"}
        try:
            c = _worker_client() if workers > 1 else client
            detail = c.fetch_call_transcript(rec)
            md = render_transcript_md(
                rec, detail, include_call_info=include_call_info,
                include_line_meta=include_line_meta)
            _atomic_write_text(path, md)
            item = {"status": "ok", "call_id": cid, "meta": meta,
                    "file": str(path)}
            if analyze:
                item["stats"] = analyze_items(
                    rec, detail, transcript_items(detail), keyword_list)
            return item
        except Exception as e:  # noqa: BLE001 - 单条失败不影响其余记录
            return {"status": "failed", "call_id": cid, "meta": meta,
                    "error": str(e)[:500]}

    if workers > 1 and len(all_records) > 1:
        with ThreadPoolExecutor(max_workers=max(1, int(workers))) as ex:
            results = list(ex.map(_one, all_records))
    else:
        results = [_one(rec) for rec in all_records]

    files, skipped, skipped_existing, failed, stats = [], [], [], [], []
    items = []
    for r in results:
        status = r["status"]
        meta = r.get("meta") or {}
        if status == "ok":
            files.append({"call_id": r["call_id"], "file": r["file"]})
            if "stats" in r:
                stats.append(r["stats"])
            item = {"call_id": r["call_id"], "status": "ok",
                    "file": Path(r["file"]).name, **meta}
            if "stats" in r:
                item["stats"] = r["stats"]
            items.append(item)
        elif status == "existing":
            skipped_existing.append({"call_id": r["call_id"],
                                     "file": r["file"]})
            items.append({"call_id": r["call_id"], "status": "existing",
                          "file": Path(r["file"]).name, **meta})
        elif status == "pruned":
            skipped.append({"call_id": r["call_id"],
                            "reason": r["reason"]})
            items.append({"call_id": r["call_id"], "status": "pruned",
                          "reason": r["reason"], **meta})
        else:
            failed.append({"call_id": r["call_id"],
                           "error": r["error"]})
            items.append({"call_id": r["call_id"], "status": "failed",
                          "error": r["error"], **meta})

    manifest = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "query": manifest_extra or {},
        "total_matched": matched_total if matched_total is not None
                         else len(all_records),
        "fetched": len(all_records),
        "requested_limit": limit,
        "workers": workers,
        "analyze": analyze,
        "exported": len(files),
        "skipped_existing": len(skipped_existing),
        "skipped_pruned": len(skipped),
        "failed": len(failed),
        "items": items,
    }
    _write_json(out_dir / "manifest.json", manifest)
    _atomic_write_text(
        out_dir / "安全提示.txt",
        "导出物含客户信息，禁止外传。\n"
        "当前版本不提供自动脱敏，请人工控制目录权限、使用范围与留存时间。\n")

    result = {
        "total": len(all_records),
        "fetched": len(all_records),
        "matched_total": manifest["total_matched"],
        "exported": len(files),
        "skipped_existing": len(skipped_existing),
        "skipped_pruned": len(skipped),
        "failed_count": len(failed),
        "files": files,
        "skipped": skipped,
        "skipped_existing_items": skipped_existing,
        "failed": failed,
        "manifest_file": str(out_dir / "manifest.json"),
    }
    if analyze:
        summary = _aggregate_summary(stats, keyword_list)
        summary["keywords"] = keyword_list
        summary["analyzed_calls"] = len(stats)
        _write_json(out_dir / "summary.json", summary)
        _atomic_write_text(out_dir / "summary.md",
                           _render_summary_md(summary))
        result["summary_file"] = str(out_dir / "summary.md")
        result["summary"] = summary
    return result