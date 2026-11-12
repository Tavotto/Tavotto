#!/usr/bin/env python3
"""证据机执行器（ADR 0166；规则全文 docs/rules/ci/evidence-box.md，运维 docs/ci/evidence-box.md）。

跑在证据机本机（root + Docker），纯标准库。由 cron 的 `nightly` 或维护者经 SSH 调用：

    python3 box.py doctor
    python3 box.py sync                               # 控制检出对齐 origin/main，打印 SHA
    python3 box.py run guardian-suites [--sha SHA]
    python3 box.py run guardian-explore [--sha SHA] [--seed N] [--shards K] [--max-seconds S]
    python3 box.py run research-s0 --case research_xxx [--sha SHA]
    python3 box.py run novice-u2404                    # PyPI 上已发布的版本，与 SHA 无关
    python3 box.py nightly                             # sync → suites → explore → 轮换一个 S0 → 周日 novice → clean
    python3 box.py reports [--last N]
    python3 box.py clean [--keep N]
    python3 box.py install-cron

三条不变量（tests/test_evidencebox.py 钉着）：
1. 只执行 `origin/main` 可达的提交（控制检出刚从 GitHub 取回）；没有「跑任意分支 / 任意脚本」的入口。
2. 执行阶段一律在一次性容器里：`--network none`、非 root、`--cap-drop ALL`、`no-new-privileges`、
   根文件系统只读、限 pids / 内存 / CPU，跑完 `--rm`。联网只发生在准备阶段（装依赖、取字体、取上游源文件）。
3. 这台机器不持有任何凭据：不注册 runner、没有 GitHub token、不往任何云存储上传；证据只留在本机，
   由维护者 scp 取回。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

# 中文报文全在 stderr：流被重定向（cron、SSH 管道）时别退回区域编码
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

REPO_URL = "https://github.com/Tavotto/Tavotto.git"
ROOT = Path(os.environ.get("TAVOTTO_EVIDENCE_ROOT", "/srv/tavotto-evidence"))
CONTROL = ROOT / "control"  # 控制检出：blobless 全历史，只用来判可达性与导出源码
SRC = ROOT / "src"  # 每个 SHA 一份浅检出，进镜像
RUNS = ROOT / "runs"
LOCK = ROOT / "lock"
#: 批准字体的持久缓存（大陆到 raw.githubusercontent.com 很慢）。只是缓存：fetch_fonts 对目录里已有的每个文件
#: 重核 allowlist 的 sha256，不符就删掉重下，所以缓存被改过也进不了镜像。
CACHE = ROOT / "cache"  # 跨次保留的下载缓存；内容每次都按仓库里钉的 sha256 重核，不是信任边界
FONT_CACHE = CACHE / "fonts"
RESEARCH_CACHE = CACHE / "research-sources"

BASE_IMAGE = os.environ.get("TAVOTTO_EVIDENCE_BASE", "python:3.13-bookworm")
NOVICE_IMAGE = "ubuntu:24.04"
IMAGE_REPO = "tavotto-evidence"
#: 执行容器的身份：与镜像里 root 拥有的源码树分开，测试代码改不了自己的执行环境。
RUN_UID = "10001:10001"

#: 任何容器里都硬关：CI 绝不产生真实的产品事件（与四条 workflow 顶层同一组开关）。
NO_PHONE_HOME = {
    "TAVOTTO_NO_TELEMETRY": "1",
    "TAVOTTO_NO_UPDATE_CHECK": "1",
    "TAVOTTO_NO_DIAG_UPLOAD": "1",
}
#: 准备阶段走国内镜像（大陆机器直连 PyPI / Debian 很慢）；版本由 runtime lock 钉死，镜像只改来源不改结果。
PIP_MIRROR = os.environ.get("TAVOTTO_EVIDENCE_PIP_INDEX", "https://mirrors.aliyun.com/pypi/simple/")
DEBIAN_MIRROR = os.environ.get("TAVOTTO_EVIDENCE_DEBIAN_MIRROR", "mirrors.aliyun.com")

TASKS = ("guardian-suites", "guardian-explore", "research-s0", "novice-u2404")
CASE_RE = re.compile(r"^research_[a-z0-9_]+$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")

# 与 compat-guardian.yml 的 observation job 同一组 case（那边改了这边跟着改：看护见 tests/test_evidencebox.py）。
ENGINE_CASES = (
    "shape_oo_savefig,shape_pyplot_show_only,shape_relative_csv,"
    "shape_local_helper_import,shape_multi_figure_a,sci_pillow"
)


# ----------------------------------------------------------------------------- 工具
def sh(
    argv: list[str], *, check: bool = True, capture: bool = False, **kw
) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        check=check,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=capture,
        **kw,
    )


def git(*args: str, cwd: Path = CONTROL) -> str:
    return sh(["git", "-C", str(cwd), *args], capture=True).stdout.strip()


def log(msg: str) -> None:
    print(f"[evidencebox] {msg}", file=sys.stderr, flush=True)


def stamp() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# ----------------------------------------------------------------------------- 可信度
def sync() -> str:
    """控制检出对齐 GitHub 上的 main；返回 main 的 SHA。"""
    ROOT.mkdir(parents=True, exist_ok=True)
    if not (CONTROL / ".git").exists():
        clone = [
            "git",
            "clone",
            "-q",
            "--filter=blob:none",
            "--no-checkout",
            REPO_URL,
            str(CONTROL),
        ]
        if not retry(clone, cleanup=CONTROL):
            raise SystemExit("git clone failed 3 times (GitHub unreachable from the box?)")
    if not retry(["git", "-C", str(CONTROL), "fetch", "-q", "origin", "main"]):
        raise SystemExit(
            "git fetch origin main failed 3 times; refusing to judge trust on a stale main"
        )
    return git("rev-parse", "origin/main")


def retry(argv: list[str], *, cleanup: Path | None = None, tries: int = 3) -> bool:
    """大陆到 GitHub 偶有 TLS 断连（2026-11-03 实测一次 `GnuTLS recv error -110`，重试即过）。"""
    for attempt in range(tries):
        if sh(argv, check=False).returncode == 0:
            return True
        if cleanup is not None:
            shutil.rmtree(cleanup, ignore_errors=True)
        time.sleep(10 * (attempt + 1))
    return False


def resolve_main_commit(sha: str | None, *, fresh: bool = True) -> str:
    main = sync() if fresh else git("rev-parse", "origin/main")
    if sha is None:
        return main
    full = git("rev-parse", "--verify", f"{sha}^{{commit}}")
    if not trust_decision(full, is_ancestor(full)):
        raise SystemExit(
            f"refused: {full[:12]} is not on origin/main. The evidence box only runs merged code; "
            "branches, forks and user scripts belong on GitHub-hosted runners."
        )
    return full


def is_ancestor(sha: str) -> bool:
    rc = sh(
        ["git", "-C", str(CONTROL), "merge-base", "--is-ancestor", sha, "origin/main"], check=False
    )
    return rc.returncode == 0


def trust_decision(sha: str, on_main: bool) -> bool:
    """唯一判据：完整 SHA 且在 origin/main 上。没有「维护者自己的提交」例外（与 Windows Lab 不同）：
    Guardian 的夹具只有合入 main 才算评审过（docs/ci/compat-guardian.md §1、§7）。"""
    return bool(SHA_RE.match(sha)) and on_main


def checkout(sha: str) -> Path:
    """每个 SHA 一份真正的 git 检出：explore 执行前要核 `git status` 与 HEAD 一致。"""
    dest = SRC / sha
    if (dest / ".git").exists() and git("rev-parse", "HEAD", cwd=dest) == sha:
        return dest
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    git("init", "-q", cwd=dest)
    # 控制检出是 blobless 的，不能当本地源；按 SHA 从 GitHub 浅取（内容由 SHA 寻址，可信度已由控制检出判过）
    if not retry(["git", "-C", str(dest), "fetch", "-q", "--depth", "1", REPO_URL, sha]):
        raise SystemExit(f"git fetch {sha[:12]} failed 3 times")
    git("checkout", "-q", "--detach", sha, cwd=dest)
    return dest


# ----------------------------------------------------------------------------- 容器
def image_tag(sha: str, profile: str) -> str:
    return f"{IMAGE_REPO}:{sha[:12]}-{profile}"


def image_exists(tag: str) -> bool:
    return sh(["docker", "image", "inspect", tag], check=False, capture=True).returncode == 0


def env_args(env: dict[str, str]) -> list[str]:
    out: list[str] = []
    for k, v in env.items():
        out += ["-e", f"{k}={v}"]
    return out


def prepare_argv(name: str, src: Path, base: str) -> list[str]:
    """准备容器：联网、root，只跑 main 上的安装步骤；完成后 `docker commit` 成执行镜像。"""
    return [
        "docker", "run", "--name", name,
        "-v", f"{src}:/srcro:ro",
        "-v", f"{CACHE}:/boxcache:rw",
        *env_args({**NO_PHONE_HOME, "PIP_INDEX_URL": PIP_MIRROR, "PIP_DEFAULT_TIMEOUT": "30",
                   "PIP_RETRIES": "5", "PIP_DISABLE_PIP_VERSION_CHECK": "1"}),
        base,
    ]  # fmt: skip


def exec_argv(
    tag: str, out: Path, cmd: list[str], *, cpus: str = "3.5", memory: str = "6g"
) -> list[str]:
    """执行容器：断网、非 root、无能力、根只读、限额、跑完即删。唯一可写的宿主路径是本次的 out 目录。"""
    return [
        "docker", "run", "--rm",
        "--network", "none",
        "--user", RUN_UID,
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--read-only",
        "--tmpfs", "/tmp:rw,exec,nosuid,size=4g",
        "--pids-limit", "1024",
        "--memory", memory, "--memory-swap", memory,
        "--cpus", cpus,
        "-v", f"{out}:/out:rw",
        "-w", "/work/src",
        *env_args({**NO_PHONE_HOME, "HOME": "/tmp/home", "MPLCONFIGDIR": "/tmp/mpl",
                   "PYTHONDONTWRITEBYTECODE": "1", "XDG_CACHE_HOME": "/tmp/cache"}),
        tag,
        *cmd,
    ]  # fmt: skip


def apt_mirror_cmd() -> str:
    return f"sed -i 's|deb.debian.org|{DEBIAN_MIRROR}|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || true"


def build_image(sha: str, profile: str, script: str, *, base: str = BASE_IMAGE) -> str:
    """在一次性准备容器里跑 `script`（源码在 /work/src），成功则 commit 成执行镜像。"""
    tag = image_tag(sha, profile)
    if image_exists(tag):
        return tag
    src = checkout(sha)
    FONT_CACHE.mkdir(parents=True, exist_ok=True)
    RESEARCH_CACHE.mkdir(parents=True, exist_ok=True)
    name = f"tavotto-evidence-prep-{sha[:12]}-{profile}".replace("_", "-")
    sh(["docker", "rm", "-f", name], check=False, capture=True)
    full = "\n".join(
        [
            "set -euo pipefail",
            apt_mirror_cmd(),
            "git config --system safe.directory '*'",
            "mkdir -p /work && cp -a /srcro /work/src && cd /work/src",
            # 字体缓存：先放进去（fetch_fonts 逐个重核 sha256），装完再存回来
            "mkdir -p src/tavotto/resources/fonts build/fonts-cache",
            "cp -a /boxcache/fonts/fonts/. src/tavotto/resources/fonts/ 2>/dev/null || true",
            "cp -a /boxcache/fonts/archives/. build/fonts-cache/ 2>/dev/null || true",
            # 新批准的字体缓存里没有时从镜像补；urlopen 的超时管不住慢速传输，所以限总时长。
            # 补不到不算失败，由 fetch_fonts 判
            "timeout 900 python scripts/evidencebox/mirror_fill.py fonts || true",
            script,
            "mkdir -p /boxcache/fonts/fonts /boxcache/fonts/archives",
            "cp -a src/tavotto/resources/fonts/. /boxcache/fonts/fonts/ 2>/dev/null || true",
            "cp -a build/fonts-cache/. /boxcache/fonts/archives/ 2>/dev/null || true",
            # 执行身份要能读源码树，但不能改它
            "chmod -R a+rX /work",
        ]
    )
    log(f"prepare {tag}")
    t0 = time.time()
    rc = sh([*prepare_argv(name, src, base), "bash", "-c", full], check=False).returncode
    try:
        if rc != 0:
            raise SystemExit(f"prepare {profile} failed (exit {rc}); container {name} removed")
        commit = sh(
            ["docker", "commit", "--change", "WORKDIR /work/src", name, tag],
            check=False,
            capture=True,
        )
        if commit.returncode != 0:
            raise SystemExit(f"docker commit {tag} failed: {commit.stderr.strip()}")
    finally:
        sh(["docker", "rm", "-f", name], check=False, capture=True)
    log(f"prepared {tag} in {time.time() - t0:.0f}s")
    return tag


# 与 compat-guardian.yml「Install」+「Approved fonts」同一组步骤
GUARDIAN_PREPARE = """
python -m pip install -q --upgrade pip
pip install -q -e ".[dev,ci]"
pip install -q $(python scripts/ci/runtime_pins.py --include-corpus)
pip install -q openpyxl==3.1.5 pyarrow==26.0.0
python scripts/fetch_fonts.py
python scripts/fetch_fonts.py --check
"""


def research_prepare(case: str, tex: bool, source_package: bool) -> str:
    """与 compat-research-s0.yml 的 baseline job 同一组步骤：两个解释器、钉版本、原样的科研依赖。"""
    lines = [
        "export TAVOTTO_RESEARCH_CACHE=/work/research-sources",
        # 上游源码从 raw.githubusercontent 取，大陆约五成以上请求被重置。fetch 只发布核过 sha256 的完整
        # 文件、已有的逐个重核，所以每一轮无论成败都把已取到的存回缓存，下一轮 / 下一晚只补缺的；
        # 重置是秒级失败，40 轮、间隔 5 s 也就几分钟。最后仍不全就让准备失败。
        "mkdir -p /work/research-sources /boxcache/research-sources",
        "cp -a /boxcache/research-sources/. /work/research-sources/ 2>/dev/null || true",
        f"timeout 900 python scripts/evidencebox/mirror_fill.py research --case {case} || true",
        "fetched=0; for i in $(seq 1 40); do"
        f" if python scripts/ci/compat_guardian_research.py fetch --case {case}; then fetched=1; fi;"
        " cp -a /work/research-sources/. /boxcache/research-sources/;"
        ' [ "$fetched" = 1 ] && break; sleep 5; done; [ "$fetched" = 1 ]',
        "python -m venv /work/tavotto-venv",
        "python scripts/ci/runtime_pins.py --format lines > /work/tavotto-constraints.txt",
        "/work/tavotto-venv/bin/python -m pip install -q -c /work/tavotto-constraints.txt -e '.[dev,ci]'",
        "python scripts/ci/runtime_pins.py --include-corpus --format lines > /work/runtime-constraints.txt",
        "python -m venv /work/science-venv",
        "/work/science-venv/bin/python -m pip install -q -r /work/runtime-constraints.txt",
        "/work/tavotto-venv/bin/python scripts/fetch_fonts.py",
        "/work/tavotto-venv/bin/python scripts/fetch_fonts.py --check",
        # pip's own retries do not cover the git subprocess used by VCS requirements.
        # Retry the same constrained install; never proceed if every attempt fails.
        "installed=0; for attempt in 1 2 3; do"
        " if /work/science-venv/bin/python -m pip install -q -c /work/runtime-constraints.txt"
        f" -r tests/compat/research-environments/{case}.txt; then installed=1; break; fi;"
        ' if [ "$attempt" -lt 3 ]; then sleep 5; fi; done; [ "$installed" = 1 ]',
    ]
    if source_package:
        lines += [
            f"python scripts/ci/compat_guardian_research.py stage-dependency --case {case}"
            " --out /work/scientific-source",
            "/work/science-venv/bin/python -m pip install -q -c /work/runtime-constraints.txt"
            " /work/scientific-source",
        ]
    lines += [
        "/work/science-venv/bin/python -m pip check",
        "export TAVOTTO_SCIENCE_PYTHON=/work/science-venv/bin/python",
        f"/work/tavotto-venv/bin/python scripts/ci/compat_guardian_research.py verify-environment --case {case}",
        "/work/tavotto-venv/bin/python scripts/ci/compat_guardian_research.py verify-science-environment"
        " --python /work/science-venv/bin/python",
    ]
    if tex:
        lines += [
            "apt-get update -qq",
            "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends"
            " texlive-latex-extra texlive-fonts-recommended texlive-fonts-extra texlive-science cm-super dvipng",
        ]
    lines += [
        "/work/science-venv/bin/python -m pip freeze --all > /work/research-installed.txt",
        "/work/tavotto-venv/bin/python -m pip freeze --all > /work/tavotto-installed.txt",
    ]
    return "\n".join(lines)


# ----------------------------------------------------------------------------- 任务
def new_run(task: str, sha: str | None) -> Path:
    out = RUNS / f"{stamp()}-{task}"
    out.mkdir(parents=True)
    os.chown(out, 10001, 10001)
    (out / "meta.json").write_text(
        json.dumps({"task": task, "sha": sha, "started": stamp(), "host": "evidence-box"}, indent=2)
        + "\n"
    )
    return out


def finish(out: Path, code: int, notes: dict | None = None) -> int:
    meta = json.loads((out / "meta.json").read_text())
    meta.update({"finished": stamp(), "exit_code": code, **(notes or {})})
    (out / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
    log(f"{meta['task']} exit {code} -> {out}")
    return code


def run_container(tag: str, out: Path, script: str, **kw) -> int:
    argv = exec_argv(tag, out, ["bash", "-c", "set -uo pipefail; mkdir -p $HOME; " + script], **kw)
    with open(out / "console.log", "w") as fh:
        return sh(argv, check=False, stdout=fh, stderr=subprocess.STDOUT).returncode


def task_guardian_suites(sha: str) -> int:
    tag = build_image(sha, "guardian", GUARDIAN_PREPARE)
    out = new_run("guardian-suites", sha)
    script = f"""
