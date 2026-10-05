"""导入即扫描（T02，`engine/projscan.py` + `/api/project/scan*`）的行为合同。

零执行的证明在 `test_project_scan_zero_exec.py`；这里守其余五件事：

1. **报告是真的**——静态项目 / 只有脚本 / 工具与绘图混合 / 多个候选 / 全已连接 / 读不动的脚本，每一种
   给出各自的 phase 与目标选择，目标是证据驱动的，不是「第一个 .py」；
2. **看不全就说看不全**——权限错误、目录项 / 脚本 / 字节 / 时间预算、符号链接环、云盘占位、单文件过大、
   取消，全部变成 `partial` / `cancelled` 加账本，**绝不**变成「没有脚本」；
3. **有界**——预算的每一项真的拦得住（用很小的预算在真实目录上跑）；
4. **服务语义**——单飞、新鲜复用、强制重扫、迟到作废、失败是终局、取消只取消扫描；
5. **接口**——项目绑定、请求体白名单、404 语义、响应里没有机器路径。
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

from tavotto import app as m
from tavotto.engine import (
    discover as engine_discover,
    prepsession,
    probe as engine_probe,
    projectenv as engine_projectenv,
    projscan,
    scanbudget,
)

PLOT = "import matplotlib.pyplot as plt\nfig, ax = plt.subplots()\nax.plot([1, 2])\nfig.savefig('{stem}.pdf')\n"
DYNAMIC = (
    "import sys\nimport matplotlib.pyplot as plt\n"
    "fig, ax = plt.subplots()\nfig.savefig(sys.argv[0] + '.pdf')\n"
)
TOOL = "def clean(rows):\n    return [r for r in rows if r]\n"

posix_only = pytest.mark.skipif(os.name == "nt", reason="需要 POSIX 权限位 / 符号链接")


def _project(tmp_path: Path, name: str = "p") -> Path:
    root = tmp_path / name
    root.mkdir()
    return root


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _asset(root: Path, rel: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.4\n" if rel.endswith(".pdf") else b"\x89PNG\r\n\x1a\n")


# ---------------------------------------------------------------- 词汇
def test_scan_phases_are_a_subset_of_the_preparation_session_vocabulary():
    """面板只面对一份 phase 词汇：扫描的 phase 必须是准备会话词汇的子集（projscan 不 import prepsession，
    子集关系由这条钉着）。"""
    assert set(projscan.PHASES) <= set(prepsession.PHASES)
    assert set(projscan.PHASES) == {
        projscan.PHASE_SCANNING,
        projscan.PHASE_AWAITING_CONFIRMATION,
        projscan.PHASE_AWAITING_CONFIGURATION,
        projscan.PHASE_COMPLETED,
        projscan.PHASE_ACTION_REQUIRED,
        projscan.PHASE_CANCELLED,
    }


def test_the_per_file_byte_cap_is_the_existing_import_scan_cap():
    """`scanbudget` 是叶子模块不能 import `importscan`，同值靠这条钉：优先采用既有常量。"""
    from tavotto.engine import importscan

    assert scanbudget.MAX_FILE_BYTES == importscan.MAX_SOURCE_BYTES


@pytest.mark.parametrize(
    "state,choice,targets,assets,partial,phase,outcome",
    [
        ("running", "none", False, 0, False, "scanning", "scanning"),
        ("cancelled", "none", False, 0, True, "cancelled", "cancelled"),
        ("failed", "none", False, 0, False, "action_required", "failed"),
        ("complete", "single", True, 3, False, "awaiting_confirmation", "target_found"),
        ("partial", "single", True, 0, True, "awaiting_confirmation", "target_found"),
        ("complete", "ambiguous", True, 0, False, "awaiting_configuration", "choose_target"),
        ("complete", "connected", True, 5, False, "completed", "already_connected"),
        ("complete", "none", False, 4, False, "completed", "static_source"),
        ("complete", "none", False, 0, False, "completed", "nothing_found"),
        # 看不全 + 没有目标：不能说「静态项目」或「空项目」
        ("partial", "none", False, 4, True, "action_required", "unchecked"),
        ("partial", "none", False, 0, True, "action_required", "unchecked"),
    ],
)
def test_phase_vectors(state, choice, targets, assets, partial, phase, outcome):
    got = projscan.phase_of(state, [{"x": 1}] if targets else [], choice, assets, partial)
    assert (got["phase"], got["outcome"]["kind"]) == (phase, outcome)
    assert got["phase"] in projscan.PHASES and got["outcome"]["kind"] in projscan.OUTCOMES


# ---------------------------------------------------------------- 报告是真的
def test_a_static_only_project_is_browsable_and_needs_no_python(tmp_path):
    root = _project(tmp_path)
    _asset(root, "fig1.pdf")
    _asset(root, "sub/fig2.png")

    report = projscan.scan(root)

    assert report["state"] == "complete"
    assert (report["phase"], report["outcome"]["kind"]) == ("completed", "static_source")
    assert report["assets"] == {"count": 2, "pdf": 1, "raster": 1, "browsable": True}
    assert report["scripts"] == [] and report["targets"] == []
    assert report["default_target"] is None
    assert [a["kind"] for a in report["actions"]] == ["rescan"]  # 没有要准备的目标


def test_a_script_only_project_offers_the_script_as_the_target_without_any_asset(tmp_path):
    """O03 / C02：没有任何素材，也能发现并选择目标；动作里带创建准备会话的请求体。"""
    root = _project(tmp_path)
    _write(root, "plot.py", DYNAMIC)

    report = projscan.scan(root)

    assert report["assets"]["count"] == 0
    assert report["default_target"] == "plot.py"
    assert (report["phase"], report["outcome"]["kind"]) == ("awaiting_confirmation", "target_found")
    prepare = next(a for a in report["actions"] if a["kind"] == "prepare")
    assert prepare["target"]["script"] == "plot.py"
    assert set(prepare["target"]) <= {"script", "entry"}  # 就是 T01 创建会话端点认的请求体


def test_a_helper_does_not_become_the_target_and_cannot_block_the_plot_script(tmp_path):
    """O04：工具 / 测试 / 样式模块不默认当目标，也不影响独立的绘图脚本被选为默认目标。"""
    root = _project(tmp_path)
    _write(root, "tools/clean.py", TOOL)
    _write(root, "test_plot.py", PLOT.format(stem="t"))
    _write(root, "paper_style.py", "RC = {}\n")
    _write(root, "fig_a.py", DYNAMIC)

    report = projscan.scan(root)

    assert {s["script"] for s in report["scripts"]} == {
        "tools/clean.py",
        "test_plot.py",
        "paper_style.py",
        "fig_a.py",
    }
    assert [t["script"] for t in report["targets"]] == ["fig_a.py"]
    assert report["default_target"] == "fig_a.py"
    roles = {s["script"]: projscan._role_of(s) for s in report["scripts"]}
    assert (
        roles["tools/clean.py"] == roles["test_plot.py"] == roles["paper_style.py"] == "auxiliary"
    )


def test_several_unconnected_plot_scripts_are_not_guessed(tmp_path):
    """单个优先绘图作用域：多个就让用户选，不替他挑；不同作用域的 requirements 不被混装。"""
    root = _project(tmp_path)
    _write(root, "a/plot.py", DYNAMIC)
    _write(root, "a/requirements.txt", "numpy\n")
    _write(root, "b/plot.py", DYNAMIC)
    _write(root, "b/requirements.txt", "scipy\n")

    report = projscan.scan(root)

    assert report["default_target"] is None and report["target_choice"] == "ambiguous"
    assert (report["phase"], report["outcome"]["kind"]) == (
        "awaiting_configuration",
        "choose_target",
    )
    scopes = {t["script"]: t["scope"] for t in report["targets"]}
    assert scopes == {"a/plot.py": "a", "b/plot.py": "b"}
    assert [a["kind"] for a in report["actions"]] == ["rescan", "choose_target"]


def test_connected_plot_scripts_do_not_nag(tmp_path):
    """已经登记的绘图脚本（panel 已可编辑）：没有要准备的目标，也不要求用户选。"""
    root = _project(tmp_path)
    _write(root, "fig.py", PLOT.format(stem="fig"))
    _write(
        root,
        "tavotto_registry.json",
        json.dumps(
            {
                "version": 1,
                "scripts": {
                    "fig.py": {"entry": "__main__", "cost": "light", "notes": "", "stems": ["fig"]}
                },
            }
        ),
    )

    report = projscan.scan(root)

    assert report["target_choice"] == "connected" and report["default_target"] is None
    assert (report["phase"], report["outcome"]["kind"]) == ("completed", "already_connected")


def test_an_unparseable_script_is_unknown_and_selectable_not_ignored(tmp_path):
    """宿主判语法错误且没有目标解析器：`syntax_error` + `parser=None`（未核验）。不是「没有脚本」。"""
    root = _project(tmp_path)
    _write(root, "weird.py", "def broken(:\n")

    report = projscan.scan(root)

    item = report["scripts"][0]
    assert item["reason"] == "unparseable" and item["problem"]["kind"] == "syntax_error"
    assert item["parser"] is None and item["can_probe"] is True
    assert [t["script"] for t in report["targets"]] == ["weird.py"]
    assert report["targets"][0]["role"] == "unknown"
    assert report["default_target"] is None
    assert report["outcome"]["kind"] == "choose_target"


def test_environment_and_dependencies_are_unknown_never_ok(tmp_path):
    """扫描阶段拿不到环境事实：环境与依赖检查一律 unknown，`verified` 恒为 False。"""
    root = _project(tmp_path)
    _write(root, "plot.py", DYNAMIC)
    _write(root, "requirements.txt", "numpy>=1.20\nmatplotlib\n-e .\n")

    report = projscan.scan(root)

    by_id = {c["id"]: c for c in report["checks"]}
    assert by_id["environment"]["status"] == "unknown"
    assert by_id["dependencies"]["status"] == "unknown"
    assert report["environment"]["verified"] is False
    deps = report["dependencies"]
    assert deps["files"] == ["requirements.txt"] and deps["requirements"] == 2
    assert deps["unsupported"] == ["editable_install"] and deps["evaluated"] is False
    assert "raw" not in json.dumps(deps)  # 原文行（可能含带凭据的 index URL）不出门


def test_environment_clues_are_read_from_disk_records_only(tmp_path):
    root = _project(tmp_path)
    _write(root, "plot.py", DYNAMIC)
    _write(root, ".python-version", "3.12.1\n")
    venv = root / ".venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = /nowhere\n", "utf-8")
    (venv / "bin" / "python").write_text("", "utf-8")
    if os.name != "nt":
        (venv / "bin" / "python").chmod(0o755)

    env = projscan.scan(root)["environment"]

    if os.name != "nt":
        venv_row = next(c for c in env["candidates"] if c["source"] == "project_venv")
        assert venv_row["python_relative"] == ".venv/bin/python"
        assert venv_row["status"] == "unchecked" and venv_row["scope"] == "project"
    assert all("python" not in c for c in env["candidates"])  # 只给相对路径 / 不透明 id


# ---------------------------------------------------------------- 看不全就说看不全
def _scan_with_unreadable_dir(root: Path, monkeypatch, victim: str):
    real = engine_discover._children

    def children(d, budget):
        if d.name == victim:
            raise PermissionError(13, "Permission denied", str(d))
        return real(d, budget)

    monkeypatch.setattr(engine_discover, "_children", children)
    return projscan.scan(root)


def test_an_unreadable_directory_is_partial_not_no_scripts(tmp_path, monkeypatch):
    """O05：读不动的目录进账本；只剩这个目录有脚本时，绝不报「没有脚本 / 静态项目」。"""
    root = _project(tmp_path)
    _write(root, "locked/plot.py", DYNAMIC)
    _asset(root, "fig.pdf")

    report = _scan_with_unreadable_dir(root, monkeypatch, "locked")

    assert report["state"] == "partial"
    assert {
        "code": "unreadable_dir",
        "severity": "partial",
        "scope": "dir",
        "path": "locked",
        "count": 1,
    } in report["issues"]
    assert report["scripts"] == []
    assert (report["phase"], report["outcome"]["kind"]) == ("action_required", "unchecked")
    assert report["assets"]["browsable"] is True  # 静态素材不受阻塞


@posix_only
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root 不受权限位约束")
def test_a_really_chmod_000_directory_is_partial(tmp_path):
    root = _project(tmp_path)
    _write(root, "ok.py", DYNAMIC)
    locked = _write(root, "locked/hidden.py", DYNAMIC).parent
    locked.chmod(0)
    try:
        report = projscan.scan(root)
    finally:
        locked.chmod(0o755)

    assert report["state"] == "partial"
    assert any(i["code"] == "unreadable_dir" and i["path"] == "locked" for i in report["issues"])
    assert [s["script"] for s in report["scripts"]] == ["ok.py"]


def test_the_entry_budget_stops_the_walk_and_says_so(tmp_path):
    root = _project(tmp_path)
    for i in range(40):
        _write(root, f"d{i // 10}/s{i}.py", TOOL)

    report = projscan.scan(root, limits=scanbudget.Limits(max_entries=12))

    assert report["state"] == "partial"
    assert any(i["code"] == "entry_budget" for i in report["issues"])
    assert len(report["scripts"]) < 40
    # 预算真的拦住了遍历本身，而不是事后才发现超了：用量停在越线的那一项
    assert report["budget"]["entries"] <= 12 + 1
    assert len(report["scripts"]) <= 12
    assert report["outcome"]["kind"] != "static_source"


def test_the_script_budget_caps_the_inventory(tmp_path):
    root = _project(tmp_path)
    for i in range(12):
        _write(root, f"s{i:02d}.py", TOOL)

    report = projscan.scan(root, limits=scanbudget.Limits(max_scripts=5))

    assert len(report["scripts"]) == 5
    assert report["state"] == "partial"
    assert any(i["code"] == "script_limit" for i in report["issues"])


def test_a_file_over_the_source_cap_is_listed_unchecked_and_never_read(tmp_path, monkeypatch):
    root = _project(tmp_path)
    _write(root, "big.py", "x = 1\n" + "# pad\n" * 40)
    _write(root, "small.py", DYNAMIC)
    read: list[str] = []
    real = engine_discover.read_source
    monkeypatch.setattr(engine_discover, "read_source", lambda p: (read.append(p.name), real(p))[1])

    report = projscan.scan(root, limits=scanbudget.Limits(max_file_bytes=100))

    big = next(s for s in report["scripts"] if s["script"] == "big.py")
    assert big["checked"] is False and big["unchecked_reason"] == "file_too_large"
    assert "big.py" not in read
    assert report["state"] == "partial"
    assert [t["script"] for t in report["targets"] if t["script"] == "big.py"] == ["big.py"]


def test_the_cumulative_source_budget_stops_reading_more_files(tmp_path):
    root = _project(tmp_path)
    for i in range(6):
        _write(root, f"s{i}.py", "x = 1\n" * 10)

    report = projscan.scan(root, limits=scanbudget.Limits(max_source_bytes=150))

    unchecked = [s for s in report["scripts"] if not s["checked"]]
    assert unchecked and all(s["unchecked_reason"] == "source_byte_budget" for s in unchecked)
    assert len(report["scripts"]) == 6  # 都列出（可手动选），只是没读
    assert report["state"] == "partial"


def test_the_time_budget_stops_the_scan(tmp_path):
    root = _project(tmp_path)
    for i in range(30):
        _write(root, f"s{i}.py", TOOL)
    ticks = iter(range(10_000))

    class Clock:
        def __call__(self):
            return next(ticks) * 1.0

    budget = scanbudget.Budget(limits=scanbudget.Limits(max_seconds=5), clock=Clock())
    report = projscan.scan(root, budget=budget)

    assert report["state"] == "partial"
    assert any(i["code"] == "time_budget" for i in report["issues"])


def test_cancel_before_start_yields_cancelled_and_never_reads_a_script(tmp_path, monkeypatch):
    root = _project(tmp_path)
    _write(root, "plot.py", DYNAMIC)
    called: list[str] = []
    monkeypatch.setattr(engine_discover, "inspect_script", lambda *a, **k: called.append("x") or {})

    report = projscan.scan(root, cancel=lambda: True)

    assert report["state"] == "cancelled" and report["phase"] == "cancelled"
    assert called == []
    assert any(i["code"] == "cancelled" for i in report["issues"])


def test_cancel_in_the_middle_keeps_what_was_found(tmp_path):
    root = _project(tmp_path)
    for i in range(10):
        _write(root, f"s{i}.py", TOOL)
    seen = {"n": 0}

    def cancel():
        seen["n"] += 1
        return seen["n"] > 3

    report = projscan.scan(root, cancel=cancel)

    assert report["state"] == "cancelled"
    assert len(report["scripts"]) < 10


@posix_only
def test_a_symlink_loop_is_bounded_and_not_followed(tmp_path):
    """O06：`a/loop -> ..` 这种环不被跟进；脚本不会因为环被重复列出；账本里有 symlinked_dir。"""
    root = _project(tmp_path)
    _write(root, "a/plot.py", DYNAMIC)
    os.symlink(root, root / "a" / "loop")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "stranger.py").write_text(DYNAMIC, "utf-8")
    os.symlink(outside, root / "linked_outside")

    report = projscan.scan(root)

    assert [s["script"] for s in report["scripts"]] == ["a/plot.py"]
    paths = {i["path"] for i in report["issues"] if i["code"] == "symlinked_dir"}
    assert {"a/loop", "linked_outside"} <= paths
    assert report["state"] == "partial"
    assert "stranger" not in json.dumps(report)


def test_a_cloud_placeholder_is_listed_but_never_opened(tmp_path, monkeypatch):
    """模拟：真实云盘占位文件做不出来，把 `is_placeholder` 换成按文件名判。证据级别=模拟。"""
    root = _project(tmp_path)
    _write(root, "cloud.py", DYNAMIC)
    _write(root, "local.py", DYNAMIC)
    opened: list[str] = []
    real = engine_discover.read_source
    monkeypatch.setattr(
        engine_discover, "read_source", lambda p: (opened.append(p.name), real(p))[1]
    )
    real_is = scanbudget.is_placeholder
    # stat 结果不带路径：这里借 size 区分（cloud.py 与 local.py 内容相同，所以给 cloud 补一个标志）
    monkeypatch.setattr(
        scanbudget,
        "is_placeholder",
        lambda st: st.st_ino == (root / "cloud.py").stat().st_ino or real_is(st),
    )

    report = projscan.scan(root)

    cloud = next(s for s in report["scripts"] if s["script"] == "cloud.py")
    assert cloud["checked"] is False and cloud["unchecked_reason"] == "placeholder_file"
    assert "cloud.py" not in opened and "local.py" in opened
    assert any(
        i["code"] == "placeholder_file" and i["path"] == "cloud.py" for i in report["issues"]
    )


def test_is_placeholder_reads_the_platform_bits():
    class St:
        st_flags = 0x40000000

    class Win:
        st_file_attributes = 0x400000

    class Plain:
        st_flags = 0

    assert scanbudget.is_placeholder(St()) and scanbudget.is_placeholder(Win())
    assert not scanbudget.is_placeholder(Plain())
    assert not scanbudget.is_placeholder(object())


def test_too_deep_directories_are_a_note_unless_nothing_was_found(tmp_path):
    root = _project(tmp_path)
    deep = "/".join(["d"] * 6)
    _write(root, "ok.py", DYNAMIC)
    _write(root, f"{deep}/x.py", DYNAMIC)
    with_scripts = projscan.scan(root)
    assert with_scripts["state"] == "complete"  # 设计内剪枝，已找到脚本：只是 note
    assert any(
        i["code"] == "depth_limit" and i["severity"] == "note" for i in with_scripts["issues"]
    )

    (root / "ok.py").unlink()
    nothing = projscan.scan(root)
    assert nothing["state"] == "partial"  # 一个脚本都没找到：不能说「没有」


def test_chinese_and_spaced_paths_are_project_relative_posix(tmp_path):
    root = _project(tmp_path, "我的 项目")
    _write(root, "图 表/第一 张.py", DYNAMIC)
    _asset(root, "成果/图 1.pdf")

    report = projscan.scan(root)

    assert report["default_target"] == "图 表/第一 张.py"
    assert report["assets"]["count"] == 1


def test_the_report_contains_no_machine_path(tmp_path, monkeypatch):
    root = _project(tmp_path)
    _write(root, "plot.py", DYNAMIC)
    _write(root, "broken.py", "def f(:\n")
    _write(root, "requirements.txt", "numpy\n")
    home = tmp_path / "home"
    (home / ".conda").mkdir(parents=True)
    env_dir = home / "conda-envs" / "work" / "bin"
    env_dir.mkdir(parents=True)
    (env_dir / "python3").write_text("", "utf-8")
    (home / ".conda" / "environments.txt").write_text(str(env_dir.parent) + "\n", "utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    blob = json.dumps(projscan.scan(root), ensure_ascii=False)

    assert str(root) not in blob and str(tmp_path) not in blob
    assert "conda-envs" not in blob


# ---------------------------------------------------------------- 增量与证据修订
def test_unchanged_scripts_are_not_reparsed_and_environments_are_not_probed(tmp_path, monkeypatch):
    root = _project(tmp_path)
    _write(root, "a.py", DYNAMIC)
    _write(root, "b.py", TOOL)
    cache: dict = {}
    projscan.scan(root, cache=cache)

    calls: list[str] = []
    real = engine_discover.inspect_script
    monkeypatch.setattr(
        engine_discover,
        "inspect_script",
        lambda path, *a, **k: (calls.append(Path(path).name), real(path, *a, **k))[1],
    )
    monkeypatch.setattr(
        engine_projectenv,
        "probe_environment",
        lambda *a, **k: pytest.fail("增量更新不该为了环境体检"),
    )
    projscan.scan(root, cache=cache)
    assert calls == []  # 内容证据没变：一个都不重新解析

    time.sleep(0.01)
    _write(root, "a.py", DYNAMIC + "# edited\n")
    projscan.scan(root, cache=cache)
    assert calls == ["a.py"]


def test_evidence_revision_follows_content_not_time(tmp_path):
    root = _project(tmp_path)
    _write(root, "a.py", DYNAMIC)
    first = projscan.scan(root)
    again = projscan.scan(root)
    assert first["evidence_revision"] == again["evidence_revision"]
    assert first["budget"]["elapsed_s"] is not None  # 耗时在报告里，但不进修订号

    _write(root, "b.py", TOOL)
    assert projscan.scan(root)["evidence_revision"] != first["evidence_revision"]


def test_the_scan_never_writes_into_the_project(tmp_path):
    root = _project(tmp_path)
    _write(root, "plot.py", DYNAMIC)
    _asset(root, "fig.pdf")
    before = sorted((p.relative_to(root).as_posix(), p.stat().st_mtime_ns) for p in root.rglob("*"))

    projscan.scan(root)

    after = sorted((p.relative_to(root).as_posix(), p.stat().st_mtime_ns) for p in root.rglob("*"))
    assert before == after


# ---------------------------------------------------------------- 与脚本清单同一份分类
def test_script_items_are_the_inventory_classification(tmp_path):
    """分类只有 `probe.inventory_entry` 一份：同一项目 `script_inventory` 与扫描给出同样的 reason。"""
    root = _project(tmp_path)
    _write(root, "a.py", DYNAMIC)
    _write(root, "tools.py", TOOL)
    _write(root, "test_x.py", TOOL)
    _write(root, "broken.py", "def f(:\n")
    _write(root, "named.py", PLOT.format(stem="named"))

    scan_rows = {s["script"]: s["reason"] for s in projscan.scan(root)["scripts"]}
    inv_rows = {
        r["script"]: r["reason"] for r in engine_probe.script_inventory(root, registered=set())
    }

    # 只有一处允许不同：通配 / 磁盘比对（扫描不做 rglob）——这里没有通配名，所以应当完全一致
    assert scan_rows == inv_rows


# ---------------------------------------------------------------- 服务
class _Gate:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def runner(self, root, **kw):
        self.calls += 1
        self.started.set()
        self.release.wait(timeout=10)
        return projscan.scan(root, **kw)


def test_single_flight_and_fresh_reuse(tmp_path):
    root = _project(tmp_path)
    _write(root, "plot.py", DYNAMIC)
    gate = _Gate()
    svc = projscan.ScanService(runner=gate.runner)

    first = svc.ensure("pj", root)
    gate.started.wait(5)
    second = svc.ensure("pj", root)  # 还在跑：同一次
    assert first["scan_id"] == second["scan_id"] and gate.calls == 1
    assert second["state"] == "running" and second["phase"] == "scanning"
    assert "percent" not in json.dumps(second)  # 不用伪百分比：只有已发现的计数
    assert second["found"] == {"scripts": 0, "assets": 0}

    gate.release.set()
    done = svc.wait("pj")
    assert done["state"] == "complete" and done["scan_id"] == first["scan_id"]
    reused = svc.ensure("pj", root)  # 刚完成：A→B→A / 两个标签页的重复认领复用
    assert reused["scan_id"] == first["scan_id"] and gate.calls == 1


def test_force_starts_a_new_scan_with_a_new_epoch(tmp_path):
    root = _project(tmp_path)
    svc = projscan.ScanService()
    first = svc.ensure("pj", root)
    svc.wait("pj")
    forced = svc.ensure("pj", root, force=True)
    svc.wait("pj")
    assert forced["epoch"] == first["epoch"] + 1 and forced["scan_id"] != first["scan_id"]


def test_a_stale_report_is_rescanned_incrementally(tmp_path):
    root = _project(tmp_path)
    _write(root, "plot.py", DYNAMIC)
    clock = {"t": 0.0}
    svc = projscan.ScanService(clock=lambda: clock["t"])
    first = svc.ensure("pj", root)
    svc.wait("pj")
    clock["t"] = projscan.FRESH_S + 1
    again = svc.ensure("pj", root)
    svc.wait("pj")
    assert again["scan_id"] != first["scan_id"]


def test_a_late_result_after_the_project_closed_is_discarded(tmp_path):
    """A→B→A / 关闭项目：迟到的扫描结果不能复活已经 drop 的账，也不能盖掉新一轮。"""
    root = _project(tmp_path)
    gate = _Gate()
    svc = projscan.ScanService(runner=gate.runner)
    hints: list[dict] = []
    old = svc.ensure("pj", root, publish=lambda event, payload: hints.append(payload))
    gate.started.wait(5)
    svc.drop("pj")
    assert svc.get("pj") is None

    fresh_gate = _Gate()
    fresh_gate.release.set()
    svc._runner = fresh_gate.runner
    new = svc.ensure("pj", root)
    assert new["epoch"] > old["epoch"] and new["scan_id"] != old["scan_id"]
    svc.wait("pj")

    gate.release.set()  # 旧线程这时才醒
    time.sleep(0.2)
    current = svc.get("pj")
    assert current["scan_id"] == new["scan_id"] and current["state"] == "complete"
    # 旧一轮只发过「开始」那一次提示；迟到的「结束」不再冒出来让前端去重读一个已不存在的账
    assert [h["scan_id"] for h in hints].count(old["scan_id"]) == 1


def test_a_failing_runner_is_a_terminal_state_not_a_hang(tmp_path):
    root = _project(tmp_path)

    def boom(root, **kw):
        raise RuntimeError("boom")

    svc = projscan.ScanService(runner=boom)
    svc.ensure("pj", root)
    done = svc.wait("pj")
    assert done["state"] == "failed" and done["phase"] == "action_required"
    assert done["outcome"] == {"kind": "failed", "code": "project_scan_failed"}
    assert [a["kind"] for a in done["actions"]] == ["rescan"]
    assert "boom" not in json.dumps(done)  # 异常原文不出门


def test_cancel_stops_only_the_scan(tmp_path):
    root = _project(tmp_path)
    for i in range(3):
        _write(root, f"s{i}.py", TOOL)
    started = threading.Event()
    release = threading.Event()

    def runner(root, cancel=None, **kw):
        started.set()
        release.wait(10)
        return projscan.scan(root, cancel=cancel, **kw)

    svc = projscan.ScanService(runner=runner)
    svc.ensure("pj", root)
    started.wait(5)
    snap = svc.cancel("pj")
    assert snap["state"] == "running"  # 取消是请求，终局由扫描线程落定
    release.set()
    done = svc.wait("pj")
    assert done["state"] == "cancelled"
    assert svc.cancel("nobody") is None


def test_a_different_root_for_the_same_project_id_starts_over(tmp_path):
    a, b = _project(tmp_path, "a"), _project(tmp_path, "b")
    _write(a, "x.py", DYNAMIC)
    svc = projscan.ScanService()
    first = svc.ensure("pj", a)
    svc.wait("pj")
    second = svc.ensure("pj", b)
    svc.wait("pj")
    assert second["scan_id"] != first["scan_id"]


# ---------------------------------------------------------------- 接口
@pytest.fixture
def client(tmp_path, monkeypatch):
    m.app.config["TESTING"] = True
    m.reset_projects()
    monkeypatch.setattr(m, "BAKED_DIR", tmp_path / "_baked")
    monkeypatch.setattr(m, "BAKED_PATH", tmp_path / "_legacy_baked.json")
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path / "_cache")
    yield m.app.test_client()
    m.reset_projects()


def _open(client, root: Path) -> str:
    resp = client.post("/api/projects/open", json={"path": str(root)})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["id"]


def _wait_done(client, pj: str, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while True:
        body = client.get("/api/project/scan", query_string={"pj": pj}).get_json()
        if body["state"] != "running":
            return body
        assert time.time() < deadline, body
        time.sleep(0.02)


def test_get_before_any_scan_is_404_and_post_starts_one(client, tmp_path):
    root = _project(tmp_path)
    _write(root, "plot.py", DYNAMIC)
    pj = _open(client, root)

    missing = client.get("/api/project/scan", query_string={"pj": pj})
    assert missing.status_code == 404 and missing.get_json()["code"] == "project_scan_not_started"

    started = client.post("/api/project/scan", json={}, query_string={"pj": pj})
    assert started.status_code == 202
    body = _wait_done(client, pj)
    assert body["state"] == "complete" and body["default_target"] == "plot.py"
    assert body["project_id"] == pj and body["scan_version"] == 1
    assert started.headers["Cache-Control"] == "no-store"

    reused = client.post("/api/project/scan", json={"reason": "restore"}, query_string={"pj": pj})
    assert reused.status_code == 200 and reused.get_json()["scan_id"] == body["scan_id"]
    forced = client.post("/api/project/scan", json={"force": True}, query_string={"pj": pj})
    assert forced.status_code == 202 and forced.get_json()["scan_id"] != body["scan_id"]


def test_the_request_body_is_a_closed_whitelist(client, tmp_path):
    root = _project(tmp_path)
    pj = _open(client, root)
    for bad in ({"script": "x.py"}, {"force": "yes"}, {"reason": "because"}):
        resp = client.post("/api/project/scan", json=bad, query_string={"pj": pj})
        assert resp.status_code == 400 and resp.get_json()["code"] == "bad_request"
    assert client.get("/api/project/scan", query_string={"pj": pj}).status_code == 404  # 没起扫描


def test_a_scan_belongs_to_its_project(client, tmp_path):
    a, b = _project(tmp_path, "a"), _project(tmp_path, "b")
    _write(a, "plot.py", DYNAMIC)
    pja, pjb = _open(client, a), _open(client, b)
    client.post("/api/project/scan", json={}, query_string={"pj": pja})
    _wait_done(client, pja)

    assert client.get("/api/project/scan", query_string={"pj": pjb}).status_code == 404
    assert client.post("/api/project/scan/cancel", query_string={"pj": pjb}).status_code == 404
    other = client.post("/api/project/scan", json={}, query_string={"pj": pjb})
    done = _wait_done(client, pjb)
    assert other.get_json()["project_id"] == pjb and done["scripts"] == []
    assert (
        client.get("/api/project/scan", query_string={"pj": pja}).get_json()["default_target"]
        == "plot.py"
    )


def test_closing_a_project_forgets_its_scan(client, tmp_path):
    root = _project(tmp_path)
    pj = _open(client, root)
    client.post("/api/project/scan", json={}, query_string={"pj": pj})
    _wait_done(client, pj)
    assert projscan.SCANS.get(pj) is not None

    m.close_project(pj)

    assert projscan.SCANS.get(pj) is None


def test_the_http_report_has_no_machine_path(client, tmp_path):
    root = _project(tmp_path)
    _write(root, "plot.py", DYNAMIC)
    pj = _open(client, root)
    client.post("/api/project/scan", json={}, query_string={"pj": pj})
    raw = json.dumps(_wait_done(client, pj), ensure_ascii=False)
    assert str(root) not in raw and str(tmp_path) not in raw


def test_scan_events_carry_no_payload_beyond_ids(client, tmp_path, monkeypatch):
    seen: list[tuple[str, dict]] = []
    monkeypatch.setattr(m, "sse_publish", lambda event, payload: seen.append((event, payload)))
    root = _project(tmp_path)
    _write(root, "plot.py", DYNAMIC)
    pj = _open(client, root)
    seen.clear()
    client.post("/api/project/scan", json={}, query_string={"pj": pj})
    _wait_done(client, pj)
    time.sleep(0.05)

    scans = [p for e, p in seen if e == "project.scan"]
    assert scans and all(set(p) == {"pj", "scan_id", "epoch"} for p in scans)


def test_an_io_error_detail_with_a_path_does_not_leave_the_report(tmp_path, monkeypatch):
    """`read_source` 的 `detail` 是异常原文，OSError 的原文里带绝对路径：公开投影只留 kind / 行号 / 版本。"""
    root = _project(tmp_path)
    _write(root, "plot.py", DYNAMIC)
    monkeypatch.setattr(
        engine_discover,
        "read_source",
        lambda p: (None, {"kind": "io_error", "detail": f"[Errno 13] Permission denied: '{p}'"}),
    )

    report = projscan.scan(root)

    item = report["scripts"][0]
    assert item["problem"] == {"kind": "io_error"}
    assert str(tmp_path) not in json.dumps(report)
    assert any(i["code"] == "unreadable_file" and i["path"] == "plot.py" for i in report["issues"])


def test_the_scan_event_is_a_kind_the_frontend_listens_for():
    """后端发的事件名写错一个字，前端（`EVENT_KINDS`）就永远收不到而后端全绿：对拍一次。"""
    api_ts = Path(__file__).resolve().parents[1] / "web" / "src" / "lib" / "api.ts"
    if not api_ts.is_file():
        pytest.skip("没有 web/（wheel/sdist 里不含前端源码）")
    source = api_ts.read_text(encoding="utf-8")
    block = source[source.index("const EVENT_KINDS = [") :]
    block = block[: block.index("] as const")]
    assert "'project.scan'" in block
    assert '"project.scan"' in (Path(m.__file__).read_text(encoding="utf-8")) or "project.scan" in (
        Path(projscan.__file__).read_text(encoding="utf-8")
    )
