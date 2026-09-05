#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""优惠券问题多次复现尝试（按原对话文本驱动，最多 100 次，可并发）。

⚠ 资源提示：文本对话测试（talk）占用生产实际外呼资源——
不要使用太大的并发（默认 10），建议在晚上测试；遇服务端临时限制
（HTTP 403）会自动暂停 90 秒等待恢复。

用途：验证「变量中优惠券全空但机器人说有优惠券」是否偶发。
规则（问题分析.md）：
- 按原对话文本（客户原话）测试；未复现则多次尝试，最多 100 次；
- 并发数默认 20（同一剧本的 talk 为无状态同步 HTTP，
  每次尝试独立 session_id，服务端按 session 隔离，并发安全）；
- 复现判据：机器人话术中出现优惠券相关表述
  （免息券 / 折扣券 / 打X折 / 券 等），即视为“说了客户有优惠券”；
  出现“没有可用借款券 / 没有优惠券 / 没有免息券”类否定表述视为未复现。

用法：
    python scripts/repro_attempts.py llm_xxx \
        --attempts 100 --concurrency 20
    python scripts/repro_attempts.py llm_xxx --attempts 10 --concurrency 5

结果文件：result/{时间_优惠券复现尝试}/
- attempts/attempt_001.md ... 每次尝试的对话记录（**每次尝试完成后实时写入**，
  每行 机器人:xxx / 客户:xxx，判定结果与命中句在文件头）
- report.json   每次尝试的判定明细（含机器人回复全文）
- summary.md    汇总（复现率、样例回复）
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from volc_aibot.client import VolcAIBotClient          # noqa: E402
from volc_aibot.logging_util import setup_logging      # noqa: E402
from volc_aibot.result import (new_result_dir,         # noqa: E402
                               write_dialog_md, write_json)

# 原对话文本（生产通话）中客户的原话，按出现顺序截取至“优惠券话题”出现处
ORIGINAL_CUSTOMER_LINES = [
    "呃正银行",
    "微众银行你们是微众银行吗",
    "我微信银行有客服啊",
    "是什么样子的",
    "那肯定是实际要优惠啊距离 页面的页面的利率有什么用没用的嘛太显 搞的呢",
    "折扣券有没有折扣券",
    "免息券免息券",
]

# 复现判定关键词（机器人“说了客户有优惠券”）
# 肯定=“拥有表述”：明确说客户有某种券
POSITIVE_PATTERNS = [
    r"您有[一这张个]?(?:免息|折扣|优惠)?券",
    r"(?:有|配了|给您配了)[一这张个](?:免息券|折扣券|优惠券|\d+折券|借据券)",
    r"85折", r"整笔(?:借据)?打?\d*[折八]", r"打[一二三四五六七八九十\d]+折",
    r"前\d+天[直接]?免(?:利息|息)", r"专属的?优惠券",
]
# 否定/排除表述（明说没有券）——整句命中则该句不算“说有券”
NEGATIVE_PATTERNS = [
    r"没有看到可用借款券", r"没有(?:可用)?(?:的)?优惠券", r"没有免息券",
    r"没有折扣券", r"没有可用的?券", r"没有.{0,6}借款券",
    r"暂无(?:可用)?(?:借款)?券", r"留意.{0,10}(?:活动|通知)",
]

POSITIVE_RE = [re.compile(p) for p in POSITIVE_PATTERNS]
NEGATIVE_RE = [re.compile(p) for p in NEGATIVE_PATTERNS]


def _sentence_has_coupon(text: str) -> tuple[bool, list[str]]:
    """逐子句判定：是否构成“说客户有优惠券”。

    子句按 。！？，；，、切分：否定子句跳过，肯定子句命中即算。
    （“没有免息券，您现有的8折券” -> 前半跳过、后半命中）
    """
    import re as _re
    parts = _re.split(r"[。！？!?,，;；、\n]", text)
    matched = []
    for part in parts:
        if not part or any(n.search(part) for n in NEGATIVE_RE):
            continue
        hits = [p.pattern for p in POSITIVE_RE if p.search(part)]
        if hits:
            matched.extend(hits)
    return bool(matched), matched


def classify_reply(text: str) -> dict:
    """判定整段机器人话术是否构成“说客户有优惠券”。

    返回 {said_coupon, positive(命中句), negative(存在否定句), mixed}。
    """
    neg_sentences = [p.pattern for p in NEGATIVE_RE if p.search(text)]
    said, pos = _sentence_has_coupon(text)
    return {"said_coupon": said, "positive": pos, "negative": neg_sentences,
            "mixed": said and bool(neg_sentences)}