python scripts/ci/compat_guardian.py run --suite compat_engine --case {ENGINE_CASES} \
  --expected-sha {sha} --timeout 60 --out /out/engine > /out/engine.out 2>&1; e=$?
python scripts/ci/compat_guardian.py run --suite compat_v2 \
  --expected-sha {sha} --timeout 90 --out /out/v2 > /out/v2.out 2>&1; v=$?
for r in /out/*/*/reports/guardian.json; do python scripts/ci/compat_guardian.py validate "$r" || exit 2; done
python scripts/ci/compat_guardian.py summarize /out/*/*/reports/guardian.json \
  --markdown-out /out/summary.md --json-out /out/summary.json > /dev/null
echo "engine=$e v2=$v" > /out/codes.txt
# 与 workflow 同一口径：只有 0 / 4 不算失败
for c in $e $v; do case $c in 0|4) ;; *) exit $c ;; esac; done
"""
    return finish(out, run_container(tag, out, script))


def task_guardian_explore(sha: str, seed: int, shards: int, max_seconds: int) -> int:
    """全候选池（不设 --limit）切 `shards` 片并行，各片一个容器，再合并 triage。"""
    tag = build_image(sha, "guardian", GUARDIAN_PREPARE)
    out = new_run("guardian-explore", sha)
    cpus = f"{max(1.0, 3.6 / shards):.1f}"
    memory = f"{max(2, 6 // shards)}g"
    procs = []
    for k in range(1, shards + 1):
        shard_out = out / f"shard{k}"
        shard_out.mkdir()
        os.chown(shard_out, 10001, 10001)
        script = (
            f"python scripts/ci/compat_guardian.py explore --seed {seed} --shard {k}/{shards}"
            f" --max-seconds {max_seconds} --confirm-differences 3 --expected-sha {sha}"
            f" --timeout 240 --out /out > /out/explore.out 2>&1"
        )
        argv = exec_argv(
            tag, shard_out, ["bash", "-c", "mkdir -p $HOME; " + script], cpus=cpus, memory=memory
        )
        procs.append((k, subprocess.Popen(argv)))
    codes = {k: p.wait() for k, p in procs}
    merge_rc = run_container(tag, out, explore_merge_script(shards))
    worst = max([c for c in codes.values() if c not in (0, 1, 4)] or [0])
    return finish(out, worst or merge_rc, {"seed": seed, "shards": shards, "shard_codes": codes})


def explore_merge_script(shards: int) -> str:
    """合并 N 片报告：同 seed / 同 HEAD / 片号齐全由 triage 自己校验，缺片显式标 partial。
    triage 失败（报告损坏、混 seed、混 HEAD、重复片）就是这次长跑没有有效证据：退出码原样带出来，
    不许被后面的 summarize 盖掉（Codex #1682 P2）。summarize 只是给人看的汇总，失败不改结论。"""
    return (
        """
shopt -s nullglob
reports=(/out/shard*/*/reports/explore-report.json)
[[ ${#reports[@]} -gt 0 ]] || { echo "no explore report" > /out/merge.txt; exit 1; }
flag=""; [[ ${#reports[@]} -eq %d ]] || flag="--allow-partial-shards"
python scripts/ci/compat_guardian.py triage "${reports[@]}" $flag --out /out/triage.json --markdown-out /out/triage.md
triage=$?
python scripts/ci/compat_guardian.py summarize "${reports[@]}" $flag --markdown-out /out/summary.md --json-out /out/summary.json > /dev/null
exit $triage
"""
        % shards
    )


