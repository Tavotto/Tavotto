"""「大陆环境」的引擎侧读法——只读，不判。

判据唯一出处是桌面壳 `src-tauri/src/app_update.rs::source_order`（ADR 0121：时区 / 系统语言 / 界面语言）；壳拉起引擎时
把结论写进 `TAVOTTO_NETWORK_REGION=cn|global`（`sidecar.rs`）。这里**不在 Python 里再抄一份时区 / 语言表**。
消费者：`contactsend`（联系作者选线路）、`managedcli`（托管 CLI 镜像在前，#1744）。

叶子模块、纯标准库：谁都能 import，不进任何 import 环。
"""

from __future__ import annotations

import os

ENV_REGION = "TAVOTTO_NETWORK_REGION"


def region_is_cn(ui_locale: str | None = None) -> bool:
    """大陆环境？壳的结论（`TAVOTTO_NETWORK_REGION=cn`）或界面语言是简体中文（ADR 0121 判据 3）。
    `global` 不否决界面语言：壳算它时还不知道界面语言。没有壳（pip / 源码）也不知道界面语言时回 False。"""
    if os.environ.get(ENV_REGION, "").strip().lower() == "cn":
        return True
    return ui_locale == "zh-CN"
