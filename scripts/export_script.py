#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导出剧本：把指定剧本导出为 JSON 文件（控制台"导出剧本"功能）。

接口：GET /console/api/v2/projects/{p}/products/9/services/{s}/
      llm/dialogue_flow_script/export?group_id={g}
响应为文件下载流（application/octet-stream），内容为 {"data":..., "checksum":...}。

注意：导出文件含服务端生成的 checksum（无法本地构造），
修改内容后导入可能被服务端校验拒绝，导入请使用导出原样文件。

用法：
    python scripts/export_script.py llm_xxx

结果文件：result/{时间_账号_导出剧本}/{剧本名}.json + info.json
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from volc_aibot.client import VolcAIBotClient          # noqa: E402
from volc_aibot.logging_util import setup_logging      # noqa: E402
from volc_aibot.result import new_result_dir, safe_filename, write_json  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="导出剧本为 JSON 文件")
    ap.add_argument("script_id", help="剧本ID（llm_ 开头，如 llm_xxx）")
    ap.add_argument("--cookie-api", default=None,
                    help="chrome_capture_operate Cookie 服务地址（默认 127.0.0.1:33445）")
    args = ap.parse_args()

    logger = setup_logging()
    client = VolcAIBotClient(cookie_api=args.cookie_api, logger=logger)

    logger.info("导出剧本: %s", args.script_id)
    coords = client.resolve_script(args.script_id)
    logger.info("剧本 %s -> ServiceID=%s GroupID=%s ProjectID=%s",
                args.script_id, coords["service"], coords["group"], coords["project"])

    filename, content = client.export_script(args.script_id)
    logger.info("导出完成: %s（%d 字节）", filename, len(content))

    out_dir = new_result_dir("导出剧本")
    export_path = out_dir / safe_filename(filename)
    export_path.write_bytes(content)

    sha256 = hashlib.sha256(content).hexdigest()
    info = {
        "script_id": args.script_id,
        "agent_name": coords.get("agent_name"),
        "service_id": coords["service"],
        "group_id": coords["group"],
        "filename": filename,
        "size_bytes": len(content),
        "sha256": sha256,
        "export_file": str(export_path),
    }
    info_path = write_json(out_dir / "info.json", info)

    print(f"\n剧本「{coords.get('agent_name')}」导出成功")
    print(f"  导出文件: {export_path}")
    print(f"  大小: {len(content)} 字节，sha256: {sha256[:16]}...")
    print(f"  信息文件: {info_path}")
    print("\n提示：导出文件含服务端 checksum，请原样用于导入，勿手工修改内容。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