def research_matrix(sha: str) -> list[dict]:
    src = checkout(sha)
    # 不加 -I：它隐含 -P，脚本目录不进 sys.path，同目录的 compat_corpus 就导入失败（Codex #1682 P1）。
    # 跑的是刚判过可信的 main 检出里的脚本，与 workflow 的 prepare job 同一条命令。
    raw = sh(
        [sys.executable, "scripts/ci/compat_guardian_research.py", "matrix"],
        capture=True,
        cwd=src,
    ).stdout
    return json.loads(raw)["include"]


def task_research_s0(sha: str, case: str) -> int:
    if not CASE_RE.match(case):
        raise SystemExit(f"invalid case id {case!r}")
    rows = {r["case_id"]: r for r in research_matrix(sha)}
    if case not in rows:
        raise SystemExit(f"{case} is not registered in research-manifest at {sha[:12]}")
    row = rows[case]
    profile = "s0-" + case.removeprefix("research_").replace("_", "-")
    tag = build_image(sha, profile, research_prepare(case, row["tex"], row["source_package"]))
    out = new_run("research-s0", sha)
    receipt = f"""
import hashlib, json, platform, sys
from pathlib import Path
sys.path.insert(0, 'scripts/ci')
import compat_provenance
w = Path('/work')
receipt = {{
    'schema': 'tavotto-research-setup/1',
    'case_id': {case!r},
    'source_sha': {sha!r},
    'run_id': 'evidence-box',
    'run_attempt': '1',
    'workflow': compat_provenance.current({case!r}),
    'python': platform.python_version(),
    'steps': {{'core': 'success', 'fonts': 'success', 'scientific': 'success',
               'tex': {'"success"' if row["tex"] else '"skipped"'}}},
    'tex_setup': None,
    'installed_sha256': hashlib.sha256((w / 'research-installed.txt').read_bytes()).hexdigest(),
    'tavotto_installed_sha256': hashlib.sha256((w / 'tavotto-installed.txt').read_bytes()).hexdigest(),
}}
Path('/out/setup-receipt.json').write_text(json.dumps(receipt, indent=2) + '\\n')
"""
    script = f"""
export TAVOTTO_SCIENCE_PYTHON=/work/science-venv/bin/python TAVOTTO_RESEARCH_CACHE=/work/research-sources
/work/tavotto-venv/bin/python scripts/ci/compat_guardian.py run --suite compat_engine --case {case} \
  --expected-sha {sha} --timeout 900 --out /out/research-s0 > /out/research-s0.out 2>&1; code=$?
cp /work/research-installed.txt /work/tavotto-installed.txt /out/
/work/tavotto-venv/bin/python - <<'PY'
{receipt}
PY
for r in /out/research-s0/*/reports/guardian.json; do
  /work/tavotto-venv/bin/python scripts/ci/compat_guardian.py validate "$r" --setup-receipt /out/setup-receipt.json || exit 2
done
exit $code
"""
    rc = run_container(tag, out, script)
    # S0 镜像大（TeX 可达数 GB），跑完就删；下次重建
    sh(["docker", "image", "rm", tag], check=False, capture=True)
    return finish(out, rc, {"case": case})


