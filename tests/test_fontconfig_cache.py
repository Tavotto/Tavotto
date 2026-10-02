"""Linux owned-child fontconfig isolation; cold control proves the write sensor works."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

from tavotto.engine import runtime


@pytest.fixture
def owned(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    for key in ("FONTCONFIG_FILE", "FONTCONFIG_PATH", "FONTCONFIG_SYSROOT", "XDG_CACHE_HOME"):
        monkeypatch.delenv(key, raising=False)
    return str(tmp_path / "data" / "environments" / "p" / "envs" / "g1" / "bin" / "python")


@pytest.mark.parametrize("platform", ["win32", "darwin", "freebsd14"])
@pytest.mark.parametrize("bundled", [False, True])
def test_other_platforms_leave_fontconfig_and_xdg_untouched(platform, bundled, owned, monkeypatch):
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setenv("FONTCONFIG_FILE", "/user/fonts.conf")
    monkeypatch.setenv("XDG_CACHE_HOME", "/user/cache")
    before = dict(os.environ)
    env = runtime.child_env() if bundled else runtime.owned_env(owned)
    assert env["FONTCONFIG_FILE"] == "/user/fonts.conf"
    assert env["XDG_CACHE_HOME"] == "/user/cache"
    assert dict(os.environ) == before
    assert "FONTCONFIG_FILE" not in (
        runtime.child_env(base={}) if bundled else runtime.owned_env(owned, base={})
    )


def test_user_python_does_not_get_fontconfig_overrides(owned, tmp_path):
    assert runtime.probe_env(str(tmp_path / "user-venv" / "bin" / "python")) is None
    assert not (tmp_path / "data" / "cache" / "fontconfig").exists()


@pytest.mark.parametrize("bundled", [False, True])
def test_linux_wrapper_keeps_original_config_and_xdg_and_is_reused(
    bundled, owned, tmp_path, monkeypatch
):
    monkeypatch.setattr(sys, "platform", "linux")
    original = str(tmp_path / "user & fonts.conf")
    monkeypatch.setenv("FONTCONFIG_FILE", original)
    monkeypatch.setenv("FONTCONFIG_PATH", str(tmp_path / "custom-fonts"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "user-cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "user-config"))
    before = dict(os.environ)
    call = runtime.child_env if bundled else lambda **kw: runtime.owned_env(owned, **kw)
    env = call()
    wrapper = Path(env["FONTCONFIG_FILE"])
    assert wrapper.parent == tmp_path / "data" / "cache" / "fontconfig"
    assert f"<include>{escape(original)}</include>" in wrapper.read_text()
    assert wrapper.read_text().index("<cachedir>") < wrapper.read_text().index("<include>")
    for key in ("FONTCONFIG_PATH", "XDG_CACHE_HOME", "XDG_CONFIG_HOME"):
        assert env[key] == before[key]
    assert dict(os.environ) == before
    stamp = wrapper.stat().st_mtime_ns
    assert call(base={})["FONTCONFIG_FILE"] == str(wrapper)
    assert call(base=env)["FONTCONFIG_FILE"] == str(wrapper)
    assert wrapper.stat().st_mtime_ns == stamp
    # Separate immutable wrappers: a concurrent spawn using another config cannot retarget this one.
    changed = call(base={"FONTCONFIG_FILE": str(tmp_path / "other.conf")})
    assert changed["FONTCONFIG_FILE"] != str(wrapper)
    assert f"<include>{escape(original)}</include>" in wrapper.read_text()


def test_custom_sysroot_and_unwritable_cache_preserve_original_environment(
    owned, tmp_path, monkeypatch
):
    monkeypatch.setattr(sys, "platform", "linux")
    original = {"FONTCONFIG_FILE": "/custom/fonts.conf", "FONTCONFIG_SYSROOT": "/sysroot"}
    env = dict(original)
    runtime._owned_fontconfig_env(str(tmp_path / "cache"), env)
    assert env == original
    assert not (tmp_path / "cache").exists()
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory")
    env = {"FONTCONFIG_FILE": "/custom/fonts.conf"}
    runtime._owned_fontconfig_env(str(blocked), env)
    assert env == {"FONTCONFIG_FILE": "/custom/fonts.conf"}


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux fontconfig subprocess")
@pytest.mark.parametrize("bundled", [False, True])
@pytest.mark.parametrize("explicit_config", [False, True])
@pytest.mark.parametrize("relative_data", [False, True])
def test_real_cold_fontconfig_caches_inside_owned_data_and_preserves_user_fonts(
    relative_data, explicit_config, bundled, owned, tmp_path, monkeypatch
):
    """A private fontconfig config forces a cold cache even on warm CI images.

    The same real fc-list first writes to a separate XDG control, proving the sensor;
    with the product's child env it must list identical fonts and leave HOME/XDG alone.
    User config includes a font directory and alias, both independently checked.
    """
    if relative_data:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("TAVOTTO_DATA_DIR", "data")
    fc_list, fc_match = shutil.which("fc-list"), shutil.which("fc-match")
    fc_pattern = shutil.which("fc-pattern")
    if not fc_list or not fc_match or not fc_pattern:
        pytest.skip("fontconfig tools are not installed")
    # Discover without importing matplotlib in the parent (the Flask dependency boundary).
    from tavotto.engine import pool

    try:
        py = pool.find_worker_python()
    except pool.WorkerError:
        pytest.skip("no scientific interpreter for the real font fixture")
    font_dir = Path(
        subprocess.check_output(
            [
                py,
                "-B",
                "-c",
                "import importlib.util,pathlib; print(pathlib.Path(importlib.util.find_spec('matplotlib').origin).parent / 'mpl-data/fonts/ttf')",
            ],
            text=True,
            timeout=30,
        ).strip()
    )
    fonts = tmp_path / "user fonts & aliases"
    fonts.mkdir()
    shutil.copyfile(font_dir / "DejaVuSans.ttf", fonts / "test.ttf")
    config = tmp_path / "fontconfig-source"
    config.mkdir()
    blocked = tmp_path / "unwritable-system-cache"
    blocked.write_text("not a directory")
    (config / "fonts.conf").write_text(
        "<fontconfig>"
        f"<cachedir>{escape(str(blocked / 'fontconfig'))}</cachedir>"
        '<cachedir prefix="xdg">fontconfig</cachedir>'
        '<include prefix="xdg">fontconfig/fonts.conf</include>'
        "</fontconfig>"
    )
    user_config = tmp_path / "home" / "xdg-config" / "fontconfig"
    user_config.mkdir(parents=True)
    (user_config / "fonts.conf").write_text(
        f"<fontconfig><dir>{escape(str(fonts))}</dir>"
        "<alias><family>TavottoCacheSensor</family><prefer>"
        "<family>DejaVu Sans</family></prefer></alias></fontconfig>"
    )
    monkeypatch.setenv("FONTCONFIG_PATH", str(config))
    if explicit_config:
        monkeypatch.setenv("FONTCONFIG_FILE", str(config / "fonts.conf"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(user_config.parent))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "home" / "xdg-cache"))

    def run(executable, args, env):
        return subprocess.run(
            [executable, *args], env=env, capture_output=True, text=True, check=True, timeout=30
        ).stdout

    control = dict(os.environ, XDG_CACHE_HOME=str(tmp_path / "control-cache"))
    expected = run(fc_list, ["--format=%{file}\\n"], control)
    assert expected.strip() == str(fonts / "test.ttf")
    assert list((tmp_path / "control-cache" / "fontconfig").glob("*cache-*")), (
        "cold sensor inactive"
    )
    before = sorted(str(p) for p in (tmp_path / "home").rglob("*"))
    env = runtime.child_env() if bundled else runtime.probe_env(owned)
    assert run(fc_list, ["--format=%{file}\\n"], env) == expected
    assert run(fc_match, ["--format=%{family}", "TavottoCacheSensor"], env) == "DejaVu Sans"
    alias = run(fc_pattern, ["-c", "--format=%{family}", "TavottoCacheSensor"], env)
    assert "DejaVu Sans" in alias.split(","), "the user's alias was not loaded"
    assert list((tmp_path / "data" / "cache" / "fontconfig").glob("*cache-*"))
    assert sorted(str(p) for p in (tmp_path / "home").rglob("*")) == before
    # Repeat with a warm owned cache; no new external files and unchanged discovery.
    assert run(fc_list, ["--format=%{file}\\n"], env) == expected
    assert sorted(str(p) for p in (tmp_path / "home").rglob("*")) == before

    # Keep reading the user's config live rather than snapshotting their font choices.
    replacement = tmp_path / "replacement-fonts"
    replacement.mkdir()
    shutil.copyfile(font_dir / "DejaVuSansMono.ttf", replacement / "replacement.ttf")
    (user_config / "fonts.conf").write_text(
        f"<fontconfig><dir>{escape(str(replacement))}</dir>"
        "<alias><family>TavottoCacheSensor</family><prefer>"
        "<family>DejaVu Sans Mono</family></prefer></alias></fontconfig>"
    )
    assert run(fc_list, ["--format=%{file}\\n"], env).strip() == str(
        replacement / "replacement.ttf"
    )
    assert run(fc_match, ["--format=%{family}", "TavottoCacheSensor"], env) == "DejaVu Sans Mono"
    alias = run(fc_pattern, ["-c", "--format=%{family}", "TavottoCacheSensor"], env)
    assert "DejaVu Sans Mono" in alias.split(",")
    assert "DejaVu Sans" not in alias.split(",")
    assert sorted(str(p) for p in (tmp_path / "home").rglob("*")) == before
