"""真窗口用例的 Win32 一侧：只对**被测进程自己的窗口**发消息，绝不注入全局输入。

这里刻意没有 `SendInput` / `keybd_event` / `mouse_event` / `SetCursorPos`——它们的主语
是「此刻前台的任何应用」，不是 Tavotto（`tests/desktop_windows/test_harness_scope.py`
逐个名字钉住它们不出现）。能做的只有三类：

* **按窗口句柄投递消息**：`WM_CLOSE`、`WM_COMMAND`（菜单项 / 加速键翻译之后的那一条）、
  `WM_SETTEXT` / `BM_CLICK`（系统文件对话框里的输入框与按钮）。句柄都先按**进程 id**
  过滤出来，打不到别的应用；
* **读菜单树**：`GetMenu` / `GetMenuStringW` 读的是 USER 对象，跨进程只读；
* **系统剪贴板**：本进程作为「另一个应用」读写 `CF_UNICODETEXT`——这正是
  「跨应用剪贴板」要量的那一侧。

全部 ctypes，零第三方依赖：这些用例跑在刚装好 NSIS 产物的 runner 上，装的越少，
「用例没跑起来」与「产品坏了」越不容易混在一起。
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass

if sys.platform != "win32":  # pragma: no cover - 由 conftest 在收集前挡住
    raise ImportError("_win32 只在 Windows 上可用")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WM_SETTEXT = 0x000C
WM_GETTEXT = 0x000D
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
BM_CLICK = 0x00F5
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
MF_BYPOSITION = 0x0400
GW_OWNER = 4

LRESULT = ctypes.c_ssize_t
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.EnumChildWindows.argtypes = [wintypes.HWND, WNDENUMPROC, wintypes.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindow.argtypes = [wintypes.HWND]
user32.GetWindow.argtypes = [wintypes.HWND, ctypes.c_uint]
user32.GetWindow.restype = wintypes.HWND
user32.GetDlgCtrlID.argtypes = [wintypes.HWND]
user32.PostMessageW.argtypes = [wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM]
user32.SendMessageW.argtypes = [wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM]
user32.SendMessageW.restype = LRESULT
user32.GetMenu.argtypes = [wintypes.HWND]
user32.GetMenu.restype = wintypes.HMENU
user32.GetSubMenu.argtypes = [wintypes.HMENU, ctypes.c_int]
user32.GetSubMenu.restype = wintypes.HMENU
user32.GetMenuItemCount.argtypes = [wintypes.HMENU]
user32.GetMenuItemID.argtypes = [wintypes.HMENU, ctypes.c_int]
user32.GetMenuItemID.restype = wintypes.UINT
user32.GetMenuStringW.argtypes = [
    wintypes.HMENU,
    wintypes.UINT,
    wintypes.LPWSTR,
    ctypes.c_int,
    wintypes.UINT,
]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE
user32.GetClipboardOwner.restype = wintypes.HWND
user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]


# ---------------------------------------------------------------- 进程


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


TH32CS_SNAPPROCESS = 0x00000002
PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
STILL_ACTIVE = 259
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
]


@dataclass(frozen=True)
class Proc:
    pid: int
    ppid: int
    name: str


def processes() -> list[Proc]:
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap in (None, INVALID_HANDLE_VALUE):
        raise OSError(ctypes.get_last_error(), "CreateToolhelp32Snapshot 失败")
    out: list[Proc] = []
    try:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(e)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            out.append(Proc(e.th32ProcessID, e.th32ParentProcessID, e.szExeFile))
            ok = kernel32.Process32NextW(snap, ctypes.byref(e))
    finally:
        kernel32.CloseHandle(snap)
    return out


class ProcHandle:
    """按**句柄**持有的一个进程。

    PID 会被复用：进程退出后同一个号很快会分给别的进程（2026-09-29 在测试机上，
    按陈旧 PID「收尾」杀掉了会话里的系统进程，整个交互会话随之消失）。持有一个打开
    的句柄期间，系统不会把这个 PID 分出去，所以判活与强杀一律经这个句柄。
    """

    def __init__(self, pid: int, name: str, handle: int, created: int):
        self.pid, self.name, self.handle, self.created = pid, name, handle, created

    def __repr__(self) -> str:
        return f"<{self.name} pid={self.pid}>"

    def alive(self) -> bool:
        code = wintypes.DWORD()
        ok = kernel32.GetExitCodeProcess(self.handle, ctypes.byref(code))
        return bool(ok) and code.value == STILL_ACTIVE

    def terminate(self, exit_code: int = 1) -> bool:
        """与任务管理器「结束任务」/ `Stop-Process -Force` 同一个系统调用：进程没有机会收尾。"""
        return bool(kernel32.TerminateProcess(self.handle, exit_code))

    def close(self) -> None:
        if self.handle:
            kernel32.CloseHandle(self.handle)
            self.handle = 0


class FILETIME(ctypes.Structure):
    _fields_ = [("lo", wintypes.DWORD), ("hi", wintypes.DWORD)]


kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(FILETIME)] * 4


def open_process(pid: int, name: str = "?") -> ProcHandle | None:
    """打开句柄并读出创建时间；进程已经没了就回 None。"""
    h = kernel32.OpenProcess(
        PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, pid
    )
    if not h:
        return None
    t = [FILETIME() for _ in range(4)]
    if not kernel32.GetProcessTimes(h, *(ctypes.byref(x) for x in t)):
        kernel32.CloseHandle(h)
        return None
    return ProcHandle(pid, name, h, (t[0].hi << 32) | t[0].lo)


def descendants(root: ProcHandle) -> list[ProcHandle]:
    """`root` 的全部后代，每个都按句柄持有（调用方负责 `close()`）。

    Toolhelp 快照里的父 PID 不随父进程退出而更新：一个老进程的「父 PID」可能恰好被
    后来的 root 复用。所以子进程必须**晚于**它的父进程创建，才认作后代。
    """
    kids: dict[int, list[Proc]] = {}
    for p in processes():
        kids.setdefault(p.ppid, []).append(p)
    out: list[ProcHandle] = []
    todo = [root]
    seen = {root.pid}
    while todo:
        parent = todo.pop()
        for c in kids.get(parent.pid, []):
            if c.pid in seen:
                continue
            h = open_process(c.pid, c.name)
            if h is None:
                continue
            if h.created < parent.created:
                h.close()  # 父 PID 是复用出来的巧合，不是这棵树
                continue
            seen.add(c.pid)
            out.append(h)
            todo.append(h)
    return out


# ---------------------------------------------------------------- 窗口


@dataclass(frozen=True)
class Window:
    hwnd: int
    pid: int
    cls: str
    title: str
    visible: bool


def _describe(hwnd) -> Window:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    cls = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, cls, 256)
    title = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, title, 512)
    return Window(
        int(hwnd or 0), pid.value, cls.value, title.value, bool(user32.IsWindowVisible(hwnd))
    )


def top_windows(pids: set[int] | None = None) -> list[Window]:
    """本会话里的顶层窗口；给了 `pids` 就只要这些进程的（打不到别的应用）。"""
    out: list[Window] = []

    def cb(hwnd, _l):
        w = _describe(hwnd)
        if pids is None or w.pid in pids:
            out.append(w)
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def child_windows(hwnd: int) -> list[tuple[Window, int]]:
    """全部后代窗口与各自的控件 id。"""
    out: list[tuple[Window, int]] = []

    def cb(h, _l):
        out.append((_describe(h), int(user32.GetDlgCtrlID(h))))
        return True

    user32.EnumChildWindows(hwnd, WNDENUMPROC(cb), 0)
    return out


def is_window(hwnd: int) -> bool:
    return bool(user32.IsWindow(hwnd))


user32.GetParent.argtypes = [wintypes.HWND]
user32.GetParent.restype = wintypes.HWND


def ancestors(hwnd: int, stop: int) -> list[int]:
    """`hwnd` 往上直到 `stop`（不含）的父窗口链。"""
    out = []
    h = user32.GetParent(hwnd)
    while h and int(h) != stop:
        out.append(int(h))
        h = user32.GetParent(h)
    return out


#: 系统「打开」对话框里文件名输入框所在的组合框（`cmb13`，commdlg 的固定控件 id）
FILE_NAME_COMBO_ID = 1148
#: 文件夹模式下「文件夹:」那一栏的输入框（`edt1`）
FOLDER_EDIT_ID = 1152
IDOK = 1
IDCANCEL = 2


def dialog_path_edit(dlg: int) -> int:
    """系统文件 / 文件夹对话框里那一个路径输入框。找不到或不唯一就抛（指代单例不许取第一个）。"""
    kids = child_windows(dlg)
    combo = {c.hwnd for c, cid in kids if cid == FILE_NAME_COMBO_ID}
    hits = [
        c.hwnd
        for c, cid in kids
        if c.cls == "Edit"
        and c.visible
        and (cid == FOLDER_EDIT_ID or combo.intersection(ancestors(c.hwnd, dlg)))
    ]
    if len(hits) != 1:
        raise AssertionError(
            f"对话框 {dlg:#x} 里的路径输入框有 {len(hits)} 个：{[(c, i) for c, i in kids if c.cls == 'Edit']}"
        )
    return hits[0]


def dialog_button(dlg: int, cid: int) -> int:
    hits = [c.hwnd for c, i in child_windows(dlg) if i == cid and c.cls == "Button" and c.visible]
    if len(hits) != 1:
        raise AssertionError(f"对话框 {dlg:#x} 里 id={cid} 的按钮有 {len(hits)} 个")
    return hits[0]


def foreground_hwnd() -> int:
    return int(user32.GetForegroundWindow() or 0)


def post(hwnd: int, msg: int, wparam: int = 0, lparam: int = 0) -> None:
    if not user32.PostMessageW(hwnd, msg, wparam, lparam):
        raise OSError(ctypes.get_last_error(), f"PostMessageW(0x{msg:04x}) 到 {hwnd:#x} 失败")


def post_close(hwnd: int) -> None:
    """与点标题栏 × 同一条消息：进 `WindowEvent::CloseRequested` → 关窗询问闸。"""
    post(hwnd, WM_CLOSE)


def get_text(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(4096)
    user32.SendMessageW(hwnd, WM_GETTEXT, 4096, ctypes.cast(buf, ctypes.c_void_p).value)
    return buf.value


def set_text(hwnd: int, text: str) -> None:
    buf = ctypes.create_unicode_buffer(text)
    user32.SendMessageW(hwnd, WM_SETTEXT, 0, ctypes.cast(buf, ctypes.c_void_p).value)


def click(hwnd: int) -> None:
    post(hwnd, BM_CLICK)


# ---------------------------------------------------------------- 菜单


@dataclass(frozen=True)
class MenuItem:
    path: tuple[str, ...]  # 顶层到本项的显示文案（含 muda 追加的「\tCtrl+Z」）
    command_id: int

    @property
    def label(self) -> str:
        return self.path[-1].split("\t", 1)[0].replace("&", "")

    @property
    def accelerator(self) -> str | None:
        parts = self.path[-1].split("\t", 1)
        return parts[1] if len(parts) == 2 else None


def _menu_text(hmenu, pos: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    user32.GetMenuStringW(hmenu, pos, buf, 512, MF_BYPOSITION)
    return buf.value


def menu_items(hwnd: int) -> list[MenuItem]:
    """窗口菜单栏上的全部叶子项（跨进程只读）。菜单栏是空的时候回空表，由调用方判红。"""
    root = user32.GetMenu(hwnd)
    out: list[MenuItem] = []

    def walk(hmenu, prefix: tuple[str, ...]):
        n = user32.GetMenuItemCount(hmenu)
        for i in range(max(n, 0)):
            text = _menu_text(hmenu, i)
            sub = user32.GetSubMenu(hmenu, i)
            if sub:
                walk(sub, (*prefix, text))
                continue
            cid = user32.GetMenuItemID(hmenu, i)
            if text and cid not in (0, 0xFFFFFFFF):
                out.append(MenuItem((*prefix, text), int(cid)))

    if root:
        walk(root, ())
    return out


def post_menu_command(hwnd: int, command_id: int, *, from_accelerator: bool) -> None:
    """投递菜单命令。

    `from_accelerator=True` 发的是 `TranslateAcceleratorW` 翻出来的那一条
    （`HIWORD(wParam) == 1`），`False` 是鼠标点菜单项那一条（`== 0`）——按键在
    Windows 上先被菜单加速键表截走，再以这条消息到达壳。加速键表本身的查表一步
    我们驱动不到（那要真按键），见 `docs/rules/ci/verification-chain.md` 的盲点表。
    """
    post(hwnd, WM_COMMAND, (1 if from_accelerator else 0) << 16 | (command_id & 0xFFFF), 0)


# ---------------------------------------------------------------- 剪贴板（「另一个应用」这一侧）


def _open_clipboard(timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not user32.OpenClipboard(None):
        if time.monotonic() > deadline:
            raise OSError(ctypes.get_last_error(), "OpenClipboard 一直被别的进程占着")
        time.sleep(0.05)


def clipboard_text() -> str | None:
    _open_clipboard()
    try:
        if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return None
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        p = kernel32.GlobalLock(h)
        try:
            return ctypes.wstring_at(p)
        finally:
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()


def clipboard_owner_pid() -> int:
    h = user32.GetClipboardOwner()
    if not h:
        return 0
    return _describe(h).pid


def set_clipboard_text(text: str) -> None:
    """以本进程的身份清空并写入剪贴板——对 Tavotto 来说这就是「别的应用复制了一段文字」。"""
    data = ctypes.create_unicode_buffer(text)
    size = ctypes.sizeof(data)
    _open_clipboard()
    try:
        user32.EmptyClipboard()
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
        if not h:
            raise OSError(ctypes.get_last_error(), "GlobalAlloc 失败")
        p = kernel32.GlobalLock(h)
        ctypes.memmove(p, data, size)
        kernel32.GlobalUnlock(h)
        if not user32.SetClipboardData(CF_UNICODETEXT, h):
            kernel32.GlobalFree(h)
            raise OSError(ctypes.get_last_error(), "SetClipboardData 失败")
    finally:
        user32.CloseClipboard()