def run_one_attempt(client: VolcAIBotClient, coords: dict, context: str,
                    coupon_vars: dict) -> dict:
    """跑一次完整尝试（开场白 + 原通话客户话术），返回判定结果。"""
    session_id = str(uuid.uuid4())
    ctx = json.dumps({**json.loads(context), **coupon_vars},
                     ensure_ascii=False, sort_keys=True)
    robot_texts: list[str] = []
    try:
        data = client.talk(coords, ctx, "", session_id, 0)
        texts, _, _ = VolcAIBotClient.extract_reply(data)
        robot_texts.extend(texts)
        for i, line in enumerate(ORIGINAL_CUSTOMER_LINES, start=1):
            data = client.talk(coords, ctx, line, session_id, i)
            texts, _, completed = VolcAIBotClient.extract_reply(data)
            robot_texts.extend(texts)
            if completed:
                break
    except Exception as e:  # noqa: BLE001 - 单次失败记录后继续
        return {"ok": False, "error": str(e)[:300], "said_coupon": None,
                "robot_texts": robot_texts}

    full = "\n".join(robot_texts)
    verdict = classify_reply(full)
    # 定位首个命中子句（便于人工复核）
    hit_line = ""
    import re as _re
    for part in _re.split(r"[。！？!?,，;；、\n]", full):
        if not part or any(n.search(part) for n in NEGATIVE_RE):
            continue
        if any(p.search(part) for p in POSITIVE_RE):
            hit_line = part[:200]
            break
    return {"ok": True, "said_coupon": verdict["said_coupon"],
            "positive": verdict["positive"], "negative": verdict["negative"],
            "mixed": verdict.get("mixed", False),
            "coupon_hit_line": hit_line,
            "robot_texts": robot_texts}


def build_dialog_items(robot_texts: list[str]) -> list[dict]:
    """把单次尝试的机器人回复序列还原为对话条目（含客户原话）。

    顺序约定：robot_texts[0]=开场白；第 i>=1 条是客户第 i-1 句后的回复。
    """
    items: list[dict] = []
    if not robot_texts:
        return items
    items.append({"Speaker": 1, "Content": robot_texts[0]})
    for i, text in enumerate(robot_texts[1:], start=1):
        if i - 1 < len(ORIGINAL_CUSTOMER_LINES):
            items.append({"Speaker": 2, "Content": ORIGINAL_CUSTOMER_LINES[i - 1]})
        else:
            items.append({"Speaker": 2, "Content": "..."})
        items.append({"Speaker": 1, "Content": text})
    return items


def write_attempt_md(out_dir: Path, attempt: int, result: dict,
                     script_id: str) -> Path:
    """单次尝试的对话写 md（每行 机器人:xxx / 客户:xxx）。"""
    md = out_dir / "attempts" / f"attempt_{attempt + 1:03d}.md"
    verdict = ("复现!" if result.get("said_coupon")
               else "未复现" if result.get("ok") else f"出错: {result.get('error', '')[:80]}")
    header = [
        f"# 复现尝试 #{attempt + 1}",
        f"- 剧本: {script_id}",
        f"- 判定: {verdict}",
    ]
    if result.get("coupon_hit_line"):
        header.append(f"- 命中句: {result['coupon_hit_line'][:120]}")
    write_dialog_md(md, header, build_dialog_items(result.get("robot_texts") or []))
    return md


