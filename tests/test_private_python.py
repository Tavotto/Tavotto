"""统一实施包 U05（ADR 0063）：私有完整 Python 的供应器——锁文件、状态机、负例、去重、取消、退役、隔离。

全部经**本地供应服务 + 假归档**（`tests/support/private_python.py`）跑真实的下载 → 校验 → 解包 →
成员校验 → 真起一次 → 原子改名 → 记账；不 mock 产品代码里的任何一步，不联公网。判据的主语：

* 「有没有联网」= 本地服务的请求日志（`server.requests`）；离线 = 连一个没人听的回环端口 + 死代理；
* 「解释器有没有被执行」= 替身每次被起来追加一行的 `launches.log`（Windows 上量不了，见装置说明）；
* 「有没有半成品」= `runtimes/` 下的目录名集合（staging 从不叫最终名字）+ `downloads/*.part`。

场景对应：FO24（离线有缓存 → automatic，零请求）、FO25（离线无缓存 → safe_stop，`private_python_offline`）、
FO26（截断 / 篡改 / 错期望值 → safe_stop，执行计数 0、无目录）、FO-027（并发去重）、FO-028（有代记着的
runtime 不 GC）、FO-024（HOME / PATH 前后无差）。经 HTTP 入口的同名场景在 `test_foundation_first_open.py`
（PR B）。真 pbs 归档的工程验证在 `TestRealArchive`（要 `TAVOTTO_PRIVATE_PYTHON_REAL=1`，nightly 腿跑）。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from support import private_python as pp_support
from support.private_python import LoopbackServer, closed_port_url, fake_archive, source_from
from tavotto.engine import config, managedenv, privatepython, runtime

ROOT = Path(__file__).resolve().parent.parent
RUNTIME_LOCK = ROOT / "packaging" / "runtime-lock.json"
POSIX = os.name != "nt"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(data))
    monkeypatch.setenv("TAVOTTO_PRIVATE_PYTHON", "1")
    monkeypatch.delenv(runtime.PRIVATE_PYTHON_BUNDLE_ENV, raising=False)
    # 死代理：任何走代理的联网当场被拒。本地回环服务由 NO_PROXY 放行——与真实机器上的
    # 代理配置同一张脸（urllib 只认环境变量），不是给产品代码开的口子。
    # urllib 对 `*_proxy` 小写优先于大写：四个变量两种拼法都设，别让宿主 / runner 里的小写那份决定判据
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    privatepython.reset_for_tests()
    yield
    privatepython.reset_for_tests()


@pytest.fixture
def launches(tmp_path) -> Path:
    return tmp_path / "launches.log"


def _launch_count(log: Path) -> int | None:
    """替身被执行的次数；Windows 上量不了回 None（那里的用例只断言目录状态）。"""
    if not POSIX:
        return None
    try:
        return len(log.read_text("utf-8").splitlines())
    except OSError:
        return 0


def _runtime_dirs() -> set[str]:
    try:
        return {p.name for p in privatepython.runtimes_dir().iterdir()}
    except OSError:
        return set()


def _parts() -> list[str]:
    try:
        return [p.name for p in privatepython.downloads_dir().glob("*.part")]
    except OSError:
        return []


def _make(tmp_path, launches, **kw):
    """假归档 + 它的来源（url 由调用方填）。"""
    archive, sha, rel = fake_archive(
        tmp_path / "serve", host_python=sys.executable, launches_log=launches, **kw
    )
    return archive, sha, rel


def _source(server: LoopbackServer | None, archive: Path, sha: str, rel: str, **kw):
    url = server.url(archive.name) if server else closed_port_url(archive.name)
    return source_from(
        archive,
        sha,
        rel,
        url=url,
        version=kw.pop("version", pp_support.host_python_version()),
        **kw,
    )


# ================================================================ 锁文件
class TestLock:
    def test_the_shipped_lock_is_valid_and_pins_exact_values(self):
        lock = privatepython.load_lock()
        assert lock["schema"] == privatepython.LOCK_SCHEMA
        assert set(lock["targets"]) == {
            "macos-arm64",
            "macos-x86_64",
            "linux-x86_64",
            "linux-arm64",
            "windows-x86_64",
        }
        for name, t in lock["targets"].items():
            assert len(t["sha256"]) == 64 and t["url"].startswith("https://"), name
            # ADR 0064 §二 / 2026-10-01 修订：五个目标全开（macos-x86_64 是维护者拍板的例外）
            assert t["size"] > 0 and t["enabled"] is True, name
            # 来源钉的是 install_only 归档，文件名里带版本 + release + 三元组
            assert t["url"].endswith(f"-{t['triple']}-install_only.tar.gz"), name
            assert f"cpython-{lock['python']['version']}%2B{lock['python']['release']}-" in t["url"]

    def test_lock_ships_inside_the_package(self):
        """锁文件在 `tavotto/resources/` 下：随 wheel / 冻结产物一起走（`packaging/tavotto.spec` 的 datas）。"""
        path = privatepython.lock_path()
        assert path.is_file()
        assert path.parent.name == "resources" and path.parent.parent.name == "tavotto"

    def test_macos_entries_are_the_same_origin_as_the_runtime_lock(self):
        """同源对：两个 macOS 目标的 CPython 来源逐字段等于 `packaging/runtime-lock.json`——
        桌面版内置渲染 runtime 与私有 Python 是**同一份**字节，不是两处各钉一遍。"""
        ours = privatepython.load_lock()
        theirs = json.loads(RUNTIME_LOCK.read_text(encoding="utf-8"))
        for name in ("macos-arm64", "macos-x86_64"):
            a, b = ours["targets"][name], theirs["targets"][name]["python"]
            assert ours["python"]["version"] == b["version"], name
            assert ours["python"]["release"] == b["release"], name
            assert ours["python"]["archive_root"] == b["archive_root"], name
            assert a["triple"] == b["triple"] and a["url"] == b["url"], name
            assert a["sha256"] == b["sha256"] and a["size"] == b["size"], name

    @pytest.mark.parametrize(
        "host_os,arch,expected",
        [
            ("macos", "arm64", "macos-arm64"),
            ("macos", "x86_64", "macos-x86_64"),
            ("linux", "x86_64", "linux-x86_64"),
            ("linux", "arm64", "linux-arm64"),
            ("windows", "x86_64", "windows-x86_64"),
        ],
    )
    def test_host_target_names_match_lock_keys(self, monkeypatch, host_os, arch, expected):
        monkeypatch.setattr(runtime, "host_os", lambda: host_os)
        monkeypatch.setattr(runtime, "host_arch", lambda: arch)
        assert privatepython.host_target() == expected
        src = privatepython.source_for()
        assert src is not None and src.target == expected
        assert src.python_rel == ("python.exe" if host_os == "windows" else "bin/python3")

    def test_unknown_host_has_no_source_and_is_not_offered(self, monkeypatch):
        monkeypatch.setattr(runtime, "host_os", lambda: "other")
        assert privatepython.host_target() is None
        assert privatepython.source_for() is None
        assert privatepython.offered(None) is False
        assert privatepython.status()["offered"] is False
        with pytest.raises(privatepython.ProvisionError) as err:
            privatepython.provision()
        assert err.value.code == privatepython.ERROR_NOT_OFFERED

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda lock: lock.update(schema=2),
            lambda lock: lock["python"].update(version="3.13"),
            lambda lock: lock["python"].update(flavor="install_only_stripped"),
            lambda lock: lock["targets"]["macos-arm64"].update(sha256="abc"),
            lambda lock: lock["targets"]["macos-arm64"].update(url="http://example.com/x.tar.gz"),
            lambda lock: lock["targets"]["macos-arm64"].update(size=0),
            lambda lock: lock["targets"]["macos-arm64"].update(enabled="yes"),
            lambda lock: lock["targets"]["macos-arm64"].update(python_rel="../bin/python3"),
            lambda lock: lock["targets"]["macos-arm64"].update(arch="x86_64"),
            lambda lock: lock["targets"]["macos-arm64"].pop("triple"),
        ],
    )
    def test_validate_lock_rejects_loose_or_inconsistent_pins(self, mutate):
        lock = privatepython.load_lock()
        mutate(lock)
        with pytest.raises(ValueError):
            privatepython.validate_lock(lock)

    def test_offered_follows_the_lock_then_the_engineering_override(self, monkeypatch):
        # 锁里五个目标都开着：取一份真实条目、把 enabled 改成 false，量「没取得资格」那一支
        real = privatepython.source_for("linux-x86_64")
        assert real is not None
        src = privatepython.PythonSource(**{**real.__dict__, "enabled": False})
        monkeypatch.delenv("TAVOTTO_PRIVATE_PYTHON", raising=False)
        assert privatepython.offered(src) is False  # 资格未取得：产品默认不提供
        monkeypatch.setenv("TAVOTTO_PRIVATE_PYTHON", "1")
        assert privatepython.offered(src) is True
        monkeypatch.setenv("TAVOTTO_PRIVATE_PYTHON", "0")
        enabled = privatepython.PythonSource(**{**src.__dict__, "enabled": True})
        assert privatepython.offered(enabled) is False  # 0 压过锁文件的 true
        for name in (
            "macos-arm64",
            "macos-x86_64",
            "linux-x86_64",
            "linux-arm64",
            "windows-x86_64",
        ):
            monkeypatch.delenv("TAVOTTO_PRIVATE_PYTHON", raising=False)
            qualified = privatepython.source_for(name)
            assert qualified is not None and privatepython.offered(qualified) is True, name

    def test_error_codes_registry_is_closed(self):
        declared = {
            v
            for k, v in vars(privatepython).items()
            if k.startswith("ERROR_") and isinstance(v, str)
        }
        assert declared == set(privatepython.ERROR_CODES)
        assert len(set(privatepython.ERROR_CODES)) == len(privatepython.ERROR_CODES)

    @pytest.mark.parametrize("locale", ["zh-CN", "en-US"])
    def test_every_code_has_repair_text_in_both_languages(self, locale):
        """这批 code 经 `app._repair_error` 漏斗到界面，文案表是 `engine.repairError`（与 deprepair 的
        同一张）；`tests/test_error_codes.py` 那张表守的是 `backend.*`，看不见这里，所以在这里守。"""
        path = ROOT / "web" / "src" / "i18n" / "locales" / locale / "errors.json"
        if not path.is_file():
            pytest.skip("没有 web/（wheel / sdist 里不含前端源码）")
        table = json.loads(path.read_text(encoding="utf-8"))["engine"]["repairError"]
        missing = sorted(set(privatepython.ERROR_CODES) - set(table))
        assert missing == [], missing
        assert all(str(table[c]).strip() for c in privatepython.ERROR_CODES)
        assert all("{{" not in str(table[c]) for c in privatepython.ERROR_CODES)  # 不插值

    def test_payloads_carry_no_machine_paths(self, tmp_path, launches):
        archive, sha, rel = _make(tmp_path, launches)
        src = _source(None, archive, sha, rel)
        for payload in (
            src.to_payload(),
            privatepython.status(src),
            privatepython.offer_payload(src),
        ):
            text = json.dumps(payload)
            assert str(tmp_path) not in text and str(config.data_dir()) not in text
        assert privatepython.offer_payload(src)["download_bytes"] == archive.stat().st_size
        assert privatepython.status(src)["provisioned"] is False


# ================================================================ 测试支持模块本身
def test_the_support_module_imports_under_both_os_names_with_the_same_exports():
    """`support.private_python` 的 Windows 分支只在 Windows 上走到——本机看不见它的 NameError（#475 ccfad63e：
    Windows 两片收集期就挂）。所以在子进程里把 `os.name` 钉成 nt / posix 各 import 一次，导出集合必须相同，
    且 import 本身不起子进程（探测延迟到首次调用）。"""
    # 先把标准库与产品模块按真实 os.name 装进 sys.modules（shutil 之类在 nt 下会去 import nt），再翻 os.name——
    # 翻的只影响 support 模块自己的顶层分支，这正是要量的那一层
    code = (
        "import os, sys, json, functools, hashlib, http.server, io, platform, shutil, subprocess, "
        "tarfile, tempfile, threading, time, pathlib, importlib, pytest; "
        "sys.path.insert(0, sys.argv[2]); from tavotto.engine import privatepython, pool; "
        "os.name = sys.argv[1]; m = importlib.import_module('support.private_python'); "
        "print(json.dumps(sorted(n for n in dir(m) if not n.startswith('__'))))"
    )
    outs = {}
    for name in ("nt", "posix"):
        proc = subprocess.run(
            [sys.executable, "-c", code, name, str(ROOT / "tests")],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
            env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        )
        assert proc.returncode == 0, f"os.name={name}: {proc.stderr[-1500:]}"
        outs[name] = json.loads(proc.stdout.strip().splitlines()[-1])
    assert outs["nt"] == outs["posix"]
    assert {"needs_real_base", "standin_can_be_base", "standin_base_probe", "fake_archive"} <= set(
        outs["nt"]
    )


def test_the_windows_standin_sees_the_hosts_site_packages_like_the_posix_one(tmp_path):
    """两种替身对「宿主的包可见」要同一种语义：POSIX 的替身 exec 宿主解释器，宿主 site-packages 天然可见；Windows
    的替身是个 venv（venvlauncher 副本 + pyvenv.cfg），必须 `include-system-site-packages = true`，否则
    `offline_managed_env` 问它要 site-packages 挂进新代时拿到的是替身自己不存在的目录——新代 import 不到 matplotlib
    （#475 d0d331f7 Windows FO24 / FO26 的真根因）。这条在任何平台都量得到：① cfg 的字面量；② 在本机用一个真的
    `--system-site-packages` venv 验证「问那个解释器要 site-packages」的取法（`_site_packages_of`）确实把
    base 的 purelib 交出来——挂进新代的就是它。"""
    from support.dependency_repair import _site_packages_of

    cfg = pp_support._windows_pyvenv_cfg(Path("/host/bin")).decode("utf-8")
    assert "include-system-site-packages = true" in cfg and "home = " in cfg
    venv = tmp_path / "sys-site-venv"
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", "--system-site-packages", str(venv)],
        check=True,
        timeout=300,
    )
    vpy = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    sites = _site_packages_of(str(vpy))
    # 「系统 site」= 这个 venv 的 base_prefix 那一层（宿主自己是 venv 时是它的 base，不是它）。
    # **问 base 解释器自己要 `site.getsitepackages()`**，不能拿 sysconfig 的 purelib 猜：Debian / Ubuntu
    # 的系统 Python 把包放在 `dist-packages`，sysconfig 算出来的 `…/site-packages` 根本不存在
    # （lab 的 Ubuntu 24.04 系统 3.12 上这条就是这样红的，hosted CI 的 hostedtoolcache Python 看不见）。
    # 只留真实存在的目录——与 `_site_packages_of` 同一口径，量的才是同一个东西。
    base_sites = json.loads(
        subprocess.run(
            [
                str(vpy),
                "-c",
                "import json, os, site, subprocess, sys\n"
                "base = getattr(sys, '_base_executable', None) or sys.executable\n"
                "code = 'import json, os, site; print(json.dumps([d for d in site.getsitepackages() if os.path.isdir(d)]))'\n"
                "print(subprocess.run([base, '-c', code], capture_output=True, text=True, check=True).stdout)",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
            timeout=120,
        ).stdout.strip()
    )
    assert base_sites, "夹具前提不成立：base 解释器没有一个真实存在的 site 目录"
    missing = [b for b in base_sites if not any(os.path.samefile(s, b) for s in sites)]
    assert not missing, (sites, base_sites, missing)
    # 反面：不带 --system-site-packages 的 venv 看不见它们
    plain = tmp_path / "plain-venv"
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(plain)], check=True, timeout=300
    )
    ppy = plain / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    plain_sites = _site_packages_of(str(ppy))
    leaked = [b for b in base_sites if any(os.path.samefile(s, b) for s in plain_sites)]
    assert not leaked, (plain_sites, base_sites, leaked)


# ================================================================ 正例：状态机
class TestProvision:
    def test_download_verify_launch_and_commit_atomically(self, tmp_path, launches):
        archive, sha, rel = _make(tmp_path, launches)
        with LoopbackServer(tmp_path / "serve") as server:
            src = _source(server, archive, sha, rel)
            seen: list[tuple[str, int, int]] = []
            python = privatepython.provision(src, on_progress=lambda *a: seen.append(a))
            assert server.requests == [f"/{archive.name}"]
            # 落点：data_dir/private-python/runtimes/<id>/<python_rel>；id 按内容命名
            final = privatepython.runtime_dir(src)
            assert Path(python) == final / rel and final.name == f"cpython-{src.version}-{sha[:12]}"
            assert Path(python).is_file() and (POSIX is False or os.access(python, os.X_OK))
            assert _runtime_dirs() == {final.name}  # 没有 staging 留下
            assert _parts() == []
            assert privatepython.archive_path(src).is_file()  # 校验过的归档进缓存（FO24 的前提）
            assert _launch_count(launches) in (1, None)  # 真起了一次
            stages = [s for s, _, _ in seen]
            assert stages[0] == privatepython.STAGE_DOWNLOADING
            assert stages[-1] == privatepython.STAGE_COMMITTED
            assert privatepython.STAGE_LAUNCHING in stages
            assert seen[-1][1] == seen[-1][2] == src.size
            ledger = privatepython.read_ledger()["runtimes"][src.id]
            assert ledger["sha256"] == sha and ledger["url"] == src.url
            assert ledger["reported_version"] == src.version
            assert privatepython.python_of(src) == python
            assert privatepython.status(src)["provisioned"] is True
            assert privatepython.offer_payload(src) is None  # 已在：不再要求授权下载
            # 再要一次：不联网、不再起解释器、同一个路径
            again = privatepython.provision(src)
            assert again == python
            assert server.requests == [f"/{archive.name}"]
            assert _launch_count(launches) in (1, None)

    def test_the_launched_interpreter_really_runs_from_the_private_dir(self, tmp_path, launches):
        """判据的前提：替身真的可执行，且能 `-c` 自报身份（否则整组用例都在测一个假的）。"""
        archive, sha, rel = _make(tmp_path, launches)
        with LoopbackServer(tmp_path / "serve") as server:
            python = privatepython.provision(_source(server, archive, sha, rel))
        out = subprocess.run(
            [python, "-I", "-c", "import platform; print(platform.python_version())"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
        assert out.returncode == 0 and out.stdout.strip() == pp_support.host_python_version()

    @pytest.mark.skipif(os.name != "nt", reason="看住的是 Windows 替身的前提")
    def test_the_windows_standin_base_probe_matches_reality(self, tmp_path, launches):
        """`needs_real_base` 的前提（`support.private_python.standin_base_probe()`）必须与真实情况一致：把替身按产品
        路径供应出来、用它建一个 venv、起 venv 里的 python、跑 pip——量的是**真依赖的那一步**，不是「目录建出来了」
        （#475 db5f994a：目录齐全、下一步照样 create_failed）。探测说能、这里就得真能；探测说不能、这里就得真不能。
        两边不一致 = 探测量错了维度，红出来。报告进 CI 日志，下次有人查这条前提时不用再猜。"""
        archive, sha, rel = _make(tmp_path, launches)
        with LoopbackServer(tmp_path / "serve") as server:
            python = privatepython.provision(_source(server, archive, sha, rel))
        root = tmp_path / "venv-from-standin"
        steps: list[dict] = []

        def _step(name: str, argv: list[str]) -> bool:
            out = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,
                stdin=subprocess.DEVNULL,
            )
            steps.append(
                {"step": name, "rc": out.returncode, "out": (out.stdout + out.stderr)[-600:]}
            )
            return out.returncode == 0

        ok = _step("venv", [python, "-m", "venv", str(root)])
        vpy = root / "Scripts" / "python.exe"
        ok = ok and vpy.is_file()
        ok = ok and _step("launch", [str(vpy), "-I", "-c", "import sys; print(sys.prefix)"])
        ok = ok and _step("pip", [str(vpy), "-m", "pip", "--version"])
        report = json.dumps(
            {"reality": ok, "probe": pp_support.standin_base_probe(), "steps": steps},
            ensure_ascii=False,
        )
        print("WINDOWS_STANDIN_BASE_REPORT " + report)
        assert ok == pp_support.standin_can_be_base(), (
            "替身能不能当 base：探测与真实不一致（探测量错了维度，或这台机器两次结果不同）。"
            + report
        )

    def test_cached_verified_archive_is_reused_without_any_network(self, tmp_path, launches):
        """FO24：缓存齐备 + 断网（死代理 + 没人听的端口）→ automatic，零请求。"""
        archive, sha, rel = _make(tmp_path, launches)
        src = _source(None, archive, sha, rel)  # url 指向没人听的端口
        privatepython.downloads_dir().mkdir(parents=True)
        shutil.copy2(archive, privatepython.archive_path(src))
        offer = privatepython.offer_payload(src)
        assert offer["cached"] is True and offer["download_bytes"] == 0
        assert offer["network_required"] is False
        t0 = time.monotonic()
        python = privatepython.provision(src)
        assert time.monotonic() - t0 < 30  # 没有去等一个不存在的服务
        assert Path(python).is_file() and privatepython.python_of(src) == python

    def test_cached_archive_with_wrong_bytes_is_not_reused(self, tmp_path, launches):
        """缓存里躺着一份对不上的（上次截断 / 磁盘坏）：不是复用对象——重下；离线时就是离线。"""
        archive, sha, rel = _make(tmp_path, launches)
        src = _source(None, archive, sha, rel)
        privatepython.downloads_dir().mkdir(parents=True)
        privatepython.archive_path(src).write_bytes(archive.read_bytes()[:-100])
        assert privatepython.offer_payload(src)["cached"] is False
        with pytest.raises(privatepython.ProvisionError) as err:
            privatepython.provision(src)
        assert err.value.code == privatepython.ERROR_OFFLINE
        assert not privatepython.archive_path(src).exists()  # 坏缓存被清掉，不留着骗下一次
        assert _launch_count(launches) in (0, None) and _runtime_dirs() == set()


# ================================================================ 安装包附带的归档（ADR 0111）
def _count_fetches(monkeypatch) -> list[str]:
    """`privatepython._fetch`（唯一的联网函数）被调用的记录：判「有没有去下载」的主语。"""
    calls: list[str] = []
    real = privatepython._fetch

    def _counting(source, url, part, job, **kw):
        calls.append(url)
        return real(source, url, part, job, **kw)

    monkeypatch.setattr(privatepython, "_fetch", _counting)
    return calls


def _bundle(tmp_path, monkeypatch, archive: Path, data: bytes | None = None) -> Path:
    """把归档摆进一个「安装包目录」并经排他覆盖指过去（冻结产物里是 `_internal/private-python/`）。"""
    folder = tmp_path / "bundle"
    folder.mkdir(exist_ok=True)
    dest = folder / archive.name
    dest.write_bytes(archive.read_bytes() if data is None else data)
    monkeypatch.setenv(runtime.PRIVATE_PYTHON_BUNDLE_ENV, str(folder))
    return dest


class TestBundledArchive:
    def test_a_verified_bundled_archive_is_used_without_calling_the_downloader(
        self, tmp_path, launches, monkeypatch
    ):
        """包内归档 sha256 与锁一致：offer 说 `origin=bundled`、零字节；供应时下载函数调用 0 次、本地服务零请求，
        仍走校验 → 解包 → 真起 → 原子改名那一条链；归档不复制进 `downloads/`，账记来源。"""
        archive, sha, rel = _make(tmp_path, launches)
        bundled = _bundle(tmp_path, monkeypatch, archive)
        fetches = _count_fetches(monkeypatch)
        with LoopbackServer(tmp_path / "serve") as server:
            src = _source(server, archive, sha, rel)
            offer = privatepython.offer_payload(src)
            assert offer["origin"] == privatepython.ORIGIN_BUNDLED
            assert offer["download_bytes"] == 0 and offer["network_required"] is False
            assert offer["cached"] is True
            python = privatepython.provision(src)
            assert fetches == [] and server.requests == []
        assert Path(python) == privatepython.runtime_dir(src) / rel and Path(python).is_file()
        assert _launch_count(launches) in (1, None)
        assert _runtime_dirs() == {src.id} and _parts() == []
        assert not privatepython.archive_path(src).exists()  # 没有复制第二份
        assert bundled.is_file()  # 安装目录里的那份原样（只读的安装目录不该被动）
        assert privatepython.read_ledger()["runtimes"][src.id]["origin"] == "bundled"

    @pytest.mark.parametrize("damage", ["truncated", "tampered"])
    def test_a_bundled_archive_with_wrong_bytes_is_refused_and_the_lock_url_is_used(
        self, tmp_path, launches, monkeypatch, damage
    ):
        """包内那份对不上锁（截断 / 被改）：不是可用来源——不解、不起；offer 如实说要下载，供应按锁 URL 下一次。"""
        archive, sha, rel = _make(tmp_path, launches)
        raw = archive.read_bytes()
        bad = raw[:-100] if damage == "truncated" else raw[:-1] + bytes([raw[-1] ^ 0xFF])
        bundled = _bundle(tmp_path, monkeypatch, archive, bad)
        fetches = _count_fetches(monkeypatch)
        with LoopbackServer(tmp_path / "serve") as server:
            src = _source(server, archive, sha, rel)
            assert privatepython.bundled_archive(src) is None
            offer = privatepython.offer_payload(src)
            assert offer["origin"] == privatepython.ORIGIN_DOWNLOAD
            assert offer["download_bytes"] == archive.stat().st_size and offer["cached"] is False
            python = privatepython.provision(src)
            assert len(fetches) == 1 and server.requests == [f"/{archive.name}"]
        assert Path(python).is_file() and _launch_count(launches) in (1, None)
        assert privatepython.read_ledger()["runtimes"][src.id]["origin"] == "download"
        assert bundled.read_bytes() == bad  # 不去改安装目录

    def test_origin_is_cached_when_only_the_data_dir_holds_a_verified_archive(
        self, tmp_path, launches, monkeypatch
    ):
        archive, sha, rel = _make(tmp_path, launches)
        src = _source(None, archive, sha, rel)
        assert privatepython.offer_payload(src)["origin"] == privatepython.ORIGIN_DOWNLOAD
        privatepython.downloads_dir().mkdir(parents=True)
        shutil.copy2(archive, privatepython.archive_path(src))
        assert privatepython.offer_payload(src)["origin"] == privatepython.ORIGIN_CACHED
        fetches = _count_fetches(monkeypatch)
        privatepython.provision(src)
        assert fetches == []
        assert privatepython.read_ledger()["runtimes"][src.id]["origin"] == "cached"

    def test_bundle_dirs_follow_the_frozen_layout_and_the_override_is_exclusive(
        self, tmp_path, monkeypatch
    ):
        """定位与内置 runtime 同一套（`_MEIPASS` → exe 同级 → `exe/_internal`）；源码树 / wheel 不带：空表。"""
        assert runtime.private_python_bundle_dirs() == []  # 非冻结
        meipass = str(tmp_path / "meipass")
        exe = str(tmp_path / "Tavotto")
        monkeypatch.setattr(runtime.sys, "frozen", True, raising=False)
        monkeypatch.setattr(runtime.sys, "_MEIPASS", meipass, raising=False)
        monkeypatch.setattr(runtime.sys, "executable", exe)
        name = runtime.PRIVATE_PYTHON_BUNDLE_DIR_NAME
        assert runtime.private_python_bundle_dirs() == [
            os.path.join(meipass, name),
            os.path.join(str(tmp_path), name),
            os.path.join(str(tmp_path), "_internal", name),
        ]
        monkeypatch.setenv(runtime.PRIVATE_PYTHON_BUNDLE_ENV, str(tmp_path / "only"))
        assert runtime.private_python_bundle_dirs() == [str(tmp_path / "only")]


# ================================================================ 计划说的来源 == 执行的来源（ADR 0111 §一）
class TestPlannedOrigin:
    """计划里告诉用户的来源（`required_origin`）在「计划之后、执行之前」变了：要么按计划执行，要么
    `private_python_source_changed`（事务层收成 `repair_plan_stale`），**绝不静默换源、绝不变成联网下载**。
    判「有没有联网」的主语：下载函数 `_fetch` 的调用记录 + 本地服务的请求日志（服务一直在线——换源是
    做得到的，拦住它的只能是判据本身）。"""

    def _setup(self, tmp_path, launches, monkeypatch, server):
        archive, sha, rel = _make(tmp_path, launches)
        src = _source(server, archive, sha, rel)
        return archive, src, _count_fetches(monkeypatch)

    def _cache(self, archive, src, data: bytes | None = None):
        privatepython.downloads_dir().mkdir(parents=True, exist_ok=True)
        privatepython.archive_path(src).write_bytes(archive.read_bytes() if data is None else data)

    def _refused(self, src, required, fetches, server):
        with pytest.raises(privatepython.ProvisionError) as err:
            privatepython.provision(src, required_origin=required)
        assert err.value.code == privatepython.ERROR_SOURCE_CHANGED
        assert fetches == [] and server.requests == []
        assert _runtime_dirs() == set() and privatepython.python_of(src) is None

    @pytest.mark.parametrize("change", ["removed", "bytes-changed", "removed-but-cache-exists"])
    def test_planned_bundled_archive_gone_or_changed_is_refused_without_network(
        self, tmp_path, launches, monkeypatch, change
    ):
        with LoopbackServer(tmp_path / "serve") as server:
            archive, src, fetches = self._setup(tmp_path, launches, monkeypatch, server)
            bundled = _bundle(tmp_path, monkeypatch, archive)
            assert privatepython.offer_payload(src)["origin"] == "bundled"  # 计划时
            if change == "bytes-changed":
                raw = archive.read_bytes()
                bundled.write_bytes(raw[:-1] + bytes([raw[-1] ^ 0xFF]))
            else:
                bundled.unlink()
            if change == "removed-but-cache-exists":
                self._cache(archive, src)  # 缓存也不是计划里的来源
            self._refused(src, privatepython.ORIGIN_BUNDLED, fetches, server)

    @pytest.mark.parametrize("change", ["deleted", "corrupted"])
    def test_planned_cache_gone_or_corrupted_is_refused_without_network(
        self, tmp_path, launches, monkeypatch, change
    ):
        with LoopbackServer(tmp_path / "serve") as server:
            archive, src, fetches = self._setup(tmp_path, launches, monkeypatch, server)
            self._cache(archive, src)
            assert privatepython.offer_payload(src)["origin"] == "cached"  # 计划时
            if change == "deleted":
                privatepython.archive_path(src).unlink()
            else:
                self._cache(archive, src, archive.read_bytes()[:-100])
            self._refused(src, privatepython.ORIGIN_CACHED, fetches, server)

    @pytest.mark.parametrize("appeared", ["cache", "bundle"])
    def test_a_local_archive_appearing_after_a_download_plan_is_not_silently_used(
        self, tmp_path, launches, monkeypatch, appeared
    ):
        """计划说「要下载 N 字节」，执行前多出一份本地归档：那不是用户看过的来源——过期、重新确认。"""
        with LoopbackServer(tmp_path / "serve") as server:
            archive, src, fetches = self._setup(tmp_path, launches, monkeypatch, server)
            assert privatepython.offer_payload(src)["origin"] == "download"  # 计划时
            if appeared == "cache":
                self._cache(archive, src)
            else:
                _bundle(tmp_path, monkeypatch, archive)
            self._refused(src, privatepython.ORIGIN_DOWNLOAD, fetches, server)

    def test_a_runtime_present_at_plan_time_but_gone_is_refused_without_network(
        self, tmp_path, launches, monkeypatch
    ):
        """计划时私有 Python 已就位（`present_payload`，零字节、不联网），执行前被删：不去下载。"""
        with LoopbackServer(tmp_path / "serve") as server:
            archive, src, fetches = self._setup(tmp_path, launches, monkeypatch, server)
            _bundle(tmp_path, monkeypatch, archive)
            privatepython.provision(src)
            assert privatepython.present_payload(src) is not None  # 计划时
            shutil.rmtree(privatepython.runtime_dir(src))
            monkeypatch.delenv(runtime.PRIVATE_PYTHON_BUNDLE_ENV)
            self._refused(src, privatepython.REQUIRE_PRESENT, fetches, server)

    @pytest.mark.parametrize("origin", ["bundled", "cached", "download"])
    def test_each_planned_origin_is_honoured_when_nothing_changed(
        self, tmp_path, launches, monkeypatch, origin
    ):
        with LoopbackServer(tmp_path / "serve") as server:
            archive, src, fetches = self._setup(tmp_path, launches, monkeypatch, server)
            if origin == "bundled":
                _bundle(tmp_path, monkeypatch, archive)
            elif origin == "cached":
                self._cache(archive, src)
            assert privatepython.offer_payload(src)["origin"] == origin
            python = privatepython.provision(src, required_origin=origin)
            assert Path(python).is_file()
            assert len(fetches) == (1 if origin == "download" else 0)
            assert privatepython.read_ledger()["runtimes"][src.id]["origin"] == origin

    def test_an_already_present_runtime_satisfies_any_plan_without_network(
        self, tmp_path, launches, monkeypatch
    ):
        """执行时 runtime 已在（别的项目刚供应过）：不取任何归档、不联网——哪个计划都照常。"""
        with LoopbackServer(tmp_path / "serve") as server:
            archive, src, fetches = self._setup(tmp_path, launches, monkeypatch, server)
            self._cache(archive, src)
            python = privatepython.provision(src)
            fetches.clear()
            privatepython.archive_path(src).unlink()
            for required in (*privatepython.ORIGINS, privatepython.REQUIRE_PRESENT):
                assert privatepython.provision(src, required_origin=required) == python
            assert fetches == [] and server.requests == []

    def test_a_consumer_attached_to_another_plans_failed_provisioning_retries_on_its_own_origin(
        self, tmp_path, launches, monkeypatch
    ):
        """挂上的是别的计划领起的那一份（它要下载，却发现多出一份缓存 → source_changed）：那不是本消费者的
        计划过期——按自己的来源（cached）再领一次，成功、不联网。"""
        with LoopbackServer(tmp_path / "serve") as server:
            archive, src, fetches = self._setup(tmp_path, launches, monkeypatch, server)
            self._cache(archive, src)
            other = privatepython._Inflight(src, privatepython.ORIGIN_DOWNLOAD)
            privatepython._inflight[src.id] = other

            def _fail_later():
                time.sleep(0.3)
                other.error = privatepython.ProvisionError(privatepython.ERROR_SOURCE_CHANGED, "x")
                with privatepython._lock:
                    privatepython._inflight.pop(src.id, None)
                other.done.set()

            threading.Thread(target=_fail_later, daemon=True).start()
            python = privatepython.provision(src, required_origin=privatepython.ORIGIN_CACHED)
            assert Path(python).is_file() and fetches == [] and server.requests == []
            assert privatepython.read_ledger()["runtimes"][src.id]["origin"] == "cached"


# ================================================================ 负例：坏 hash / 截断 / 离线 / 越界 / 起不来
class TestRefusals:
    def _refused(self, tmp_path, launches, *, mode, code, launched: int = 0, **kw):
        archive, sha, rel = _make(tmp_path, launches, **kw)
        with LoopbackServer(tmp_path / "serve") as server:
            server.mode = mode
            src = _source(server, archive, sha, rel)
            with pytest.raises(privatepython.ProvisionError) as err:
                privatepython.provision(src)
            assert err.value.code == code, err.value
            assert server.requests, "判据的前提：确实向本地服务发了请求"
        assert _launch_count(launches) in (launched, None), "坏归档的路上解释器一次都不许起"
        assert _runtime_dirs() == set() and _parts() == []
        assert privatepython.python_of(src) is None
        assert privatepython.read_ledger()["runtimes"] == {}
        return src

    def test_corrupted_bytes_are_refused_before_any_execution(self, tmp_path, launches):
        """FO26：篡改一个字节 → `private_python_hash_mismatch`；执行计数 0、无目录、无 .part。"""
        src = self._refused(
            tmp_path, launches, mode="corrupt", code=privatepython.ERROR_HASH_MISMATCH
        )
        assert not privatepython.archive_path(src).exists()

    def test_truncated_download_is_refused(self, tmp_path, launches):
        """FO26：服务少发一段就断——读侧要么 hash 不符要么传输断，两种都不发布。"""
        archive, sha, rel = _make(tmp_path, launches)
        with LoopbackServer(tmp_path / "serve") as server:
            server.mode = "truncate"
            src = _source(server, archive, sha, rel)
            with pytest.raises(privatepython.ProvisionError) as err:
                privatepython.provision(src)
            assert err.value.code in (
                privatepython.ERROR_HASH_MISMATCH,
                privatepython.ERROR_OFFLINE,
            )
        assert _launch_count(launches) in (0, None)
        assert _runtime_dirs() == set() and _parts() == []
        assert not privatepython.archive_path(src).exists()

    def test_wrong_expected_hash_is_refused(self, tmp_path, launches):
        """FO26 的第三种形状：归档没坏，锁里的期望值错了——同样拒绝，不「以下载为准」。"""
        archive, sha, rel = _make(tmp_path, launches)
        wrong = "0" * 64
        with LoopbackServer(tmp_path / "serve") as server:
            src = _source(server, archive, wrong, rel)
            with pytest.raises(privatepython.ProvisionError) as err:
                privatepython.provision(src)
            assert err.value.code == privatepython.ERROR_HASH_MISMATCH
            assert err.value.detail["expected"] == wrong and err.value.detail["got"] == sha
            # 传输层没问题也**不重试**：对不上的文件是拒绝的对象
            assert server.requests == [f"/{archive.name}"]
        assert _launch_count(launches) in (0, None) and _runtime_dirs() == set()

    def test_offline_without_cache_is_a_bounded_safe_stop(self, tmp_path, launches):
        """FO25：没缓存 + 断网 → `private_python_offline`，有界（不空转），什么都没建。"""
        archive, sha, rel = _make(tmp_path, launches)
        src = _source(None, archive, sha, rel)
        assert privatepython.offer_payload(src)["network_required"] is True
        t0 = time.monotonic()
        with pytest.raises(privatepython.ProvisionError) as err:
            privatepython.provision(src)
        assert err.value.code == privatepython.ERROR_OFFLINE
        assert time.monotonic() - t0 < 60
        assert _launch_count(launches) in (0, None)
        assert _runtime_dirs() == set() and _parts() == []
        assert privatepython.python_of(src) is None
        assert privatepython.status(src)["provisioned"] is False

    def test_a_dead_proxy_really_blocks_the_network(self, tmp_path, launches, monkeypatch):
        """对照组：把回环从 NO_PROXY 里拿掉，本地服务也连不上——证明死代理不是摆设。"""
        archive, sha, rel = _make(tmp_path, launches)
        monkeypatch.setenv("NO_PROXY", "")
        monkeypatch.setenv("no_proxy", "")  # 小写优先于大写：两种都清
        with LoopbackServer(tmp_path / "serve") as server:
            src = _source(server, archive, sha, rel)
            with pytest.raises(privatepython.ProvisionError) as err:
                privatepython.provision(src)
            assert err.value.code == privatepython.ERROR_OFFLINE
            assert server.requests == []  # 请求根本没到本地服务

    def test_missing_source_is_not_reported_as_offline(self, tmp_path, launches):
        """来源回 404：那是「钉死的地址上没有这个文件」（要升级 Tavotto），不是离线（重试没用）。"""
        self._refused(
            tmp_path, launches, mode="missing", code=privatepython.ERROR_SOURCE_UNAVAILABLE
        )

    @pytest.mark.parametrize(
        "member",
        [
            ("../evil.txt", b"x", None),
            ("/tmp/evil.txt", b"x", None),
            ("python/../../evil.txt", b"x", None),
            ("other/evil.txt", b"x", None),
            ("python/lib/escape", None, "../../../etc/passwd"),
            ("python/lib/abs", None, "/etc/passwd"),
        ],
        ids=["dotdot", "absolute", "dotdot-inside", "other-root", "symlink-out", "symlink-abs"],
    )
    def test_archive_members_outside_the_root_are_refused(self, tmp_path, launches, member):
        """zip-slip：越界成员整份拒绝（`private_python_invalid_archive`），数据目录外一个字节不写。"""
        archive, sha, rel = _make(tmp_path, launches, extra_members=[member])
        before = pp_support.snapshot_tree(tmp_path)
        with LoopbackServer(tmp_path / "serve") as server:
            src = _source(server, archive, sha, rel)
            with pytest.raises(privatepython.ProvisionError) as err:
                privatepython.provision(src)
            assert err.value.code == privatepython.ERROR_INVALID_ARCHIVE
            assert server.requests, "判据的前提：确实向本地服务发了请求"
        assert _launch_count(launches) in (0, None)
        assert _runtime_dirs() == set() and _parts() == []
        after = pp_support.snapshot_tree(tmp_path)
        new = {p for p in after - before if not p.startswith("data/private-python")}
        assert new == set(), new
        assert (
            not (tmp_path / "evil.txt").exists() and not (tmp_path / "data" / "evil.txt").exists()
        )

    @pytest.mark.parametrize(
        "name,kind,link,ok",
        [
            ("python/bin/python3", "file", None, True),
            ("python/lib", "dir", None, True),
            ("python/bin/python", "sym", "python3.13", True),
            ("python/lib/x", "sym", "../bin/python3", True),
            ("python/lib/y", "sym", "../../python/bin/python3", True),
            ("../evil", "file", None, False),
            ("/etc/evil", "file", None, False),
            ("python/../../evil", "file", None, False),
            ("other/evil", "file", None, False),
            ("python\\bin\\python3", "file", None, False),
            ("C:/python/evil", "file", None, False),
            ("python/lib/escape", "sym", "../../../etc/passwd", False),
            ("python/lib/abs", "sym", "/etc/passwd", False),
            ("python/lib/up", "sym", "../../other", False),
            ("python/lib/hard", "lnk", "python/bin/python3", False),
            ("python/lib/win1", "sym", "..\\..\\outside", False),
            ("python/lib/win2", "sym", "C:\\outside", False),
            ("python/lib/win3", "sym", "C:/outside", False),
            ("python/dev/null", "chr", None, False),
            ("python/fifo", "fifo", None, False),
        ],
    )
    def test_member_validation_is_our_own_first_line(self, name, kind, link, ok):
        """成员校验是**我们自己的**第一道（`tarfile` 的 `data` 过滤器是第二道，3.12 前的解释器上不一定有）：
        逐种形状钉住接受 / 拒绝，不靠第二道兜底。"""
        import tarfile

        info = tarfile.TarInfo(name)
        info.type = {
            "file": tarfile.REGTYPE,
            "dir": tarfile.DIRTYPE,
            "sym": tarfile.SYMTYPE,
            "lnk": tarfile.LNKTYPE,
            "chr": tarfile.CHRTYPE,
            "fifo": tarfile.FIFOTYPE,
        }[kind]
        if link is not None:
            info.linkname = link
        if ok:
            privatepython._validate_member(info, "python")
        else:
            with pytest.raises(privatepython.ProvisionError) as err:
                privatepython._validate_member(info, "python")
            assert err.value.code == privatepython.ERROR_INVALID_ARCHIVE

    @pytest.mark.skipif(
        not POSIX, reason="替身是 sh 脚本；Windows 上用 venvlauncher 副本，退出码由真解释器决定"
    )
    def test_an_interpreter_that_fails_to_launch_is_not_published(self, tmp_path, launches):
        src = self._refused(
            tmp_path,
            launches,
            mode="ok",
            code=privatepython.ERROR_LAUNCH_FAILED,
            exit_code=7,
            launched=1,  # 起了一次、退出 7；不发布、不留 staging
        )
        assert privatepython.archive_path(src).is_file()  # 归档本身是好的，缓存留着

    def test_an_interpreter_reporting_another_version_is_not_published(self, tmp_path, launches):
        """锁说 3.13.15、起来的说别的：不发布——版本是锁的一部分，不由下载到的东西说了算。"""
        archive, sha, rel = _make(tmp_path, launches)
        with LoopbackServer(tmp_path / "serve") as server:
            src = _source(server, archive, sha, rel, version="9.9.9")
            with pytest.raises(privatepython.ProvisionError) as err:
                privatepython.provision(src)
            assert err.value.code == privatepython.ERROR_LAUNCH_FAILED
            assert err.value.detail == {
                "expected": "9.9.9",
                "got": pp_support.host_python_version(),
            }
        assert _runtime_dirs() == set()

    @pytest.mark.skipif(not POSIX, reason="Windows 没有可执行位")
    def test_missing_exec_bit_is_refused(self, tmp_path, launches):
        self._refused(
            tmp_path, launches, mode="ok", code=privatepython.ERROR_INVALID_ARCHIVE, exec_bit=False
        )

    @pytest.mark.skipif(
        not POSIX or os.geteuid() == 0, reason="只读目录的判据要 POSIX 权限位且不是 root"
    )
    def test_an_unwritable_download_dir_is_a_write_error_not_offline(self, tmp_path, launches):
        """`.part` 打不开是本机的事（write_failed），不是离线（offline）：两种恢复动作不同
        （Codex #464 第二轮 P2）。"""
        archive, sha, rel = _make(tmp_path, launches)
        downloads = privatepython.downloads_dir()
        downloads.mkdir(parents=True)
        downloads.chmod(0o500)
        try:
            with LoopbackServer(tmp_path / "serve") as server:
                src = _source(server, archive, sha, rel)
                with pytest.raises(privatepython.ProvisionError) as err:
                    privatepython.provision(src)
                assert err.value.code == privatepython.ERROR_WRITE_FAILED
                assert server.requests == []  # 打不开就不去下
        finally:
            downloads.chmod(0o700)
        assert _runtime_dirs() == set() and _parts() == []

    @pytest.mark.skipif(
        not POSIX or os.geteuid() == 0, reason="只读目录的判据要 POSIX 权限位且不是 root"
    )
    def test_an_unwritable_staging_dir_is_a_write_error_not_an_invalid_archive(
        self, tmp_path, launches, monkeypatch
    ):
        """解包写不进（磁盘满 / 配额 / 权限）：归档没坏，是本机的事——write_failed，不是 invalid_archive
        （Codex #464 P2）。让 staging 目录只读来表达。"""
        archive, sha, rel = _make(tmp_path, launches)
        real_mkdir = Path.mkdir

        def _readonly_staging(self, *a, **kw):
            real_mkdir(self, *a, **kw)
            if self.name.startswith(privatepython.STAGING_PREFIX):
                self.chmod(0o500)

        monkeypatch.setattr(Path, "mkdir", _readonly_staging)
        with LoopbackServer(tmp_path / "serve") as server:
            src = _source(server, archive, sha, rel)
            with pytest.raises(privatepython.ProvisionError) as err:
                privatepython.provision(src)
            assert err.value.code == privatepython.ERROR_WRITE_FAILED
        assert _launch_count(launches) in (0, None)
        assert privatepython.archive_path(src).is_file()  # 归档是好的，缓存留着

    def test_disk_quota_is_checked_before_any_download(self, tmp_path, launches, monkeypatch):
        archive, sha, rel = _make(tmp_path, launches)
        real = shutil.disk_usage

        def _tiny(path):
            u = real(path)
            return type(u)(u.total, u.used, 1024)

        monkeypatch.setattr(shutil, "disk_usage", _tiny)
        with LoopbackServer(tmp_path / "serve") as server:
            src = _source(server, archive, sha, rel)
            with pytest.raises(privatepython.ProvisionError) as err:
                privatepython.provision(src)
            assert err.value.code == privatepython.ERROR_DISK_LOW
            assert err.value.detail["need_bytes"] > err.value.detail["free_bytes"]
            assert server.requests == []
        assert _runtime_dirs() == set() and _parts() == []


