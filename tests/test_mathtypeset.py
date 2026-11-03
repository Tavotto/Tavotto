"""标注公式排版（ADR 0152 PR 4）：子进程 + 按内容缓存。

真跑 matplotlib（测试解释器自己有它；没有就跳过）：排得出的给轮廓与盒子，排不出的给原因；
同一条第二次从缓存来、不再起子进程；没有可用解释器时回 `no_runtime` 且不落盘。
"""

from __future__ import annotations

import importlib.util

import pytest

from tavotto.engine import mathtypeset

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("matplotlib") is None, reason="测试解释器没有 matplotlib（not_run）"
)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path))
    for d in (mathtypeset._memory, mathtypeset._picked, mathtypeset._missed):
        d.clear()
    yield
    for d in (mathtypeset._memory, mathtypeset._picked, mathtypeset._missed):
        d.clear()


def test_typesets_to_em_outlines_and_reports_syntax_errors():
    got = mathtypeset.typeset_many([("A e^{-E/RT}", "serif"), ("x^{2", "serif")])
    ok = got[mathtypeset.key_of("A e^{-E/RT}", "serif")]
    assert ok["ok"] and ok["width"] > 1.0 and ok["ascent"] > 0.5
    assert ok["segments"][0][0] == "M"
    assert {s[0] for s in ok["segments"]} <= {"M", "L", "C", "Z"}
    bad = got[mathtypeset.key_of("x^{2", "serif")]
    assert bad["ok"] is False and bad["error"]


def test_second_call_comes_from_cache_without_a_child(monkeypatch):
    mathtypeset.typeset_many([("x_i", "serif")])
    mathtypeset._memory.clear()  # 进程内缓存清掉，只剩磁盘那份

    def boom(_items):
        raise AssertionError("命中缓存时不该再起子进程")

    monkeypatch.setattr(mathtypeset, "_run_child", boom)
    assert mathtypeset.typeset_many([("x_i", "serif")])[mathtypeset.key_of("x_i", "serif")]["ok"]


def test_no_runtime_is_reported_and_not_cached(monkeypatch, tmp_path):
    monkeypatch.setattr(mathtypeset, "_pick_python", lambda _dir=None: None)
    got = mathtypeset.typeset_many([("y", "serif")])
    assert got[mathtypeset.key_of("y", "serif")] == {"ok": False, "error": mathtypeset.NO_RUNTIME}
    assert not (tmp_path / "cache" / "mathtext").exists()


def test_math_items_only_reads_latex_annotations():
    objs = [
        {"type": "text", "text": "k = $x^2$", "text_format": "latex", "font_family": "sans-serif"},
        {"type": "text", "text": "$y$"},  # 老标注：$ 是字
        {"type": "text", "text": "$z$", "text_format": "latex", "hidden": True},
    ]
    assert mathtypeset.math_items(objs) == [("x^2", "sans-serif")]


def test_a_missing_interpreter_is_retried_later(monkeypatch):
    """第一次找不到解释器不记死：装好之后（这里是隔了 RETRY_S）不重启也能排（Codex #1497）。"""
    import sys

    answers = [None, (sys.executable, False)]
    monkeypatch.setattr(mathtypeset, "_pick_python", lambda _dir=None: answers.pop(0))
    monkeypatch.setattr(mathtypeset, "RETRY_S", 0.0)
    first = mathtypeset.typeset_many([("q", "serif")])[mathtypeset.key_of("q", "serif")]
    assert first == {"ok": False, "error": mathtypeset.NO_RUNTIME}
    assert mathtypeset.typeset_many([("q", "serif")])[mathtypeset.key_of("q", "serif")]["ok"]


def test_the_figure_interpreter_decision_is_a_candidate(monkeypatch, tmp_path):
    """内置 runtime 不在、当前解释器也不算（打包态）时，用图渲染那条决策给的解释器——
    环境变量 / 设置 / 项目环境都在那条决策里（Codex #1497）。"""
    import sys

    from tavotto.engine import pool, runtime

    seen = {}

    def resolve(figures_dir, **_kw):
        seen["dir"] = figures_dir
        return sys.executable, "env_override"

    monkeypatch.setattr(runtime, "bundled_python", lambda: None)
    monkeypatch.setattr(runtime, "is_frozen", lambda: True)
    monkeypatch.setattr(pool, "resolve_worker_python", resolve)
    assert mathtypeset._pick_python(tmp_path) == (sys.executable, False)
    assert seen["dir"] == tmp_path


# ---------------- 图内文字预览：`face:<族名>` -----------------------------------


def _first_font_of(face: str, src: str = r"\mathit{T}") -> dict:
    return mathtypeset.typeset_many([(src, f"face:{face}")])[
        mathtypeset.key_of(src, f"face:{face}")
    ]


def _used_fontset(face: str) -> str:
    """子进程为这个 face 选了哪个公式字体集（直接调子进程里的映射，不起进程）。"""
    import importlib.util as iu

    spec = iu.spec_from_file_location("mathtext_child_under_test", mathtypeset.CHILD)
    child = iu.module_from_spec(spec)
    spec.loader.exec_module(child)
    rc, _name = child._face_rc(face)
    return rc["mathtext.fontset"]


