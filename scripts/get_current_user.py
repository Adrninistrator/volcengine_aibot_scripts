#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""获取当前登录的账号（GET /console/api/v2/user，返回的 id 即账号）。

该接口同时是账号守卫的基础：所有其他脚本/MCP 工具执行请求前都会先
校验登录账号是否为全局配置（~/.volcengine_aibot_scripts/global.json）
中允许的账号；同一 Cookie 未变化时免重复检查。
若全局配置中账号为空，运行其他功能时会提示先打开配置页设置
（本脚本可加 --save-allowed 直接把当前账号写入全局配置）。

用法：
    python scripts/get_current_user.py
    python scripts/get_current_user.py --save-allowed   # 将当前账号设为允许账号

结果文件：result/{时间_账号_获取当前登录的账号}/user.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from volc_aibot import global_config                      # noqa: E402
from volc_aibot.client import VolcAIBotClient             # noqa: E402
from volc_aibot.logging_util import setup_logging         # noqa: E402
from volc_aibot.result import new_result_dir, write_json  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="获取当前登录的账号")
    ap.add_argument("--save-allowed", action="store_true",
                    help="把当前登录账号写入全局配置的允许账号（唯一）")
    ap.add_argument("--cookie-api", default=None,
                    help="chrome_capture_operate Cookie 服务地址（默认 127.0.0.1:33445）")
    args = ap.parse_args()

    logger = setup_logging()
    # 获取账号本身不受账号守卫限制（skip 由 client 内部处理）
    client = VolcAIBotClient(cookie_api=args.cookie_api, logger=logger,
                             enforce_account=False)

    logger.info("获取当前登录账号 ...")
    user = client.get_current_user()
    account = str(user.get("id"))
    print(f"\n当前登录账号（id）: {account}")
    print(f"  用户名:  {user.get('username')}")
    print(f"  邮箱:    {user.get('email')}")
    print(f"  类型:    {user.get('type')}")

    out_dir = new_result_dir("获取当前登录的账号")
    out = write_json(out_dir / "user.json", user)
    print(f"\n结果已写入: {out}")

    if args.save_allowed:
        global_config.set_allowed_account(account)
        print(f"\n已将账号 {account} 写入全局配置允许账号："
              f"{global_config.config_path()}")
    else:
        allowed = global_config.get_allowed_account()
        if not allowed:
            print("\n提醒：全局配置中允许账号为空。后续功能执行前会要求配置；"
                  "可重跑本脚本加 --save-allowed，或在配置页设置。")
        elif allowed != account:
            print(f"\n注意：当前账号 {account} 与允许账号 {allowed} 不一致，"
                  f"其他功能将被拒绝执行。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
