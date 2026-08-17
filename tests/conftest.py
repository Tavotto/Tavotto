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

# 渲染控制面**默认走 Python 池**。开发机上 `cargo build` 之后
# `workerd/target/debug/magplot-workerd` 就在那儿，pool 会自动认出来——
# 那样整套既有用例会在不知不觉间换一条控制面跑，「Python 实现是参考实现」
# 这件事就没人看着了。走 workerd 的用例自己把这个变量改掉（见
# tests/test_workerd_pool.py 的 workerd_enabled fixture）。
os.environ.setdefault("MAGPLOT_WORKERD", "0")


@pytest.fixture(autouse=True)
def _isolated_user_config(tmp_path_factory, monkeypatch):
    """所有测试的用户级配置（最近项目等）落在临时目录，绝不碰真实用户配置。"""
    monkeypatch.setenv(
        "MAGPLOT_CONFIG_DIR",
        str(tmp_path_factory.mktemp("magplot-config")),
    )