# ================================================================ 去重、取消、租约、孤儿
class TestConsumers:
    def test_concurrent_consumers_share_one_download(self, tmp_path, launches):
        """FO-027：三个消费者同时要同一份 → 一次请求、一次执行、同一个路径。"""
        archive, sha, rel = _make(tmp_path, launches)
        with LoopbackServer(tmp_path / "serve") as server:
            server.mode = "hold"  # 第一段发完就等：三个消费者都在同一次下载上
            server.gate.clear()
            src = _source(server, archive, sha, rel)
            results: list = []

            def _go():
                try:
                    results.append(privatepython.provision(src))
                except privatepython.ProvisionError as exc:
                    results.append(exc)

            threads = [threading.Thread(target=_go) for _ in range(3)]
            threads[0].start()
            deadline = time.time() + 30
            while not server.requests and time.time() < deadline:
                time.sleep(0.05)
            for t in threads[1:]:
                t.start()
            time.sleep(0.5)
            with privatepython._lock:
                assert privatepython._inflight[src.id].consumers == 3
            server.gate.set()
            for t in threads:
                t.join(120)
            assert all(isinstance(r, str) for r in results), results
            assert len(set(results)) == 1
            assert server.requests == [f"/{archive.name}"]
        assert _launch_count(launches) in (1, None)
        assert _runtime_dirs() == {privatepython.runtime_dir(src).name}

    def test_two_processes_provisioning_the_same_runtime_both_succeed(self, tmp_path, launches):
        """跨进程：两个 Tavotto 进程同时供应同一份——各写各的 `.part`、各自 staging，先就位的赢，后来的复用；
        两个都退出 0、同一个路径、一个最终目录、没有 `.part` 残留（Codex #464 P2）。"""
        archive, sha, rel = _make(tmp_path, launches)
        with LoopbackServer(tmp_path / "serve") as server:
            server.mode = "hold"
            server.gate.clear()
            src = _source(server, archive, sha, rel)
            code = (
                "import json, sys; from tavotto.engine import privatepython as pp; "
                "src = pp.PythonSource(**json.loads(sys.argv[1])); print(pp.provision(src))"
            )
            env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
            procs = [
                subprocess.Popen(
                    [sys.executable, "-c", code, json.dumps(src.__dict__)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    env=env,
                )
                for _ in range(2)
            ]
            deadline = time.time() + 60
            while len(server.requests) < 2 and time.time() < deadline:
                time.sleep(0.05)
            assert len(server.requests) == 2, (
                "判据的前提：两个进程各发了一次请求（进程间没有共享的去重表）"
            )
            server.gate.set()
            outs = [p.communicate(timeout=180) for p in procs]
            assert [p.returncode for p in procs] == [0, 0], outs
            paths = {o[0].strip() for o in outs}
            assert paths == {str(privatepython.runtime_python(src))}
        assert _runtime_dirs() == {src.id} and _parts() == []
        assert privatepython.archive_path(src).is_file()

    def test_archive_rename_refused_while_another_process_holds_it_reuses_that_copy(
        self, tmp_path, launches, monkeypatch
    ):
        """Windows 形状（#467 目标腿确定性红）：两个进程同时供应，先到的把归档搬到正式名并打开解包，
        后到的 `os.replace(.part → 正式名)` 被共享冲突拒掉——正式名上那份校验过、字节一样，后到的
        必须复用它而不是报 write_failed。本机没有 Windows：把「别的进程刚把同一份放上去、且 replace
        被拒」这一步按 Windows 语义摆出来（拒之前先把同样的字节落到正式名上）。"""
        archive, sha, rel = _make(tmp_path, launches)
        real_replace = os.replace
        refused: list[tuple[str, str]] = []

        def _windows_like_replace(src_p, dst_p):
            if str(src_p).endswith(".part"):
                shutil.copyfile(src_p, dst_p)  # 别的进程那份已经在正式名上（同一份字节）
                refused.append((str(src_p), str(dst_p)))
                raise PermissionError(13, "Access is denied")  # [WinError 5]：目标被别的进程打开着
            return real_replace(src_p, dst_p)

        monkeypatch.setattr(privatepython.os, "replace", _windows_like_replace)
        with LoopbackServer(tmp_path / "serve") as server:
            src = _source(server, archive, sha, rel)
            python = privatepython.provision(src)
            assert python == str(privatepython.runtime_python(src))
            assert server.requests == [f"/{archive.name}"]
        assert len(refused) == 1 and refused[0][1] == str(privatepython.archive_path(src))
        assert privatepython.archive_path(src).is_file() and _parts() == []
        assert _runtime_dirs() == {src.id}

    def test_archive_rename_refused_with_wrong_bytes_on_the_name_is_a_write_error(
        self, tmp_path, launches, monkeypatch
    ):
        """同一形状但正式名上的字节对不上（不是校验过的那份）：不复用、报 write_failed、不发布。"""
        archive, sha, rel = _make(tmp_path, launches)
        real_replace = os.replace

        def _windows_like_replace(src_p, dst_p):
            if str(src_p).endswith(".part"):
                Path(dst_p).write_bytes(b"not the archive")
                raise PermissionError(13, "Access is denied")
            return real_replace(src_p, dst_p)

        monkeypatch.setattr(privatepython.os, "replace", _windows_like_replace)
        with LoopbackServer(tmp_path / "serve") as server:
            src = _source(server, archive, sha, rel)
            with pytest.raises(privatepython.ProvisionError) as err:
                privatepython.provision(src)
            assert err.value.code == privatepython.ERROR_WRITE_FAILED
        assert _parts() == [] and _runtime_dirs() == set()

    def test_one_consumer_cancelling_does_not_stop_the_others(self, tmp_path, launches):
        """取消按消费者管理（D11）：A 取消只是 A 退出，B 照样拿到；下载不因 A 而中止。"""
        archive, sha, rel = _make(tmp_path, launches)
        with LoopbackServer(tmp_path / "serve") as server:
            server.mode = "hold"
            server.gate.clear()
            src = _source(server, archive, sha, rel)
            cancel_a = threading.Event()
            out: dict = {}

            def _a():
                try:
                    out["a"] = privatepython.provision(src, cancel_ev=cancel_a)
                except privatepython.ProvisionError as exc:
                    out["a"] = exc

            def _b():
                out["b"] = privatepython.provision(src)

            ta, tb = threading.Thread(target=_a), threading.Thread(target=_b)
            ta.start()
            time.sleep(0.5)
            tb.start()
            time.sleep(0.5)
            cancel_a.set()
            ta.join(30)
            assert isinstance(out["a"], privatepython.ProvisionError)
            assert out["a"].code == privatepython.ERROR_CANCELLED
            assert not privatepython.runtime_dir(src).exists()  # A 退出时还没提交
            server.gate.set()
            tb.join(120)
            assert isinstance(out["b"], str) and Path(out["b"]).is_file()
            assert server.requests == [f"/{archive.name}"]

    def test_the_last_consumer_cancelling_aborts_and_leaves_nothing(self, tmp_path, launches):
        """唯一的消费者在下载中取消 → `private_python_cancelled`，`.part` / staging / 目录都没有；之后能再来。"""
        archive, sha, rel = _make(tmp_path, launches)
        with LoopbackServer(tmp_path / "serve") as server:
            server.mode = "hold"
            server.gate.clear()
            src = _source(server, archive, sha, rel)
            cancel = threading.Event()
            out: dict = {}

            def _go():
                try:
                    out["r"] = privatepython.provision(src, cancel_ev=cancel)
                except privatepython.ProvisionError as exc:
                    out["r"] = exc

            t = threading.Thread(target=_go)
            t.start()
            deadline = time.time() + 30
            while not server.requests and time.time() < deadline:
                time.sleep(0.05)
            time.sleep(0.3)
            cancel.set()
            t.join(30)
            assert isinstance(out["r"], privatepython.ProvisionError)
            assert out["r"].code == privatepython.ERROR_CANCELLED
            server.gate.set()
            deadline = time.time() + 30
            while privatepython._inflight and time.time() < deadline:
                time.sleep(0.05)
            assert privatepython._inflight == {}
            assert _runtime_dirs() == set() and _parts() == []
            assert not privatepython.archive_path(src).exists()
            assert _launch_count(launches) in (0, None)
            # 再来一次成功
            server.mode = "ok"
            python = privatepython.provision(src)
            assert Path(python).is_file()

    def test_an_abort_arriving_before_the_commit_point_is_honoured(self, tmp_path, launches):
        """接受时刻的边界：下载完、解开了、真起过了，提交之前所有消费者都放弃 → 仍不提交，staging 清掉。
        （中止信号在 `launching` 那一刻从下载线程内部置上——与消费者轮询的时序无关。）"""
        archive, sha, rel = _make(tmp_path, launches)

        def _abort_at_launch(stage, done, total):
            if stage == privatepython.STAGE_LAUNCHING:
                with privatepython._lock:
                    privatepython._inflight[src.id].abort.set()

        with LoopbackServer(tmp_path / "serve") as server:
            src = _source(server, archive, sha, rel)
            with pytest.raises(privatepython.ProvisionError) as err:
                privatepython.provision(src, on_progress=_abort_at_launch)
            assert err.value.code == privatepython.ERROR_CANCELLED
        assert _runtime_dirs() == set()  # 没有最终目录，也没有 staging
        assert privatepython.python_of(src) is None
        assert privatepython.read_ledger()["runtimes"] == {}

    def test_a_cancellation_set_before_provisioning_starts_is_honoured(self, tmp_path, launches):
        """进来之前就取消了：不下载、不起线程、不发布——快的缓存供应也不会在第一次轮询之前先提交
        （Codex #464 第二轮 P2）。"""
        archive, sha, rel = _make(tmp_path, launches)
        src = _source(None, archive, sha, rel)
        privatepython.downloads_dir().mkdir(parents=True)
        shutil.copy2(archive, privatepython.archive_path(src))  # 缓存齐备：本来会瞬间完成
        cancel = threading.Event()
        cancel.set()
        with pytest.raises(privatepython.ProvisionError) as err:
            privatepython.provision(src, cancel_ev=cancel)
        assert err.value.code == privatepython.ERROR_CANCELLED
        assert _runtime_dirs() == set() and privatepython.python_of(src) is None
        assert _launch_count(launches) in (0, None)
        with privatepython._lock:
            assert privatepython._inflight == {}

    def test_a_cancellation_racing_the_commit_never_publishes_after_being_accepted(
        self, tmp_path, launches, monkeypatch
    ):
        """接受与提交在同一把锁下判（Codex #464 P2）：把 `os.replace` 拖住，期间取消 → 要么取消被接受且没有
        目录，要么提交先赢、消费者拿到的是路径——绝不会「报了取消、目录却在」。"""
        archive, sha, rel = _make(tmp_path, launches)
        real_replace = os.replace
        entered = threading.Event()
        release = threading.Event()

        def _slow_replace(src_p, dst_p):
            if privatepython.STAGING_PREFIX in str(src_p):
                entered.set()
                release.wait(30)
            return real_replace(src_p, dst_p)

        monkeypatch.setattr(privatepython.os, "replace", _slow_replace)
        cancel = threading.Event()
        out: dict = {}
        with LoopbackServer(tmp_path / "serve") as server:
            src = _source(server, archive, sha, rel)

            def _go():
                try:
                    out["r"] = privatepython.provision(src, cancel_ev=cancel)
                except privatepython.ProvisionError as exc:
                    out["r"] = exc

            t = threading.Thread(target=_go)
            t.start()
            assert entered.wait(60)
            cancel.set()  # 提交进行中（锁被下载线程持有）：消费者的取消判断要等提交完成
            time.sleep(0.5)
            release.set()
            t.join(60)
        published = privatepython.python_of(src) is not None
        if isinstance(out["r"], privatepython.ProvisionError):
            assert out["r"].code == privatepython.ERROR_CANCELLED
            assert not published, "报了取消，目录却在——接受与提交交错了"
        else:
            assert published and out["r"] == privatepython.python_of(src)

    def test_cancel_after_the_commit_point_changes_nothing(self, tmp_path, launches):
        """提交点之后取消无效：目录不可变，留下的永远是完整的一份。"""
        archive, sha, rel = _make(tmp_path, launches)
        cancel = threading.Event()

        def _late(stage, done, total):
            if stage == privatepython.STAGE_COMMITTED:
                cancel.set()

        with LoopbackServer(tmp_path / "serve") as server:
            src = _source(server, archive, sha, rel)
            python = privatepython.provision(src, cancel_ev=cancel, on_progress=_late)
        assert cancel.is_set() and Path(python).is_file()
        assert privatepython.python_of(src) == python
        assert _runtime_dirs() == {privatepython.runtime_dir(src).name}

    def test_retire_keeps_the_current_and_anything_a_generation_records(self, tmp_path, launches):
        """FO-028 / FO-027：当前那份永不删；被任一项目某一代记为 base 的旧份不删；没人记的旧份删掉。"""
        archive, sha, rel = _make(tmp_path, launches)
        with LoopbackServer(tmp_path / "serve") as server:
            src = _source(server, archive, sha, rel)
            privatepython.provision(src)
        runtimes = privatepython.runtimes_dir()
        for name in ("cpython-1.0.0-aaaaaaaaaaaa", "cpython-1.0.0-bbbbbbbbbbbb"):
            (runtimes / name / "bin").mkdir(parents=True)
            (runtimes / name / "bin" / "python3").write_text("#!/bin/sh\n", encoding="utf-8")
        # 一个项目的受管环境某一代以 aaaa 为 base
        project = tmp_path / "paper"
        project.mkdir()
        managedenv.register_generation(
            project,
            "gaaaa",
            requirements=[],
            constraints=[],
            identity="x",
            base_python=str(runtimes / "cpython-1.0.0-aaaaaaaaaaaa" / "bin" / "python3"),
            base_runtime="cpython-1.0.0-aaaaaaaaaaaa",
        )
        assert managedenv.referenced_base_runtimes() == {"cpython-1.0.0-aaaaaaaaaaaa"}

        def _in_use(runtime_id, python):
            return runtime_id in managedenv.referenced_base_runtimes()

        removed = privatepython.retire_unused(in_use=_in_use, source=src)
        assert removed == ["cpython-1.0.0-bbbbbbbbbbbb"]
        assert _runtime_dirs() == {src.id, "cpython-1.0.0-aaaaaaaaaaaa"}
        # 判「在用」失败按在用处理（宁可留着）
        (runtimes / "cpython-1.0.0-cccccccccccc").mkdir()

        def _boom(runtime_id, python):
            raise RuntimeError("x")

        assert privatepython.retire_unused(in_use=_boom, source=src) == []
        assert "cpython-1.0.0-cccccccccccc" in _runtime_dirs()

    @pytest.mark.skipif(not POSIX, reason="pid 存活判据只在 POSIX 上量得到")
    def test_orphan_staging_from_a_dead_process_is_never_used_and_gets_reaped(
        self, tmp_path, launches
    ):
        """应用重开：上一次进程死在解包中间留下的 staging 不是可用 runtime（`python_of` 看不见它），
        下一次供应时清掉、正常走完。"""
        archive, sha, rel = _make(tmp_path, launches)
        with LoopbackServer(tmp_path / "serve") as server:
            src = _source(server, archive, sha, rel)
            stale = privatepython.runtimes_dir() / f"{privatepython.STAGING_PREFIX}{src.id}-999999"
            (stale / "python" / "bin").mkdir(parents=True)
            (stale / "python" / "bin" / "python3").write_text("#!/bin/sh\n", encoding="utf-8")
            os.chmod(stale / "python" / "bin" / "python3", 0o755)
            assert privatepython.python_of(src) is None
            assert privatepython.offer_payload(src)["required"] is True
            python = privatepython.provision(src)
            assert Path(python).is_file()
            assert not stale.exists()
            assert _runtime_dirs() == {src.id}


# ================================================================ 探测链末级
class TestBaseChain:
    def test_a_provisioned_runtime_is_not_used_once_the_capability_is_off(
        self, tmp_path, launches, monkeypatch
    ):
        """早先（逃生门开着 / 旧版锁 enabled）供应好的一份躺在磁盘上：能力关掉之后探测链末级不再用它，
        行为回到 U04 的 `managed_env_unavailable`（Codex #464 P2）。"""
        from tavotto.engine import bootstrap, managedenv

        archive, sha, rel = _make(tmp_path, launches)
        with LoopbackServer(tmp_path / "serve") as server:
            src = _source(server, archive, sha, rel)
            python = privatepython.provision(src)
        monkeypatch.setattr(privatepython, "source_for", lambda target=None, lock=None: src)
        monkeypatch.setattr(bootstrap, "find_base_python", lambda accept=None: None)
        assert managedenv.base_python() == python  # 逃生门 1：用
        monkeypatch.setenv("TAVOTTO_PRIVATE_PYTHON", "0")
        assert privatepython.python_of(src) == python  # 磁盘上还在
        assert managedenv.base_python() is None  # 但不再是 base
        monkeypatch.delenv("TAVOTTO_PRIVATE_PYTHON")
        assert managedenv.base_python() is None  # 锁 enabled=false 也一样


# ================================================================ 隔离
class TestIsolation:
    def test_everything_lands_under_data_dir_and_home_path_env_are_untouched(
        self, tmp_path, launches, monkeypatch
    ):
        """FO-024：HOME 指向空目录跑完仍为空；PATH 与整个环境前后相同；写入全在 data_dir/private-python 下。"""
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("USERPROFILE", str(home))
        env_before = dict(os.environ)
        cwd_before = os.getcwd()
        archive, sha, rel = _make(tmp_path, launches)
        before = pp_support.snapshot_tree(tmp_path)
        with LoopbackServer(tmp_path / "serve") as server:
            src = _source(server, archive, sha, rel)
            privatepython.provision(src)
        after = pp_support.snapshot_tree(tmp_path)
        assert pp_support.snapshot_tree(home) == set()
        assert dict(os.environ) == env_before and os.getcwd() == cwd_before
        new = after - before
        assert new, "判据的前提：确实写了东西"
        # 写入只有两类：私有目录下的，和替身自己追加的 launches.log（那是装置的，不是产品的）
        offenders = {
            p for p in new if not p.startswith("data/private-python") and p != launches.name
        }
        assert offenders == set(), offenders
        assert (
            Path(os.path.realpath(privatepython.runtime_dir(src)))
            .relative_to(Path(os.path.realpath(config.data_dir())))
            .parts[0]
            == "private-python"
        )

    def test_the_downloader_reads_no_project_or_pip_settings(self):
        """下载器只用 urllib + 环境变量代理（AST 判）：import 闭集里没有 ssl / configparser / 第三方
        （TLS 上下文只经 `tlstrust` 来——平台原生校验，不降级，`tests/test_private_python_tls.py` 钉）；
        对 `config` 只调 `data_path` / `data_dir`（不读用户配置、不读项目设置）。"""
        import ast

        tree = ast.parse(Path(privatepython.__file__).read_text(encoding="utf-8"))
        imported: set[str] = set()
        config_calls: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    imported.update(a.name for a in node.names)
                else:
                    imported.add((node.module or "").split(".")[0])
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "config"
            ):
                config_calls.add(node.func.attr)
        allowed = {
            "dataclasses", "hashlib", "http", "json", "logging", "os", "platform", "posixpath", "re", "secrets", "shutil",
            "socket", "stat", "subprocess", "tarfile", "tempfile", "privatepython_bundle", "threading", "time", "urllib", "pathlib",
            "importlib", "__future__", "brand", "config", "logsafe", "runtime", "tlstrust", "files", "__version__",
        }  # fmt: skip
        assert imported <= allowed, imported - allowed
        assert "ssl" not in imported and "configparser" not in imported
        assert config_calls <= {"data_path", "data_dir"}, config_calls
        # 代理在下载那一刻读：现建 opener（`build_opener()`），不用会把第一次的代理配置缓存到进程结束的
        # 模块级 `urlopen`（全量跑时别的用例先 urlopen 一次，死代理对照就失效——2026-09-21 实测）
        calls = {
            f"{n.func.value.attr}.{n.func.attr}"
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and isinstance(n.func.value, ast.Attribute)
        }
        assert "request.build_opener" in calls and "request.urlopen" not in calls, calls