def task_novice_u2404() -> int:
    """全新 Ubuntu 24.04 + 普通用户，按 README 原样装 PyPI 上的 Tavotto 与 Codex 插件。
    装的是已发布版本、不执行 main 以外的仓库代码；需要联网与 sudo，所以不加执行阶段的那组限制，
    但仍是跑完即删的一次性容器、不挂任何宿主路径（除本次 out）。"""
    out = new_run("novice-u2404", None)
    argv = novice_argv(Path(__file__).resolve().parent / "novice_u2404.sh", out)
    with open(out / "console.log", "w") as fh:
        rc = sh(argv, check=False, stdout=fh, stderr=subprocess.STDOUT).returncode
    return finish(out, rc)


def novice_argv(script: Path, out: Path) -> list[str]:
    return [
        "docker", "run", "--rm", "--pids-limit", "2048", "--memory", "4g", "--cpus", "2",
        "-v", f"{script}:/novice.sh:ro", "-v", f"{out}:/out:rw",
        *env_args(NO_PHONE_HOME),
        NOVICE_IMAGE, "bash", "/novice.sh",
    ]  # fmt: skip


# ----------------------------------------------------------------------------- 维护
def reports(last: int) -> int:
    rows = sorted(RUNS.glob("*/meta.json"))[-last:]
    for p in rows:
        m = json.loads(p.read_text())
        print(
            f"{p.parent.name:48} exit={m.get('exit_code', '…')!s:4} sha={(m.get('sha') or '-')[:12]:12}"
            f" {m.get('case', '')}"
        )
    return 0


