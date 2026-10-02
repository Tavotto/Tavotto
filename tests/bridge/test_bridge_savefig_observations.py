"""Real native passthrough retains results and exceptions while recording occurrences."""

from __future__ import annotations

import json
import subprocess

from support.bridgekit import child_env, run_runner, write
from tavotto.engine import bridge
from test_savefig_observations import SCRIPT


def test_native_occurrences_do_not_change_passthrough_success_failure_or_streams(
    user_python, tmp_path
):
    body = (
        SCRIPT.replace("plt.close('all')", "")
        + """\
import io, json
buffer = io.BytesIO()
result = other.savefig(buffer, format='png')
assert result is None and len(buffer.getvalue()) > 0
try:
    other.savefig('failed.pdf', unknown_save_keyword=True)
except TypeError as exc:
    print(json.dumps({'exception_type': type(exc).__name__, 'message': str(exc)}))
else:
    raise AssertionError('the save should fail')
plt.close('all')
"""
    )
    native, ordinary = tmp_path / "native", tmp_path / "ordinary"
    script = write(native / "figure.py", body)
    write(ordinary / "figure.py", body)
    reference = subprocess.run(
        [user_python, "figure.py"],
        cwd=ordinary,
        env=child_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    report_path = tmp_path / "report.json"
    result = run_runner(
        user_python,
        bridge.RUNNER_PY,
        target=script,
        cwd=str(native),
        report=report_path,
        out_dir=tmp_path / "out",
    )
    assert reference.returncode == result.returncode == 0, (reference.stderr, result.stderr)
    assert json.loads(result.stdout) == json.loads(reference.stdout)
    assert (native / "same.png").read_bytes() == (ordinary / "same.png").read_bytes()
    assert (native / "same.pdf").is_file()
    report = json.loads(report_path.read_text(encoding="utf-8"))["savefig_observations"]
    assert report["complete"] and report["observed_count"] == 4
    rows = report["records"]
    assert [r["figure_ordinal"] for r in rows] == [1, 1, 2, 2]
    assert [r["figure_size_inches"] for r in rows] == [[4, 3], [5, 3], [6, 2], [6, 2]]
    assert [r["result"] for r in rows] == ["saved", "saved", "saved", "failed"]
    assert [r["destination"]["path"] for r in rows] == [
        "same.pdf",
        "same.png",
        "same.pdf",
        "failed.pdf",
    ]
    assert str(tmp_path) not in json.dumps(report)


def test_native_default_canvas_format_and_bytes_names_are_not_guessed(user_python, tmp_path):
    script = write(
        tmp_path / "figure.py",
        """\
from matplotlib.figure import Figure
from matplotlib.backends.backend_pdf import FigureCanvasPdf
from matplotlib.backends.backend_agg import FigureCanvasAgg
pdf = Figure(figsize=(2, 1))
FigureCanvasPdf(pdf)
pdf.savefig('no_ext_pdf')
agg = Figure(figsize=(2, 1))
FigureCanvasAgg(agg)
agg.savefig(b'no_ext_bytes')
agg.savefig(b'bytes_suffix.pdf')
""",
    )
    report_path = tmp_path / "report.json"
    result = run_runner(
        user_python,
        bridge.RUNNER_PY,
        target=script,
        cwd=str(tmp_path),
        report=report_path,
        out_dir=tmp_path / "out",
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "no_ext_pdf.pdf").read_bytes().startswith(b"%PDF-")
    assert (tmp_path / "no_ext_bytes").read_bytes().startswith(b"\x89PNG")
    assert (tmp_path / "bytes_suffix.pdf").read_bytes().startswith(b"\x89PNG")
    rows = json.loads(report_path.read_text(encoding="utf-8"))["savefig_observations"]["records"]
    assert [r["options"]["format"] for r in rows] == [None, None, None]
    assert [r["destination"] for r in rows] == [
        {"scope": "unresolved", "path": None},
        {"scope": "execution", "path": "no_ext_bytes"},
        {"scope": "execution", "path": "bytes_suffix.pdf"},
    ]


def test_native_hook_hides_unresolved_stem_without_changing_the_save(user_python, tmp_path):
    outside = tmp_path / "private-outside-file.pdf"
    project = tmp_path / "project"
    script = write(
        project / "figure.py",
        f"import matplotlib.pyplot as plt\nplt.figure().savefig({str(outside)!r})\n",
    )
    report_path = tmp_path / "report.json"
    result = run_runner(
        user_python,
        bridge.RUNNER_PY,
        target=script,
        cwd=str(project),
        report=report_path,
        out_dir=tmp_path / "out",
    )
    assert result.returncode == 0, result.stderr
    assert outside.read_bytes().startswith(b"%PDF-")
    ledger = json.loads(report_path.read_text(encoding="utf-8"))["savefig_observations"]
    (row,) = ledger["records"]
    assert row["destination"] == {"scope": "unresolved", "path": None}
    assert row["stem"] is None
    assert row["result"] == "saved"
    assert "private-outside-file" not in json.dumps(ledger)


def test_observation_never_adds_native_custom_metadata_getter_calls(user_python, tmp_path):
    body = """\
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
calls = []
class StatefulFigure(Figure):
    def get_size_inches(self):
        calls.append('size')
        return super().get_size_inches()
standard = plt.figure(figsize=(4, 3), dpi=100)
standard.subplots().plot([0, 1], [1, 0])
standard.savefig('standard.png')
fig = plt.figure(FigureClass=StatefulFigure, figsize=(4, 3), dpi=100)
fig.subplots().plot([0, 1], [1, 0])
result = fig.savefig('subclass.png')
try:
    fig.savefig('failed.png', unknown_save_keyword=True)
except TypeError as exc:
    error = (type(exc).__name__, str(exc))
else:
    raise AssertionError('the save must fail')
original = standard.get_size_inches
def patched_size():
    calls.append('instance size')
    return original()
standard.get_size_inches = patched_size
standard.savefig('instance.png')
del standard.get_size_inches
original_dpi = Figure.dpi
def patched_dpi(self):
    calls.append('class dpi')
    return original_dpi.fget(self)
Figure.dpi = property(patched_dpi, original_dpi.fset)
try:
    standard.savefig('descriptor.png')
finally:
    Figure.dpi = original_dpi
print(json.dumps({'calls': calls, 'return': result, 'exception': error}))
plt.close('all')
"""
    native, ordinary = tmp_path / "native", tmp_path / "ordinary"
    script = write(native / "figure.py", body)
    write(ordinary / "figure.py", body)
    reference = subprocess.run(
        [user_python, "figure.py"],
        cwd=ordinary,
        env=child_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    report_path = tmp_path / "report.json"
    observed = run_runner(
        user_python,
        bridge.RUNNER_PY,
        target=script,
        cwd=str(native),
        report=report_path,
        out_dir=tmp_path / "out",
    )
    assert reference.returncode == observed.returncode == 0, (reference.stderr, observed.stderr)
    assert json.loads(reference.stdout) == json.loads(observed.stdout)
    for name in ("standard.png", "subclass.png", "instance.png", "descriptor.png"):
        assert (ordinary / name).read_bytes() == (native / name).read_bytes()
    ledger = json.loads(report_path.read_text())["savefig_observations"]
    assert ledger["observed_count"] == 5 and ledger["complete"] is False
    rows = ledger["records"]
    assert rows[0]["figure_size_inches"] == [4, 3]
    assert [row["figure_ordinal"] for row in rows] == [1, 2, 2, 1, 1]
    assert [row["result"] for row in rows] == ["saved", "saved", "failed", "saved", "saved"]
    assert all(row["figure_size_inches"] is None for row in rows[1:])
