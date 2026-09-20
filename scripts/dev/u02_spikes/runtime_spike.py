"""U02 runtime_spike：一条固定的 provisioner 路径（uv）+ 私有完整 CPython 来源（python-build-standalone）
+ 最小 venv + 离线 wheel 安装，全部落在应用私有目录之下；负例：坏 hash 不执行、无授权不联网。

    PYTHONPATH=scripts:src python -m dev.u02_spikes.runtime_spike --out <evidence dir>
        [--data-dir DIR]        # 默认：临时目录（经 TAVOTTO_DATA_DIR → engine.config.data_dir()）
        [--wheelhouse DIR]      # 已有的本地 wheelhouse；缺省时先联网下到私有目录（这一步是「有授权的下载」）
        [--keep]                # 跑完不删私有目录

一个 spike 只选**一条**首轮生产路径（U02 任务书：不同时自制多个 resolver）：

* **provisioner = uv 0.12.17**（PyPI wheel，按平台钉 sha256，从 wheel 里取出 `uv` 二进制；MIT OR Apache-2.0）。
  只用它的两个子命令：`uv venv --python <私有解释器>` 与 `uv pip install --offline --no-index
  --find-links <wheelhouse>`。不用 `uv python install`（那会引入 uv 自己的解释器来源与目录约定）。
* **私有 Python 来源 = python-build-standalone 20260814 · CPython 3.13.15 · install_only**——与
  `packaging/runtime-lock.json` 的 macOS 目标**同一份钉法**（版本 / build / 架构 / URL / sha256 都从锁文件
  读；锁里没有的 Linux / Windows-pbs 目标在本模块补一张同形状的表，只服务 spike）。
* **Windows embeddable**（锁文件的 windows-amd64 目标）在本模块只做**静态检查**：解开 zip 看它有没有
  `venv` / `ensurepip`——「embeddable 与完整 Python 的差别」这一条在非 Windows 机器上能给出的证据
  就到这里；真起 `python.exe -m venv` 的运行时证据要 Windows 目标（CI dispatch 的 windows-latest 腿）。

每一步都写进 report.json（命令、退出码、关键路径、hash），任一步不过退出码 1。
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path

from dev.u02_spikes.hashcheck import (
    HashMismatch,
    OutsidePrivateDir,
    assert_under,
    download_verified,
    sha256_file,
    verify_sha256,
)

# Windows 上 stdout / stderr 被重定向成管道时会退回系统区域编码（cp1252 / cp936），
# 第一句中文就 UnicodeEncodeError；两条流都钉成 UTF-8（tests/test_windows_regressions.py 看护）。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[3]
LOCK = ROOT / "packaging" / "runtime-lock.json"

UV_VERSION = "0.12.17"
UV_LICENSE = "MIT OR Apache-2.0"
#: uv 的 PyPI wheel（按平台钉；来源 https://pypi.org/pypi/uv/0.12.17/json 的 digests，2026-09-20 抄录）。
UV_WHEELS: dict[str, dict] = {
    "macos-arm64": {
        "file": "uv-0.12.17-py3-none-macosx_11_0_arm64.whl",
        "sha256": "c33d2fb4fb407678e2da3907376e7f5a7a38ae11e53d608bb7be70c1ecb6f223",
    },
    "linux-x86_64": {
        "file": "uv-0.12.17-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl",
        "sha256": "9e25bb39e1674799c408345a6397ebc2c7c719d498be0ce9d935466d36ceacf5",
    },
    "linux-arm64": {
        "file": "uv-0.12.17-py3-none-manylinux_2_17_aarch64.manylinux2014_aarch64.musllinux_1_1_aarch64.whl",
        "sha256": "eea6e1b0eb66d7f058312239a6863dd32ee0dd4da7fc08626ac85468e3d7bca6",
    },
    "windows-amd64": {
        "file": "uv-0.12.17-py3-none-win_amd64.whl",
        "sha256": "7a14aafc5d5816cebfb8b61b8529fe6f9b99210363523fe2a9d441e757bd3cb7",
    },
}
PYPI_UV = "https://files.pythonhosted.org/packages/py3/u/uv/"

PBS_RELEASE = "20260814"
PBS_BASE = f"https://github.com/astral-sh/python-build-standalone/releases/download/{PBS_RELEASE}/"
#: 锁文件之外的 pbs 目标（只服务 spike；sha256 来自该 release 的 SHA256SUMS，2026-09-20 抄录）。
PBS_EXTRA: dict[str, dict] = {
    "linux-x86_64": {
        "triple": "x86_64-unknown-linux-gnu",
        "sha256": "45816a2653b47a6cc48d8ada4ea1185758a4c2db389d012b31e0205e5ccb548b",
    },
    "linux-arm64": {
        "triple": "aarch64-unknown-linux-gnu",
        "sha256": "303efcce34b86fd8b0d8a260327dbf8d0d4fba6d2d77b2bca311e8bbd19265e1",
    },
    "windows-amd64-pbs": {
        "triple": "x86_64-pc-windows-msvc",
        "sha256": "4ca61e4b09c2240cc50cc6910c90664051e93ab7caa2f48b3c6b3c070670c0bd",
    },
}

#: 离线安装用的三个小包（与 tests/fixtures/foundation/dependency_declarations 同一组；纯 Python wheel）。
WHEELS: list[dict] = [
    {
        "name": "six",
        "version": "1.17.0",
        "file": "six-1.17.0-py2.py3-none-any.whl",
        "sha256": "4721f391ed90541fddacab5acf947aa0d3dc7d27b2e1e8eda2be8970586c3274",
        "url": "https://files.pythonhosted.org/packages/b7/ce/149a00dd41f10bc29e5921b496af8b574d8413afcd5e30dfa0ed46c2cc5e/six-1.17.0-py2.py3-none-any.whl",
    },
    {
        "name": "tabulate",
        "version": "0.9.0",
        "file": "tabulate-0.9.0-py3-none-any.whl",
        "sha256": "024ca478df22e9340661486f85298cff5f6dcdba14f3813e8830015b9ed1948f",
        "url": "https://files.pythonhosted.org/packages/40/44/4a5f08c96eb108af5cb50b41f76142f0afa346dfa99d5296fe7202a11854/tabulate-0.9.0-py3-none-any.whl",
    },
    {
        "name": "sortedcontainers",
        "version": "2.4.0",
        "file": "sortedcontainers-2.4.0-py2.py3-none-any.whl",
        "sha256": "a163dcaede0f1c021485e957a39245190e74249897e2ae4b2aa38595db237ee0",
        "url": "https://files.pythonhosted.org/packages/32/46/9cb0e58b2deb7f82b84065f37f3bffeb12413f947f9388e4cac22c4621ce/sortedcontainers-2.4.0-py2.py3-none-any.whl",
    },
]

#: 死代理：任何联网尝试都会立刻 connection refused。它是「无授权不联网」的对照组的一半——
#: 另一半是证明它真能挡住一次故意的联网（见 step `network_blocked_control`）。
DEAD_PROXY = "http://127.0.0.1:9"


class SpikeFailure(Exception):
    pass


def host_target() -> str:
    m = platform.machine().lower()
    arch = "arm64" if m in ("arm64", "aarch64") else ("x86_64" if m in ("x86_64", "amd64") else m)
    if sys.platform == "darwin":
        return f"macos-{arch}"
    if sys.platform.startswith("linux"):
        return f"linux-{arch}"
    if sys.platform == "win32":
        return "windows-amd64"
    raise SpikeFailure(f"不认识的平台 {sys.platform}/{m}")


def python_source(target: str, lock: dict) -> dict:
    """{url, sha256, kind, archive_root, python_rel}：锁文件优先，spike 表补 Linux 与 Windows-pbs。"""
    if target in lock["targets"] and lock["targets"][target]["kind"] == "macos-standalone":
        t = lock["targets"][target]["python"]
        return {
            "source": "packaging/runtime-lock.json",
            "url": t["url"],
            "sha256": t["sha256"],
            "kind": "pbs-install_only",
            "triple": t["triple"],
            "python_rel": "bin/python3",
        }
    key = "windows-amd64-pbs" if target == "windows-amd64" else target
    if key in PBS_EXTRA:
        e = PBS_EXTRA[key]
        return {
            "source": "dev/u02_spikes/runtime_spike.PBS_EXTRA",
            "url": f"{PBS_BASE}cpython-3.13.15%2B{PBS_RELEASE}-{e['triple']}-install_only.tar.gz",
            "sha256": e["sha256"],
            "kind": "pbs-install_only",
            "triple": e["triple"],
            "python_rel": "python.exe" if key.startswith("windows") else "bin/python3",
        }
    raise SpikeFailure(f"没有 {target} 的私有 Python 来源")


# ---------------------------------------------------------------------------
class Spike:
    def __init__(self, out: Path, data_dir: Path, home: Path, wheelhouse: Path | None, target: str):
        self.out = out
        self.data_dir = data_dir
        self.home = home
        self.wheelhouse_arg = wheelhouse
        self.target = target
        self.steps: list[dict] = []
        self.lock = json.loads(LOCK.read_text(encoding="utf-8"))
        self.interpreter_executions = 0  # 坏 hash 负例要证明这个计数没动

    # -- 记账 -----------------------------------------------------------
    def step(self, name: str, ok: bool, **detail) -> None:
        self.steps.append({"step": name, "ok": ok, **detail})
        print(("PASS " if ok else "FAIL ") + name)
        if not ok:
            print("     " + json.dumps(detail, ensure_ascii=False, default=str)[:600])

    def run(
        self,
        argv: list[str],
        *,
        env: dict | None = None,
        cwd: Path | None = None,
        timeout: float = 600,
    ) -> subprocess.CompletedProcess:
        base = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(self.home),
            "USERPROFILE": str(self.home),
            "TMPDIR": os.environ.get("TMPDIR", ""),
            "TEMP": os.environ.get("TEMP", ""),
            "TMP": os.environ.get("TMP", ""),
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
            "UV_CACHE_DIR": str(self.data_dir / "uv-cache"),
            "UV_NO_CONFIG": "1",
            "UV_NO_ENV_FILE": "1",
            "UV_PYTHON_PREFERENCE": "only-system",
            "UV_NO_PYTHON_DOWNLOADS": "1",
            "UV_NO_PROGRESS": "1",
            "PYTHONNOUSERSITE": "1",
        }
        base = {k: v for k, v in base.items() if v}
        if env:
            base.update(env)
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=base,
            cwd=str(cwd or self.data_dir),
            timeout=timeout,
        )

    # -- 1. provisioner --------------------------------------------------
    def fetch_uv(self) -> Path:
        spec = UV_WHEELS[self.target]
        wheel = download_verified(
            PYPI_UV + spec["file"], self.data_dir / "downloads" / spec["file"], spec["sha256"]
        )
        uv_dir = self.data_dir / "tools" / f"uv-{UV_VERSION}"
        exe = "uv.exe" if self.target.startswith("windows") else "uv"
        member = f"uv-{UV_VERSION}.data/scripts/{exe}"
        uv_dir.mkdir(parents=True, exist_ok=True)
        target = uv_dir / exe
        with zipfile.ZipFile(wheel) as zf:
            with zf.open(member) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            licenses = [n for n in zf.namelist() if "/licenses/" in n]
        target.chmod(0o755)
        res = self.run([str(target), "--version"])
        self.step(
            "provisioner.uv_from_pinned_wheel",
            res.returncode == 0 and UV_VERSION in res.stdout,
            wheel=spec["file"],
            sha256=spec["sha256"],
            license=UV_LICENSE,
            license_files_in_wheel=licenses,
            version_output=res.stdout.strip(),
        )
        return target

    # -- 2. 私有 Python --------------------------------------------------
    def provision_python(
        self,
        src: dict,
        *,
        archive_override: Path | None = None,
        expected_override: str | None = None,
        name: str = "cpython-3.13.15",
    ) -> Path:
        """下载/校验 → 解到 staging → 真起一次 → 原子改名到最终目录 → 写 active 指针。
        任何一步失败：staging 删掉、最终目录不存在、`interpreter_executions` 不增。"""
        runtimes = self.data_dir / "runtimes"
        final = runtimes / name
        staging = runtimes / f".staging-{name}-{os.getpid()}"
        expected = expected_override or src["sha256"]
        if archive_override is not None:
            archive = verify_sha256(archive_override, expected)  # 抛 HashMismatch 就到不了下面
        else:
            archive = download_verified(
                src["url"],
                self.data_dir / "downloads" / Path(src["url"]).name.replace("%2B", "+"),
                expected,
            )
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        try:
            with tarfile.open(archive, "r:gz") as tar:
                members = tar.getmembers()
                root = members[0].name.split("/")[0]
                tar.extractall(staging, filter="data")
            extracted = staging / root
            python = extracted / src["python_rel"]
            assert_under(python, self.data_dir)
            self.interpreter_executions += 1
            res = self.run(
                [
                    str(python),
                    "-I",
                    "-c",
                    "import sys, json; print(json.dumps({'version': sys.version, 'prefix': sys.prefix, 'executable': sys.executable}))",
                ]
            )
            if res.returncode != 0:
                raise SpikeFailure(f"私有解释器起不来：{res.stderr[-400:]}")
            if final.exists():
                shutil.rmtree(final)
            os.replace(extracted, final)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        (runtimes / "active.json").write_text(
            json.dumps({"active": name, "sha256": expected, "url": src["url"]}, indent=1) + "\n",
            encoding="utf-8",
        )
        return final / src["python_rel"]

    # -- 3. wheelhouse（有授权的联网只在这一步） ----------------------------
    def build_wheelhouse(self) -> Path:
        if self.wheelhouse_arg:
            wh = self.wheelhouse_arg
            for w in WHEELS:
                verify_sha256(wh / w["file"], w["sha256"])
            self.step("wheelhouse.reused_and_verified", True, dir=str(wh))
            return wh
        wh = self.data_dir / "wheelhouse"
        for w in WHEELS:
            download_verified(w["url"], wh / w["file"], w["sha256"])
        self.step(
            "wheelhouse.downloaded_with_pinned_hashes",
            True,
            dir=str(wh),
            wheels=[w["file"] for w in WHEELS],
        )
        return wh

    # -- 4. venv + 离线安装 ----------------------------------------------
    def venv_and_install(self, uv: Path, python: Path, wheelhouse: Path) -> Path:
        venv = self.data_dir / "envs" / "spike-venv"
        res = self.run(
            [
                str(uv),
                "venv",
                "--python",
                str(python),
                "--no-python-downloads",
                "--quiet",
                str(venv),
            ]
        )
        vpy = venv / ("Scripts/python.exe" if self.target.startswith("windows") else "bin/python")
        self.step(
            "venv.created_from_private_python",
            res.returncode == 0 and vpy.exists(),
            venv=str(venv),
            stderr=res.stderr[-600:],
        )
        offline = {
            "HTTP_PROXY": DEAD_PROXY,
            "HTTPS_PROXY": DEAD_PROXY,
            "ALL_PROXY": DEAD_PROXY,
            "UV_OFFLINE": "1",
        }
        req = self.data_dir / "requirements-offline.txt"
        req.write_text(
            "".join(f"{w['name']}=={w['version']} --hash=sha256:{w['sha256']}\n" for w in WHEELS),
            encoding="utf-8",
        )
        t0 = time.perf_counter()
        res = self.run(
            [
                str(uv),
                "pip",
                "install",
                "--python",
                str(vpy),
                "--offline",
                "--no-index",
                "--find-links",
                str(wheelhouse),
                "--require-hashes",
                "-r",
                str(req),
            ],
            env=offline,
        )
        self.step(
            "install.offline_from_wheelhouse_with_dead_proxy",
            res.returncode == 0,
            seconds=round(time.perf_counter() - t0, 2),
            stderr=res.stderr[-800:],
        )
        # 负例 1：wheelhouse 空 → 必须失败（证明刚才装的确实来自 wheelhouse，而不是别的什么缓存 / 网络）
        empty = self.data_dir / "empty-wheelhouse"
        empty.mkdir(exist_ok=True)
        venv2 = self.data_dir / "envs" / "spike-venv-empty"
        self.run(
            [
                str(uv),
                "venv",
                "--python",
                str(python),
                "--no-python-downloads",
                "--quiet",
                str(venv2),
            ]
        )
        vpy2 = venv2 / ("Scripts/python.exe" if self.target.startswith("windows") else "bin/python")
        res = self.run(
            [
                str(uv),
                "pip",
                "install",
                "--python",
                str(vpy2),
                "--offline",
                "--no-index",
                "--find-links",
                str(empty),
                "--require-hashes",
                "-r",
                str(req),
            ],
            env={**offline, "UV_CACHE_DIR": str(self.data_dir / "uv-cache-empty")},
        )
        self.step(
            "install.negative_empty_wheelhouse_fails",
            res.returncode != 0,
            returncode=res.returncode,
            stderr=res.stderr[-400:],
        )
        # 负例 2：不带 --offline、想装一个 wheelhouse 里没有的包 → 死代理必须把它挡住（证明代理真在挡网）
        t0 = time.perf_counter()
        res = self.run(
            [str(uv), "pip", "install", "--python", str(vpy2), "requests==2.32.5"],
            env={
                "HTTP_PROXY": DEAD_PROXY,
                "HTTPS_PROXY": DEAD_PROXY,
                "ALL_PROXY": DEAD_PROXY,
                "UV_CACHE_DIR": str(self.data_dir / "uv-cache-empty"),
            },
            timeout=120,
        )
        self.step(
            "install.network_blocked_control",
            res.returncode != 0 and "requests" not in self._site_packages_names(vpy2),
            returncode=res.returncode,
            seconds=round(time.perf_counter() - t0, 2),
            stderr=res.stderr[-400:],
        )
        return vpy

    def _site_packages_names(self, vpy: Path) -> list[str]:
        res = self.run(
            [
                str(vpy),
                "-c",
                "import importlib.metadata as m, json; print(json.dumps(sorted({d.metadata['Name'].lower() for d in m.distributions()})))",
            ]
        )
        return json.loads(res.stdout) if res.returncode == 0 else []

    # -- 5. 核验 ----------------------------------------------------------
    def verify_venv(self, vpy: Path, private_python: Path) -> None:
        res = self.run(
            [
                str(vpy),
                "-c",
                "import sys, json, six, tabulate, sortedcontainers;"
                "print(json.dumps({'prefix': sys.prefix, 'base_prefix': sys.base_prefix, 'executable': sys.executable,"
                " 'path': sys.path, 'six': six.__version__, 'tabulate': tabulate.__version__, 'sortedcontainers': sortedcontainers.__version__,"
                " 'version': sys.version}))",
            ]
        )
        info = json.loads(res.stdout) if res.returncode == 0 else {}
        under = True
        why = []
        for key in ("prefix", "base_prefix", "executable"):
            try:
                assert_under(Path(info[key]), self.data_dir)
            except (OutsidePrivateDir, KeyError) as exc:
                under = False
                why.append(str(exc))
        for p in info.get("path", []):
            if not p:
                continue
            try:
                assert_under(Path(p), self.data_dir)
            except OutsidePrivateDir as exc:
                under = False
                why.append(str(exc))
        self.step(
            "verify.imports_and_everything_under_private_dir",
            res.returncode == 0
            and under
            and info.get("six") == "1.17.0"
            and info.get("tabulate") == "0.9.0"
            and info.get("sortedcontainers") == "2.4.0"
            and info.get("version", "").startswith("3.13.15"),
            info=info,
            why=why,
        )
        # base_prefix 指向私有解释器所在的 runtime（不是系统的）
        self.step(
            "verify.base_prefix_is_the_private_runtime",
            Path(os.path.realpath(info.get("base_prefix", "/nonexistent")))
            == Path(
                os.path.realpath(
                    private_python.parent.parent
                    if private_python.name != "python.exe"
                    else private_python.parent
                )
            ),
            base_prefix=info.get("base_prefix"),
            private_python=str(private_python),
        )

    # -- 6. 负例：坏 hash ---------------------------------------------------
    def bad_hash_negatives(self, src: dict) -> None:
        archive = self.data_dir / "downloads" / Path(src["url"]).name.replace("%2B", "+")
        tampered = self.data_dir / "downloads" / "tampered.tar.gz"
        data = bytearray(archive.read_bytes())
        data[len(data) // 2] ^= 0xFF
        tampered.write_bytes(bytes(data))
        before = self.interpreter_executions
        runtimes = self.data_dir / "runtimes"
        snapshot = sorted(p.name for p in runtimes.iterdir()) if runtimes.exists() else []
        for label, kwargs in (
            ("tampered_archive", {"archive_override": tampered}),
            ("wrong_expected_hash", {"archive_override": archive, "expected_override": "0" * 64}),
        ):
            try:
                self.provision_python(src, name=f"negative-{label}", **kwargs)
                ok, err = False, "没有抛 HashMismatch"
            except HashMismatch as exc:
                ok, err = True, str(exc).splitlines()[0]
            after = sorted(p.name for p in runtimes.iterdir()) if runtimes.exists() else []
            self.step(
                f"negative.{label}_refused_before_any_execution",
                ok
                and self.interpreter_executions == before
                and after == snapshot
                and not (runtimes / f"negative-{label}").exists(),
                error=err,
                interpreter_executions_before=before,
                interpreter_executions_after=self.interpreter_executions,
                runtimes_dir_unchanged=after == snapshot,
            )

    # -- 7. embeddable 静态检查 ----------------------------------------------
    def inspect_embeddable(self) -> None:
        t = self.lock["targets"]["windows-amd64"]
        if t["kind"] != "windows-embeddable":
            self.step("embeddable.inspect", False, why="锁文件的 windows-amd64 不是 embeddable 了")
            return
        zpath = download_verified(
            t["python"]["url"],
            self.data_dir / "downloads" / Path(t["python"]["url"]).name,
            t["python"]["sha256"],
        )
        with zipfile.ZipFile(zpath) as zf:
            names = zf.namelist()
            stdlib_zip = t["python"]["stdlib_zip"]
            with zf.open(stdlib_zip) as inner:
                inner_names = zipfile.ZipFile(inner).namelist()
        has_pth = t["python"]["pth"] in names
        has_venv = any(n.startswith("venv/") for n in inner_names) or any(
            n.startswith("Lib/venv/") for n in names
        )
        has_ensurepip = any(n.startswith("ensurepip/") for n in inner_names)
        has_tkinter = any(n.startswith("tkinter/") for n in inner_names)
        self.step(
            "embeddable.static_inspection",
            has_pth and not has_venv and not has_ensurepip,
            archive=Path(t["python"]["url"]).name,
            sha256=t["python"]["sha256"],
            has_pth=has_pth,
            has_venv_module=has_venv,
            has_ensurepip=has_ensurepip,
            has_tkinter=has_tkinter,
            stdlib_entries=len(inner_names),
            meaning="embeddable 没有 venv/ensurepip：`python.exe -m venv` 在它上面注定失败；完整 Python（pbs install_only）才有。运行时证据要 Windows 目标。",
        )
        if self.target == "windows-amd64":
            # 真在 Windows 上：解开 embeddable 试一次 -m venv（预期失败）
            emb = self.data_dir / "runtimes" / "embeddable"
            with zipfile.ZipFile(zpath) as zf:
                zf.extractall(emb)
            res = self.run(
                [
                    str(emb / "python.exe"),
                    "-m",
                    "venv",
                    str(self.data_dir / "envs" / "from-embeddable"),
                ]
            )
            self.step(
                "embeddable.python_m_venv_fails_at_runtime",
                res.returncode != 0,
                returncode=res.returncode,
                stderr=res.stderr[-300:],
                stdout=res.stdout[-300:],
            )

    # -- 8. 系统未被触碰 --------------------------------------------------
    def isolation(self, path_before: str, home_before: set[str]) -> None:
        home_after = {str(p.relative_to(self.home)) for p in self.home.rglob("*")}
        self.step(
            "isolation.home_untouched_and_path_unchanged",
            home_after == home_before and os.environ.get("PATH", "") == path_before,
            new_files_in_home=sorted(home_after - home_before)[:20],
            path_unchanged=os.environ.get("PATH", "") == path_before,
        )
        written = [
            str(p.relative_to(self.data_dir))
            for p in self.data_dir.rglob("*")
            if p.is_dir() and p.parent == self.data_dir
        ]
        self.step(
            "isolation.everything_under_data_dir",
            all((self.data_dir / w).exists() for w in written),
            top_level_dirs=sorted(written),
        )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--data-dir", type=Path, default=None)
    ap.add_argument("--wheelhouse", type=Path, default=None)
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--skip-embeddable", action="store_true", help="不下载 embeddable zip（11 MB）")
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)

    tmp = Path(tempfile.mkdtemp(prefix="u02-runtime-"))
    home = tmp / "home"
    home.mkdir()
    if args.data_dir:
        data_dir = args.data_dir
    else:
        os.environ["TAVOTTO_DATA_DIR"] = str(tmp / "data")
        from tavotto.engine import config

        data_dir = config.data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    target = host_target()
    spike = Spike(args.out, data_dir, home, args.wheelhouse, target)
    path_before = os.environ.get("PATH", "")
    home_before = {str(p.relative_to(home)) for p in home.rglob("*")}
    src = python_source(target, spike.lock)
    spike.step(
        "target.resolved",
        True,
        target=target,
        python_source=src,
        data_dir=str(data_dir),
        via="engine.config.data_dir()" if not args.data_dir else "--data-dir",
    )
    ok = True
    try:
        uv = spike.fetch_uv()
        python = spike.provision_python(src)
        spike.step(
            "python.provisioned_atomically",
            python.exists(),
            python=str(python),
            active=json.loads((data_dir / "runtimes" / "active.json").read_text(encoding="utf-8")),
        )
        wh = spike.build_wheelhouse()
        vpy = spike.venv_and_install(uv, python, wh)
        spike.verify_venv(vpy, python)
        spike.bad_hash_negatives(src)
        if not args.skip_embeddable:
            spike.inspect_embeddable()
        spike.isolation(path_before, home_before)
    except (SpikeFailure, HashMismatch, OutsidePrivateDir, subprocess.TimeoutExpired) as exc:
        spike.step("aborted", False, error=f"{type(exc).__name__}: {exc}")
        ok = False
    ok = ok and all(s["ok"] for s in spike.steps)
    report = {
        "platform": {
            "python": platform.python_version(),
            "system": platform.system(),
            "machine": platform.machine(),
            "release": platform.release(),
        },
        "target": target,
        "provisioner": {
            "name": "uv",
            "version": UV_VERSION,
            "license": UV_LICENSE,
            "wheel": UV_WHEELS[target],
        },
        "python_source": src,
        "wheels": WHEELS,
        "lock_file": {"path": "packaging/runtime-lock.json", "sha256": sha256_file(LOCK)},
        "steps": spike.steps,
        "all_ok": ok,
    }
    (args.out / f"report-{target}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1, default=str) + "\n", encoding="utf-8"
    )
    print(
        f"{'ALL OK' if ok else 'FAILED'}: {sum(s['ok'] for s in spike.steps)}/{len(spike.steps)} → {args.out / f'report-{target}.json'}"
    )
    if not args.keep:
        shutil.rmtree(tmp, ignore_errors=True)
    else:
        print(f"kept: {tmp}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
