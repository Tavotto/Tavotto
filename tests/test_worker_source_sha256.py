"""worker 自报 build 实际执行的源码 sha256（TAF 改图验证的判据，ADR 0138 Phase 3b）：真 worker 往返。"""

from __future__ import annotations

import hashlib
import subprocess

import pytest

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="not_run：找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

SCRIPT = """\
import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots(figsize=(3, 2))
    ax.plot([0, 1], [0, 1])
    fig.savefig("Fig1.pdf")
"""


def test_build_reports_the_sha256_of_the_executed_source_and_a_second_build_keeps_it(tmp_path):
    script = tmp_path / "fig1.py"
    script.write_bytes(SCRIPT.encode())
    executed = hashlib.sha256(SCRIPT.encode()).hexdigest()
    w = pool.one_shot("fig1.py", str(tmp_path), "main")
    try:
        assert w.last_build_source_sha256 is None
        resp = w.ensure_built()
        assert resp["source_sha256"] == executed
        assert w.last_build_source_sha256 == executed
        # 磁盘在 build 之后被改：二次 build（workerd 去重回旧会话时前端 / after 就是这么拿摘要）报的仍是执行版本
        script.write_bytes(SCRIPT.encode() + b"\n# edited\n")
        w.built = False
        w.ensure_built()
        assert w.last_build_source_sha256 == executed
        assert executed != hashlib.sha256(script.read_bytes()).hexdigest()
    finally:
        pool.discard(w)


SELF_REWRITING = SCRIPT.replace(
    "    fig.savefig",
    "    import os, pathlib\n"
    "    p = pathlib.Path(__file__)\n"
    "    p.write_bytes(p.read_bytes() + b'\\n# rewritten\\n')\n"
    "    os.utime(p, ns=(2_000_000_000_000_000_000, 2_000_000_000_000_000_000))\n"
    "    fig.savefig",
)


def test_rewrite_during_module_execution_reports_the_executed_version(tmp_path):
    """按模块 import 的入口：摘要由 loader 在编译那一刻报，main() 里才改自己时 loader 早已编译完，
    所以 sha 等于**执行的那一版**（原始字节），不是 None，也不是改写后的字节。"""
    (tmp_path / "fig1.py").write_bytes(SELF_REWRITING.encode())
    w = pool.one_shot("fig1.py", str(tmp_path), "main")
    try:
        resp = w.ensure_built()
        assert resp["source_sha256"] == hashlib.sha256(SELF_REWRITING.encode()).hexdigest()
        assert w.last_build_source_sha256 == resp["source_sha256"]
    finally:
        pool.discard(w)


def test_rewrite_during_main_entry_execution_reports_none(tmp_path):
    """`__main__` 入口（runpy 读源码本身）走执行前后括号：执行中被改写 → None。"""
    body = SELF_REWRITING + "\n\nmain()\n"
    (tmp_path / "fig1.py").write_bytes(body.encode())
    w = pool.one_shot("fig1.py", str(tmp_path), "__main__")
    try:
        resp = w.ensure_built()
        assert resp["source_sha256"] is None
        assert w.last_build_source_sha256 is None
    finally:
        pool.discard(w)


def test_main_entry_unmodified_reports_its_sha(tmp_path):
    body = SCRIPT + "\n\nmain()\n"
    (tmp_path / "fig1.py").write_bytes(body.encode())
    w = pool.one_shot("fig1.py", str(tmp_path), "__main__")
    try:
        resp = w.ensure_built()
        assert resp["source_sha256"] == hashlib.sha256(body.encode()).hexdigest()
    finally:
        pool.discard(w)


def test_rewrite_by_paper_style_import_reports_the_rewritten_bytes_the_loader_compiled(tmp_path):
    """paper_style 在 import 时改写目标脚本：入口模块之后才由 loader 加载，编译的就是**改写后**的字节，
    所以 sha 等于改写后字节的 sha（loader 报摘要，判据是定义上的；不再是 None）。"""
    script = tmp_path / "fig1.py"
    script.write_bytes(SCRIPT.encode())
    (tmp_path / "paper_style.py").write_text(
        "import pathlib\n"
        "p = pathlib.Path(__file__).with_name('fig1.py')\n"
        "p.write_bytes(p.read_bytes() + b'\\n# edited by import\\n')\n"
        "def save(*a, **k):\n    pass\n",
        encoding="utf-8",
    )
    w = pool.one_shot("fig1.py", str(tmp_path), "main")
    try:
        resp = w.ensure_built()
        assert script.read_bytes().endswith(b"# edited by import\n")
        assert resp["source_sha256"] == hashlib.sha256(script.read_bytes()).hexdigest()
    finally:
        pool.discard(w)


OLD = SCRIPT.replace("Fig1.pdf", "OldFig.pdf")
NEW = SCRIPT.replace(
    "    fig.savefig",
    "    import pathlib\n    pathlib.Path(MARKER).write_text('new', encoding='utf-8')\n    fig.savefig",
).replace("Fig1.pdf", "NewFig.pdf")


def test_stale_unchecked_hash_pyc_is_not_executed_and_digest_is_of_the_new_source(tmp_path):
    """项目 `__pycache__` 里有外部运行遗留的 unchecked-hash .pyc（由旧版源码生成，永不校验源码）：
    loader 只从源码编译，执行的是新版，摘要是新版字节的。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    marker = tmp_path / "ran-new.txt"
    script = proj / "fig1.py"
    script.write_bytes(OLD.encode())
    # 用 worker 解释器生成（magic number 须一致）
    subprocess.run(
        [
            WORKER_PY,
            "-c",
            "import importlib.util, py_compile, sys;"
            "src = sys.argv[1];"
            "py_compile.compile(src, cfile=importlib.util.cache_from_source(src),"
            " doraise=True, invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)",
            str(script),
        ],
        check=True,
        encoding="utf-8",
    )
    new_src = NEW.replace("MARKER", repr(str(marker)))
    script.write_bytes(new_src.encode())
    w = pool.one_shot("fig1.py", str(proj), "main")
    try:
        resp = w.ensure_built()
        assert marker.read_text(encoding="utf-8") == "new"
        assert resp["source_sha256"] == hashlib.sha256(new_src.encode()).hexdigest()
        assert w.last_build_source_sha256 == resp["source_sha256"]
    finally:
        pool.discard(w)
