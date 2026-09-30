# -*- coding: utf-8 -*-
"""以 Chrome 打开 URL（prompt 2026-09-30：打开网页用 Chrome 而非默认浏览器）。

本工具整条链路依赖日常 Chrome（登录火山引擎控制台 + 插件推送 Cookie），
打开页面统一用 Chrome 可保证环境与登录态一致。Chrome 未安装/未找到时
回退默认浏览器，不致失败。
"""

from __future__ import annotations

import logging
import os
import webbrowser
from pathlib import Path

logger = logging.getLogger("volc_aibot")

# Chrome 常见安装路径（64 位系统级 / 32 位系统级 / 用户级）
_CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
)


def find_chrome() -> str:
    """定位 chrome.exe：注册表 App Paths（HKLM/HKCU）+ 常见安装路径。

    返回可执行文件完整路径；未安装/未找到返回空串。
    """
    try:
        import winreg
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(
                        root, r"SOFTWARE\Microsoft\Windows\CurrentVersion"
                        r"\App Paths\chrome.exe") as k:
                    val, _ = winreg.QueryValueEx(k, "")
                    if val and Path(str(val)).is_file():
                        return str(val)
            except OSError:
                continue
    except ImportError:
        pass      # 非 Windows（本工具面向 Windows，防御性兜底）
    for p in _CHROME_CANDIDATES:
        cand = Path(os.path.expandvars(p))
        if cand.is_file():
            return str(cand)
    return ""


def open_url_in_chrome(url: str) -> bool:
    """用 Chrome 打开 URL；未找到 Chrome 或启动失败时回退默认浏览器。

    返回 True 表示经 Chrome 打开，False 表示走了默认浏览器回退。
    """
    chrome = find_chrome()
    if chrome:
        try:
            import subprocess
            subprocess.Popen([chrome, url])
            logger.info("已用 Chrome 打开页面: %s", url)
            return True
        except OSError as e:
            logger.warning("Chrome 启动失败(%s)，回退默认浏览器: %s", e, url)
    webbrowser.open(url)
    return False
