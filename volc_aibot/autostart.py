# -*- coding: utf-8 -*-
"""系统自启动（Windows 注册表 HKCU Run 键，prompt 2026-09-07）。

默认不开启；由用户在「配置参数」页面切换（写注册表 HKCU\\...\\Run，
仅当前用户，无需管理员权限；删除键值即关闭）。

自启动命令：pythonw <项目目录>\\mcp_server.py（无窗口 + 系统托盘），
端口用全局配置（mcp_server 默认行为），不带 --port 以便改端口后自启动
仍跟随配置。

注册表键名含项目路径哈希（区分不同目录的部署）。注意项目目录拷贝/
移动后自启动项不会跟随（仍指向旧目录）：在新目录的配置页重新勾选
保存即可迁移——开启时会自动清理其他目录的残留项（全局配置端口为
整机共享，多份自启动只会互相抢占端口，无法共存）。
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
_REG_PREFIX = "volc_aibot_scripts_"


def _reg_name() -> str:
    """注册表值名：固定前缀 + 项目路径短哈希（多部署互不覆盖）。"""
    import hashlib
    h = hashlib.md5(str(PROJECT_ROOT).lower().encode("utf-8")).hexdigest()[:6]
    return f"{_REG_PREFIX}{h}"


def _run_entries() -> dict[str, str]:
    """Run 键下全部值（值名 -> 命令行）；读取失败返回空。"""
    if winreg is None:
        return {}
    out: dict[str, str] = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            i = 0
            while True:
                try:
                    name, val, _t = winreg.EnumValue(k, i)
                except OSError:      # 枚举结束（ERROR_NO_MORE_ITEMS）
                    break
                out[name] = str(val)
                i += 1
    except OSError:
        return {}
    return out


def other_autostart_entries() -> list[dict]:
    """其他目录部署残留的自启动项（本工具前缀、但不属于当前目录）。

    典型场景：项目拷贝/移动到新目录使用，旧目录的自启动项仍在——开机
    会启动旧目录的服务并抢占端口（全局配置端口整机共享，只有一份能
    绑定成功）。配置页据此提示用户重新开启以完成迁移。
    """
    me = _reg_name()
    return [{"name": name, "command": cmd}
            for name, cmd in _run_entries().items()
            if name.startswith(_REG_PREFIX) and name != me]


def _clean_other_entries() -> list[str]:
    """删除其他目录的自启动项（开启自启动时调用），返回已删除的值名。"""
    removed: list[str] = []
    if not other_autostart_entries():
        return removed
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY,
                            0, winreg.KEY_SET_VALUE) as k:
            for e in other_autostart_entries():
                try:
                    winreg.DeleteValue(k, e["name"])
                    removed.append(e["name"])
                except OSError:
                    pass
    except OSError:
        pass
    return removed


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


def autostart_status() -> dict:
    """自启动状态汇总（配置页 GET /api/autostart 与 CLI status 共用）。

    返回 {autostart, command, reg_name, other_entries}：
    - autostart：当前是否开启；
    - command：开启后将执行的自启动命令行（pythonw 后台运行）；
    - reg_name：注册表值名（前缀+项目路径哈希，多部署互不覆盖）；
    - other_entries：其他目录部署的残留自启动项（迁移提示用）。
    prompt 2026-09-22：配置参数提供 HTTP 接口供 AI 查询/设置系统自启动，
    查询接口即基于本函数。
    """
    return {
        "autostart": is_autostart_enabled(),
        "command": _command(),
        "reg_name": _reg_name(),
        "other_entries": other_autostart_entries(),
    }


def set_autostart(enabled: bool) -> dict:
    """开启/关闭自启动，返回 {autostart, command, cleaned}。

    开启：写 Run 键（REG_SZ 命令行），并清理其他目录部署的残留自启动
    项（项目拷贝/迁移场景：端口整机共享，多份自启动无法共存）；
    关闭：删除本目录键值（不存在视为成功）。
    """
    if winreg is None:
        return {"autostart": False, "error": "仅支持 Windows"}
    name = _reg_name()
    cleaned: list[str] = []
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
        if enabled:
            cleaned = _clean_other_entries()
        return {"autostart": is_autostart_enabled(),
                "command": _command() if enabled else "",
                "cleaned": cleaned}
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
        others = other_autostart_entries()
        if others:
            print("其他目录残留项（重新开启自启动时会自动清理）:")
            for e in others:
                print(f"  {e['name']} = {e['command']}")
    elif action in ("on", "off"):
        r = set_autostart(action == "on")
        print(f"自启动: {'开启' if r.get('autostart') else '关闭'}"
              + (f" | 错误: {r['error']}" if r.get("error") else ""))
        if r.get("command"):
            print(f"命令: {r['command']}")
        if r.get("cleaned"):
            print("已清理其他目录残留项: " + ", ".join(r["cleaned"]))
    else:
        print("用法: python volc_aibot/autostart.py [on|off|status]")
