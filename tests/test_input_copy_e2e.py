"""ADR 0173 端到端：真 sidecar + 真用户 Python 的 native 运行缺读取 → 提议复制 → 复制 → 重跑成功。

独立可观测事实只来自磁盘上的文件（复制出来的内容、脚本自己写出的 `cache.csv`），不来自接口回显。
不触发任何提权（macOS 上会弹系统授权窗口）：提权分支在 `test_input_copy.py` 里用假助手验。
"""

from __future__ import annotations

import os

import pytest

from support import nativekit
from test_execution_fallback_e2e import ENV, SESSIONS, _create, _run, _settled, sidecar_app

pytestmark = nativekit.needs_user_python

SCRIPT = (
    "import os\nfrom pathlib import Path\nimport matplotlib.pyplot as plt\n"
    "data = int(open({missing!r}, encoding='utf-8').read())\n"
    "root = Path(__file__).resolve().parent\n"
    "name = os.environ.get('CACHE_NAME', 'cache')\n"
    "tmp, final = root / (name + '.tmp'), root / (name + '.csv')\n"
    "tmp.write_text(str(data))\n"
    "os.replace(tmp, final)\n"
    # 覆盖层（#1813 方案 A）兜得住写 / 改名 / 读回；对覆盖层路径的元数据探测仍回退 native（防 C 层读旧值），
    # 这里用它造出「A 不行、落到 C」的那条路
    "os.path.getsize(final)\n"
    "value = int(final.read_text())\n"
    "plt.plot([0, data], [0, value])\n"
)


def _project(tmp_path, *, parent=True):
    root = tmp_path / "project"
    root.mkdir()
    data = root / "data" / "values.csv"
    data.parent.mkdir()
    data.write_text("7", encoding="utf-8")
    missing = tmp_path / "incoming" / "values.csv"
    if parent:
        missing.parent.mkdir()
    (root / "cache.csv").write_text("1", encoding="utf-8")
    (root / "fig.py").write_text(SCRIPT.format(missing=str(missing)), encoding="utf-8")
    return root, data, missing


def _rerun(app):
    """重新点「运行」：同一个脚本会复用已有的准备会话（200）或新建（201）。"""
    status, report = app.call(SESSIONS, {"script": "fig.py"}, timeout=120)
    assert status in (200, 201), report
    return _settled(app, _run(app, report))


def _first_run(app):
    first = _settled(app, _run(app, _create(app)))
    assert [(a["profile"], a["code"]) for a in first["attempt_history"]][-1] == (
        "native",
        "script_error",
    )
    return first


def _offer(report):
    (req,) = [r for r in report["requirements"] if r["kind"] == "input_location"]
    return req["payload"]


def test_a_confirmed_data_identity_is_copied_to_the_original_path_and_the_rerun_succeeds(tmp_path):
    root, data, missing = _project(tmp_path)
    with sidecar_app(root, tmp_path / "work", env_overrides=ENV) as app:
        status, link = app.call(
            "/api/engine/input-remap",
            {"requested": str(missing), "chosen": str(data), "chosen_kind": "file"},
        )
        assert status == 200, link
        offer = _offer(_first_run(app))
        assert offer["via"] == "native" and offer["requested"] == str(missing)
        assert offer["copy"]["state"] == "ask"
        assert (offer["copy"]["source"], offer["copy"]["target"]) == (str(data), str(missing))
        assert not missing.exists()  # 提议本身什么都不写
        status, res = app.call("/api/engine/input-copy", {"token": offer["copy"]["token"]})
        assert status == 200 and res["state"] == "done", res
        assert missing.read_text(encoding="utf-8") == "7"
        assert data.read_text(encoding="utf-8") == "7"  # 原件不动
        (root / "cache.csv").write_text("1", encoding="utf-8")
        final = _rerun(app)
        assert final["phase"] == "completed", final
        assert (root / "cache.csv").read_text() == "7"
        # 第二次点：目标已在，不覆盖、不报错
        missing.write_text("mine", encoding="utf-8")
        status, again = app.call("/api/engine/input-copy", {"token": offer["copy"]["token"]})
        assert status == 200 and again["state"] == "exists", again
        assert missing.read_text(encoding="utf-8") == "mine"


def test_without_a_confirmed_data_identity_nothing_is_offered(tmp_path):
    root, _data, missing = _project(tmp_path)
    with sidecar_app(root, tmp_path / "work", env_overrides=ENV) as app:
        first = _settled(app, _run(app, _create(app)))
        # 没有改指规则：沿用 #1817 的改脚本入口，但不带复制提议
        reqs = [r for r in first["requirements"] if r["kind"] == "input_location"]
        for req in reqs:
            assert "copy" not in req["payload"]
        assert not missing.exists()
        # 端点也不会凭空复制
        status, res = app.call("/api/engine/input-copy", {"token": "no-such-token"})
        assert status == 200 and res["state"] == "auth_failed" and res["reason"] == "no_source"
        assert not missing.exists()


def test_a_missing_parent_directory_is_the_last_card_and_is_not_created(tmp_path):
    root, data, missing = _project(tmp_path, parent=False)
    with sidecar_app(root, tmp_path / "work", env_overrides=ENV) as app:
        app.call(
            "/api/engine/input-remap",
            {"requested": str(missing), "chosen": str(data), "chosen_kind": "file"},
        )
        offer = _offer(_first_run(app))
        assert (offer["copy"]["state"], offer["copy"]["reason"]) == ("auth_failed", "no_parent")
        status, res = app.call("/api/engine/input-copy", {"token": offer["copy"]["token"]})
        assert status == 200 and res["state"] == "auth_failed" and res["reason"] == "no_parent"
        assert not missing.parent.exists()


@pytest.mark.skipif(
    os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="用只读目录模拟，POSIX 非 root",
)
def test_a_read_only_directory_reports_denied_and_leaves_nothing_behind(tmp_path):
    root, data, missing = _project(tmp_path)
    with sidecar_app(root, tmp_path / "work", env_overrides=ENV) as app:
        app.call(
            "/api/engine/input-remap",
            {"requested": str(missing), "chosen": str(data), "chosen_kind": "file"},
        )
        offer = _offer(_first_run(app))
        missing.parent.chmod(0o555)
        try:
            status, res = app.call("/api/engine/input-copy", {"token": offer["copy"]["token"]})
        finally:
            missing.parent.chmod(0o755)
        assert status == 200 and res["state"] == "denied", res
        assert list(missing.parent.iterdir()) == []


def test_the_endpoint_takes_only_an_opaque_token_never_a_path(tmp_path):
    root, data, missing = _project(tmp_path)
    stray = tmp_path / "incoming" / "other.csv"
    with sidecar_app(root, tmp_path / "work", env_overrides=ENV) as app:
        app.call(
            "/api/engine/input-remap",
            {"requested": str(missing), "chosen": str(data), "chosen_kind": "file"},
        )
        offer = _offer(_first_run(app))
        # 请求里的路径串、脚本名一律不被当成来源：没有令牌就是「没有来源」
        for body in (
            {"script": "fig.py", "requested": str(missing)},
            {"script": "fig.py", "requested": str(stray)},
            {"token": str(missing)},
            {"token": 5},
        ):
            status, res = app.call("/api/engine/input-copy", body)
            assert status == 200 and (res["state"], res["reason"]) == (
                "auth_failed",
                "no_source",
            ), body
        assert not stray.exists() and not missing.exists()
        status, res = app.call("/api/engine/input-copy", {"token": offer["copy"]["token"]})
        assert res["state"] == "done"