@pytest.mark.parametrize(
    "face, fontset",
    [
        ("DejaVu Sans", "dejavusans"),
        ("DejaVu Serif", "dejavuserif"),
        ("STIXGeneral", "stix"),
        ("STIX Sans", "stixsans"),
        ("Computer Modern", "cm"),
        ("cmr10", "cm"),
        ("dejavu serif", "dejavuserif"),  # 比较不分大小写
        ("cmss10", "custom"),  # matplotlib 自带但不是内置公式字体集的族：走 custom
        ("No Such Family 12345", "dejavusans"),  # 这台机器没有：与 matplotlib 一样退 DejaVu Sans
    ],
)
def test_face_names_map_to_mathtext_fontsets(face, fontset):
    assert _used_fontset(face) == fontset


def test_face_spec_typesets_with_the_named_face_and_differs_from_the_canvas_family():
    serif = _first_font_of("DejaVu Serif", "T^2")
    sans = _first_font_of("DejaVu Sans", "T^2")
    lib = mathtypeset.typeset_many([("T^2", "serif")])[mathtypeset.key_of("T^2", "serif")]
    assert serif["ok"] and sans["ok"] and lib["ok"]
    # 不同字体集排出的轮廓不同（字宽 / 路径）；缓存键也不同
    assert serif["segments"] != sans["segments"]
    assert sans["segments"] != lib["segments"]
    assert mathtypeset.key_of("T^2", "face:DejaVu Sans") != mathtypeset.key_of("T^2", "serif")


def test_face_spec_is_only_for_the_preview_endpoint_not_annotations():
    # 标注 / 导出路径（family_of）仍严格三族
    assert mathtypeset.family_of("face:DejaVu Sans") == "serif"
    assert mathtypeset.math_items(
        [{"type": "text", "text": "$x$", "text_format": "latex", "font_family": "face:DejaVu Sans"}]
    ) == [("x", "serif")]
    assert mathtypeset.request_family("face:DejaVu Sans") == "face:DejaVu Sans"
    assert mathtypeset.request_family("bogus") == "serif"


@pytest.mark.parametrize(
    "bad",
    [
        "face:",
        "face:   ",
        "face:a:italic",
        "face:/etc/passwd",
        "face:a,b",
        "face:" + "x" * 81,
        "face:a\nb",
    ],
)
def test_face_spec_rejects_paths_and_pattern_characters(bad):
    assert mathtypeset.face_of(bad) is None
    assert mathtypeset.request_family(bad) == "serif"


class TestMathtextEndpointFace:
    @pytest.fixture
    def client(self):
        from tavotto import app as m

        m.app.config["TESTING"] = True
        return m.app.test_client()

    def test_accepts_face_family_and_returns_a_box(self, client):
        r = client.post(
            "/api/mathtext", json={"items": [{"src": r"\mathit{T}", "family": "face:DejaVu Serif"}]}
        )
        assert r.status_code == 200
        assert r.get_json()["results"][0]["ok"] is True

    @pytest.mark.parametrize("fam", ["face:../../x", "face:a:italic", "face:", "face:" + "y" * 200])
    def test_malformed_face_is_a_400_not_a_silent_serif(self, client, fam):
        r = client.post("/api/mathtext", json={"items": [{"src": "x", "family": fam}]})
        assert r.status_code == 400 and r.get_json()["error"] == "bad_items"

    def test_unknown_plain_family_still_falls_back_to_serif(self, client):
        r = client.post("/api/mathtext", json={"items": [{"src": "x", "family": "Comic"}]})
        assert r.status_code == 200 and r.get_json()["results"][0]["ok"] is True

    def test_limits_are_unchanged(self, client):
        r = client.post(
            "/api/mathtext", json={"items": [{"src": "x" * 2001, "family": "face:DejaVu Sans"}]}
        )
        assert r.status_code == 400


def test_child_face_table_mirrors_the_manifest_math_face_table():
    """严格同源：`manifest._MATHTEXT_SET_FACES`（字体集 → 脸名）与子进程的 `FACE_SETS`（脸名 → 字体集）。

    manifest 里 stix / stixsans 都报 `STIXGeneral`，所以那一脸只能回到 `stix`；其余一一对应。"""
    import ast
    import importlib.util as iu

    # manifest 在测试解释器里 import 不起来（要渲染依赖）：按 AST 读那张表，不 import
    tree = ast.parse((mathtypeset.CHILD.parent / "manifest.py").read_text(encoding="utf-8"))
    table = next(
        ast.literal_eval(n.value)
        for n in tree.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "_MATHTEXT_SET_FACES" for t in n.targets)
    )
    assert table, "manifest 里的 _MATHTEXT_SET_FACES 找不到"

    spec = iu.spec_from_file_location("mathtext_child_under_test2", mathtypeset.CHILD)
    child = iu.module_from_spec(spec)
    spec.loader.exec_module(child)
    for fontset, face in table.items():
        got = child.FACE_SETS[face.lower()]
        assert got == fontset or (face == "STIXGeneral" and got == "stix"), (fontset, face, got)
