# -*- coding: utf-8 -*-
"""系统自启动（Windows 注册表 HKCU Run 键，prompt 2026-09-07）。

默认不开启；由用户在「配置参数」页面切换（写注册表 HKCU\\...\\Run，
仅当前用户，无需管理员权限；删除键值即关闭）。

自启动命令：pythonw <项目根>\\mcp_server.py（无窗口 + 系统托盘），
端口用全局配置（mcp_server 默认行为），不传 --port 以便改端口后自启动
仍跟随配置。

注意：注册表键名含项目路径哈希（避免多份部署互相覆盖）。
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

try:
    import winreg
except ImportError:      # 非 Windows（理论不发生）
    winreg = None

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _reg_name() -> str:
    """注册表值名：固定前缀 + 项目路径短哈希（多部署互不覆盖）。"""
    import hashlib
    h = hashlib.md5(str(PROJECT_ROOT).lower().encode("utf-8")).hexdigest()[:6]
    return f"volc_aibot_scripts_{h}"


def _pythonw_path() -> str:
    """pythonw 绝对路径（venv 优先，无则系统 pythonw）。"""
    venv = PROJECT_ROOT / ".venv" / "Scripts" / "pythonw.exe"
    if venv.is_file():
        return str(venv)
    return "pythonw"


def _command() -> str:
    """自启动命令行：pythonw 后台运行 mcp_server.py。"""
    return f'"{_pythonw_path()}" "{PROJECT_ROOT / "mcp_server.py"}"'


def is_autostart_enabled() -> bool:
    """当前是否已开启自启动（注册表值存在且命令指向本项目）。"""
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            val, _ = winreg.QueryValueEx(k, _reg_name())
            return str(PROJECT_ROOT / "mcp_server.py").lower() \
                in str(val).lower()
    except (FileNotFoundError, OSError):
        return False


def set_autostart(enabled: bool) -> dict:
    """开启/关闭自启动，返回 {autostart, command}。

    开启：写 Run 键（REG_SZ 命令行）；关闭：删除该键值（不存在视为成功）。
    """
    if winreg is None:
        return {"autostart": False, "error": "仅支持 Windows"}
    name = _reg_name()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY,
                            0, winreg.KEY_SET_VALUE) as k:
            if enabled:
                winreg.SetValueEx(k, name, 0, winreg.REG_SZ, _command())
            else:
                try:
                    winreg.DeleteValue(k, name)
                except FileNotFoundError:
                    pass
        return {"autostart": is_autostart_enabled(),
                "command": _command() if enabled else ""}
    except OSError as e:
        return {"autostart": is_autostart_enabled(),
                "error": f"注册表操作失败: {e}"}


if __name__ == "__main__":
    # 命令行自检：python volc_aibot/autostart.py [on|off|status]
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    if action == "status":
        print(f"自启动: {'开启' if is_autostart_enabled() else '关闭'}")
        print(f"值名: {_reg_name()}")
        print(f"命令: {_command()}")
    elif action in ("on", "off"):
        r = set_autostart(action == "on")
        print(f"自启动: {'开启' if r.get('autostart') else '关闭'}"
              + (f" | 错误: {r['error']}" if r.get("error") else ""))
        if r.get("command"):
            print(f"命令: {r['command']}")
    else:
        print("用法: python volc_aibot/autostart.py [on|off|status]")