def clean(keep: int) -> int:
    """证据目录留最近 keep 份；镜像只留当前 main 的 guardian 镜像；源码检出只留当前 main。"""
    runs = sorted(p for p in RUNS.iterdir() if p.is_dir()) if RUNS.exists() else []
    for p in runs[:-keep] if keep else runs:
        shutil.rmtree(p, ignore_errors=True)
    main = git("rev-parse", "origin/main")
    keep_tag = image_tag(main, "guardian")
    tags = sh(
        ["docker", "images", IMAGE_REPO, "--format", "{{.Repository}}:{{.Tag}}"], capture=True
    ).stdout.split()
    for t in tags:
        if t != keep_tag:
            sh(["docker", "image", "rm", t], check=False, capture=True)
    if SRC.exists():
        for d in SRC.iterdir():
            if d.name != main:
                shutil.rmtree(d, ignore_errors=True)
    sh(["docker", "image", "prune", "-f"], check=False, capture=True)
    return 0


def doctor() -> int:
    ok = True
    for name, argv in {
        "docker": ["docker", "info", "--format", "{{.ServerVersion}}"],
        "git": ["git", "--version"],
    }.items():
        r = sh(argv, check=False, capture=True)
        print(f"{name:8} {'ok' if r.returncode == 0 else 'MISSING'} {r.stdout.strip()}")
        ok &= r.returncode == 0
    free = shutil.disk_usage("/").free / 2**30
    print(f"disk     {free:.1f} GiB free {'(low: S0 TeX images need ~6 GiB)' if free < 8 else ''}")
    ok &= free >= 4
    print(f"cron     {'installed' if Path(CRON_FILE).exists() else 'not installed'}")
    print(f"runs     {len(list(RUNS.glob('*/meta.json'))) if RUNS.exists() else 0}")
    return 0 if ok else 1


