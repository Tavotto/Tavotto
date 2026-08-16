"""测试跑在 .venv（flask+pymupdf，无 matplotlib）。

需要科学栈的用例（worker round-trip）自行 spawn worker 解释器子进程，
本进程始终保持与 Flask 父进程相同的依赖边界。
"""
import os
import tempfile

import pytest

# 模块级设置：app / engine 的路径常量在 import 时求值，必须先于用例模块被
# import 就位，否则测试会写到真实的用户数据目录。单个用例仍各自 monkeypatch
# 具体常量，这里只是兜底护栏。
_DATA_DIR = tempfile.mkdtemp(prefix="magplot-data-")
os.environ.setdefault("MAGPLOT_DATA_DIR", _DATA_DIR)


@pytest.fixture(autouse=True)
def _isolated_user_config(tmp_path_factory, monkeypatch):
    """所有测试的用户级配置（最近项目等）落在临时目录，绝不碰真实用户配置。"""
    monkeypatch.setenv(
        "MAGPLOT_CONFIG_DIR",
        str(tmp_path_factory.mktemp("magplot-config")),
    )
