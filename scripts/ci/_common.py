#!/usr/bin/env python3
"""实验室 CI 的公共地基：持久化根目录、路径安全、运行元数据、报告与摘要。

**纯标准库**——这些工具要在 preflight 阶段就能跑，那时候连产品依赖装没装
都还不知道；用不上的 import 会让「环境有问题」变成「诊断工具自己崩了」。

三件事在别处不要重复实现：

* `state_root()` —— 跨 run 保留的东西**只能**放这儿（`TAVOTTO_CI_STATE_ROOT`，
  默认 `/srv/tavotto-ci`）。工作目录每次都会被清掉，把 baseline 放那儿等于
  每次比较都在和自己刚生成的那份比，永远绿。
* `assert_within()` —— 任何删除动作前的强制体检。`rm -rf "$VAR"` 在 CI 上
  是灾难级的：变量拼错一次就从根目录开始删。这里用 `Path.resolve()` 之后
  做包含判定，符号链接也解开——`state_root/tmp` 指向 `/` 的话，只比字符串
  前缀是拦不住的。
* `run_metadata()` —— 性能与视觉基线**没有元数据就没有长期价值**：换了
  CPU、换了 Python、换了 matplotlib 之后的数字和上一版根本不可比，而报告里
  只写一个百分比的话，没人能事后判断那次回归是真的还是换机器了。
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

def use_utf8_streams() -> None:
    """把 stdout/stderr 钉成 UTF-8。

    Windows 上 stdout 一旦不是真控制台（被 CI/测试捕获）就退回 cp1252，
    argparse 打印中文 help 直接 UnicodeEncodeError 打死进程——`--help` 都跑
    不了的诊断工具比没有更糟。与 engine/cli.use_utf8_streams 同一手法。

    **不 import `_common` 的 CLI 要自己调一次**（compat_matrix / compat_driver
    就是这样）：靠「import 这个模块的都自动生效」是隐式耦合，下一个不需要
    `_common` 里任何东西的脚本会安静地漏掉它，而症状只在 Windows 上出现。
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


# 放在 import 期调一次：所有 `import _common` 的脚本连 argparse 之前就已生效。
use_utf8_streams()

# 持久化根目录下的固定布局。谁都不许在别处另开一套。
LAYOUT = (
    "cache",
    "locks",
    "upgrade/state",
    "upgrade/projects",
    "baselines/perf",
    "baselines/visual",
    "reports",
    "tmp",
)

DEFAULT_STATE_ROOT = "/srv/tavotto-ci"