CRON_FILE = "/etc/cron.d/tavotto-evidence"


def cron_command() -> str:
    """cron 那一行要执行的命令。

    控制检出是 blobless，`git show` 要现去 GitHub 取文件内容；大陆链路常断 TLS。所以：
    取回三次、每次间隔 60 s；先写 `.new` 再一起 `mv`，取一半失败不会把本机副本截成空文件；
    取回成功才 `exec` **main 上的** box.py，三次都失败就记一行日志、今晚不跑（不退回旧副本）。
    """
    c, r = CONTROL, ROOT
    fetch = (
        f"git -C {c} fetch -q origin main"
        f" && git -C {c} show origin/main:scripts/evidencebox/box.py > {r}/box.py.new"
        f" && git -C {c} show origin/main:scripts/evidencebox/novice_u2404.sh > {r}/novice_u2404.sh.new"
        f" && mv -f {r}/box.py.new {r}/box.py && mv -f {r}/novice_u2404.sh.new {r}/novice_u2404.sh"
    )
    return (
        f"for i in 1 2 3; do {fetch} && exec python3 {r}/box.py nightly >> {r}/nightly.log 2>&1;"
        f" rm -f {r}/box.py.new {r}/novice_u2404.sh.new; sleep 60; done;"
        f' echo "$(date -u +%FT%TZ) nightly skipped: could not fetch main" >> {r}/nightly.log'
    )


