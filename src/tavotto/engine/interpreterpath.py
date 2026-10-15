"""解释器路径身份（纯标准库叶子）：venv 与指向的基础 Python 是两个环境。"""

import os


def same_python(a: str | None, b: str | None) -> bool:
    """比较规范化路径，不 realpath；Windows 上大小写与两种路径分隔符等价。"""
    if not a or not b:
        return False

    def norm(p: str) -> str:
        try:
            s = os.path.normpath(os.path.abspath(os.path.expanduser(p)))
        except (OSError, ValueError):
            s = p
        return os.path.normcase(s)

    return norm(a) == norm(b)
