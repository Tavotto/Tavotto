"""图片内容与扩展名不符（`image_format_mismatch`，TVD-3MWS）。

现场：用户把一张 JPEG 照片存成了 `xxx.png`，脚本 `plt.imread("xxx.png")` 报 `SyntaxError: not a PNG file`
（matplotlib 对 .png 扩展名直接用 PNG 解码器，不嗅探内容；直接用 Python 跑也一样失败）。Tavotto 只给了笼统的
「脚本执行失败」，AI 修复也拿不到这条事实。本文件钉：

* 识别（`bridgeboot.image_format_facts`）：异常链里有 PIL 解码失败 + 被读的文件在项目内 + 前 16 字节嗅探出的格式与
  扩展名不符才产出事实；一致 / 嗅探不出 / 项目外 / 读不了 / 不是 PIL 失败一律 `{}`；敏感运行里与 token 有关的文件名不带；
* 失败码转换（`pool.image_format_error` / `recovery_params`）与登记；
* 端到端：真 safe worker 往返（真 PNG 照常成功）、真 native bridge。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from support import bridgekit
from tavotto.engine import bridgeboot, pool as engine_pool, workdir

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402
from PIL.PngImagePlugin import PngImageFile  # noqa: E402


def _save_as(path: Path, fmt: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (4, 4), (200, 30, 30)).save(path, format=fmt)
    return path


def _png_error(path: Path) -> BaseException:
    """真 PIL 对一个不是 PNG 的 .png 抛出的异常（matplotlib 的 imread 走的就是这条）。"""
    try:
        PngImageFile(str(path))
    except SyntaxError as exc:
        return exc
    raise AssertionError("PIL 居然把它当 PNG 读了")


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "proj"
    r.mkdir()
    return r


def test_jpeg_saved_as_png_is_named_with_both_formats(root):
    bad = _save_as(root / "photos" / "cell.png", "JPEG")
    fact = bridgeboot.image_format_facts(_png_error(bad), [str(root)])
    assert fact == {"file": "photos/cell.png", "claimed": "PNG", "actual": "JPEG", "ext": "png"}


def test_a_real_png_is_not_blamed_even_when_the_failure_has_another_cause(root):
    good = _save_as(root / "ok.png", "PNG")
    assert bridgeboot.image_format_facts(SyntaxError("not a PNG file"), [str(root)]) == {}
    try:
        try:
            PngImageFile(str(good)).load()
            raise ValueError("later failure unrelated to decoding")
        except ValueError as exc:
            raise RuntimeError("wrapped") from exc
    except RuntimeError as exc:
        assert bridgeboot.image_format_facts(exc, [str(root)]) == {}


def test_a_mismatched_file_outside_the_project_is_never_reported(tmp_path, root):
    outside = _save_as(tmp_path / "elsewhere" / "x.png", "JPEG")
    assert bridgeboot.image_format_facts(_png_error(outside), [str(root)]) == {}


def test_relative_path_read_from_a_sandbox_cwd_resolves_against_the_roots(
    tmp_path, root, monkeypatch
):
    _save_as(root / "cell.png", "JPEG")
    monkeypatch.chdir(root)
    exc = _png_error(
        root / "cell.png"
    )  # 先在项目里读失败，帧里的 fp 是绝对路径；再造相对路径的一次
    try:
        PngImageFile("cell.png")
    except SyntaxError as rel_exc:
        exc = rel_exc
    other = tmp_path / "sandbox"
    other.mkdir()
    monkeypatch.chdir(other)  # 异常处理时 cwd 已不是读文件时的那个
    fact = bridgeboot.image_format_facts(exc, [str(root)])
    assert (fact["file"], fact["claimed"], fact["actual"]) == ("cell.png", "PNG", "JPEG")


def test_script_dir_counts_as_inside(tmp_path, root):
    scripts = tmp_path / "scripts"
    bad = _save_as(scripts / "x.png", "GIF")
    fact = bridgeboot.image_format_facts(_png_error(bad), [str(root), str(scripts)])
    assert (fact["claimed"], fact["actual"], fact["file"]) == ("PNG", "GIF", "x.png")


def test_unsniffable_unreadable_or_unknown_extension_is_nothing(root):
    junk = root / "junk.png"
    junk.write_bytes(b"this is plain text, not an image at all")
    assert bridgeboot.image_format_facts(_png_error(junk), [str(root)]) == {}
    empty = root / "empty.png"
    empty.write_bytes(b"")
    assert bridgeboot.image_format_facts(_png_error(empty), [str(root)]) == {}
    # 文件在失败后被删了：读不了
    gone = _save_as(root / "gone.png", "JPEG")
    exc = _png_error(gone)
    gone.unlink()
    assert bridgeboot.image_format_facts(exc, [str(root)]) == {}
    # 扩展名不属于任何认得的图片格式
    odd = _save_as(root / "x.dat", "JPEG")
    assert bridgeboot.image_format_facts(_png_error(odd), [str(root)]) == {}


def test_matching_content_and_extension_are_equivalent_spellings(root):
    jpg = _save_as(root / "a.jpg", "JPEG")
    jpeg = _save_as(root / "b.jpeg", "JPEG")
    for p in (jpg, jpeg):
        assert bridgeboot.image_mismatch_of_file(str(p)) is None
        assert bridgeboot.sniff_image_format(str(p)) == "JPEG"
    assert bridgeboot.image_format_of_extension(".JPG") == "JPEG"
    assert bridgeboot.image_format_of_extension("tif") == "TIFF"


@pytest.mark.parametrize(
    "fmt, ext, claimed, actual",
    [
        ("PNG", "jpg", "JPEG", "PNG"),
        ("GIF", "tiff", "TIFF", "GIF"),
        ("BMP", "webp", "WebP", "BMP"),
        ("JPEG", "bmp", "BMP", "JPEG"),
        ("TIFF", "png", "PNG", "TIFF"),
        ("WEBP", "gif", "GIF", "WebP"),
    ],
)
def test_other_mismatch_directions(root, fmt, ext, claimed, actual):
    bad = _save_as(root / f"f.{ext}", fmt)
    assert bridgeboot.image_mismatch_of_file(str(bad)) == (claimed, actual, ext)


def test_sensitive_runs_drop_a_file_name_related_to_any_token(root):
    bad = _save_as(root / "secret_sample.png", "JPEG")
    exc = _png_error(bad)
    plain = bridgeboot.image_format_facts(exc, [str(root)], ["unrelated"])
    assert plain["file"] == "secret_sample.png"
    for token in ("secret_sample", "SECRET", str(bad), "data/secret_sample.png"):
        fact = bridgeboot.image_format_facts(exc, [str(root)], [token])
        assert "file" not in fact and fact["claimed"] == "PNG" and fact["actual"] == "JPEG", token


def test_not_a_pil_failure_is_nothing(root):
    bad = _save_as(root / "cell.png", "JPEG")
    try:
        raise SyntaxError("not a PNG file")  # 帧不在 PIL 里
    except SyntaxError as exc:
        assert bridgeboot.image_format_facts(exc, [str(root)]) == {}
    assert bridgeboot.image_format_facts(ValueError("x"), [str(root)]) == {}
    assert bad.exists()


# --------------------------------------------------------------------------- 失败码转换
def _extra(**over):
    fact = {"file": "photos/cell.png", "claimed": "PNG", "actual": "JPEG", "ext": "png"}
    fact.update(over)
    return {"image_format_mismatch": fact}


def test_failure_becomes_the_closed_code():
    err = engine_pool.image_format_error("boom", "tb", _extra())
    assert err.code == engine_pool.IMAGE_FORMAT_MISMATCH_CODE == "image_format_mismatch"
    assert err.traceback_text == "tb" and str(err) == "boom"
    params = engine_pool.recovery_params(err)
    assert params == {"file": "photos/cell.png", "claimed": "PNG", "actual": "JPEG", "ext": "png"}
    assert engine_pool.IMAGE_FORMAT_MISMATCH_CODE in engine_pool.ERROR_CODES


def test_without_a_file_name_the_params_have_no_file():
    err = engine_pool.image_format_error("m", "tb", _extra(file=None))
    assert "file" not in engine_pool.recovery_params(err)


def test_malformed_facts_are_distrusted():
    for bad in (
        {"claimed": "TARGA"},
        {"actual": "PNG"},  # 与 claimed 相同不是错配
        {"actual": None},
        {"ext": "../x"},
        {"file": "a\\b.png"},
        {"file": "x" * 300},
    ):
        assert engine_pool.image_format_error("m", "tb", _extra(**bad)) is None, bad
    assert engine_pool.image_format_error("m", "tb", None) is None
    assert engine_pool.image_format_error("m", "tb", {"image_format_mismatch": "x"}) is None


def test_classification_comes_before_the_generic_script_error():
    err = engine_pool._worker_error("SyntaxError: not a PNG file", "script_error", "tb", _extra())
    assert err.code == engine_pool.IMAGE_FORMAT_MISMATCH_CODE


# --------------------------------------------------------------------------- 端到端：真 worker / 真 bridge
needs_worker = pytest.mark.skipif(
    bridgekit.USER_PYTHON is None, reason="找不到装有 matplotlib 的解释器"
)

SCRIPT = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
fig, ax = plt.subplots()
img = plt.imread({path!r})
ax.imshow(img)
{show}
"""