NIGHTLY_UTC = (18, 30)  # 02:30 CST


def cron_schedule(utc_offset: _dt.timedelta | None = None) -> str:
    """cron 按**本机时区**解释时刻（这台机器是 Asia/Shanghai），所以把 NIGHTLY_UTC 换成本地时刻再写。"""
    if utc_offset is None:
        utc_offset = _dt.datetime.now().astimezone().utcoffset() or _dt.timedelta(0)
    hour, minute = NIGHTLY_UTC
    local = _dt.datetime(2000, 1, 1, hour, minute) + utc_offset
    return f"{local.minute} {local.hour} * * *"


def install_cron() -> int:
    """02:30 CST 夜跑。cron 先把控制检出对齐 main，再执行**main 上的** box.py（不跑本机任何旧副本）。"""
    cmd = cron_command().replace("%", "\\%")  # crontab 里 % 是换行，要转义
    line = (
        "SHELL=/bin/bash\nPATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\n"
        f"{cron_schedule()} root {cmd}\n"
    )
    Path(CRON_FILE).write_text(line)
    os.chmod(CRON_FILE, 0o644)
    print(f"wrote {CRON_FILE}")
    return 0


def guarded(fn, *args) -> int | str:
    """一个任务失败不拖垮整晚：准备失败（SystemExit）、子命令失败（CalledProcessError）、
    读不出 JSON 等都记下原因，接着跑下一个，最后照样清理并写 nightly 汇总。"""
    try:
        return fn(*args)
    except (SystemExit, Exception) as exc:  # noqa: BLE001 — 夜跑的边界：任何失败都记录，不中断
        return f"error: {type(exc).__name__}: {exc}"