# ================================================================ 自动测速选源：主地址 → 镜像（2026-09-29）
class TestMirrorFallback:
    """ADR 0063 修订（2026-09-29，阿里云华东 Windows 实测：GitHub 20–40 KB/s 硬等 21 分钟、npmmirror
    11 MB/s）。主地址失败 / 太慢 → 同一份文件的镜像；**信任只看锁里的 sha256**——镜像篡改照样拒绝。

    两个本地服务：主地址写 `127.0.0.1`、镜像写 `localhost`（`LoopbackServer(host=)`；两者都在 NO_PROXY 里），
    所以「用的是哪个来源」既能从各自的请求日志量，也能从账里的 `downloaded_from` 主机名量。
    测速阈值按用例缩短（`SLOW_GRACE_S` / `SLOW_ETA_S`）：判据的形状不变，只是秒数。"""

    @pytest.fixture(autouse=True)
    def _fast_judgement(self, monkeypatch):
        monkeypatch.setattr(privatepython, "SLOW_GRACE_S", 0.3)
        monkeypatch.setattr(privatepython, "SLOW_ETA_S", 0.5)

    @staticmethod
    def _with_mirror(src, mirror: LoopbackServer, archive: Path):
        import dataclasses

        return dataclasses.replace(src, mirrors=(mirror.url(archive.name),))

    # ---- 锁里的推导规则（唯一出处 `mirror_urls`）
    def test_every_target_derives_its_mirror_from_the_lock_url(self):
        lock = privatepython.load_lock()
        bases = [m["base"] for m in lock["python"]["mirrors"]]
        assert bases == ["https://registry.npmmirror.com/-/binary/python-build-standalone/"]
        for name, t in lock["targets"].items():
            src = privatepython.source_for(name)
            filename = t["url"].rsplit("/", 1)[-1]
            assert src.mirrors == (f"{bases[0]}{lock['python']['release']}/{filename}",), name
            assert src.urls == (t["url"], *src.mirrors), name  # 主地址永远在前

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda lock: lock["python"]["mirrors"][0].update(base="http://mirror.example/"),
            lambda lock: lock["python"]["mirrors"][0].update(base="https://mirror.example"),
            lambda lock: lock["python"]["mirrors"][0].pop("name"),
            lambda lock: lock["python"].update(mirrors={"npmmirror": "https://x/"}),
            # 推导规则的前提：主地址是 pbs 这个 release 的发行地址；别的 release / 别的仓库都不行
            lambda lock: lock["targets"]["windows-x86_64"].update(
                url=lock["targets"]["windows-x86_64"]["url"].replace("/20260814/", "/20260101/")
            ),
            lambda lock: lock["targets"]["windows-x86_64"].update(
                url="https://example.com/cpython-install_only.tar.gz"
            ),
        ],
    )
    def test_the_lock_refuses_mirrors_whose_derivation_would_not_hold(self, mutate):
        lock = privatepython.load_lock()
        mutate(lock)
        with pytest.raises(ValueError):
            privatepython.validate_lock(lock)

    # ---- 行为：换源的三种理由
    def _provision_via(self, tmp_path, launches, *, primary_mode="ok", primary_bps=0):
        archive, sha, rel = _make(tmp_path, launches)
        hosts: list[str] = []
        with (
            LoopbackServer(tmp_path / "serve") as primary,
            LoopbackServer(tmp_path / "serve", host="localhost") as mirror,
        ):
            primary.mode = primary_mode
            primary.throttle_bps = primary_bps
            src = self._with_mirror(_source(primary, archive, sha, rel), mirror, archive)
            python = privatepython.provision(
                src,
                on_progress=lambda *a: hosts.append(privatepython.downloading_from(src)),
            )
            return src, python, list(primary.requests), list(mirror.requests), hosts

    def test_a_slow_primary_is_abandoned_for_the_mirror(self, tmp_path, launches, caplog):
        caplog.set_level(logging.INFO, logger="tavotto.privatepython")
        # 64 KiB/s：第一段立刻到，之后每段隔 1 s——量程过了之后按平均速度估的剩余时间 > 阈值
        src, python, primary, mirror, hosts = self._provision_via(
            tmp_path, launches, primary_bps=64 * 1024
        )
        name = f"/{privatepython.archive_path(src).name}"
        assert primary == [name] and mirror == [name]
        assert Path(python).is_file() and privatepython.python_of(src) == python
        assert privatepython.read_ledger()["runtimes"][src.id]["downloaded_from"] == "localhost"
        assert "localhost" in hosts  # 进度说出了此刻真在下的那个主机
        text = "\n".join(r.getMessage() for r in caplog.records)
        assert "下载源 127.0.0.1 放弃：too_slow" in text and "改用 localhost" in text, text
        assert "下载完成：来源 localhost" in text, text

    def test_a_missing_primary_falls_back_to_the_mirror(self, tmp_path, launches, caplog):
        caplog.set_level(logging.WARNING, logger="tavotto.privatepython")
        src, python, primary, mirror, _ = self._provision_via(
            tmp_path, launches, primary_mode="missing"
        )
        assert primary and mirror, (primary, mirror)
        assert privatepython.python_of(src) == python
        assert privatepython.read_ledger()["runtimes"][src.id]["downloaded_from"] == "localhost"
        assert any("放弃：HTTP 404" in r.getMessage() for r in caplog.records)

    def test_an_unreachable_primary_falls_back_to_the_mirror(self, tmp_path, launches):
        archive, sha, rel = _make(tmp_path, launches)
        with LoopbackServer(tmp_path / "serve", host="localhost") as mirror:
            src = self._with_mirror(_source(None, archive, sha, rel), mirror, archive)
            python = privatepython.provision(src)
            assert mirror.requests == [f"/{archive.name}"]
        assert privatepython.python_of(src) == python
        assert privatepython.read_ledger()["runtimes"][src.id]["downloaded_from"] == "localhost"

    def test_a_primary_abandoned_as_slow_is_retried_when_the_mirror_fails(self, tmp_path, launches):
        """慢总比没有好：镜像也不行（404）时回到因慢被放弃的主地址，**不再测速**地下完。"""
        archive, sha, rel = _make(tmp_path, launches)
        with (
            LoopbackServer(tmp_path / "serve") as primary,
            LoopbackServer(tmp_path / "serve", host="localhost") as mirror,
        ):
            primary.throttle_bps = 64 * 1024
            mirror.mode = "missing"
            src = self._with_mirror(_source(primary, archive, sha, rel), mirror, archive)
            python = privatepython.provision(src)
            assert len(primary.requests) == 2 and len(mirror.requests) == 1
        assert privatepython.python_of(src) == python
        assert privatepython.read_ledger()["runtimes"][src.id]["downloaded_from"] == "127.0.0.1"

    def test_without_a_next_source_a_slow_download_is_not_abandoned(self, tmp_path, launches):
        """只有一个来源时不测速：放弃了也没处可去（旧行为，慢但能下完）。"""
        archive, sha, rel = _make(tmp_path, launches)
        with LoopbackServer(tmp_path / "serve") as primary:
            primary.throttle_bps = 64 * 1024
            src = _source(primary, archive, sha, rel)
            assert src.mirrors == ()
            python = privatepython.provision(src)
            assert primary.requests == [f"/{archive.name}"]
        assert privatepython.python_of(src) == python

    # ---- 信任：镜像的字节照样只认锁里的 sha256
    @pytest.mark.parametrize("primary_mode", ["missing", "slow"])
    def test_a_mirror_serving_tampered_bytes_is_refused_before_any_execution(
        self, tmp_path, launches, caplog, primary_mode
    ):
        caplog.set_level(logging.WARNING, logger="tavotto.privatepython")
        archive, sha, rel = _make(tmp_path, launches)
        with (
            LoopbackServer(tmp_path / "serve") as primary,
            LoopbackServer(tmp_path / "serve", host="localhost") as mirror,
        ):
            if primary_mode == "slow":
                primary.throttle_bps = 64 * 1024
            else:
                primary.mode = "missing"
            mirror.mode = "corrupt"
            src = self._with_mirror(_source(primary, archive, sha, rel), mirror, archive)
            with pytest.raises(privatepython.ProvisionError) as err:
                privatepython.provision(src)
            assert mirror.requests == [f"/{archive.name}"], "判据的前提：确实从镜像拿了字节"
            # hash 不符不换源、不重试：主地址不会因此再被请求一次
            assert len(primary.requests) == 1
        assert err.value.code == privatepython.ERROR_HASH_MISMATCH
        assert err.value.detail["expected"] == sha and err.value.detail["got"] != sha
        assert _launch_count(launches) in (0, None), "篡改的归档：解释器一次都不许起"
        assert _runtime_dirs() == set() and _parts() == []
        assert not privatepython.archive_path(src).exists()
        assert privatepython.python_of(src) is None
        assert privatepython.read_ledger()["runtimes"] == {}
        text = "\n".join(r.getMessage() for r in caplog.records)
        assert "SHA-256 与锁不符（来源 localhost）" in text, text
        assert "供应失败：private_python_hash_mismatch" in text, text

    def test_every_source_offline_is_still_offline_and_logged(self, tmp_path, launches, caplog):
        caplog.set_level(logging.WARNING, logger="tavotto.privatepython")
        archive, sha, rel = _make(tmp_path, launches)
        import dataclasses

        src = dataclasses.replace(
            _source(None, archive, sha, rel), mirrors=(closed_port_url(archive.name),)
        )
        with pytest.raises(privatepython.ProvisionError) as err:
            privatepython.provision(src)
        assert err.value.code == privatepython.ERROR_OFFLINE
        assert _runtime_dirs() == set() and _parts() == []
        msgs = [r.getMessage() for r in caplog.records]
        assert sum("下载第" in m for m in msgs) == 2 * privatepython.DOWNLOAD_ATTEMPTS, msgs
        assert any("供应失败：private_python_offline" in m for m in msgs), msgs