def main() -> int:
    ap = argparse.ArgumentParser(description="优惠券问题多次复现尝试")
    ap.add_argument("script_id", help="剧本ID（如 llm_xxx）")
    ap.add_argument("--attempts", type=int, default=100,
                    help="最多尝试次数（默认 100）")
    ap.add_argument("--concurrency", type=int, default=10,
                    help="并发数（默认 10。⚠ 本接口占用生产实际外呼资源，"
                         "请勿使用太大并发，建议晚上测试；遇服务端 403 "
                         "临时限制会自动暂停 90 秒）")
    ap.add_argument("--empty-value", default="无",
                    help="优惠券空变量取值（默认 无；可传 空串）")
    ap.add_argument("--var-file", default=None,
                    help="JSON 变量覆盖文件 {key: 值}（如真实场景变量；"
                         "优惠券字段会按 --empty-value 强制置空，其余按文件）")
    ap.add_argument("--cookie-api", default=None,
                    help="chrome_capture_operate Cookie 服务地址")
    args = ap.parse_args()

    logger = setup_logging()
    client = VolcAIBotClient(cookie_api=args.cookie_api, logger=logger)
    out_dir = new_result_dir("优惠券复现尝试")

    coords = client.resolve_script(args.script_id)
    check = client.check_preview_published(coords)
    if not check["published"]:
        raise SystemExit("错误：剧本测试版本未发布，请先 publish_preview.py")

    # 基础 context：测试版本变量当前值，可被 --var-file 覆盖（真实场景变量）
    variables = client.fetch_preview_variable_values(coords=coords)
    if args.var_file:
        overrides = json.loads(Path(args.var_file).read_text(encoding="utf-8"))
        unknown = [k for k in overrides if k not in variables]
        if unknown:
            raise SystemExit(f"错误：--var-file 中有剧本不存在的变量: {unknown[:5]}")
        variables.update(overrides)
    base_context = json.dumps(variables, ensure_ascii=False, sort_keys=True)
    # 优惠券字段强制置空值（测试目标：全空时机器人是否说有券）
    coupon_vars = {k: args.empty_value for k in variables if k.startswith("coupon_")}
    if not coupon_vars:
        raise SystemExit("错误：剧本无优惠券变量")
    logger.info("开始复现尝试: 剧本 %s，%d 次 x 并发 %d，优惠券变量置为 %r（%d 个字段）%s",
                args.script_id, args.attempts, args.concurrency,
                args.empty_value, len(coupon_vars),
                f"，另覆盖 {len(args.var_file or '') and len(overrides) or 0} 个真实值变量"
                if args.var_file else "")

    results: list[dict] = []
    cooldown_until = [0.0]   # 403 冷却截止时间（服务端临时限制时暂停压测）

    def _maybe_cooldown(error_text: str) -> None:
        """遇服务端 403（会话被临时限制）时暂停压测 90 秒。

        实测：持续高并发对话后，火山服务端会临时吊销会话操作权限
        （HTTP 403, code=103 权限语义），停止压测 1~2 分钟自动恢复。
        非并发压测场景（脚本/MCP 日常使用）从未出现。
        """
        if "HTTP 403" not in error_text:
            return
        wait = cooldown_until[0] - time.time()
        if wait > 0:
            return   # 已在冷却中
        cooldown_until[0] = time.time() + 90
        print("  [服务端临时限制(HTTP 403)] 暂停压测 90 秒等待恢复...", flush=True)
        time.sleep(90)

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = {pool.submit(run_one_attempt, client, coords,
                               base_context, coupon_vars): i
                   for i in range(args.attempts)}
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                r = fut.result()
            except Exception as e:  # noqa: BLE001
                r = {"ok": False, "error": str(e)[:300], "said_coupon": None}
            r["attempt"] = idx
            results.append(r)
            # 每次尝试结束后实时写独立 md（attempts/attempt_XXX.md）
            write_attempt_md(out_dir, idx, r, args.script_id)
            mark = ("复现!" if r.get("said_coupon")
                    else "ok" if r.get("ok") else "ERR")
            print(f"  尝试 {idx + 1}/{args.attempts}: {mark}", flush=True)
            if not r.get("ok"):
                _maybe_cooldown(r.get("error") or "")

    results.sort(key=lambda r: r.get("attempt", 0))
    write_json(out_dir / "report.json", {
        "script_id": args.script_id, "attempts": args.attempts,
        "concurrency": args.concurrency, "empty_value": args.empty_value,
        "results": results,
    })

    ok_results = [r for r in results if r.get("ok")]
    repro = [r for r in ok_results if r.get("said_coupon")]
    errors = [r for r in results if not r.get("ok")]
    rate = (len(repro) / len(ok_results) * 100) if ok_results else 0.0

    lines = [
        "# 优惠券问题复现尝试汇总",
        "",
        f"- 剧本: {args.script_id}",
        f"- 尝试: {len(results)} 次（并发 {args.concurrency}），"
        f"优惠券变量全部置为 {args.empty_value!r}",
        f"- 成功完成: {len(ok_results)}，出错: {len(errors)}",
        f"- **复现次数: {len(repro)}（复现率 {rate:.1f}%）**",
        "",
    ]
    if repro:
        lines.append("## 复现样例（首次命中句）")
        for r in repro[:10]:
            lines.append(f"- 尝试#{r['attempt'] + 1}: {r.get('coupon_hit_line', '')[:120]}")
    else:
        lines.append("## 结论")
        lines.append("全部尝试均未复现：优惠券变量全空时，机器人始终回答"
                     "“没有可用借款券”，未出现“说客户有优惠券”。")
    if errors:
        lines.append("")
        lines.append("## 出错的尝试")
        for r in errors[:5]:
            lines.append(f"- 尝试#{r['attempt'] + 1}: {r.get('error', '')[:150]}")
    (out_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"\n=== 汇总 ===")
    print(f"  成功: {len(ok_results)}/{len(results)}  复现: {len(repro)}"
          f"（{rate:.1f}%）  出错: {len(errors)}")
    print(f"  汇总: {out_dir / 'summary.md'}")
    print(f"  明细: {out_dir / 'report.json'}")
    return 0 if ok_results else 1


if __name__ == "__main__":
    sys.exit(main())
