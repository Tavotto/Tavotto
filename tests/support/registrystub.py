"""测试里自造的注册表替身：只给 `for_stem` 就够的用例用这个，别各写一份会漏方法的 `type("R", ...)`。

app 里「磁盘文件 → 图」走 `Registry.for_artifact` / `artifact_stem`（#1251）；替身把它们委托给 `for_stem`
（按文件名 stem），与没有 `artifacts` 映射的真注册表行为一致。
"""

from __future__ import annotations

from pathlib import Path


class StemRegistry:
    def __init__(self, for_stem):
        self._for_stem = for_stem

    def for_stem(self, stem):
        return self._for_stem(stem)

    def for_artifact(self, rel):
        stem = Path(str(rel).replace("\\", "/")).stem
        info = self._for_stem(stem)
        return None if info is None else {**info, "stem": stem}

    def artifact_stem(self, rel):
        return Path(str(rel).replace("\\", "/")).stem


def stem_registry(for_stem):
    """`for_stem` 可以是函数，或固定返回的 dict / None。"""
    return StemRegistry(for_stem if callable(for_stem) else (lambda _stem: for_stem))