class CiError(RuntimeError):
    """带稳定 code 的 CI 失败。

    与产品里 `HandoffError` 同一套路数：文案随时可改，`code` 不行——
    workflow 与后续脚本按它分诊，改名等于悄悄改掉调用方的判断依据。
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def as_json(self) -> str:
        return json.dumps({"ok": False, "code": self.code, "error": self.message},
                          ensure_ascii=False)


# --------------------------------------------------------------------------
# 持久化根目录
# --------------------------------------------------------------------------
def state_root() -> Path:
    """跨 run 保留的东西的唯一落点。

    刻意**不**回退到 `$RUNNER_TEMP`：那个目录每次 run 都是新的，baseline 落在
    那儿的话「和基线比」实际是「和自己比」，永远不会红——正是本仓库反复强调的
    那种空转门禁。宁可在 preflight 阶段就明确报错说这台机器没配好。
    """
    return Path(os.environ.get("TAVOTTO_CI_STATE_ROOT", DEFAULT_STATE_ROOT))


def ensure_layout(root: Path | None = None) -> Path:
    """建出固定布局并确认可写。目录已存在是正常情况（持久化本来就是目的）。"""
    root = root or state_root()
    try:
        for rel in LAYOUT:
            (root / rel).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise CiError("state_root_unwritable",
                      f"建不出 CI 持久化目录 {root}：{exc}。"
                      f"请确认 runner 用户拥有该目录（见 docs/ci/self-hosted-runner.md）") from exc
    probe = root / ".write-probe"
    try:
        probe.write_text(str(time.time()), encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise CiError("state_root_unwritable",
                      f"{root} 不可写：{exc}") from exc
    return root


def assert_within(path: Path, root: Path) -> Path:
    """确认 path 真的落在 root 里面，否则抛错。删除动作前必须过这一关。

    用 `resolve()` 而不是字符串前缀比较：`/srv/tavotto-ci/../../etc` 和指向
    仓外的符号链接都能靠它现形。另外挡住 root 本身——`assert_within(root, root)`
    通过的话，一句「清理这个目录」就会把整个持久化根删掉。
    """
    p = path.resolve()
    r = root.resolve()
    if p == r:
        raise CiError("unsafe_path", f"拒绝操作持久化根目录本身：{p}")
    if not p.is_relative_to(r):
        raise CiError("unsafe_path", f"路径 {p} 不在 {r} 之内，拒绝操作")
    return p


def safe_rmtree(path: Path, root: Path) -> bool:
    """删除 path，但只在它确实位于 root 之内时。返回是否真的删了。"""
    target = assert_within(path, root)
    if not target.exists():
        return False
    if target.is_dir() and not target.is_symlink():
        shutil.rmtree(target, ignore_errors=True)
    else:
        target.unlink(missing_ok=True)
    return True


# --------------------------------------------------------------------------
# 运行元数据
# --------------------------------------------------------------------------
def _cmd(argv: list[str]) -> str:
    """取一条命令的首行输出；取不到就返回空串（元数据缺一条不该让 CI 红）。"""
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return ""
    return (out.stdout or out.stderr).strip().splitlines()[0] if (out.stdout or out.stderr).strip() else ""


def _cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def _mem_total_gib() -> float:
    """物理内存（GiB）。取不到返回 0.0，调用方必须把 0 当作**未知**而不是「不足」。

    实验室 runner 是 Linux，但这些脚本的单测会在 macOS / Windows 上跑（ci.yml
    的 backend job 是三平台矩阵）。只读 `/proc/meminfo` 的话，别的平台会拿到 0
    并被判成「内存不足」——一条永远在别人机器上红的门禁，比没有门禁更糟。
    """
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("MemTotal:"):
                return round(int(line.split()[1]) / 1024 / 1024, 1)
    except (OSError, ValueError, IndexError):
        pass
    if sys.platform == "darwin":
        raw = _cmd(["sysctl", "-n", "hw.memsize"])
        try:
            return round(int(raw) / 1024 ** 3, 1)
        except ValueError:
            pass
    return 0.0


def run_metadata(mode: str = "") -> dict:
    """一份报告要能在几个月后还说明问题，靠的就是这些字段。"""
    return {
        "sha": os.environ.get("GITHUB_SHA", _cmd(["git", "rev-parse", "HEAD"])),
        "ref": os.environ.get("GITHUB_REF", ""),
        "run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
        "mode": mode or os.environ.get("LAB_MODE", ""),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tavotto_version": _tavotto_version(),
        "python": platform.python_version(),
        "python_impl": platform.python_implementation(),
        "node": _cmd(["node", "--version"]),
        "pnpm": _cmd(["pnpm", "--version"]),
        "rust": _cmd(["rustc", "--version"]),
        "os": f"{platform.system()} {platform.release()}",
        "kernel": platform.version(),
        "cpu_model": _cpu_model(),
        "cpu_count": os.cpu_count() or 0,
        "ram_gib": _mem_total_gib(),
    }


def _tavotto_version() -> str:
    """读产品版本，读不到就空。

    刻意不 import tavotto：preflight 可能跑在产品还没装好的环境里，
    而「诊断工具因为被诊断的东西没装好而崩溃」是最没用的失败方式。
    """
    try:
        import tavotto  # noqa: PLC0415  (延迟 import 是有意的)
        return getattr(tavotto, "__version__", "")
    except Exception:
        init = Path(__file__).resolve().parents[2] / "src" / "tavotto" / "__init__.py"
        try:
            for line in init.read_text(encoding="utf-8").splitlines():
                if line.startswith("__version__"):
                    return line.split("=", 1)[1].strip().strip("\"'")
        except OSError:
            pass
    return ""


# --------------------------------------------------------------------------
# 报告与 Step Summary
# --------------------------------------------------------------------------
def reports_dir(root: Path | None = None) -> Path:
    d = (root or state_root()) / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_report(name: str, payload: dict, root: Path | None = None) -> Path:
    """报告统一落 `reports/`，原子写。

    原子写不是洁癖：报告在失败路径上会被 upload-artifact 收走，写到一半的
    JSON 传上去之后，看报告的人得到的是「解析失败」而不是「哪里出了问题」。
    """
    d = reports_dir(root)
    dest = d / name
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, dest)
    return dest


def summary(text: str) -> None:
    """追加到 GitHub Step Summary；本地跑时退回 stdout。

    存在的理由很直接：没有它，判断这次跑成什么样要翻几千行日志。
    """
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        print(text)
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text.rstrip() + "\n")
    except OSError:
        print(text)


def summary_table(rows: list[tuple[str, str, str]]) -> str:
    """(项目, 结果, 细节) → markdown 表。结果列用 emoji 让扫读能一眼定位。"""
    out = ["| 项目 | 结果 | 细节 |", "| --- | --- | --- |"]
    for name, verdict, detail in rows:
        out.append(f"| {name} | {verdict} | {detail} |")
    return "\n".join(out)


def fail(code: str, message: str) -> None:
    """把失败同时送到 stderr、Step Summary 和退出码。"""
    print(f"::error::{message}", file=sys.stderr)
    summary(f"\n> **失败** `{code}` — {message}\n")
    raise CiError(code, message)


# --------------------------------------------------------------------------
# corpus 产物
# --------------------------------------------------------------------------
def materialize_corpus(python: str, corpus_dir: Path, timeout: int = 900) -> list[str]:
    """把 corpus 脚本跑一遍，生成产物文件。返回产出的文件名。

    **为什么必须有这一步**：Tavotto 的 `/api/panels` 扫的是图库里的**产物**
    （PDF/PNG），不是脚本——注册表只负责回答「这个 stem 是哪个脚本产出的」。
    一个从没跑过的 corpus 目录里一张图都没有，面板列表自然是空的，
    而症状是「corpus 里这些 stem 没被扫出来」，指向注册表，与真实原因无关。

    这同时也是用户的真实流程：先跑脚本出图，再用 Tavotto 打开那张图去编辑。

    脚本用 `cwd=corpus_dir` 执行——corpus 里的 `savefig("x.pdf")` 是相对路径，
    换个 cwd 就会把图写到别处去。
    """
    scripts = sorted(p for p in corpus_dir.glob("*.py") if not p.name.startswith("_"))
    if not scripts:
        raise CiError("corpus_empty", f"{corpus_dir} 里没有 corpus 脚本")
    for script in scripts:
        out = subprocess.run([python, script.name], cwd=str(corpus_dir),
                             capture_output=True, text=True, timeout=timeout)
        if out.returncode != 0:
            raise CiError("corpus_script_failed",
                          f"corpus 脚本 {script.name} 跑不起来（退出码 {out.returncode}）：\n"
                          f"{out.stdout[-1200:]}\n{out.stderr[-1200:]}")
    produced = sorted(p.name for p in corpus_dir.iterdir()
                      if p.suffix.lower() in (".pdf", ".png"))
    if not produced:
        raise CiError("corpus_no_output",
                      f"{len(scripts)} 个 corpus 脚本都跑完了，却没产出任何 PDF/PNG")
    return produced
