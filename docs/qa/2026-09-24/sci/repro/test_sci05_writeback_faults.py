# ruff: noqa: F811 — pytest 夹具按名注入，参数名与导入的夹具同名是有意的
"""SCI-04 / SCI-05 复现：写回事务在 staging 导出失败、备份阶段磁盘满时的行为（真 matplotlib worker）。

**这不是回归测试集的一员**——其中有用例会红（暴露产品缺陷），所以放在 docs/qa 的 repro 里，
用 `bash docs/qa/2026-09-24/sci/repro/pytest.sh docs/qa/2026-09-24/sci/repro/test_sci05_writeback_faults.py`
（在 worktree 根目录）运行。夹具复用 tests/test_write_back_real_409.py 的 `project`。

合同（docs/rules/backend/writeback-transaction.md、根 AGENTS.md「写回事务不变式」）：
prepare → verify → commit 任一环不过 **一律 409 且原文件零改动**；staging 阶段任何异常都要 unlink 掉
所有 `.updating`；commit 第 2+ 个失败要把已换掉的回滚。
"""

from __future__ import annotations

import errno
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[5] / "tests"))

from tavotto.engine import pool  # noqa: E402
from test_write_back_real_409 import (  # noqa: E402
    _render,
    _snapshot,
    _title_gid,
    project,  # noqa: F401 — pytest 夹具
)


def _leftovers(figs: Path) -> list[str]:
    return sorted(p.name for p in figs.iterdir() if p.name.endswith(".updating"))


def _prepared(project):
    m, client, figs = project
    man = _render(client, [])
    patches = [{"gid": _title_gid(man), "prop": "text", "value": "Edited"}]
    _render(client, patches)
    return m, client, figs, patches


def test_staging_export_failure_keeps_bytes_and_reports_status(project, monkeypatch):
    """verify 阶段一次性 worker 导出 PNG 时崩溃：字节不变、无半成品；**状态码按合同应为 409**。"""
    m, client, figs, patches = _prepared(project)
    before = _snapshot(figs)
    real_one_shot = pool.one_shot

    def one_shot(*a, **k):
        w = real_one_shot(*a, **k)
        real_export = w.export

        def export(stem, p, path, fmt="pdf", dpi=600):
            if fmt == "png":
                Path(path).write_bytes(b"half")
                raise pool.WorkerError("injected: png export crashed", "")
            return real_export(stem, p, path, fmt=fmt, dpi=dpi)

        w.export = export
        return w

    monkeypatch.setattr(pool, "one_shot", one_shot)
    r = client.post("/api/engine/update_source", json={"id": "Fig1.pdf", "patches": patches})
    for name, data in before.items():
        assert (figs / name).read_bytes() == data
    assert _leftovers(figs) == []
    print("staging-export-failure status:", r.status_code, (r.get_json() or {}).get("code"))
    assert r.status_code == 409, f"合同要求 409，实际 {r.status_code}（{r.get_json()}）"


def test_backup_disk_full_on_second_target_rolls_back_and_leaves_no_halves(project, monkeypatch):
    """commit 阶段给第二个目标做备份时磁盘满（ENOSPC）：应 409、PDF 回滚、无 `.updating` 残留。"""
    m, client, figs, patches = _prepared(project)
    before = _snapshot(figs)
    import shutil

    real_copy2 = shutil.copy2
    calls: list[str] = []

    def copy2(src, dst, *a, **k):
        calls.append(Path(src).name)
        if len(calls) == 2:
            raise OSError(errno.ENOSPC, "No space left on device (injected)", str(dst))
        return real_copy2(src, dst, *a, **k)

    monkeypatch.setattr(m.shutil, "copy2", copy2)
    try:
        r = client.post("/api/engine/update_source", json={"id": "Fig1.pdf", "patches": patches})
        status = r.status_code
    except OSError as exc:  # test client 可能把未处理异常直接抛上来
        status = f"raised {exc!r}"
    monkeypatch.setattr(m.shutil, "copy2", real_copy2)
    assert len(calls) >= 2, calls
    changed = [n for n, d in before.items() if (figs / n).read_bytes() != d]
    left = _leftovers(figs)
    print("backup-ENOSPC status:", status, "changed:", changed, "leftovers:", left)
    assert changed == [], f"磁盘满后原件被部分替换：{changed}（status={status}）"
    assert left == [], f"图库留下半成品：{left}"
    assert status == 409, f"合同要求 409，实际 {status}"
