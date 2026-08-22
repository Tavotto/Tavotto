#!/usr/bin/env python3
"""N-1 → N 真升级验收：同一份用户状态，先被上一版写出来，再交给候选版打开。

这是临时 runner 很难可靠做到、而实验室 runner 最该做的事——它需要**同一块
持久化磁盘上先后跑两个版本**。

流程（每一步都必须真的发生）：

    上一版 wheel → 装进 venv A
        └─ 在一份全新的用户根里：打开项目 → 渲染 → 改参数 → 存布局
           → 导出 → 触发自动保存 → 干净退出
    候选 wheel   → 装进 venv B
        └─ **指向完全相同的** TAVOTTO_DATA_DIR / TAVOTTO_CONFIG_DIR / 项目目录
           → 项目还在吗 → 布局读得回来吗 → 还能渲染吗 → 还能导出吗
           → 配置有没有被悄悄重置 → 有没有 traceback → 有没有孤儿 worker
        └─ 再启动一次，确认第二次也正常

几条刻意的设计：

* **用户状态一个字节都不删**。两版之间不做任何清理——「升级后重开」就是
  用户的真实处境。
* **项目路径带中文与空格**。这两样在 Windows 与 macOS 上分别踩过坑，
  放进默认路径而不是单独一个 case，是为了让它们始终在主路径上被覆盖。
* **配置被静默重置属于失败**。用户在设置里改过的东西升级后回到默认，
  比崩溃更难被发现——崩溃至少有人报。
* **不凭空发明 state**：写进去的都是产品自己会写的东西（config.json 的
  recent_projects / projects、layouts/ 下的画布与自动保存、baked_overrides、
  项目内 tavottofile/），审计自 src/tavotto/app.py 与 engine/config.py。

用法：
    python scripts/ci/upgrade_acceptance.py --candidate dist/tavotto-0.8.0-py3-none-any.whl
    python scripts/ci/upgrade_acceptance.py --candidate dist/*.whl --baseline-tag v0.7.0
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent))
from _common import (  # noqa: E402
    CiError, ensure_layout, materialize_corpus, run_metadata, summary,
    summary_table, write_report,
)
import smoke_app as SA  # noqa: E402

REPO = _HERE.parents[1]
CORPUS = REPO / "tests" / "acceptance" / "corpus"
API = "https://api.github.com"
REPO_SLUG = os.environ.get("GITHUB_REPOSITORY", "Tavotto/Tavotto")
# 项目目录名刻意同时带中文与空格：两者在 Windows / macOS 上分别踩过坑，
# 放在主路径上比单开一个 case 更能保证它们一直被覆盖。
PROJECT_DIRNAME = "升级 测试 项目"


# ---------------------------------------------------------------- 取上一版
def _api_json(url: str, timeout: int = 60) -> dict | list:
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _version_key(tag: str) -> tuple:
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)", tag)
    return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)


def resolve_baseline(candidate_version: str, explicit: str | None) -> str:
    """挑 N-1：候选版本之前最近的一个正式 release tag。

    预发布（rc/beta/dev）一律不作为基线——用户不会从一个 rc 升上来，
    拿它当基线只会把 CI 的注意力浪费在没人走过的路径上。
    """
    if explicit:
        return explicit
    try:
        releases = _api_json(f"{API}/repos/{REPO_SLUG}/releases?per_page=30")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise CiError("baseline_lookup_failed",
                      f"取不到 release 列表：{exc}。可用 --baseline-tag 显式指定") from exc
    cand = _version_key(candidate_version)
    tags = [r["tag_name"] for r in releases
            if not r.get("prerelease") and not r.get("draft")
            and _version_key(r["tag_name"]) < cand]
    if not tags:
        raise CiError("no_baseline_release",
                      f"找不到早于 {candidate_version} 的正式 release。"
                      "首个版本无从做升级测试；用 --baseline-tag 指定，或在 workflow 里跳过这一项")
    return max(tags, key=_version_key)


def download_wheel(tag: str, dest_dir: Path) -> Path:
    """下上一版的 wheel。

    **走 api.github.com 的 assets 端点**，不用 `releases/download/…`：后者的
    第一跳是 github.com，在实验室这条网络上不可达（见 docs/ci/self-hosted-runner.md）。
    """
    try:
        rel = _api_json(f"{API}/repos/{REPO_SLUG}/releases/tags/{tag}")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise CiError("baseline_release_missing", f"取不到 {tag} 的 release：{exc}") from exc
    assets = [a for a in rel.get("assets", []) if a["name"].endswith(".whl")]
    if not assets:
        raise CiError("baseline_wheel_missing", f"{tag} 的 release 里没有 wheel 资产")
    asset = assets[0]
    dest = dest_dir / asset["name"]
    req = urllib.request.Request(f"{API}/repos/{REPO_SLUG}/releases/assets/{asset['id']}",
                                 headers={"Accept": "application/octet-stream"})
    if os.environ.get("GITHUB_TOKEN"):
        req.add_header("Authorization", f"Bearer {os.environ['GITHUB_TOKEN']}")
    with urllib.request.urlopen(req, timeout=600) as resp, dest.open("wb") as fh:
        shutil.copyfileobj(resp, fh)
    if dest.stat().st_size != asset["size"]:
        raise CiError("baseline_wheel_truncated",
                      f"{asset['name']} 下载不完整：{dest.stat().st_size} != {asset['size']}")
    return dest


def wheel_dist_name(wheel: Path) -> str:
    """从 wheel 文件名取分发包名（`tavotto-0.8.0-py3-none-any.whl` → `tavotto`）。

    PEP 427 规定 wheel 文件名以 `-` 分段且第一段就是分发名，所以这里不需要
    解包读 METADATA。
    """
    return wheel.name.split("-", 1)[0].replace("_", "-").lower()


def crosses_rename_boundary(old_wheel: Path, new_wheel: Path) -> tuple[bool, str]:
    """N-1 与候选是不是同一个分发包。

    2026-08-20 从 Magplot 改名到 Tavotto 时，产品**有意选择了干净断裂**：
    包名、数据目录、配置目录、格式标识全部换掉，且刻意不做 LEGACY 兼容
    （理由写在 `src/tavotto/engine/brand.py` 的模块 docstring 里）。

    这意味着跨越那条边界的「升级」在产品语义上根本不存在——用户不是
    `pip install --upgrade`，而是装了另一个包。硬去测一条产品明确不支持的
    路径，得到的失败没有任何信息量，只会训练人忽略这个 job。

    所以这里识别它并如实说明，**不伪装成通过，也不报成产品缺陷**。
    等有了同代的上一版（如 v0.8.0 → v0.9.0），这条判断自然不再命中。
    """
    old, new = wheel_dist_name(old_wheel), wheel_dist_name(new_wheel)
    if old == new:
        return False, ""
    return True, (f"N-1 的分发包是 `{old}`，候选是 `{new}`——跨越了产品改名边界。"
                  f"改名时选的是干净断裂（见 src/tavotto/engine/brand.py）：包名、"
                  f"数据目录、配置目录、格式标识全部更换且不做兼容读取，"
                  f"因此这两版之间不存在「升级」这条路径。等出现同代的上一版之后"
                  f"（{new} 的前一个 release），这项验收会自动恢复。")


def make_venv(where: Path, wheel: Path) -> Path:
    subprocess.run([sys.executable, "-m", "venv", str(where)], check=True,
                   capture_output=True, timeout=300)
    py = where / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")
    for args in ([str(wheel)], ["matplotlib"]):
        out = subprocess.run([str(py), "-m", "pip", "install", "-q",
                              "--disable-pip-version-check", *args],
                             capture_output=True, text=True, timeout=1800)
        if out.returncode != 0:
            raise CiError("pip_install_failed",
                          f"装 {args} 失败：{out.stdout[-1500:]}{out.stderr[-1500:]}")
    return py


# ---------------------------------------------------------------- 会话
class Session:
    """起一个 Tavotto，指向给定的用户根。退出后可再起一次，state 不动。"""

    def __init__(self, py: Path, user_root: Path, project: Path, label: str) -> None:
        self.py, self.user_root, self.project, self.label = py, user_root, project, label
        self.data_dir = user_root / "data"
        self.config_dir = user_root / "config"
        self.proc: subprocess.Popen | None = None
        self.base = ""
        self.port = 0
        self.log: list[str] = []

    def __enter__(self) -> "Session":
        port = self.port = SA._free_port()
        self.base = f"http://127.0.0.1:{port}"
        for d in (self.data_dir, self.config_dir, self.user_root / "home"):
            d.mkdir(parents=True, exist_ok=True)
        env = {
            **os.environ,
            "TAVOTTO_DATA_DIR": str(self.data_dir),
            "TAVOTTO_CONFIG_DIR": str(self.config_dir),
            "HOME": str(self.user_root / "home"),
            "USERPROFILE": str(self.user_root / "home"),
            "TAVOTTO_NO_UPDATE_CHECK": "1",
            "TAVOTTO_ALLOW_SHUTDOWN": "1",
        }
        cmd = [str(self.py), "-m", "tavotto", "--port", str(port),
               "--no-browser", "--figures", str(self.project)]
        print(f"  [{self.label}] $ {' '.join(cmd[-5:])}", flush=True)
        self.proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True,
                                     encoding="utf-8", errors="replace")
        SA._wait_ready(self.base, self.proc, SA.BOOT_TIMEOUT_S)
        self._adopt_credentials(port)
        return self

    def _adopt_credentials(self, port: int) -> None:
        """带上本机会话凭据（ADR 0008）。**按文件在不在判，不按版本号判。**

        0.9.0 起浏览器模式也要认证，而升级验收的另一头是 N-1——`--baseline`
        可以指定任意历史版本，其中大多数早于这道边界。所以判据只能是「这一版
        写没写凭据文件」。

        不带的后果不是「少测一项」：`_wait_ready` 打的 `/api/version` 是公共
        端点，照样就绪，随后**每一个** API 调用 401。v0.9.0 发版时就是这么
        炸的——阶段一（0.8.0，无认证）一路绿，阶段二（候选）当场 401，而这个
        脚本在会话认证合并之后一次都没跑过（实验室 runner 那时还没有 runner
        领得走）。

        `SA._AUTH` 是模块级的，两个阶段共用同一个进程：**先清空**，否则
        N-1 那一轮会带着候选版的头（或反过来）。
        """
        SA._AUTH.clear()
        cred = self.data_dir / "session" / f"port-{port}.json"
        if not cred.is_file():
            print(f"  [{self.label}] 无会话凭据文件（这一版早于 ADR 0008），裸走",
                  flush=True)
            return
        secret = json.loads(cred.read_text(encoding="utf-8"))["secret"]
        SA._AUTH["X-Tavotto-Auth"] = secret
        print(f"  [{self.label}] 已取得会话凭据", flush=True)

    def __exit__(self, *exc) -> None:
        try:
            SA._post(f"{self.base}/api/shutdown", {}, timeout=60)
        except Exception:                                   # noqa: BLE001
            pass
        if self.proc:
            try:
                self.proc.wait(timeout=120)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=30)

    def app_log(self) -> str:
        return SA._tail(self.data_dir / "cache" / "app.log", 80)


def _tracebacks(text: str) -> list[str]:
    """从 app.log 里挑 traceback。升级路径上出现 traceback 一律算失败。"""
    return [ln for ln in text.splitlines()
            if "Traceback (most recent call last)" in ln
            or re.search(r"\b(TypeError|ValueError|KeyError|AttributeError|JSONDecodeError)\b", ln)]


# ---------------------------------------------------------------- 两个阶段
def write_state_with_old(py: Path, user_root: Path, project: Path) -> dict:
    """用 N-1 造出一份真实的用户状态。返回后面要拿来核对的指纹。"""
    facts: dict = {}
    with Session(py, user_root, project, "N-1") as s:
        panels = SA._get(f"{s.base}/api/panels")["panels"]
        scripted = [p for p in panels if p.get("script")]
        if not scripted:
            raise CiError("no_scripted_panel", "测试项目里没有可参数化面板")
        target = scripted[0]

        # 渲染 → 改参数再渲染：产生 baked/override 侧的真实状态
        first = SA._post(f"{s.base}/api/engine/render",
                         {"id": target["id"], "patches": []}, timeout=600)
        manifest = first.get("manifest") or {}
        facts["element_count"] = len(manifest.get("elements", []))

        import bench_render as BR                             # 复用靶子挑选
        patch = BR._pick_patch(manifest)
        patches = BR._variant(patch, 1) if patch else []
        SA._post(f"{s.base}/api/engine/render",
                 {"id": target["id"], "patches": patches}, timeout=600)
        facts["patches"] = patches

        # 存一份命名画布布局（落项目内 tavottofile/）
        doc = {
            "schema": 2, "id": "upgrade-doc", "name": "升级用例",
            "pageWmm": 90, "pageHmm": 60,
            "objects": [{"type": "panel", "id": target["id"], "x_mm": 5, "y_mm": 5,
                         "w_mm": 60, "h_mm": 40, "overrides": patches}],
        }
        try:
            SA._post(f"{s.base}/api/layouts/升级布局", {"doc": doc}, timeout=120)
            facts["layout_saved"] = True
        except Exception as exc:                              # noqa: BLE001
            facts["layout_saved"] = False
            facts["layout_error"] = str(exc)[:200]

        # 自动保存（磁盘为主，落 layouts/_autosave/）
        try:
            req = urllib.request.Request(
                f"{s.base}/api/autosave/upgrade-doc",
                data=json.dumps({"doc": doc, "updatedAt": int(time.time() * 1000)}).encode(),
                # **SA._AUTH 不能漏**：这里是全文件唯一一处不经 SA._post 的
                # 应用请求（PUT，SA 没有对应助手），而它外面裹着 try/except，
                # 漏了的话表现是 autosave_saved=False 静静记进报告，
                # 升级验收照旧「通过」——一条本该验的东西被验没了。
                headers={"Content-Type": "application/json", **SA._AUTH},
                method="PUT")
            urllib.request.urlopen(req, timeout=60).read()
            facts["autosave_saved"] = True
        except Exception as exc:                              # noqa: BLE001
            facts["autosave_saved"] = False
            facts["autosave_error"] = str(exc)[:200]

        # 导出一次
        spec = {"page_w_mm": 80, "page_h_mm": 40, "formats": ["pdf"], "stem": "升级导出",
                "objects": [{"type": "panel", "id": target["id"], "x_mm": 5, "y_mm": 5,
                             "w_mm": 60, "h_mm": 30}]}
        out = SA._post(f"{s.base}/api/export", spec, timeout=600)
        facts["export_name"] = out["files"][0]["name"]

        facts["panel_id"] = target["id"]
        facts["version"] = SA._get(f"{s.base}/api/version").get("version", "")
        facts["old_log_tracebacks"] = _tracebacks(s.app_log())

    # 关掉之后抓一份配置指纹，用来核对候选版有没有把它悄悄重置
    cfg_path = user_root / "config" / "config.json"
    facts["config_exists"] = cfg_path.is_file()
    if facts["config_exists"]:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        facts["config_recent_count"] = len(cfg.get("recent_projects", []))
        facts["config_keys"] = sorted(cfg.keys())
    facts["autosave_files"] = sorted(
        p.name for p in (user_root / "data" / "layouts" / "_autosave").glob("*")) \
        if (user_root / "data" / "layouts" / "_autosave").is_dir() else []
    facts["baked_files"] = sorted(
        p.name for p in (user_root / "data" / "baked_overrides").glob("*.json")) \
        if (user_root / "data" / "baked_overrides").is_dir() else []
    return facts


def verify_with_new(py: Path, user_root: Path, project: Path,
                    facts: dict, round_no: int) -> list[tuple[str, bool, str]]:
    """用候选版打开同一份状态并逐条核对。"""
    checks: list[tuple[str, bool, str]] = []
    with Session(py, user_root, project, f"N#{round_no}") as s:
        ver = SA._get(f"{s.base}/api/version").get("version", "")
        checks.append((f"第 {round_no} 次启动", True, f"版本 {ver}（上一版 {facts['version']}）"))

        # 1) 老项目还打得开
        proj = SA._get(f"{s.base}/api/project")
        checks.append(("老项目可打开", bool(proj.get("figures_dir")),
                       proj.get("figures_dir", "(空)")))

        # 2) 面板还在，数量一致
        panels = SA._get(f"{s.base}/api/panels")["panels"]
        ids = {p["id"] for p in panels}
        checks.append(("老面板仍在", facts["panel_id"] in ids,
                       facts["panel_id"] if facts["panel_id"] in ids else f"不见了；现有 {sorted(ids)[:3]}"))

        # 3) 还能按老 patches 渲染，且元素数量没变
        res = SA._post(f"{s.base}/api/engine/render",
                       {"id": facts["panel_id"], "patches": facts["patches"]}, timeout=600)
        m = res.get("manifest") or {}
        n = len(m.get("elements", []))
        checks.append(("老 patches 仍可渲染", bool(m), f"{n} 个元素"))
        checks.append(("元素数量与升级前一致", n == facts["element_count"],
                       f"{n} vs {facts['element_count']}"))
        warn = res.get("warnings") or []
        checks.append(("渲染无 warning", not warn, "无" if not warn else str(warn[:2])))

        # 4) 布局读得回来
        if facts.get("layout_saved"):
            try:
                lay = SA._get(f"{s.base}/api/layouts")
                names = [x.get("name") for x in (lay.get("layouts") or lay if isinstance(lay, list) else lay.get("layouts", []))]
                checks.append(("老布局可列出", "升级布局" in names, str(names[:4])))
            except Exception as exc:                        # noqa: BLE001
                checks.append(("老布局可列出", False, str(exc)[:150]))

        # 5) 自动保存读得回来
        if facts.get("autosave_saved"):
            try:
                got = SA._get(f"{s.base}/api/autosave/upgrade-doc")
                checks.append(("老自动保存可解析", bool(got and got.get("doc")),
                               "读回且含 doc" if got.get("doc") else str(got)[:120]))
            except Exception as exc:                        # noqa: BLE001
                checks.append(("老自动保存可解析", False, str(exc)[:150]))

        # 6) 还能导出
        spec = {"page_w_mm": 80, "page_h_mm": 40, "formats": ["pdf"], "stem": "升级后导出",
                "objects": [{"type": "panel", "id": facts["panel_id"], "x_mm": 5, "y_mm": 5,
                             "w_mm": 60, "h_mm": 30}]}
        out = SA._post(f"{s.base}/api/export", spec, timeout=600)
        f = Path(out["export_dir"]) / out["files"][0]["name"]
        checks.append(("升级后仍可导出", f.is_file() and f.stat().st_size > 500,
                       f"{f.name} {f.stat().st_size if f.is_file() else 0} 字节"))

        # 7) app.log 里不能出现 traceback
        tb = _tracebacks(s.app_log())
        checks.append(("app.log 无 traceback", not tb,
                       "无" if not tb else f"{len(tb)} 条：{tb[0][:110]}"))

    # 8) 配置没有被静默重置——比崩溃更难发现，所以单独一条
    cfg_path = user_root / "config" / "config.json"
    if facts.get("config_exists"):
        if cfg_path.is_file():
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            kept = len(cfg.get("recent_projects", [])) >= facts["config_recent_count"]
            checks.append(("用户配置未被静默重置", kept,
                           f"recent_projects {facts['config_recent_count']} → {len(cfg.get('recent_projects', []))}"))
        else:
            checks.append(("用户配置未被静默重置", False, "config.json 升级后不见了"))

    # 9) 没有孤儿 worker
    time.sleep(2)
    orphans = SA._leftover_workers(user_root / "data")
    checks.append(("无孤儿 worker", not orphans,
                   "0" if not orphans else f"{len(orphans)} 个"))
    return checks


# ---------------------------------------------------------------- 主流程
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="N-1 → N 升级验收")
    ap.add_argument("--candidate", required=True, help="候选 wheel（build job 的产物）")
    ap.add_argument("--baseline-tag", default=None, help="显式指定 N-1 的 tag")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args(argv)

    root = ensure_layout()
    work = Path(tempfile.mkdtemp(prefix="artifact-upgrade-", dir=str(root / "tmp")))
    rows: list[tuple[str, str, str]] = []
    all_checks: list[tuple[str, bool, str]] = []
    ok = True
    baseline_tag = ""

    try:
        cand = Path(args.candidate)
        if not cand.is_file():
            matches = sorted(Path(args.candidate).parent.glob(Path(args.candidate).name))
            if not matches:
                raise CiError("candidate_missing", f"找不到候选 wheel：{args.candidate}")
            cand = matches[0]
        m = re.search(r"-(\d+\.\d+\.\d+[^-]*)-py3", cand.name)
        cand_version = m.group(1) if m else "0.0.0"

        baseline_tag = resolve_baseline(cand_version, args.baseline_tag)
        print(f"候选: {cand.name}（{cand_version}）\n基线: {baseline_tag}")

        old_wheel = download_wheel(baseline_tag, work)
        print(f"已取得上一版 wheel: {old_wheel.name}")

        crossed, why = crosses_rename_boundary(old_wheel, cand)
        if crossed:
            # 如实标注并放行。**不算通过**（报告里 skipped 明确可见），
            # 也不算失败——失败会让人去修一个产品刻意不支持的路径。
            print(f"::notice::升级验收跳过：{why}")
            summary(f"\n### 升级验收 · 跳过\n\n> {why}\n")
            write_report("upgrade.json",
                         {"ok": True, "skipped": True, "reason": "rename_boundary",
                          "detail": why, "baseline_tag": baseline_tag,
                          "baseline_wheel": old_wheel.name, "candidate_wheel": cand.name,
                          "metadata": run_metadata()}, root)
            return 0

        # 项目目录：带中文与空格，且**位于持久化用户根之外**——用户的图库
        # 本来就不在数据目录里，把它塞进去会掩盖路径解析上的问题。
        project = work / PROJECT_DIRNAME
        shutil.copytree(CORPUS, project)
        user_root = work / "user root 用户"        # 用户根同样带空格与中文
        user_root.mkdir(parents=True, exist_ok=True)

        py_old = make_venv(work / "venv-old", old_wheel)
        py_new = make_venv(work / "venv-new", cand)

        # 先把 corpus 跑出产物。Tavotto 的面板列表扫的是图库里的**产物文件**，
        # 不是脚本——一个从没跑过的目录里没有任何面板可开。
        # 用 N-1 的解释器生成，贴近「用户在旧版本时手上已经有的那些图」。
        produced = materialize_corpus(str(py_old), project)
        print(f"corpus 产物 {len(produced)} 个")

        print("\n=== 阶段一：用 N-1 写出用户状态 ===")
        facts = write_state_with_old(py_old, user_root, project)
        rows.append(("N-1 写出状态", "✅",
                     f"{baseline_tag} · {facts['element_count']} 元素 · "
                     f"布局 {'✓' if facts.get('layout_saved') else '✗'} · "
                     f"自动保存 {'✓' if facts.get('autosave_saved') else '✗'}"))
        if facts["old_log_tracebacks"]:
            rows.append(("N-1 自身日志", "⚠️", f"{len(facts['old_log_tracebacks'])} 条 traceback"))

        print("\n=== 阶段二：候选版打开同一份状态 ===")
        checks1 = verify_with_new(py_new, user_root, project, facts, 1)
        print("\n=== 阶段三：再启动一次（第二次仍须正常）===")
        checks2 = verify_with_new(py_new, user_root, project, facts, 2)

        for tag, checks in (("", checks1), ("（二次启动）", checks2)):
            for name, good, detail in checks:
                all_checks.append((name + tag, good, detail))
                rows.append((name + tag, "✅" if good else "❌", detail))
                ok = ok and good
    except CiError as exc:
        ok = False
        rows.append((exc.code, "❌", exc.message))
        print(f"::error::{exc.message}", file=sys.stderr)
    except Exception as exc:                                # noqa: BLE001
        ok = False
        rows.append(("未预期异常", "❌", f"{type(exc).__name__}: {exc}"))
        print(f"::error::升级验收异常：{type(exc).__name__}: {exc}", file=sys.stderr)
    finally:
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)

    payload = {"ok": ok, "baseline_tag": baseline_tag,
               "checks": [{"name": n, "ok": g, "detail": d} for n, g, d in all_checks],
               "metadata": run_metadata()}
    write_report("upgrade.json", payload, root)
    summary(f"\n### 升级验收 · {baseline_tag or '?'} → 候选\n\n" + summary_table(rows))
    print(f"\n升级验收：{'通过' if ok else '失败'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
