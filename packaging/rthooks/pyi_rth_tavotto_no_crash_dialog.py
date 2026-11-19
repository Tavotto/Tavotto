"""PyInstaller 运行时钩子：Windows 冻结产物里未处理的异常不弹英文报错框，写日志后直接退出（TVD-MHQJ）。

`packaging/tavotto.spec` 把它放在 `runtime_hooks` 第一个——自定义钩子先于 PyInstaller 自带的那些执行
（`pyi_rth_multiprocessing` 等）。Windows Lab 实测（0.21.2）：杀软隔离或锁住 `_internal` 里一个 `.pyd` 时，
导入就失败在自带钩子里（早于 `packaging/entry.py`），窗口化 bootloader 随后弹「Unhandled exception in script」：
用户不关它，引擎就挂着直到壳 60 秒握手超时；关掉才以退出码 1 退出。救援页能给的出路（重新安装）被一个英文对话框
挡在后面。

bootloader 在弹框之前先调 `PyErr_Print()`，后者对非 `SystemExit` 的异常调 `sys.excepthook`：这里把堆栈写进
stderr（壳把它重定向到 `sidecar.log`；流已是 None 时写 fd 2），跑完已登记的退出清理，然后 `os._exit(1)`——
与 bootloader 原本的退出码一致，只是没有那个框、不再白等。`SystemExit` 不经过这里（照常退出）。

只在 Windows 冻结产物里生效：macOS 的窗口化 bootloader 不弹框（只写 syslog），源码树上不动 `sys.excepthook`。
"""


def _tavotto_install_no_crash_dialog() -> None:
    import sys

    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    import os

    def hook(exc_type, exc, tb):
        try:
            import traceback

            text = "".join(traceback.format_exception(exc_type, exc, tb))
            stream = sys.stderr
            if stream is not None:
                stream.write(text)
                stream.flush()
            else:
                os.write(2, text.encode("utf-8", "replace"))
        except Exception:  # noqa: BLE001 —— 写不出日志也要退出，不能退回弹框
            pass
        try:
            import atexit

            atexit._run_exitfuncs()
        except Exception:  # noqa: BLE001
            pass
        os._exit(1)

    sys.excepthook = hook


_tavotto_install_no_crash_dialog()
del _tavotto_install_no_crash_dialog
