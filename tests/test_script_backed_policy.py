"""`script-backed-figure-v1`（ADR 0139，#1200）：来源驱动采用。

真实 worker + 真实 matplotlib 为主；假 worker（`test_selected_artifact_api.api`）只补充
「不需要真重画」的闸门类用例。精确策略（`selected-figsize-v1`）的行为由既有四个文件看护，
这里只加对照：同一张带图例的图，精确策略拒绝、来源驱动策略准入。
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import subprocess
import threading
from pathlib import Path

import pytest

from tavotto import app as m, pixelmetrics
from tavotto.engine import artifactcontext as ac, config, figcapture, pool
from test_savefig_capture_params import WORKER_PY, needs_worker
from test_selected_artifact_api import api, refused  # noqa: F401  (api 是 fixture)

SB = figcapture.SCRIPT_BACKED_POLICY
STRICT = figcapture.SELECTED_FIGSIZE_POLICY
FRAME = {"gid": "figure", "prop": "frame", "value": "figsize"}
COLOR = {"gid": "axes_0.lines_0", "prop": "color", "value": "red"}

SCRIPT = """\
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
fig, ax = plt.subplots(figsize=(4, 3), dpi=100)
ax.plot([0, 1, 2], [2, 0, 3], label='signal')
ax.scatter([0, 1, 2], [1, 2, 1], label='points')
ax.set(xlabel='Time', ylabel='Response', title='Legend figure')
ax.legend()
EXTRA
fig.savefig('same.png', dpi=100)
plt.close('all')
"""

PLAIN = """\
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
fig, ax = plt.subplots(figsize=(4, 3), dpi=100)
ax.plot([0, 1, 2], [2, 0, 3])
ax.set(xlabel='Time', ylabel='Response', title='Plain')
fig.savefig('same.png', dpi=100)
plt.close('all')
"""


def run(script: Path, cwd: Path):
    proc = subprocess.run(
        [WORKER_PY, str(script)],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=90,
    )
    assert proc.returncode == 0, proc.stderr


def project(root: Path, source: str = SCRIPT, *, extra: str = "", original_prelude: str = ""):
    """脚本 = 用户的 figure.py；原件 = `original_prelude` 之后的同一份脚本画出来的（模拟另一台机器 / 另一个环境）。"""
    root.mkdir(parents=True, exist_ok=True)
    body = source.replace("EXTRA", extra)
    (root / "figure.py").write_text(body, encoding="utf-8")
    if original_prelude:
        gen = root.parent / f"gen-{root.name}.py"
        gen.write_text(original_prelude + "\n" + body, encoding="utf-8")
        run(gen, root)
    else:
        run(root / "figure.py", root)
    (root / "tavotto_registry.json").write_text(
        json.dumps(
            {"scripts": {"figure.py": {"entry": "__main__", "stems": ["same"], "cost": "light"}}}
        )
    )
    return root


def context(root: Path, policy: str = SB, name: str = "same.png") -> dict:
    return {**ac.create_context(root / name, name), "render_policy": policy}


def build(root: Path, ctx: dict):
    worker = pool.one_shot("figure.py", str(root), "__main__", artifact_source=ctx)
    return worker, worker.ensure_built()["artifact_probe"]


def reason_of(root: Path, ctx: dict) -> str:
    worker = pool.one_shot("figure.py", str(root), "__main__", artifact_source=ctx)
    try:
        with pytest.raises(pool.WorkerError) as error:
            worker.ensure_built()
        assert error.value.code == "artifact_source_unavailable"
        return error.value.extra["reason"]
    finally:
        pool.discard(worker)


@pytest.fixture
def sb_env(tmp_path, monkeypatch):
    """私有数据 / 配置目录 + 实验开关打开（真实配置文件，不 mock 开关函数）。"""
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(tmp_path / "config"))
    config.save({**config._defaults(), "experiments": {ac.EXPERIMENT: True}})
    return tmp_path / "project"


@pytest.fixture
def opened(sb_env, monkeypatch):
    """`opened(root)` 打开项目并返回 test_client；收尾关项目。"""
    monkeypatch.setattr(m, "PROJECTS", {})
    monkeypatch.setattr(m, "DEFAULT_PROJECT", None)
    m.app.config["TESTING"] = True

    def open_it(root: Path):
        m.open_project(str(root))
        return m.app.test_client()

    yield open_it
    for pid in list(m.PROJECTS):
        m.close_project(pid, wait=True)


def post(client, route, **body):
    return client.post("/api/engine/" + route, json=body)


def render(client, patches=(), *, confirmed=None, expected=None, name="same.png", **extra):
    body = {"id": name, "source_policy": SB, "patches": list(patches), "inline_svg": True, **extra}
    if expected is not None:
        body["expected_source"] = expected
    if confirmed is not None:
        body["confirmed_reconstruction_sha256"] = confirmed
    return post(client, "render", **body)


def identity(root, name="same.png"):
    c = ac.create_context(root / name, name)
    return {k: c[k] for k in ("bytes_sha256", "size_bytes")}


# ----------------------------------------------------------------------------- 纯函数


def test_diff_extent_matches_a_per_pixel_oracle():
    rng = random.Random(7)
    for _ in range(200):
        w, h = rng.randint(1, 9), rng.randint(1, 7)
        a = bytearray(rng.randbytes(w * h * 4))
        b = bytearray(a)
        for _ in range(rng.randint(0, 6)):
            i = rng.randrange(w * h * 4)
            b[i] = (b[i] + rng.choice([1, 255, 128, 3])) % 256
        diff = sorted({i // 4 for i in range(w * h * 4) if a[i] != b[i]})
        got = pixelmetrics.diff_extent(bytes(a), bytes(b), (w, h))
        assert got["differing_pixels"] == len(diff)
        assert got["total_pixels"] == w * h
        if diff:
            xs, ys = [p % w for p in diff], [p // w for p in diff]
            assert got["bbox"] == [min(xs), min(ys), max(xs) + 1, max(ys) + 1]
        else:
            assert got["bbox"] is None


def test_one_channel_off_by_one_is_never_accepted_as_exact(tmp_path):
    """没有任何自动接受的容差：一个像素的一个通道差 1 也只能是 needs_confirmation。"""
    from PIL import Image

    a = Image.new("RGBA", (6, 5), (10, 20, 30, 255))
    b = a.copy()
    b.putpixel((3, 2), (10, 20, 31, 255))
    a.save(tmp_path / "a.png")
    b.save(tmp_path / "b.png")
    verdict = ac._adoption_verdict(
        tmp_path / "a.png", tmp_path / "b.png", "png", {}, (tmp_path / "b.png").read_bytes()
    )
    assert verdict["status"] == ac.STATUS_NEEDS_CONFIRMATION and not verdict["pixels_equal"]
    assert verdict["metrics"]["differing_pixels"] == 1
    assert verdict["metrics"]["bbox"] == [3, 2, 4, 3]
    same = ac._adoption_verdict(
        tmp_path / "a.png", tmp_path / "a.png", "png", {}, (tmp_path / "a.png").read_bytes()
    )
    assert same["status"] == ac.STATUS_EXACT and same["metrics"]["bbox"] is None


def test_real_edits_ignores_only_the_structural_frame():
    assert not m._real_edits([]) and not m._real_edits(None)
    assert not m._real_edits([FRAME])
    assert m._real_edits([FRAME, COLOR]) and m._real_edits([COLOR])


def test_policy_joins_the_closed_set_with_its_own_pool_identity(tmp_path):
    assert SB in figcapture.SELECTED_ARTIFACT_POLICIES
    (tmp_path / "same.png").write_bytes(b"x")
    a = {**ac.create_context(tmp_path / "same.png", "same.png"), "render_policy": STRICT}
    b = {**a, "render_policy": SB}
    assert pool._worker_key(str(tmp_path), "figure.py", a) != pool._worker_key(
        str(tmp_path), "figure.py", b
    )
    assert pool._worker_base(str(tmp_path), "figure.py", a) != pool._worker_base(
        str(tmp_path), "figure.py", b
    )
    with pytest.raises(figcapture.ArtifactContextError):
        figcapture.artifact_request("script-backed-figure-v2", None)
    # 与精确策略同口径：编辑请求必须带 figsize 图幅
    with pytest.raises(figcapture.ArtifactContextError) as error:
        figcapture.artifact_request(SB, {"bytes_sha256": "a" * 64, "size_bytes": 1}, [COLOR])
    assert error.value.reason == "unsupported_render_state"
    pdf = {**a, "kind": "pdf", "render_policy": SB}
    with pytest.raises(figcapture.ArtifactContextError):
        figcapture.selected_artifact_context(pdf)


# ----------------------------------------------------------------------------- 真 worker：准入


@needs_worker
def test_legend_figure_strict_refuses_artist_profile_script_backed_admits_exact(sb_env):
    root = project(sb_env)
    original = (root / "same.png").read_bytes()
    assert reason_of(root, context(root, STRICT)) == "artist_profile"
    ctx = context(root, SB)
    worker, probe = build(root, ctx)
    try:
        verdict = ac.admit_reconstruction(root / "same.png", Path(probe["initialized"]), ctx)
        # 实测（同一 matplotlib、同一解释器画的带图例 + scatter 原件）：零编辑重画逐像素相等 → exact
        assert verdict["status"] == ac.STATUS_EXACT and verdict["pixels_equal"]
        assert verdict["metrics"]["differing_pixels"] == 0
        assert (
            verdict["reconstruction_sha256"]
            == hashlib.sha256(Path(probe["initialized"]).read_bytes()).hexdigest()
        )
        # 编辑语义与普通路径同一条代码：图例随图重画，改线色不动原件
        result = worker.override("same", [FRAME, COLOR], inline_svg=True)
        assert not result["warnings"]
        assert any("legend" in e["gid"] for e in result["manifest"]["elements"])
    finally:
        pool.discard(worker)
    assert (root / "same.png").read_bytes() == original


@needs_worker
def test_plain_figure_still_admitted_by_strict_policy_unchanged(sb_env):
    root = project(sb_env, PLAIN)
    ctx = context(root, STRICT)
    worker, probe = build(root, ctx)
    try:
        assert ac.validate_candidate(root / "same.png", Path(probe["initialized"]), ctx)[
            "pixels_equal"
        ]
    finally:
        pool.discard(worker)


@needs_worker
def test_cross_environment_pixels_need_confirmation_with_nonzero_metrics(sb_env):
    root = project(
        sb_env, original_prelude="import matplotlib; matplotlib.rcParams['lines.antialiased']=False"
    )
    ctx = context(root, SB)
    worker, probe = build(root, ctx)
    try:
        verdict = ac.admit_reconstruction(root / "same.png", Path(probe["initialized"]), ctx)
    finally:
        pool.discard(worker)
    assert verdict["status"] == ac.STATUS_NEEDS_CONFIRMATION and not verdict["pixels_equal"]
    metrics = verdict["metrics"]
    assert metrics["differing_pixels"] > 0 and 0 < metrics["differing_ratio"] < 1
    x0, y0, x1, y1 = metrics["bbox"]
    assert 0 <= x0 < x1 <= 400 and 0 <= y0 < y1 <= 300
    # 精确策略对同一份原件的判据不变：像素不等 → source_mismatch
    strict_ctx = context(root, SB)
    strict_ctx["render_policy"] = STRICT
    strict_worker = pool.one_shot("figure.py", str(root), "__main__", artifact_source=ctx)
    try:
        probe = strict_worker.ensure_built()["artifact_probe"]
        with pytest.raises(ac.ArtifactContextError) as error:
            ac.validate_candidate(root / "same.png", Path(probe["initialized"]), ctx)
        assert error.value.reason == "source_mismatch"
    finally:
        pool.discard(strict_worker)


@needs_worker
def test_different_frame_is_refused_not_needs_confirmation(sb_env):
    """figure.dpi 200（Retina / macOS 直跑）→ 图幅 800x600 ≠ 重画的 400x300：直接拒绝。"""
    follow_dpi = PLAIN.replace("figsize=(4, 3), dpi=100", "figsize=(4, 3)").replace(
        "fig.savefig('same.png', dpi=100)", "fig.savefig('same.png')"
    )
    root = project(
        sb_env,
        follow_dpi,
        original_prelude="import matplotlib; matplotlib.rcParams['figure.dpi']=200",
    )
    from PIL import Image

    with Image.open(root / "same.png") as image:
        assert image.size == (800, 600)
    ctx = context(root, SB)
    worker, probe = build(root, ctx)
    try:
        with pytest.raises(ac.ArtifactContextError) as error:
            ac.admit_reconstruction(root / "same.png", Path(probe["initialized"]), ctx)
        assert error.value.reason == "source_mismatch"
    finally:
        pool.discard(worker)


@needs_worker
def test_pixel_density_metadata_difference_is_refused_even_with_equal_pixels(sb_env, tmp_path):
    """图幅检查（尺寸与 pHYs）与精确策略共用：像素全等但密度标签不同也不准入。"""
    from PIL import Image

    root = project(sb_env)
    ctx = context(root, SB)
    worker, probe = build(root, ctx)
    try:
        other = tmp_path / "reconstruction.png"
        with Image.open(probe["initialized"]) as image:
            image.save(other, dpi=(300, 300))
        with pytest.raises(ac.ArtifactContextError) as error:
            ac.admit_reconstruction(root / "same.png", other, ctx)
        assert error.value.reason == "source_mismatch"
    finally:
        pool.discard(worker)


@needs_worker
def test_save_then_mutate_figure_is_needs_confirmation_never_exact(sb_env):
    source = PLAIN.replace(
        "fig.savefig('same.png', dpi=100)",
        "fig.savefig('same.png', dpi=100)\nax.plot([0, 1, 2], [3, 3, 3], color='red', lw=4)",
    )
    root = project(sb_env, source)
    ctx = context(root, SB)
    worker, probe = build(root, ctx)
    try:
        verdict = ac.admit_reconstruction(root / "same.png", Path(probe["initialized"]), ctx)
    finally:
        pool.discard(worker)
    assert verdict["status"] == ac.STATUS_NEEDS_CONFIRMATION
    assert verdict["metrics"]["differing_pixels"] > 0


@needs_worker
def test_same_path_saved_twice_is_ambiguous(sb_env):
    root = project(
        sb_env, PLAIN.replace("plt.close", "fig.savefig('same.png', dpi=100)\nplt.close")
    )
    assert reason_of(root, context(root, SB)) == "save_ambiguous"


@needs_worker
def test_png_bound_to_its_own_save_not_the_same_stem_pdf(sb_env):
    """同 stem 的 PDF 与 PNG 内容不同：被选的 PNG 只绑定保存到 same.png 的那一次。"""
    source = PLAIN.replace(
        "fig.savefig('same.png', dpi=100)",
        "fig.savefig('same.png', dpi=100)\nax.set_title('changed after png')\n"
        "fig.savefig('same.pdf')",
    )
    root = project(sb_env, source)
    ctx = context(root, SB)
    worker, probe = build(root, ctx)
    try:
        assert probe["occurrence"] == 1
        verdict = ac.admit_reconstruction(root / "same.png", Path(probe["initialized"]), ctx)
        # 最终态 != 保存 PNG 那一刻（标题变了）：绝不 exact
        assert verdict["status"] == ac.STATUS_NEEDS_CONFIRMATION
    finally:
        pool.discard(worker)


@needs_worker
@pytest.mark.parametrize(
    "hook",
    [
        "fig.canvas.mpl_connect('draw_event', lambda event: None)",
        "ax.lines[0].add_callback(lambda artist: None)",
        "ax.lines[0].stale_callback = lambda artist, stale: None",
        "ax.lines[0].draw = lambda renderer: None",
        "ax.legend().draw = lambda renderer: None",
        "ax.lines[0].set_agg_filter(lambda im, dpi: (im, 0, 0))",
        "import matplotlib.patheffects as pe; ax.lines[0].set_path_effects([pe.Stroke(linewidth=3)])",
        "ax.set_title('x', usetex=True)",
        "from matplotlib.transforms import Affine2D\n"
        "class CustomTransform(Affine2D): pass\n"
        "ax.collections[0].set_offset_transform(CustomTransform())",
        "from matplotlib.lines import Line2D\n"
        "class MyLine(Line2D):\n    def draw(self, renderer):\n        super().draw(renderer)\n"
        "ax.add_line(MyLine([0, 1], [0, 1]))",
        "from matplotlib.lines import Line2D\nclass PlainSubclass(Line2D): pass\n"
        "ax.add_line(PlainSubclass([0, 1], [0, 1]))",
        "import matplotlib.lines as ml; ml.Line2D.draw = lambda self, renderer: None",
        "ax.annotate('a', (1, 1), xycoords=lambda renderer: __import__('matplotlib').transforms.IdentityTransform())",
        "from matplotlib.colors import Normalize\nclass UserNorm(Normalize): pass\n"
        "ax.scatter([0, 1], [0, 1], c=[1, 2], norm=UserNorm())",
        "ax.xaxis.set_major_formatter(__import__('matplotlib').ticker.FuncFormatter(lambda x, p: str(x)))",
        "ax.set_xscale('function', functions=(lambda x: x, lambda x: x))",
        "fig.set_layout_engine('tight')",
        "import threading; t = threading.Timer(60, lambda: None); t.daemon = True; t.start()",
        "ax.inset_axes([0.5, 0.5, 0.3, 0.3])",
    ],
    ids=lambda h: h.splitlines()[0][:48],
)
def test_unsafe_states_stay_refused_under_the_editing_profile(sb_env, hook):
    root = project(sb_env, SCRIPT.replace("plt.close('all')", ""), extra="")
    # 原件由干净的脚本画出，重放时才带上不安全状态（与既有 artifact 用例同一做法）
    (root / "figure.py").write_text(
        SCRIPT.replace("EXTRA", hook).replace("plt.close('all')", ""), encoding="utf-8"
    )
    assert reason_of(root, context(root, SB)) in (
        "unsupported_render_state",
        "layout_history_required",
    )


@needs_worker
def test_native_backend_savefig_is_refused(sb_env):
    root = project(sb_env, SCRIPT.replace("plt.close('all')", ""))
    (root / "figure.py").write_text(
        SCRIPT.replace("EXTRA", "").replace(
            "fig.savefig('same.png', dpi=100)", "fig.savefig('same.png', dpi=100, backend='agg')"
        ),
        encoding="utf-8",
    )
    assert reason_of(root, context(root, SB)) == "unsupported_render_state"


# ----------------------------------------------------------------------------- 真 worker + 真 API


@needs_worker
def test_api_adoption_confirmation_binding_and_zero_edit_original(sb_env, opened):
    root = project(
        sb_env, original_prelude="import matplotlib; matplotlib.rcParams['lines.antialiased']=False"
    )
    original = (root / "same.png").read_bytes()
    client = opened(root)
    ident = identity(root)
    response = render(client, [])
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    backed = body["source_backed"]
    assert backed["status"] == "needs_confirmation" and backed["metrics"]["differing_pixels"] > 0
    sha = backed["reconstruction_sha256"]
    assert re.fullmatch(r"[0-9a-f]{64}", sha)
    assert body["artifact_source"]["render_policy"] == SB
    # 零编辑（含只有结构性 FRAME）不需要确认
    assert render(client, [FRAME], expected=ident).status_code == 200
    # 带真实编辑却没有确认摘要：在 worker 之前拒绝
    refused(render(client, [FRAME, COLOR], expected=ident), "confirmation_required")
    # 摘要对不上本次重画：不放行，且这是请求问题不是 worker 问题（worker 保持存活）
    wrong = render(client, [FRAME, COLOR], expected=ident, confirmed="0" * 64)
    assert refused(wrong, "reconstruction_changed")["retryable"] is False
    ok = render(client, [FRAME, COLOR], expected=ident, confirmed=sha)
    assert ok.status_code == 200, ok.get_json()
    assert ok.get_json()["source_backed"]["reconstruction_sha256"] == sha
    # 预览对同一份确认同口径
    preview = post(
        client,
        "preview_png",
        id="same.png",
        source_policy=SB,
        expected_source=ident,
        patches=[FRAME, COLOR],
        with_manifest=True,
        w=400,
        confirmed_reconstruction_sha256=sha,
    )
    assert preview.status_code == 200 and preview.get_json()["source_backed"]["status"] == (
        "needs_confirmation"
    )
    refused(
        post(
            client,
            "preview_png",
            id="same.png",
            source_policy=SB,
            expected_source=ident,
            patches=[FRAME, COLOR],
            with_manifest=True,
            w=400,
            confirmed_reconstruction_sha256="f" * 64,
        ),
        "reconstruction_changed",
    )
    # 原件始终零写入
    assert (root / "same.png").read_bytes() == original


@needs_worker
def test_api_source_replacement_is_source_changed(sb_env, opened):
    root = project(sb_env)
    client = opened(root)
    ident = identity(root)
    sha = render(client, []).get_json()["source_backed"]["reconstruction_sha256"]
    (root / "same.png").write_bytes((root / "same.png").read_bytes() + b"tail")
    body = refused(render(client, [FRAME, COLOR], expected=ident, confirmed=sha), "source_changed")
    assert body["retryable"] is False


@needs_worker
def test_server_side_writeback_gate_does_not_depend_on_the_request_policy(sb_env, opened):
    root = project(sb_env)
    original = (root / "same.png").read_bytes()
    client = opened(root)
    assert render(client, []).status_code == 200  # 采用成功才落记录
    store = json.loads((config.data_dir() / "source-backed" / "adoptions.json").read_text())
    assert store["entries"] == [
        {
            "project": config.normalize_path_identity(str(root.resolve())),
            "source_id": "same.png",
            "bytes_sha256": ac.create_context(root / "same.png", "same.png")["bytes_sha256"],
        }
    ]
    # 不带 source_policy 的请求也被拒
    for route, body in (
        ("update_source", {"id": "same.png", "patches": [FRAME, COLOR]}),
        ("history/restore", {"id": "same.png", "n": -1}),
    ):
        refused(post(client, route, **body), "writeback_not_supported")
    refused(
        post(client, "sync_overrides", from_id="same.png", to_id="same.png", patches=[]),
        "unsupported_render_state",
    )
    refused(
        post(client, "specfix", id="same.png", patches=[], scale=1, profile={}),
        "unsupported_render_state",
    )
    refused(post(client, "invalidate", id="same.png"), "unsupported_render_state")
    refused(
        post(client, "preview_png", id="same.png", patches=[], w=400), "unsupported_render_state"
    )
    for route in ("png", "history", "history/preview", "svg"):
        refused(client.get(f"/api/engine/{route}?id=same.png"), "unsupported_render_state")
    assert (root / "same.png").read_bytes() == original
    # 原件字节变了：记录不再命中，历史 GET 回到各入口自己的判断
    (root / "same.png").write_bytes(original + b"x")
    assert client.get("/api/engine/history?id=same.png").status_code == 200


@needs_worker
def test_flag_off_rejects_the_new_policy_and_keeps_legacy_bytes(sb_env, opened, monkeypatch):
    root = project(sb_env, PLAIN)
    config.save({**config._defaults(), "experiments": {ac.EXPERIMENT: False}})  # 应急关闭
    client = opened(root)
    for response in (
        render(client, []),
        post(
            client, "preview_png", id="same.png", source_policy=SB, patches=[], with_manifest=True
        ),
        post(client, "update_source", id="same.png", source_policy=SB, patches=[]),
        client.get(f"/api/engine/png?id=same.png&source_policy={SB}"),
        client.post(
            "/api/export",
            json={
                "scope": "original",
                "filename": "x",
                "formats": ["png"],
                "original": {"figure_id": "same.png", "source_policy": SB, "overrides": []},
            },
        ),
    ):
        refused(response, "policy_disabled")
    assert not (config.data_dir() / "source-backed").exists()
    # 字符串 "false" / 0 不算关闭：开关值不是布尔就当没写（默认开）
    for value in ("false", 0):
        config.save({**config._defaults(), "experiments": {ac.EXPERIMENT: value}})
        assert render(client, []).status_code != 409
    config.save({**config._defaults(), "experiments": {ac.EXPERIMENT: False}})
    # 旧行为：不带 policy 的 PNG 渲染照旧，并标 source_unverified；精确策略照旧
    legacy = post(client, "render", id="same.png", patches=[])
    assert legacy.status_code == 200 and legacy.get_json()["source_unverified"]["kind"] == "png"
    strict = post(client, "render", id="same.png", source_policy=STRICT, patches=[])
    assert strict.status_code == 200
    assert "source_backed" not in strict.get_json()


def test_experiments_endpoint_exposes_only_the_boolean_projection(sb_env):
    """前端据此决定要不要在严格策略被拒后再试来源驱动策略：默认开、只有布尔 false 关，现读配置，不带别的东西；
    不进公开的 /api/version（那里只说这一版会什么，tests/test_engine_capabilities.py 钉着键集）。"""
    m.app.config["TESTING"] = True
    client = m.app.test_client()
    assert client.get("/api/experiments").get_json() == {ac.EXPERIMENT: True}
    config.save({**config._defaults()})  # 没写：默认开
    assert client.get("/api/experiments").get_json() == {ac.EXPERIMENT: True}
    for value in ("false", 0):
        config.save({**config._defaults(), "experiments": {ac.EXPERIMENT: value}})
        assert client.get("/api/experiments").get_json() == {ac.EXPERIMENT: True}
    config.save({**config._defaults(), "experiments": {ac.EXPERIMENT: False}})
    assert client.get("/api/experiments").get_json() == {ac.EXPERIMENT: False}


@needs_worker
def test_zero_edit_export_copies_disk_bytes_and_edited_export_needs_confirmation(sb_env, opened):
    root = project(
        sb_env, original_prelude="import matplotlib; matplotlib.rcParams['lines.antialiased']=False"
    )
    original = (root / "same.png").read_bytes()
    client = opened(root)
    ident = identity(root)
    sha = render(client, []).get_json()["source_backed"]["reconstruction_sha256"]

    def export(overrides, name="copy", **extra):
        return client.post(
            "/api/export",
            json={
                "scope": "original",
                "filename": name,
                "formats": ["png"],
                "ppi": 100,
                "original": {
                    "figure_id": "same.png",
                    "source_policy": SB,
                    "expected_source": ident,
                    "overrides": overrides,
                    **extra,
                },
            },
        )

    built = pool._workers.copy()
    response = export([])
    assert response.status_code == 200, response.get_json()
    out = response.get_json()
    assert (Path(out["export_dir"]) / out["files"][0]["name"]).read_bytes() == original
    assert pool._workers.keys() == built.keys()
    # 有真实编辑：没有确认摘要 → 拒绝；带上 → 整图重渲染（不再是原像素）
    refused(export([FRAME, COLOR]), "confirmation_required")
    refused(
        export([FRAME, COLOR], name="wrong", confirmed_reconstruction_sha256="1" * 64),
        "reconstruction_changed",
    )
    edited = export([FRAME, COLOR], name="edited", confirmed_reconstruction_sha256=sha)
    assert edited.status_code == 200, edited.get_json()
    out = edited.get_json()
    assert (Path(out["export_dir"]) / out["files"][0]["name"]).read_bytes() != original
    assert (root / "same.png").read_bytes() == original


# ----------------------------------------------------------------------------- 假 worker：闸门


@pytest.fixture
def fake(api, sb_env, monkeypatch):  # noqa: F811
    monkeypatch.setattr(
        ac,
        "admit_reconstruction",
        lambda source, candidate, ctx: {
            "status": "exact",
            "pixels_equal": True,
            "reconstruction_sha256": "a" * 64,
            "metrics": {
                "differing_pixels": 0,
                "differing_ratio": 0.0,
                "total_pixels": 1,
                "bbox": None,
            },
        },
    )
    return api


def test_native_session_is_refused_before_anything_is_recorded(fake, monkeypatch):
    monkeypatch.setattr(m.engine_enginesession, "is_native", lambda worker: True)
    body = {"id": "plot.png", "source_policy": SB, "patches": []}
    refused(fake.client.post("/api/engine/render", json=body), "unsupported_render_state")
    assert not (config.data_dir() / "source-backed").exists()


def test_fake_worker_zero_edit_export_never_starts_a_worker(fake):
    from PIL import Image

    Image.new("RGBA", (8, 6), (255, 0, 0, 255)).save(fake.root / "plot.png")
    spec = {
        "scope": "original",
        "filename": "copy",
        "formats": ["png"],
        "original": {"figure_id": "plot.png", "source_policy": SB, "overrides": []},
    }
    response = fake.client.post("/api/export", json=spec)
    assert response.status_code == 200, response.get_json()
    assert not fake.workers and not fake.resolutions
    out = response.get_json()
    assert (Path(out["export_dir"]) / out["files"][0]["name"]).read_bytes() == (
        fake.root / "plot.png"
    ).read_bytes()


def test_gate_hits_only_the_adopted_bytes_and_project(fake, monkeypatch):
    request = {"id": "plot.png", "source_policy": SB, "patches": []}
    assert fake.client.post("/api/engine/render", json=request).status_code == 200
    refused(
        fake.client.post("/api/engine/update_source", json={"id": "plot.png", "patches": []}),
        "writeback_not_supported",
    )
    # 另一张没被采用的图不受影响（拒绝来自写回入口自己的判断，而不是采用记录）
    other = fake.client.get("/api/engine/history?id=plot.pdf")
    assert other.status_code != 409 or other.get_json()["params"].get("reason") != (
        "unsupported_render_state"
    )
    # 记录按项目隔离
    assert not ac.is_adopted(fake.root.parent, fake.root / "plot.png")
    assert ac.is_adopted(fake.root, fake.root / "plot.png")


def test_confirmation_requirement_is_fail_closed_for_foreign_contexts(fake):
    """不是 `_SourceContext` 的上下文（别处构造）一律当作带编辑且没确认。"""
    worker = fake.worker_type(None, 99)
    ctx = {**ac.create_context(fake.root / "plot.png", "plot.png"), "render_policy": SB}
    ctx = figcapture.selected_artifact_context(ctx)
    worker._artifact_admission = {"reconstruction_sha256": "b" * 64}
    with pytest.raises(ac.ArtifactContextError) as error:
        m._admit_script_backed(worker, ctx)
    assert error.value.reason == "confirmation_required"


# ----------------------------------------------------------------------------- 记录有界


def test_adoption_store_is_bounded_and_prunes_only_dead_entries(sb_env, monkeypatch):
    root = sb_env
    root.mkdir(parents=True)
    monkeypatch.setattr(ac, "ADOPTION_MAX_ENTRIES", 2)
    contexts = []
    for i in range(3):
        (root / f"f{i}.png").write_bytes(b"png%d" % i)
        contexts.append(ac.create_context(root / f"f{i}.png", f"f{i}.png"))
    ac.record_adoption(root, contexts[0])
    ac.record_adoption(root, contexts[0])  # 幂等
    ac.record_adoption(root, contexts[1])
    with pytest.raises(ac.ArtifactContextError) as error:  # 满了且都还有效：拒绝，绝不挤掉旧的
        ac.record_adoption(root, contexts[2])
    assert error.value.reason == "adoption_store_full"
    assert ac.is_adopted(root, root / "f0.png") and ac.is_adopted(root, root / "f1.png")
    (root / "f0.png").write_bytes(b"changed")  # 字节变了的记录已不会命中：可以被清掉
    assert not ac.is_adopted(root, root / "f0.png")
    ac.record_adoption(root, contexts[2])
    assert ac.is_adopted(root, root / "f2.png") and ac.is_adopted(root, root / "f1.png")


def test_adoption_writes_are_serialized(sb_env):
    root = sb_env
    root.mkdir(parents=True)
    contexts = []
    for i in range(12):
        (root / f"g{i}.png").write_bytes(b"g%d" % i)
        contexts.append(ac.create_context(root / f"g{i}.png", f"g{i}.png"))
    threads = [threading.Thread(target=ac.record_adoption, args=(root, c)) for c in contexts]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert all(ac.is_adopted(root, root / f"g{i}.png") for i in range(12))


@needs_worker
def test_admission_rechecks_the_disk_identity_before_during_and_after(sb_env, monkeypatch):
    """身份复核两层都要有：请求入口之后原件被换（assert_current），以及验证快照被换（快照重算 hash）。"""
    root = project(sb_env, PLAIN)
    ctx = context(root, SB)
    worker, probe = build(root, ctx)
    candidate = Path(probe["initialized"])
    try:
        # 1) 上下文建好之后磁盘字节变了：准入当场 source_changed，不产出任何结论
        original = (root / "same.png").read_bytes()
        (root / "same.png").write_bytes(original + b"x")
        with pytest.raises(ac.ArtifactContextError) as error:
            ac.admit_reconstruction(root / "same.png", candidate, ctx)
        assert error.value.reason == "source_changed" and error.value.retryable
        (root / "same.png").write_bytes(original)
        # 2) 磁盘上的字节没变，但被快照走的那份被换成了另一张同图幅的图：快照 hash 对不上
        from PIL import Image

        swapped = root.parent / "swapped.png"
        Image.open(root / "same.png").convert("RGBA").point(lambda v: 255 - v).save(
            swapped, dpi=(100, 100)
        )
        snapshot = ac._snapshot

        def racy(path, out):
            data = snapshot(path, out)
            if Path(path) == root / "same.png":
                out.write_bytes(swapped.read_bytes())
            return data

        monkeypatch.setattr(ac, "_snapshot", racy)
        with pytest.raises(ac.ArtifactContextError) as error:
            ac.admit_reconstruction(root / "same.png", candidate, ctx)
        assert error.value.reason == "source_changed"
        monkeypatch.setattr(ac, "_snapshot", snapshot)
        # 3) 比较进行中原件被换：快照没问题，只有收尾的复核能发现
        extent = ac.pdfbackend.compare_png_extent

        def during(*args):
            (root / "same.png").write_bytes(original + b"late")
            return extent(*args)

        monkeypatch.setattr(ac.pdfbackend, "compare_png_extent", during)
        with pytest.raises(ac.ArtifactContextError) as error:
            ac.admit_reconstruction(root / "same.png", candidate, ctx)
        assert error.value.reason == "source_changed"
    finally:
        pool.discard(worker)


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        json.dumps({"version": 2, "entries": []}),
        json.dumps({"version": 1, "entries": 5}),
    ],
    ids=["malformed", "unknown-version", "entries-not-list"],
)
def test_unknown_adoption_store_fails_closed_and_is_never_overwritten(sb_env, content):
    root = sb_env
    root.mkdir(parents=True)
    (root / "a.png").write_bytes(b"png")
    (root / "a.pdf").write_bytes(b"pdf")
    store = ac._adoption_path()
    store.parent.mkdir(parents=True)
    store.write_text(content, encoding="utf-8")
    assert ac.is_adopted(root, root / "a.png")  # PNG：状态未知按已采用，写回被拒
    assert not ac.is_adopted(root, root / "a.pdf")  # 非 PNG 照常放行
    with pytest.raises(ac.ArtifactContextError) as error:
        ac.record_adoption(root, ac.create_context(root / "a.png", "a.png"))
    assert error.value.reason == "source_unreadable"
    assert store.read_text(encoding="utf-8") == content
    store.unlink()  # 文件不存在 = 没有采用
    assert not ac.is_adopted(root, root / "a.png")


# ----------------------------------------------------------------------------- Codex #1205 P1


def _canvas_export(objects):
    return {
        "scope": "canvas",
        "filename": "canvas",
        "formats": ["png"],
        "canvas": {
            "page_w_mm": 40,
            "page_h_mm": 20,
            "objects": [
                {
                    "type": "panel",
                    "id": "plot.png",
                    "x_mm": 0,
                    "y_mm": 0,
                    "w_mm": 10,
                    "h_mm": 10,
                    "source_policy": SB,
                    **o,
                }
                for o in objects
            ],
        },
    }


@pytest.mark.parametrize("edited_first", [True, False], ids=["edited-first", "zero-edit-first"])
def test_canvas_instances_keep_their_own_confirmation(fake, edited_first):
    """同一源在画布上出现多次：带编辑实例的过期摘要不能被零编辑实例盖掉（两种顺序都要拒）。"""
    expected = basis_of(fake)
    edited = {
        "expected_source": expected,
        "overrides": [FRAME, COLOR],
        "confirmed_reconstruction_sha256": "9" * 64,
    }  # fixture 的重画摘要是 "a"*64
    zero = {"expected_source": expected, "overrides": []}
    objects = [edited, zero] if edited_first else [zero, edited]
    refused(fake.client.post("/api/export", json=_canvas_export(objects)), "reconstruction_changed")


def test_canvas_instances_accumulate_edit_confirmations_per_object(fake):
    expected = basis_of(fake)
    edited = {"expected_source": expected, "overrides": [FRAME, COLOR]}
    spec = _canvas_export(
        [
            {**edited, "confirmed_reconstruction_sha256": "a" * 64},
            {"expected_source": expected, "overrides": []},
            {**edited, "confirmed_reconstruction_sha256": "b" * 64},
        ]
    )
    with m.app.test_request_context():
        contexts = m._export_artifact_sources(m.engine_exportreq.normalize(spec))
    assert contexts["plot.png"].edit_confirmations == ("a" * 64, "b" * 64)


def basis_of(fake, name="plot.png"):
    c = ac.create_context(fake.root / name, name)
    return {k: c[k] for k in ("bytes_sha256", "size_bytes")}


class _SpyGate:
    """包住真实的 ADOPTION_GATE：指定线程进门前先发信号（确定性同步点，不靠 sleep）。"""

    def __init__(self, real, waiting, who):
        self.real, self.waiting, self.who = real, waiting, who

    def __enter__(self):
        if threading.current_thread().name == self.who:
            self.waiting.set()
        self.real.acquire()

    def __exit__(self, *exc):
        self.real.release()


def test_admission_cannot_interleave_with_a_writeback_past_the_gate(fake, monkeypatch):
    entered, release, waiting = threading.Event(), threading.Event(), threading.Event()
    original = (fake.root / "plot.png").read_bytes()
    monkeypatch.setattr(ac, "ADOPTION_GATE", _SpyGate(ac.ADOPTION_GATE, waiting, "admission"))

    def slow_write(src, patches, worker, **kw):
        entered.set()  # 已过闸、未提交
        assert release.wait(30)
        src.write_bytes(b"rewritten by writeback")
        return {"file_identity": {}}

    monkeypatch.setattr(m, "_write_source_files", slow_write)
    monkeypatch.setattr(m, "append_baked", lambda *a, **k: None)
    monkeypatch.setattr(m, "_write_back_response", lambda *a, **k: {"ok": True})
    results = {}

    def writeback():
        results["wb"] = fake.client.post(
            "/api/engine/update_source", json={"id": "plot.png", "patches": []}
        )

    def admission():
        results["adm"] = fake.client.post(
            "/api/engine/render", json={"id": "plot.png", "source_policy": SB, "patches": []}
        )

    a = threading.Thread(target=writeback, name="writeback")
    a.start()
    assert entered.wait(30)
    b = threading.Thread(target=admission, name="admission")
    b.start()
    assert waiting.wait(30)  # 准入已到门口；写回仍持锁
    assert not _gate_free(ac.ADOPTION_GATE.real)
    release.set()
    a.join(30)
    b.join(30)
    assert results["wb"].status_code == 200
    # 准入要么被拒（原件已变，在新字节上重核身份），要么先于写回——绝不是「已记录 + 原件被覆盖」
    assert results["adm"].status_code == 409
    assert results["adm"].get_json()["params"]["reason"] == "source_changed"
    assert (fake.root / "plot.png").read_bytes() != original
    assert not ac.is_adopted(fake.root, fake.root / "plot.png")
    assert not (config.data_dir() / "source-backed" / "adoptions.json").exists()


def _gate_free(lock) -> bool:
    got = []
    t = threading.Thread(target=lambda: got.append(lock.acquire(False)))
    t.start()
    t.join()
    if got[0]:
        lock.release()
    return got[0]


def test_writeback_after_admission_is_refused_at_the_gate(fake, monkeypatch):
    request = {"id": "plot.png", "source_policy": SB, "patches": []}
    assert fake.client.post("/api/engine/render", json=request).status_code == 200
    called = []
    monkeypatch.setattr(m, "_write_source_files", lambda *a, **k: called.append(1))
    refused(
        fake.client.post("/api/engine/history/restore", json={"id": "plot.png", "n": -1}),
        "writeback_not_supported",
    )
    assert not called


def test_project_identity_is_the_project_systems_normalized_one(sb_env):
    root = sb_env
    root.mkdir(parents=True)
    (root / "a.png").write_bytes(b"png")
    assert ac._project_key(root) == config.normalize_path_identity(str(root.resolve()))
    ac.record_adoption(root, ac.create_context(root / "a.png", "a.png"))
    swapped = Path(str(root).swapcase())
    if not config.path_is_case_insensitive(root) or not swapped.exists():
        pytest.skip(
            "需要大小写不敏感的卷（macOS 默认 APFS / Windows）才能用另一种大小写打开同一目录"
        )
    assert ac.is_adopted(swapped, swapped / "a.png")
