"""Converted outputs keep their own identity in a fresh application process."""

# ruff: noqa: F811 - imported pytest fixture
from __future__ import annotations

import io
import json
import shutil
import time

import pytest

from tavotto.engine import ai_convert, convertoutputs, registry
from test_ai_convert_app import _start, _wait_final, project  # noqa: F401

SID = "abcdef012345"
SUFFIX = "_matplotlib_" + SID


@pytest.mark.parametrize(
    "source",
    [
        "fig.savefig('a.png', dpi=72)",
        "fig.savefig(fname=Path('a.png'), dpi=72)",
        "(fig.savefig)('a.png', dpi=72)",
        "save = fig.savefig\nsave('a.png', dpi=72)",
        "from matplotlib.pyplot import savefig as store\nstore('a.png', dpi=72)",
        "getattr(fig, 'savefig')('a.png', dpi=72)",
    ],
)
def test_paths_and_aliases_have_a_stable_idempotent_identity(source, monkeypatch):
    import matplotlib.pyplot as plt
    from matplotlib.figure import Figure

    seen = []

    def save(_self=None, *args, **kwargs):
        seen.append((args, kwargs))

    monkeypatch.setattr(Figure, "savefig", save)
    monkeypatch.setattr(plt, "savefig", lambda *a, **k: seen.append((a, k)))
    code = (
        "from pathlib import Path\nfrom matplotlib.figure import Figure\nfig=Figure()\n"
        + source
        + "\n"
    ).encode()
    normalized = convertoutputs.normalize(code, SID)
    assert convertoutputs.normalize(normalized, SID) == normalized
    exec(compile(normalized, "candidate.py", "exec"), {})
    args, kw = seen[0]
    assert (args[0] if args else kw["fname"]) == "a" + SUFFIX + ".png"
    assert kw["dpi"] == 72


def test_other_savefig_methods_and_argument_evaluation_are_unchanged():
    seen = []

    class Other:
        def savefig(self, *args, **kwargs):
            seen.append((args, kwargs))

    def owner():
        seen.append("owner")
        return Other()

    def argument():
        seen.append("argument")
        return "original.png"

    code = convertoutputs.normalize(b"owner().savefig(argument(), transparent=True)\n", SID)
    exec(compile(code, "candidate.py", "exec"), {"owner": owner, "argument": argument})
    assert seen == ["owner", "argument", (("original.png",), {"transparent": True})]


def test_file_stream_is_not_renamed_and_unicode_future_imports_survive(monkeypatch):
    from matplotlib.figure import Figure

    seen = []
    monkeypatch.setattr(Figure, "savefig", lambda _self, name: seen.append(name))
    stream = io.BytesIO()
    source = '"""示例"""\nfrom __future__ import annotations\nfig.savefig(stream)\n'.encode()
    code = convertoutputs.normalize(source, SID)
    exec(compile(code, "candidate.py", "exec"), {"fig": Figure(), "stream": stream})
    assert seen == [stream]


def test_tampered_helper_is_rejected_and_different_conversions_do_not_share_names():
    code = b"import matplotlib.pyplot as plt\nplt.savefig('out.png')\n"
    first = convertoutputs.normalize(code, SID)
    second = convertoutputs.normalize(code, "123456abcdef")
    assert first != second
    changed = first.replace(b"_path = _os.fsdecode(_value)", b"_path = 'stolen.png'")
    with pytest.raises(ValueError, match="output_helper_changed"):
        convertoutputs.normalize(changed, SID)


def test_adopted_output_does_not_take_original_owner_and_reopens_without_cache(project):
    m, client, root, state = project
    original = (root / "fig.py").read_bytes()
    original_image = (root / "out.png").read_bytes()
    record = {"entry": "main", "cost": "medium", "notes": "original", "stems": ["out"]}
    (root / registry.REGISTRY_NAME).write_text(json.dumps({"scripts": {"fig.py": record}}))
    sid = _start(client).get_json()["session"]
    result = _wait_final(client, sid)
    assert result["task"]["outcome"] == "completed", result
    staged = (ai_convert.CONVERT_DIR / sid / "fig_matplotlib.py").read_bytes()
    adopted = client.post(f"/api/ai/convert/{sid}/adopt").get_json()
    assert (root / adopted["script"]).read_bytes() == staged
    assert (root / "fig.py").read_bytes() == original
    assert (root / "out.png").read_bytes() == original_image
    assert json.loads((root / registry.REGISTRY_NAME).read_text())["scripts"]["fig.py"] == record
    shutil.rmtree(ai_convert.CONVERT_DIR / sid)
    m.reset_projects()
    m.open_project(str(root))
    report = client.post(
        "/api/engine/preparation-sessions", json={"script": adopted["script"]}
    ).get_json()
    for kind in ["recheck", "run"]:
        action = next((a for a in report["actions"] if a["kind"] == kind), None)
        if action is None:
            continue
        response = client.post(
            f"/api/engine/preparation-sessions/{report['session_id']}/actions",
            json={"action_id": action["id"], "expected_config_revision": report["config_revision"]},
        )
        assert response.status_code in (200, 202), response.get_json()
        report = response.get_json()["report"]
        deadline = time.monotonic() + 90
        while (
            report["phase"] in ("running", "scanning", "preparing_environment")
            and time.monotonic() < deadline
        ):
            time.sleep(0.1)
            report = client.get(
                f"/api/engine/preparation-sessions/{report['session_id']}"
            ).get_json()
    assert report["phase"] == "completed", report
    assert report["facts"]["execution_finished"] and report["facts"]["figure_captured"]
    assert (root / "fig.py").read_bytes() == original
    assert (root / "out.png").read_bytes() == original_image


@pytest.mark.parametrize(
    "constructor",
    [
        "PdfPages('Rplots.pdf')",
        "PdfPages(filename=Path('Rplots.pdf'))",
        "pages('Rplots.pdf')",
        "backend_pdf.PdfPages('Rplots.pdf')",
    ],
)
def test_multipage_pdf_keeps_original_and_all_pages(tmp_path, monkeypatch, constructor):
    import pypdfium2

    monkeypatch.chdir(tmp_path)
    original = tmp_path / "Rplots.pdf"
    original.write_bytes(b"original native output")
    source = f"""from pathlib import Path
import matplotlib.pyplot as plt
from matplotlib.backends import backend_pdf
from matplotlib.backends.backend_pdf import PdfPages, PdfPages as pages
with {constructor} as pdf:
    for n in range(2):
        fig, ax = plt.subplots()
        ax.text(.5, .5, str(n))
        fig.savefig(pdf, format='pdf')
        plt.close(fig)
""".encode()
    converted = convertoutputs.normalize(source, SID)
    assert convertoutputs.normalize(converted, SID) == converted
    exec(compile(converted, "converted.py", "exec"), {})
    assert original.read_bytes() == b"original native output"
    with pypdfium2.PdfDocument(str(tmp_path / f"Rplots_matplotlib_{SID}.pdf")) as pdf:
        assert len(pdf) == 2


def test_unrelated_pdfpages_constructor_and_stream_remain_unchanged():
    from io import BytesIO

    seen = []

    class Other:
        def PdfPages(self, value):
            seen.append(value)

    source = b"other.PdfPages(stream)\n"
    stream = BytesIO()
    exec(convertoutputs.normalize(source, SID), {"other": Other(), "stream": stream})
    assert seen == [stream]