# ================================================================ 真归档（工程验证；nightly 腿）
REAL = os.environ.get("TAVOTTO_PRIVATE_PYTHON_REAL") == "1"


@pytest.mark.skipif(
    not REAL,
    reason="真 pbs 归档要联网 / 要缓存：TAVOTTO_PRIVATE_PYTHON_REAL=1 才跑（nightly 目标腿）",
)
class TestRealArchive:
    def test_real_pbs_runtime_provisions_launches_and_builds_a_venv(self, tmp_path, monkeypatch):
        """宿主目标的真 pbs 归档：下载（或复用 TAVOTTO_PRIVATE_PYTHON_CACHE 里校验过的缓存）→ 校验 →
        真起 → prefix 在私有目录下 → `-m venv` → venv 里有 pip。这是**工程验证**，不是无系统 Python 的资格。"""
        src = privatepython.source_for()
        assert src is not None, "宿主目标不在锁文件里"
        cache = os.environ.get("TAVOTTO_PRIVATE_PYTHON_CACHE")
        if cache and (Path(cache) / src.archive_name).is_file():
            privatepython.downloads_dir().mkdir(parents=True, exist_ok=True)
            shutil.copy2(Path(cache) / src.archive_name, privatepython.archive_path(src))
        # 夹具设的死代理两种拼法都要摘掉，真下载才走得出去
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            monkeypatch.delenv(name, raising=False)
        python = privatepython.provision(src)
        info = json.loads(
            subprocess.run(
                [
                    python,
                    "-I",
                    "-c",
                    "import sys, json, platform; print(json.dumps({'v': platform.python_version(), "
                    "'prefix': sys.prefix, 'exe': sys.executable}))",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=120,
                check=True,
            ).stdout.strip()
        )
        assert info["v"] == src.version
        root = Path(os.path.realpath(privatepython.runtime_dir(src)))
        assert Path(os.path.realpath(info["prefix"])) == root
        assert Path(os.path.realpath(info["exe"])).is_relative_to(root)
        venv = tmp_path / "venv"
        subprocess.run([python, "-m", "venv", str(venv)], check=True, timeout=300)
        vpy = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        out = subprocess.run(
            [str(vpy), "-m", "pip", "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
        assert out.returncode == 0, out.stderr
        assert (
            Path(
                os.path.realpath(
                    subprocess.run(
                        [str(vpy), "-c", "import sys;print(sys.base_prefix)"],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        timeout=120,
                        check=True,
                    ).stdout.strip()
                )
            )
            == root
        )


def test_source_hosts_allowlist_matches_the_shipped_lock():
    """进日志明文的来源主机是固定常量；它与锁里真正会用到的主机（每个目标的主地址 + 推导出的
    镜像地址）逐一相等——锁加了新来源而这里没跟，新主机就只会被哈希（看不出换没换源），反过来
    这里多一个没人用的主机就是白给的明文口子。"""
    from tavotto.engine import privatepython as pp

    lock = pp.load_lock()
    urls = []
    for target in lock["targets"].values():
        urls.append(target["url"])
        urls.extend(pp.mirror_urls(lock, target))
    import urllib.parse

    hosts = {urllib.parse.urlsplit(u).hostname for u in urls}
    assert hosts == set(pp.SOURCE_HOSTS)


def test_an_unexpected_source_host_is_not_logged_in_plain(tmp_path):
    """调用方传进来的来源里若有别的主机（坏锁 / 以后的调用方），它进日志时不是明文——判据是
    固定的主机表，不是「这份来源自己的 url 里有没有它」（那样恒真，#744 Codex P1）。"""
    from tavotto.engine import logsafe, privatepython as pp

    assert isinstance(pp._plain_host("github.com"), logsafe.Plain)
    assert isinstance(pp._plain_host("registry.npmmirror.com"), logsafe.Plain)
    leaked = pp._plain_host("intranet.corp.example")
    assert not isinstance(leaked, logsafe.Plain)
    assert leaked == "intranet.corp.example"  # 原样交回，诊断包按自由文本哈希
    assert pp._plain_host("") == "-"
