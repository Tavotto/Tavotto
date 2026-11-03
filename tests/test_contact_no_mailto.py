"""「联系作者」不依赖用户自己的邮箱程序（ADR 0145，用户拍板）：全产品没有 `mailto:`，也不唤起系统邮件程序。

投递由 Tavotto 服务器代发；发送失败时界面只给“复制内容 / 复制地址”。这条扫描钉住 `web/src` 与 `src/tavotto`（含 Flask
模板与随包前端产物之外的全部源码）里没有 `mailto:` 这个协议字面量，也没有把链接交给系统去开邮件的写法。

判据的主语：**源文件文本**里的协议字面量（大小写不敏感，因为 URL scheme 不分大小写）；测试文件自己在断言“没有 mailto”
时需要写出这个词，所以只扫源码目录、不扫测试。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCAN_ROOTS = (ROOT / "web" / "src", ROOT / "src" / "tavotto")
SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".html", ".css", ".md"}
#: 随包前端构建产物不是源码（`src/tavotto/web/` 由 build_frontend 生成、被 .gitignore 忽略），它在源码里同样不得出现，
#: 但构建产物里的第三方库可能带这个字面量，所以只扫被 git 跟踪的源码目录：产物目录整个跳过。
SKIP_PARTS = {"node_modules", "__pycache__", "dist"}
#: `src/tavotto/web` = 构建产物目录
BUILT = ROOT / "src" / "tavotto" / "web"
PATTERN = re.compile(r"mailto\s*:", re.I)


def _files():
    for base in SCAN_ROOTS:
        for p in base.rglob("*"):
            if not p.is_file() or p.suffix not in SUFFIXES:
                continue
            if SKIP_PARTS & set(p.parts) or BUILT in p.parents:
                continue
            yield p


def test_the_scan_actually_sees_the_product_sources():
    seen = list(_files())
    assert len(seen) > 300, len(seen)
    assert any(p.name == "contactsend.py" for p in seen)
    assert any(p.name == "TopBar.tsx" for p in seen)


def test_no_mailto_anywhere_in_product_sources():
    hits = []
    for p in _files():
        text = p.read_text(encoding="utf-8", errors="replace")
        for m in PATTERN.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            hits.append(f"{p.relative_to(ROOT).as_posix()}:{line}")
    assert not hits, f"产品里不许出现 mailto:（用户不需要配置邮箱）：{hits}"


def test_the_scanner_would_catch_a_mailto(tmp_path):
    sample = tmp_path / "x.tsx"
    sample.write_text('<a href="MailTo: a@b.c">x</a>', encoding="utf-8")
    assert PATTERN.search(sample.read_text())
    sample.write_text('<a href="https://tavotto.com">x</a>', encoding="utf-8")
    assert not PATTERN.search(sample.read_text())