def nightly_s0(sha: str, today: _dt.date) -> tuple[str, int | str]:
    """按日期轮换一个 S0 case；读矩阵本身也在 guarded 里，读不出来不影响后面的任务。"""
    cases = [r["case_id"] for r in research_matrix(sha)]
    case = cases[today.toordinal() % len(cases)]
    return case, task_research_s0(sha, case)


def nightly() -> int:
    sha = resolve_main_commit(None)
    today = _dt.date.today()
    results: dict[str, int | str] = {"sha": sha}
    results["guardian-suites"] = guarded(task_guardian_suites, sha)
    # 每天换 seed：hosted 的周扫按 ISO 周号取 seed，这里按日期取，七天覆盖七组派生候选
    seed = int(today.strftime("%Y%m%d"))
    results["guardian-explore"] = guarded(task_guardian_explore, sha, seed, 3, 3 * 3600)
    s0 = guarded(nightly_s0, sha, today)
    if isinstance(s0, tuple):
        results[f"research-s0:{s0[0]}"] = s0[1]
    else:
        results["research-s0"] = s0
    if today.weekday() == 6:
        results["novice-u2404"] = guarded(task_novice_u2404)
    clean(keep=60)
    (RUNS / f"{stamp()}-nightly.json").write_text(json.dumps(results, indent=2) + "\n")
    log(json.dumps(results))
    return 0


# ----------------------------------------------------------------------------- 入口
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="box.py")
    sub = ap.add_subparsers(dest="verb", required=True)
    sub.add_parser("doctor")
    sub.add_parser("sync")
    run = sub.add_parser("run")
    run.add_argument("task", choices=TASKS)
    run.add_argument("--sha")
    run.add_argument("--case")
    run.add_argument("--seed", type=int, default=42)
    run.add_argument("--shards", type=int, default=3, choices=range(1, 5))
    run.add_argument("--max-seconds", type=int, default=3600)
    rep = sub.add_parser("reports")
    rep.add_argument("--last", type=int, default=20)
    cl = sub.add_parser("clean")
    cl.add_argument("--keep", type=int, default=60)
    sub.add_parser("install-cron")
    sub.add_parser("nightly")
    a = ap.parse_args(argv)

    if a.verb == "doctor":
        return doctor()
    if a.verb == "reports":
        return reports(a.last)
    if a.verb == "install-cron":
        return install_cron()

    ROOT.mkdir(parents=True, exist_ok=True)
    RUNS.mkdir(parents=True, exist_ok=True)
    import fcntl  # 只在证据机（Linux）上用到；模块顶层不导入，看护用例在 Windows 上也要能 import

    with open(LOCK, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("another evidence-box task is running") from None
        if a.verb == "sync":
            print(sync())
            return 0
        if a.verb == "clean":
            return clean(a.keep)
        if a.verb == "nightly":
            return nightly()
        if a.task == "novice-u2404":
            return task_novice_u2404()
        sha = resolve_main_commit(a.sha)
        if a.task == "guardian-suites":
            return task_guardian_suites(sha)
        if a.task == "guardian-explore":
            return task_guardian_explore(sha, a.seed, a.shards, a.max_seconds)
        if not a.case:
            raise SystemExit("research-s0 needs --case")
        return task_research_s0(sha, a.case)
    return 2


if __name__ == "__main__":
    sys.exit(main())
