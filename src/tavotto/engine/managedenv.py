"""Tavotto 替某个项目管的隔离 Python 环境（Compatibility Bridge Session 7B）。

用户脚本缺依赖时有两个可能的安装目标，本模块是**第二个**：

    A. 项目自己的 `.venv`          —— 改的是**用户的**环境，必须明确确认
    B. Tavotto 管理的项目环境      —— 改的是**我们的**东西，可删可重建 ← 本模块

它存在的理由只有一个：项目**没有**可用的 `.venv`（或者用户不愿意让我们动他
那一个）时，仍然要有一条「点一下就能继续」的路，而这条路不能以污染内置
runtime 为代价。

    <data_dir>/environments/<项目指纹>/
        venv/                 真正的虚拟环境
        environment.json      我们自己记的账（谁建的、装过什么、什么时候）

三条纪律：

* **每个项目一个**。`environments/<项目指纹>/` 而不是一个全局的
  `worker-env/`——后者会慢慢变成所有科研项目共享的依赖垃圾桶：A 项目要
  numpy 1.x、B 项目要 numpy 2.x，共用一个环境时后装的那个把先装的顶掉，
  症状是「昨天还好好的图今天画不出来了」。`bootstrap.py` 的那个全局
  `worker-env/` 是**另一件事**（「这台机器上一个科学栈都没有」的兜底），
  两者刻意不合并。
* **绝不建在用户项目里**。`<项目>/.tavotto-venv/` 会进他的 git、会被同步到
  别的机器、会在他 `rm -rf` 项目时一起消失又悄悄重建。我们自己的东西放在
  自己的数据目录（`engine/config.data_dir()`，仓库级不变量）。
* **可删可重建**。这是它相对「改用户 .venv」的**唯一优势**，所以
  `environment.json` 必须如实记下我们装过什么——重建时照着装回去。
  但**不声称 lockfile 级复现**：某个版本从 index 消失时如实报错。

纯标准库（被 `engine/deprepair.py` import）。设计见
`docs/adr/0019-controlled-dependency-repair.md`。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import config, runtime

LOG = logging.getLogger("tavotto.managedenv")

#: `environment.json` 的 schema。加可选字段不升；改语义/删字段才升。
#: 读到更新的 schema 一律当作「这个环境不是这一版建的」→ 拒绝复用、提示重建，
#: 而不是硬着头皮往下跑。
SCHEMA = 1

MANIFEST_NAME = "environment.json"
ENVIRONMENTS_DIRNAME = "environments"
VENV_DIRNAME = "venv"

#: 环境状态。`incomplete` = 建到一半被取消/失败，**下次不直接复用**。
STATE_READY = "ready"
STATE_INCOMPLETE = "incomplete"

#: 建 venv / 装基础栈的超时。首次要下载几十 MB 的 matplotlib+numpy。
CREATE_TIMEOUT_S = 900
VENV_TIMEOUT_S = 300

#: 新环境的基础科学栈。**只有 matplotlib**——numpy 由它自己带进来，
#: 我们不写第二份依赖清单（写了就会与 matplotlib 的真实依赖漂移）。
#: worker 侧真正 import 的只有 matplotlib / numpy / 标准库
#: （`worker.py` / `figcapture.py` / `manifest.py` / `overrides.py` /
#: `pathgeom.py` 逐个查过），pandas / scipy / seaborn **不装**——
#: 用户脚本真要用时会走同一条 missing_dependency 修复路。
BASE_PACKAGES = ("matplotlib",)

_lock = threading.RLock()


# --------------------------------------------------------------- 位置与身份
def project_fingerprint(project: str | Path) -> str:
    """项目 → 稳定的短指纹（目录名用）。

    **按 `config.normalize_path_identity` 归一后取 sha256 前 16 位**：与
    `app._project_id()` / `pool._norm_dir()` 同一份大小写判据，否则同一个项目
    用不同大小写打开两次会拿到两个环境（各装各的，用户看到的是「装过了怎么
    还缺」）。用 hash 而不是路径本身做目录名，顺带让数据目录里不再出现用户
    的课题名。
    """
    text = config.normalize_path_identity(os.path.abspath(os.fspath(project)))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def env_dir(project: str | Path) -> Path:
    """`<data_dir>/environments/<项目指纹>/`。"""
    return config.data_path(ENVIRONMENTS_DIRNAME, project_fingerprint(project))


def venv_dir(project: str | Path) -> Path:
    return env_dir(project) / VENV_DIRNAME


def venv_python(project: str | Path) -> Path:
    """受管环境里的解释器路径（**不保证存在**）。"""
    base = venv_dir(project)
    return base / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def manifest_path(project: str | Path) -> Path:
    return env_dir(project) / MANIFEST_NAME


def base_interpreter_fingerprint(python: str) -> str:
    """基础解释器的身份指纹——**不是路径**。

    记路径会把用户主目录名带进 `environment.json`（那份文件会被诊断包读到）。
    我们只需要回答「基础解释器换过没有」，一个指纹就够。
    """
    try:
        real = os.path.realpath(str(python))
    except OSError:
        real = str(python)
    return hashlib.sha256(real.encode("utf-8", "replace")).hexdigest()[:16]


# --------------------------------------------------------------- manifest
def read_manifest(project: str | Path) -> dict | None:
    """读 `environment.json`；不存在/损坏/schema 更新一律回 None。"""
    try:
        data = json.loads(manifest_path(project).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return None
    if not data.get("created_by_tavotto"):
        # 不是我们建的东西一律不认——这个目录在**我们自己的**数据目录下，
        # 但「路径对」不等于「是我们建的」（用户可能手动放了点什么进去）。
        return None
    return data


def write_manifest(project: str | Path, data: dict) -> None:
    path = manifest_path(project)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        LOG.warning("受管环境 manifest 写入失败: %s", exc)


def new_manifest(project: str | Path, base_python: str) -> dict:
    return {
        "schema": SCHEMA,
        "created_by_tavotto": True,
        "project_id": project_fingerprint(project),
        "python_version": "",
        "base_interpreter_fingerprint": base_interpreter_fingerprint(base_python),
        "created_at": int(time.time()),
        "last_used": int(time.time()),
        "state": STATE_INCOMPLETE,
        "installed_by_tavotto": [],
    }


def update_manifest(project: str | Path, **fields) -> dict:
    with _lock:
        data = read_manifest(project) or {}
        data.update(fields)
        if data:
            write_manifest(project, data)
        return data


def mark_incomplete(project: str | Path, reason: str = "") -> None:
    """把环境标成「建到一半」——**下次不直接复用，先重建**。

    取消一次 pip install 之后，site-packages 里可能留着半个包。对**我们自己
    管的**环境，最安全的处置就是不再假装它是干净的。
    """
    update_manifest(project, state=STATE_INCOMPLETE, incomplete_reason=str(reason)[:200])


def mark_ready(project: str | Path) -> None:
    update_manifest(project, state=STATE_READY, incomplete_reason="", last_used=int(time.time()))


def touch(project: str | Path) -> None:
    update_manifest(project, last_used=int(time.time()))


#: 「为什么装的」——两档。`missing_dependency` 是脚本缺包时一键修复装上的，
#: `user_requested` 是用户在设置 → 包管理里自己点装的。界面按它标来源，
#: 重建时两档都装回去（它们都是「Tavotto 往这个环境里装过的」）。
REASON_MISSING_DEPENDENCY = "missing_dependency"
REASON_USER_REQUESTED = "user_requested"


def _same_distribution(a: str, b: str) -> bool:
    """PEP 503 规范化之后相等才算同一个包（`Scikit_Learn` == `scikit-learn`）。

    判据与 `depresolve.normalize_distribution` 同一条；这里内联一份最小实现
    是为了不让本模块 import depresolve（它被 deprepair import，方向要单向）。
    """
    import re

    norm = lambda n: re.sub(r"[-_.]+", "-", str(n or "")).lower()  # noqa: E731
    return norm(a) == norm(b)


def record_install(
    project: str | Path,
    *,
    import_name: str,
    distribution: str,
    requested_specifier: str,
    resolved_version: str,
    reason: str,
) -> None:
    """记一笔「Tavotto 往这个环境里装过什么」。

    重建时照着这份装回去。**同一个 distribution 只留最后一笔**：装过两次的
    是同一个包的两个版本，不是两个包。
    """
    with _lock:
        data = read_manifest(project)
        if not data:
            return
        entries = [
            e
            for e in (data.get("installed_by_tavotto") or [])
            if isinstance(e, dict) and not _same_distribution(e.get("distribution"), distribution)
        ]
        entries.append(
            {
                "import_name": import_name,
                "distribution": distribution,
                "requested_specifier": requested_specifier,
                "resolved_version": resolved_version,
                "reason": reason,
                "at": int(time.time()),
            }
        )
        data["installed_by_tavotto"] = entries[-64:]
        write_manifest(project, data)


def forget_install(project: str | Path, distribution: str) -> bool:
    """用户卸载了它：从账上划掉，重建时不再装回去。回「账上原来有没有」。"""
    with _lock:
        data = read_manifest(project)
        if not data:
            return False
        before = [e for e in (data.get("installed_by_tavotto") or []) if isinstance(e, dict)]
        after = [e for e in before if not _same_distribution(e.get("distribution"), distribution)]
        if len(after) == len(before):
            return False
        data["installed_by_tavotto"] = after
        write_manifest(project, data)
        return True


def installed_entry(project: str | Path, distribution: str) -> dict | None:
    """账上关于这个包的那一笔（没有回 None）。"""
    data = read_manifest(project) or {}
    for e in data.get("installed_by_tavotto") or []:
        if isinstance(e, dict) and _same_distribution(e.get("distribution"), distribution):
            return dict(e)
    return None


# --------------------------------------------------------------- 快照
#: 每次改动环境前后各记一份 `pip freeze`，留给「装坏了要修」的时候对照。
#: **不是回滚机制**（ADR 0019 §八：pip 没有事务），只是修复时的证据；
#: 最多留这么多份，旧的滚掉。
SNAPSHOT_DIRNAME = "snapshots"
SNAPSHOT_KEEP = 12


def snapshot_dir(project: str | Path) -> Path:
    return env_dir(project) / SNAPSHOT_DIRNAME


def record_snapshot(project: str | Path, label: str, text: str) -> Path | None:
    """把一份（已脱敏的）freeze 文本落到 `snapshots/<时间>-<label>.txt`。

    写不进去只记日志：快照是修复时的佐证，不该让一次安装因为它失败。
    """
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in str(label or "snapshot"))[
        :40
    ]
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    path = snapshot_dir(project) / f"{stamp}-{safe}.txt"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(str(text or ""), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        LOG.warning("环境快照写入失败: %s", exc)
        return None
    _prune_snapshots(project)
    return path


def _prune_snapshots(project: str | Path) -> None:
    try:
        files = sorted(p for p in snapshot_dir(project).glob("*.txt"))
    except OSError:
        return
    for stale in files[:-SNAPSHOT_KEEP]:
        try:
            stale.unlink()
        except OSError:
            pass


def list_snapshots(project: str | Path) -> list[str]:
    """快照文件名（不含路径——诊断包读它，路径里有数据目录）。"""
    try:
        return sorted(p.name for p in snapshot_dir(project).glob("*.txt"))
    except OSError:
        return []


# --------------------------------------------------------------- 状态查询
def python_of(project: str | Path) -> str | None:
    """这个项目的受管环境**现在可用**吗——可用回解释器路径，否则 None。

    三个条件缺一不可：manifest 认得出、状态是 ready、解释器文件真的在。
    「目录还在」不算数：用户清过数据目录、磁盘满了写坏过、上一次建到一半
    被取消，都会留下一个形状对但用不了的目录。
    """
    data = read_manifest(project)
    if not data or data.get("state") != STATE_READY:
        return None
    python = venv_python(project)
    try:
        return str(python) if python.is_file() else None
    except OSError:
        return None


def state(project: str | Path) -> dict:
    """受管环境的对外视图（环境状态 API / 诊断包读它）。

    **不做任何体检**（那要起子进程）：只把已经记下的事实交出去。
    """
    data = read_manifest(project)
    if not data:
        return {
            "exists": False,
            "state": "",
            "python_version": "",
            "installed": [],
            "created_at": 0,
        }
    return {
        "exists": python_of(project) is not None,
        "state": data.get("state", ""),
        "python_version": data.get("python_version", ""),
        "created_at": int(data.get("created_at") or 0),
        "last_used": int(data.get("last_used") or 0),
        # 只出包名与版本：requested_specifier / reason 是本地账，
        # 诊断包不需要它们（脱敏原则：能少给就少给）。
        "installed": [
            {
                "distribution": e.get("distribution", ""),
                "resolved_version": e.get("resolved_version", ""),
                # 来源是个两值枚举（缺包修复 / 用户自己装的），不是用户内容
                "reason": e.get("reason", "") or REASON_MISSING_DEPENDENCY,
            }
            for e in (data.get("installed_by_tavotto") or [])
            if isinstance(e, dict)
        ],
    }


def installed_requirements(project: str | Path) -> list[str]:
    """重建时要装回去的那些（`distribution==版本`，没版本就只给包名）。"""
    data = read_manifest(project) or {}
    out: list[str] = []
    for entry in data.get("installed_by_tavotto") or []:
        if not isinstance(entry, dict):
            continue
        dist = str(entry.get("distribution") or "")
        if not dist:
            continue
        ver = str(entry.get("resolved_version") or "")
        out.append(f"{dist}=={ver}" if ver else dist)
    return out


def remove(project: str | Path) -> bool:
    """删掉整个受管环境（重建前、用户主动清理时）。

    **只删我们自己数据目录下的那一份**，而且删之前确认 manifest 认得出它是
    我们建的——`shutil.rmtree` 是不可逆的，多问一句不亏。
    """
    root = env_dir(project)
    if read_manifest(project) is None and not (root / VENV_DIRNAME).exists():
        return False
    try:
        shutil.rmtree(root)
        return True
    except OSError as exc:
        LOG.warning("受管环境删除失败: %s: %s", root, exc)
        return False


# --------------------------------------------------------------- 创建
def base_python() -> str | None:
    """能用来建 venv 的基础解释器；一个都没有回 None。

    直接复用 `bootstrap.find_base_python()`——它已经排除了内置 runtime
    （官方 embeddable 不带 `ensurepip`，`python -m venv` 建到一半就失败）
    并覆盖了 conda / python.org / PATH 的常见落点。**这里绝不新造一条探测链**。

    **但要多一条判据：版本得在支持区间内**（Codex 评审 P2）。`bootstrap` 那边
    只问「`import venv` 行不行」，于是一台只有 Python 3.14 的机器会一路通过：
    界面提供受管修复 → 建 venv → 下载装 matplotlib 与那个包 → **最后**才在
    体检那一步报「这个 Python 不在支持范围内」。用户白等一场下载。
    把判据提到选解释器那一刻，代价从「几十 MB + 几分钟」降到一次版本探测。
    """
    from . import bootstrap, projectenv

    def _supported(_path: str, version: str) -> bool:
        try:
            parts = tuple(int(x) for x in version.strip().split(".")[:2])
        except ValueError:
            return False
        return bool(parts) and projectenv.PYTHON_MIN <= parts < projectenv.PYTHON_MAX_EXCLUSIVE

    try:
        return bootstrap.find_base_python(accept=_supported)
    except (OSError, ValueError) as exc:  # 探测本身不该让请求 500
        LOG.warning("基础解释器探测失败: %s", exc)
        return None


def _run(argv: list[str], timeout: int) -> tuple[int, str]:
    """跑一条子进程。**shell=False、argv 是 list**（全模块唯一的执行入口）。"""
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            creationflags=runtime.CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return 1, f"超时（{timeout}s）"
    except OSError as exc:
        return 1, str(exc)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def create_venv(project: str | Path, base: str) -> tuple[bool, str]:
    """建一个空 venv（带 pip）。已经存在就原地复用。

    **不用 `--system-site-packages`**：那会让受管环境看见基础解释器的
    site-packages，于是「隔离环境」这四个字就不成立了——基础解释器上的一次
    升级会当场改变这个项目的渲染结果。
    """
    target = venv_python(project)
    if target.is_file():
        return True, ""
    root = venv_dir(project)
    if root.exists():
        # 残留的半个 venv 会让后续 pip 行为诡异（bootstrap 里同样的处置）
        shutil.rmtree(root, ignore_errors=True)
    try:
        root.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, str(exc)
    rc, out = _run([base, "-m", "venv", str(root)], VENV_TIMEOUT_S)
    if rc != 0 or not target.is_file():
        return False, out[-2000:]
    return True, out[-2000:]


def python_version_of(python: str) -> str:
    """目标解释器自报的版本；问不出来回空串。"""
    rc, out = _run([str(python), "-c", "import platform;print(platform.python_version())"], 60)
    return out.strip().splitlines()[-1].strip() if rc == 0 and out.strip() else ""
