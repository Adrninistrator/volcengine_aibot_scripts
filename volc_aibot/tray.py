# -*- coding: utf-8 -*-
"""系统托盘（prompt 要求：pythonw 无窗口运行，托盘双击/右键打开配置页，右键退出）。

实现：Windows 原生 Shell_NotifyIcon + ctypes（不引入 pystray 等额外依赖）。
- 托盘图标：使用程序内生成的小位图（蓝色方块 + "V"），无外部资源文件；
- 双击：打开配置页（webbrowser）；
- 右键菜单：「打开配置页」「退出」（退出结束整个进程）。

失败不致命：托盘创建失败时仅记日志（服务仍可用，stdout 模式可见）。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import threading
import webbrowser

logger = logging.getLogger("volc_aibot")

WM_DESTROY = 0x0002
WM_COMMAND = 0x0111
WM_APP = 0x8000
WM_TRAYICON = WM_APP + 1          # 托盘消息
WM_LBUTTONDBLCLK = 0x0203         # 双击
WM_RBUTTONUP = 0x0205             # 右键弹起
SC_CLOSE = 0xF060

NIM_ADD = 0
NIM_DELETE = 2

NIF_MESSAGE = 0x01
NIF_ICON = 0x02
NIF_TIP = 0x04

_IDM_OPEN = 40001
_IDM_EXIT = 40002

_kernel32 = ctypes.windll.kernel32
_user32 = ctypes.windll.user32
_shell32 = ctypes.windll.shell32

# ---------------------------------------------------------------- argtypes 声明
# 64 位 Windows 句柄超过 int 范围，ctypes 未声明类型时按 int 传参会触发
# “OverflowError: int too long to convert”（PyCharm 启动报错根因）。

_kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
_kernel32.GetModuleHandleW.restype = wt.HINSTANCE

_user32.LoadIconW.argtypes = [wt.HINSTANCE, wt.LPCWSTR]
_user32.LoadIconW.restype = wt.HICON

_user32.RegisterWindowMessageW.argtypes = [wt.LPCWSTR]
_user32.RegisterWindowMessageW.restype = wt.UINT

_user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
_user32.DefWindowProcW.restype = ctypes.c_long  # LRESULT

_user32.DestroyWindow.argtypes = [wt.HWND]
_user32.DestroyWindow.restype = wt.BOOL

_user32.PostQuitMessage.argtypes = [ctypes.c_int]

_user32.RegisterClassW.argtypes = [ctypes.c_void_p]
_user32.RegisterClassW.restype = wt.ATOM

_user32.CreateWindowExW.argtypes = [
    wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
_user32.CreateWindowExW.restype = wt.HWND

_user32.GetMessageW.argtypes = [ctypes.c_void_p, wt.HWND,
                                wt.UINT, wt.UINT]
_user32.GetMessageW.restype = ctypes.c_long

_user32.TranslateMessage.argtypes = [ctypes.c_void_p]
_user32.TranslateMessage.restype = wt.BOOL

_user32.DispatchMessageW.argtypes = [ctypes.c_void_p]
_user32.DispatchMessageW.restype = ctypes.c_long

_user32.CreatePopupMenu.argtypes = []
_user32.CreatePopupMenu.restype = wt.HMENU

_user32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ctypes.c_void_p,
                                wt.LPCWSTR]
_user32.AppendMenuW.restype = wt.BOOL

_user32.GetCursorPos.argtypes = [ctypes.c_void_p]
_user32.GetCursorPos.restype = wt.BOOL

_user32.SetForegroundWindow.argtypes = [wt.HWND]
_user32.SetForegroundWindow.restype = wt.BOOL

_user32.TrackPopupMenu.argtypes = [wt.HMENU, wt.UINT,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   wt.HWND, ctypes.c_void_p]
_user32.TrackPopupMenu.restype = wt.BOOL

_user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
_user32.PostMessageW.restype = wt.BOOL

_user32.DestroyMenu.argtypes = [wt.HMENU]
_user32.DestroyMenu.restype = wt.BOOL

_shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.c_void_p]
_shell32.Shell_NotifyIconW.restype = wt.BOOL


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", ctypes.c_uint),
        ("uFlags", wt.UINT), ("uCallbackMessage", wt.UINT),
        ("hIcon", wt.HICON), ("szTip", ctypes.c_wchar * 128),
        ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD),
        ("szInfo", ctypes.c_wchar * 256), ("uVersion", wt.UINT),
        ("szInfoTitle", ctypes.c_wchar * 64), ("dwInfoFlags", wt.DWORD),
    ]


def _create_app_icon():
    """托盘图标：使用 Windows 内置应用图标（LoadIcon + IDI_APPLICATION）。

    说明：先前用 CreateIcon 位图方式，因 ctypes 未声明参数类型导致
    “argument 11: OverflowError: int too long to convert”（bytes 被当
    int 传参）。改用 LoadIconW 加载系统标准图标，零位图数据、最可靠。
    """
    IDI_APPLICATION = 32512
    # MAKEINTRESOURCE：低 16 位为资源 ID，转为指针
    return _user32.LoadIconW(None, ctypes.c_wchar_p(IDI_APPLICATION))


def run_tray(page_url: str) -> None:
    """托盘消息循环（阻塞，需在独立线程运行）。"""
    try:
        _run_tray_loop(page_url)
    except Exception as e:  # noqa: BLE001 - 托盘失败不影响服务
        logger.warning("系统托盘创建失败（服务继续运行）: %s", e)


def _run_tray_loop(page_url: str) -> None:
    _user32.SetProcessDpiAwareness = getattr(
        _user32, "SetProcessDpiAwareness", None)

    WM_TASKBARCREATED = _user32.RegisterWindowMessageW("TaskbarCreated")

    class WNDCLASS(ctypes.Structure):
        _fields_ = [("style", wt.UINT), ("lpfnWndProc", ctypes.WINFUNCTYPE(
            ctypes.c_long, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)),
            ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
            ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
            ("hCursor", ctypes.c_void_p), ("hbrBackground", wt.HBRUSH),
            ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]

    def wnd_proc(hWnd, msg, wParam, lParam):
        if msg == WM_TRAYICON:
            if lParam == WM_LBUTTONDBLCLK:
                webbrowser.open(page_url)
            elif lParam == WM_RBUTTONUP:
                _popup_menu(hWnd)
            return 0
        if msg == WM_COMMAND:
            cmd = wParam & 0xFFFF
            if cmd == _IDM_OPEN:
                webbrowser.open(page_url)
            elif cmd == _IDM_EXIT:
                _user32.DestroyWindow(hWnd)
            return 0
        if msg == WM_TASKBARCREATED:
            _add_tray(hWnd, nid)
            return 0
        if msg == WM_DESTROY:
            nid = NOTIFYICONDATAW()
            nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            nid.hWnd = hWnd
            nid.uID = 1
            _shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
            _user32.PostQuitMessage(0)
            return 0
        return _user32.DefWindowProcW(hWnd, msg, wParam, lParam)

    wnd_proc_c = ctypes.WINFUNCTYPE(
        ctypes.c_long, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)(wnd_proc)

    wc = WNDCLASS()
    wc.style = 0
    wc.lpfnWndProc = wnd_proc_c
    wc.hInstance = _kernel32.GetModuleHandleW(None)
    wc.lpszClassName = "VolcAIBotTrayWnd"
    if not _user32.RegisterClassW(ctypes.byref(wc)):
        raise RuntimeError("RegisterClassW 失败")

    # argtypes 已在模块级统一声明（64 位句柄需按指针传）
    hWnd = _user32.CreateWindowExW(
        0, wc.lpszClassName, "volc-aibot tray", 0,
        0, 0, 0, 0, None, None, wc.hInstance, None)
    if not hWnd:
        raise RuntimeError("CreateWindowExW 失败")

    global _icon
    _icon = _create_app_icon()

    nid = NOTIFYICONDATAW()
    nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
    nid.hWnd = hWnd
    nid.uID = 1
    nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
    nid.uCallbackMessage = WM_TRAYICON
    nid.hIcon = _icon
    nid.szTip = "AI外呼机器人智能分析工具（火山引擎）-双击打开配置页"
    _add_tray(hWnd, nid)

    msg = wt.MSG()
    while _user32.GetMessageW(ctypes.byref(msg), 0, 0, 0) > 0:
        _user32.TranslateMessage(ctypes.byref(msg))
        _user32.DispatchMessageW(ctypes.byref(msg))
    # 退出（DestroyWindow -> PostQuitMessage -> 循环结束）
    import os
    os._exit(0)


def _add_tray(hWnd, nid) -> None:
    if _shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)) != 1:
        raise RuntimeError("Shell_NotifyIconW 失败（托盘不可用）")


def _popup_menu(hWnd) -> None:
    menu = _user32.CreatePopupMenu()
    _user32.AppendMenuW(menu, 0, _IDM_OPEN, "打开配置页")
    _user32.AppendMenuW(menu, 0, _IDM_EXIT, "退出")
    pos = wt.POINT()
    _user32.GetCursorPos(ctypes.byref(pos))
    # SetForegroundWindow 保证菜单可关闭
    _user32.SetForegroundWindow(hWnd)
    _user32.TrackPopupMenu(menu, 0, pos.x, pos.y, 0, hWnd, 0)
    _user32.PostMessageW(hWnd, 0x001F, 0, 0)  # WM_INITMENU
    _user32.DestroyMenu(menu)