@pytest.fixture
def safe_project(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    yield root
    engine_pool.shutdown_all(str(root), wait=True)
    workdir.set_mode(root, workdir.MODE_SANDBOX)


def _user_has_pil() -> bool:
    import subprocess

    return (
        subprocess.run([bridgekit.USER_PYTHON, "-c", "import PIL"], capture_output=True).returncode
        == 0
    )


@needs_worker
def test_safe_worker_names_the_real_format_and_a_real_png_still_works(safe_project):
    if not _user_has_pil():
        pytest.skip("用户解释器没有 PIL")
    proj = safe_project
    _save_as(proj / "photo.png", "JPEG")
    (proj / "fig.py").write_text(SCRIPT.format(path=str(proj / "photo.png"), show=""))
    workdir.set_mode(proj, workdir.MODE_SANDBOX)
    with pytest.raises(engine_pool.WorkerError) as err:
        engine_pool.build("fig.py", str(proj), "__main__")
    assert err.value.code == engine_pool.IMAGE_FORMAT_MISMATCH_CODE, err.value
    params = engine_pool.recovery_params(err.value)
    assert params == {"file": "photo.png", "claimed": "PNG", "actual": "JPEG", "ext": "png"}
    assert str(proj) not in json.dumps(params)
    # 相对路径（沙盒 cwd 里读）同样认得
    (proj / "fig.py").write_text(SCRIPT.format(path="photo.png", show=""))
    engine_pool.shutdown_all(str(proj), wait=True)
    with pytest.raises(engine_pool.WorkerError) as rel:
        engine_pool.build("fig.py", str(proj), "__main__")
    assert rel.value.code == engine_pool.IMAGE_FORMAT_MISMATCH_CODE, rel.value
    # 同一脚本换成真 PNG：照常成功
    (proj / "fig.py").write_text(SCRIPT.format(path=str(proj / "photo.png"), show=""))
    engine_pool.shutdown_all(str(proj), wait=True)
    _save_as(proj / "photo.png", "PNG")
    worker, resp = engine_pool.build("fig.py", str(proj), "__main__")
    assert resp is not None


@needs_worker
def test_native_bridge_carries_the_fact(tmp_path):
    if not _user_has_pil():
        pytest.skip("用户解释器没有 PIL")
    from tavotto.engine import bridge

    proj = tmp_path / "proj"
    proj.mkdir()
    _save_as(proj / "photo.png", "JPEG")
    script = bridgekit.write(
        proj / "fig.py", SCRIPT.format(path=str(proj / "photo.png"), show="plt.show()")
    )
    spec = bridge.spec_for(
        str(script), interpreter=bridgekit.USER_PYTHON, cwd=str(proj), project_root=str(proj)
    )
    saved = os.environ.pop("PYTHONPATH", None)
    try:
        sess = bridge.BridgeSession(spec, out_dir=tmp_path / "out")
        sess.start()
        try:
            failed = sess.wait_event("barrier")["script_error"]
        finally:
            sess.close()
    finally:
        if saved is not None:
            os.environ["PYTHONPATH"] = saved
    assert failed["image_format_mismatch"] == {
        "file": "photo.png",
        "claimed": "PNG",
        "actual": "JPEG",
        "ext": "png",
    }
