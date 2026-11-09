"""No-figure native failures carry a bounded exception category over IPC."""

from __future__ import annotations

import pytest

from support.bridgekit import write

pytestmark = pytest.mark.usefixtures("clean_env")


def test_no_figure_failure_reports_category_without_user_exception_text(tmp_path, bridge_session):
    project = tmp_path / "project"
    script = project / "fig.py"
    write(script, "open('private-user-data.csv').read()\n")
    with bridge_session(script, cwd=str(project)) as session:
        event = session.wait_event("exit")
    assert event["code"] == 1
    assert event["exception_category"] == "file_not_found"
    assert "private-user-data.csv" not in str(event)


@pytest.mark.parametrize(
    ("source", "code", "category"),
    [
        ("open('absent-dir/output.csv', 'w')\n", 1, "file_not_found"),
        ("raise RuntimeError('private-detail')\n", 1, "other"),
        ("try:\n open('missing.csv')\nexcept FileNotFoundError:\n pass\n", 0, None),
        ("raise SystemExit(1)\n", 1, None),
    ],
)
def test_only_an_escaping_exception_is_classified(tmp_path, bridge_session, source, code, category):
    project = tmp_path / "project"
    script = project / "fig.py"
    write(script, source)
    with bridge_session(script, cwd=str(project)) as session:
        event = session.wait_event("exit")
    assert event["code"] == code
    assert event.get("exception_category") == category
    assert "private-detail" not in str(event)


def test_error_after_a_figure_carries_the_same_category_at_barrier_and_exit(
    tmp_path, bridge_session
):
    project = tmp_path / "project"
    script = project / "fig.py"
    write(
        script,
        "import matplotlib.pyplot as plt\nplt.plot([1, 2])\n"
        "open('private-data.csv').read()\n",
    )
    with bridge_session(script, cwd=str(project)) as session:
        barrier = session.wait_event("barrier")
        assert barrier["script_error"]["exception_category"] == "file_not_found"
        session.resume()
        event = session.wait_event("exit")
    assert event["exception_category"] == "file_not_found"
    assert "private-data.csv" not in str(event)
