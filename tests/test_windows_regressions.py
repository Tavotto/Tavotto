"""朋友在 Windows 上撞出来的那几类问题，先在这里钉死再谈修。

原则：**每个「只在别人电脑上发生」的 bug 都先变成回归测试**。否则修掉的是
这一次的现象，下个版本它会换个形式回来。这里覆盖的类别：

  * 默认编码不是 UTF-8（cp936）——中文标签一出现就打死 worker/启动流程
  * 文件被别的程序占用（PDF 阅读器开着）——「写回原始文件」的覆盖行为
  * 路径：盘符、反斜杠、中文与空格
  * 端口被占用
  * 换名盖不掉正被读的文件（os.replace → WinError 5）
  * 关进程慢：poll() 还说活着，握手其实早就失败了
  * AI CLI 只有 .cmd 外壳 / 装在微软商店的执行别名下
  * 渲染解释器探测：python.org / conda / 商店版

跨平台可跑：拿不到真实 Windows 语义的地方就直接测**那段逻辑本身**
（monkeypatch 出同样的失败），而不是假装在 Windows 上跑。
"""
import ast
import io
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pymupdf
import pytest

from magplot import app as m
from magplot.engine import ai_bridge, pool, workerd_client


@pytest.fixture
def client():
    m.app.config["TESTING"] = True
    m.reset_projects()
    yield m.app.test_client()
    m.reset_projects()
    pool.stop_watcher()


def _figs(tmp_path, name="figs"):
    figs = tmp_path / name
    figs.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    doc.new_page(width=100, height=50)
    doc.save(figs / "Fig1.pdf")
    doc.close()
    (figs / "mm_registry.json").write_text(json.dumps({"version": 1, "scripts": {
        "fig1.py": {"entry": "main", "cost": "light", "notes": "", "stems": ["Fig1"]},
    }}), encoding="utf-8")
    (figs / "fig1.py").write_text("def main():\n    pass\n", encoding="utf-8")
    return figs


# ---------------- 文件被占用（Windows 独占锁） -------------------------------

def _fake_workers(monkeypatch, figs, tmp_path, payload: bytes) -> None:
    """接上假 worker（热会话 + 写回用的一次性重放）。

    写回的 staging 一律出自一次性 worker（干净重放，见 app._write_source_files），
    所以这里两个角色都要给：热会话只用来读 manifest，导出全在重放那边。
    """
    out = tmp_path / "_replay_out"
    out.mkdir(exist_ok=True)
    man = {"stem": "Fig1", "size_mm": [35.28, 17.64], "elements": []}
    (out / "Fig1.json").write_text(json.dumps(man), encoding="utf-8")

    class FakeWorker:
        script_name, entry = "fig1.py", "main"
        figures_dir = str(figs)
        base = out_dir = out
        built = True
        script_sha1 = ""     # 空 = 会话没记指纹，前置的脚本检查自然跳过
        last_patch_hash = ""

        def override(self, stem, patches, preview_dpi=None, inline_svg=False):
            return {"ok": True, "manifest": man, "warnings": []}

        def export(self, stem, patches, path, fmt="pdf", dpi=600):
            Path(path).write_bytes(payload)
            return {"ok": True, "path": path, "warnings": []}

        def shutdown(self):
            pass

    worker = FakeWorker()
    monkeypatch.setattr(m.engine_pool, "get", lambda *a, **k: worker)
    monkeypatch.setattr(m.engine_pool, "one_shot", lambda *a, **k: worker)
    monkeypatch.setattr(m.engine_pool, "discard", lambda w: None)


def _lock(monkeypatch, name: str) -> None:
    """让某个目标文件的原子替换抛 PermissionError（独占锁的形状）。"""
    real_replace = Path.replace

    def locked(self, target):
        if Path(target).name == name:
            raise PermissionError(13, "另一个程序正在使用此文件")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", locked)


def test_write_back_reports_locked_file_instead_of_500(client, tmp_path, monkeypatch):
    """目标 PDF 被别的程序打开时，写回必须给一个能照做的错误。

    Windows 上文件被 Acrobat / 看图工具打开就是**独占锁**，`Path.replace`
    直接抛 PermissionError。不接住的话用户拿到 500 + 一串 traceback，
    图库里还留下一个 `.Fig1.pdf.updating` 垃圾文件。
    """
    figs = _figs(tmp_path)
    m.open_project(str(figs))
    _fake_workers(monkeypatch, figs, tmp_path, b"%PDF-1.4\n")   # 假装导出成功
    _lock(monkeypatch, "Fig1.pdf")

    resp = client.post("/api/engine/update_source",
                       json={"id": "Fig1.pdf", "patches": []})
    assert resp.status_code == 409
    body = resp.get_json()
    assert body["code"] == "file_locked"
    assert body["file"] == "Fig1.pdf"
    assert "关闭" in body["error"]          # 告诉用户该去做什么
    # 半成品不许留在图库里
    assert not list(figs.glob(".*updating"))
    assert (figs / "Fig1.pdf").is_file()   # 原文件完好


def test_write_back_rolls_back_when_the_second_target_is_locked(client, tmp_path,
                                                                monkeypatch):
    """PDF 换成功、PNG 被占用：把 PDF 从备份恢复回去，并说清事情的结局。

    一张图的 PDF 是新的、PNG 还是旧的，比整件事失败糟糕得多——用户在画布上看
    位图、投出去的是矢量，两者从此不一致且没有任何提示。
    """
    figs = _figs(tmp_path)
    (figs / "Fig1.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    before_pdf = (figs / "Fig1.pdf").read_bytes()
    m.open_project(str(figs))
    _fake_workers(monkeypatch, figs, tmp_path, b"x" * 16)
    _lock(monkeypatch, "Fig1.png")

    body = client.post("/api/engine/update_source",
                       json={"id": "Fig1.pdf", "patches": []}).get_json()
    assert body["file"] == "Fig1.png"
    assert body["rolled_back"] == ["Fig1.pdf"] and body["rollback_failed"] == []
    assert body["updated"] == []          # 回滚成功 = 没有文件停在「已被换掉」
    assert (figs / "Fig1.pdf").read_bytes() == before_pdf
    assert not list(figs.glob(".*updating"))


# ---------------- 路径：盘符、反斜杠、中文与空格 ------------------------------

def test_browse_accepts_backslashes_and_chinese_spaces(client, tmp_path):
    """路径可以手输/粘贴，用户粘过来的就是资源管理器那种反斜杠写法。"""
    target = tmp_path / "我的 论文" / "figures"
    target.mkdir(parents=True)
    for raw in (str(target), str(target).replace("/", "\\") if os.name == "nt"
                else str(target)):
        body = client.get(f"/api/projects/browse?path={raw}").get_json()
        assert body["path"] == str(target)


def test_open_project_with_chinese_and_spaces(client, tmp_path):
    figs = _figs(tmp_path / "我的 论文 图")
    body = client.post("/api/projects/open", json={"path": str(figs)}).get_json()
    assert body["open"] is True
    assert client.get("/api/panels").get_json()["panels"][0]["id"] == "Fig1.pdf"


def test_drive_roots_listed_on_windows_only(client):
    """驱动器层：Windows 给盘符，POSIX 给 `/`。缺了它 Windows 上到不了 D 盘。"""
    roots = client.get("/api/projects/browse?path=@roots").get_json()["dirs"]
    assert roots
    if os.name == "nt":
        assert all(r["path"].endswith(":\\") for r in roots)
        assert any(r["name"].upper() == "C:" for r in roots)
    else:
        assert roots == [{"name": "/", "path": "/"}]


# ---------------- 端口被占用 --------------------------------------------------

def test_busy_port_falls_back_instead_of_crashing():
    """5089 被别的程序占着时顺延到下一个空闲端口。

    双击启动的应用不能因为端口冲突就一声不响地退出——窗口化打包下用户连
    traceback 都看不到。
    """
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        busy = s.getsockname()[1]
        chosen = m.resolve_port(busy)
        assert chosen is not None and chosen != busy


# ---------------- 默认编码不是 UTF-8（cp936） --------------------------------

WORKER_PY = pool.WORKER_PY


def test_worker_pipes_survive_non_utf8_locale(tmp_path):
    """系统区域编码是 cp936 时，中文标签不能把 worker 打死。

    worker 的 stdin/stdout 与 pool 侧的管道都显式钉了 UTF-8；这里用
    PYTHONIOENCODING 模拟一个非 UTF-8 的默认编码，确认协议照常往返。
    """
    try:
        worker_py = pool.find_worker_python()
    except pool.WorkerError:
        pytest.skip("找不到装有 matplotlib 的解释器")

    figs = tmp_path / "figures"
    figs.mkdir()
    (figs / "fig_cjk.py").write_text(
        "import matplotlib\n"
        "matplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n\n"
        "def main():\n"
        "    fig, ax = plt.subplots(figsize=(2, 1.5))\n"
        "    ax.plot([0, 1], [0, 1])\n"
        "    ax.set_xlabel('波长 / µm⁻¹')\n"   # 中文 + µ + 上标：cp936 的经典雷区
        "    fig.savefig('CJK_1.pdf')\n",
        encoding="utf-8")

    proc = subprocess.Popen(
        [worker_py, str(WORKER_PY), "--script", str(figs / "fig_cjk.py"),
         "--figures-dir", str(figs), "--out-dir", str(tmp_path / "out"),
         "--sandbox", str(tmp_path / "sandbox"), "--entry", "main"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1, encoding="utf-8", errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "gbk:replace",
             "PYTHONUTF8": "0", "LC_ALL": "C"})
    try:
        proc.stdin.write(json.dumps({"cmd": "build"}) + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        assert line, f"worker 无响应\n{proc.stderr.read()[-2000:]}"
        resp = json.loads(line)
        assert resp.get("ok"), resp
        assert "CJK_1" in resp["stems"]
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)


def test_startup_prints_reconfigure_stdout_to_utf8():
    """启动信息里有中文；stdout 一旦不是真控制台就退回系统区域编码，
    print 会 UnicodeEncodeError 直接打死进程（用户看到「启动即崩」）。"""
    src = Path(m.__file__).read_text(encoding="utf-8")
    assert 'reconfigure(encoding="utf-8"' in src


def test_packaging_entry_points_reconfigure_stdout_to_utf8():
    """打包/冒烟脚本的日志带中文与 ✓✗↓；Windows 管道 stdout 默认 cp1252/cp936，
    不 reconfigure 的话第一条日志就 UnicodeEncodeError 打死整个构建
    （windows-exe-smoke 首跑连撞两处：build_worker_runtime 的「↓」、
    magplot.spec 的中文 print）。新加的入口脚本都要沿用 build_frontend.py
    的同一段写法。"""
    repo = Path(__file__).resolve().parent.parent
    for rel in ("packaging/magplot.spec",
                "scripts/build_frontend.py",
                "scripts/build_desktop.py",
                "scripts/build_worker_runtime.py",
                "scripts/smoke_app.py",
                "scripts/smoke_desktop.py",
                # Codex 插件的交接脚本：Codex 调它时 stdout 就是管道，
                # 输出的 JSON 带中文（hint / magplot open 回来的错误）
                "codex-plugin/skills/magplot-figure/scripts/handoff.py"):
        src = (repo / rel).read_text(encoding="utf-8")
        assert 'reconfigure(encoding="utf-8"' in src, \
            f"{rel} 没做 stdout reconfigure，Windows 管道下中文日志会打死进程"


def test_codex_handoff_json_survives_cp1252_stdout():
    """Codex 插件的交接脚本在非 UTF-8 stdout 下必须照样吐出那行 JSON。

    实测（CI 的 windows-latest 腿）：Codex 调这个脚本时 stdout 是管道，Windows 上
    于是退回 cp1252/cp936；输出里有中文（这里走的是「路径不存在」那条），
    第一次 print 直接 UnicodeEncodeError——**调用方看到的是脚本挂了，
    而不是那行说明该怎么修的 JSON**，整条交接在 Windows 上等于不可用。
    """
    repo = Path(__file__).resolve().parent.parent
    script = repo / "codex-plugin/skills/magplot-figure/scripts/handoff.py"
    r = subprocess.run(
        [sys.executable, str(script), str(repo / "不存在的图.pdf")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        env={**os.environ, "PYTHONIOENCODING": "cp1252"})
    assert r.returncode == 2, r.stderr        # 2 = 路径不对，不是 1（崩了）
    assert "路径不存在" in json.loads(r.stdout.strip().splitlines()[-1])["error"]


def test_codex_handoff_pins_utf8_on_every_decoding_spawn():
    """它读的是 `magplot open` 的中文 JSON 与用户脚本的 traceback。

    `text=True` 不钉 encoding 就跟随系统区域编码，cp936 下解码当场抛——
    与 `test_every_backend_subprocess_that_decodes_pins_utf8` 同一条纪律，
    只是那条只扫 engine/ 与 app.py，够不到插件目录。
    """
    repo = Path(__file__).resolve().parent.parent
    script = repo / "codex-plugin/skills/magplot-figure/scripts/handoff.py"
    tree = ast.parse(script.read_text(encoding="utf-8"))
    checked = 0
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "run"):
            continue
        kwargs = {kw.arg for kw in node.keywords}
        if "text" not in kwargs:
            continue                          # 只看 returncode 的那次不解码
        assert "encoding" in kwargs and "errors" in kwargs, \
            f"handoff.py 第 {node.lineno} 行 text=True 却没钉 encoding/errors"
        checked += 1
    assert checked >= 2, "一处都没扫到 = 匹配逻辑坏了，别让空断言冒充通过"


def test_runtime_build_log_survives_cp1252_stdout():
    """runtime 构建脚本的日志带「↓」（U+2193）；Windows 上管道 stdout 默认
    cp1252/cp936，第一条下载日志就 UnicodeEncodeError 打死整个构建
    （GitHub CI windows-exe-smoke 实测）。log() 必须自己兜底。"""
    scripts = Path(__file__).resolve().parent.parent / "scripts"
    r = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, sys.argv[1]); "
         "import build_worker_runtime as brt; brt.log('↓ https://example.invalid')",
         str(scripts)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        env={**os.environ, "PYTHONIOENCODING": "cp1252"})
    assert r.returncode == 0, r.stderr


# ---------------- AI CLI 的 Windows 落点 --------------------------------------

def test_cli_search_dirs_cover_windows_install_locations(monkeypatch):
    """Windows 上 PATH 最不可靠：npm 全局目录要重开终端才进 PATH，从桌面
    快捷方式启动的进程拿到的又是启动那一刻的旧环境块。"""
    monkeypatch.setattr(os, "name", "nt", raising=False)
    dirs = " | ".join(ai_bridge._search_dirs("codex")).lower()
    assert "npm" in dirs
    # 微软商店版 codex 的真身在受 ACL 保护的 WindowsApps 包体里，
    # 能用的入口是这个执行别名目录——少了它，商店版就是「系统找不到」
    assert "microsoft\\windowsapps" in dirs
    assert "winget" in dirs and "scoop" in dirs


def test_npm_cmd_shim_resolves_to_real_executable(tmp_path, monkeypatch):
    """npm 装出来的是 `codex.cmd` 外壳，经 cmd.exe 中转会吃掉提示词里的
    `%`、`&`、`^`、`<`、`>`、`|`——中文提示里写个「透明度调到 50%」就够出事。
    外壳里指向的原生 exe 拿出来直接跑，整类问题消失。"""
    pkg = tmp_path / "node_modules" / "@openai" / "codex" / "bin"
    pkg.mkdir(parents=True)
    exe = pkg / "codex.exe"
    exe.write_bytes(b"MZ")
    shim = tmp_path / "codex.cmd"
    shim.write_text(
        '@ECHO off\r\n'
        '"%dp0%\\node_modules\\@openai\\codex\\bin\\codex.exe" %*\r\n',
        encoding="utf-8")
    assert ai_bridge._resolve_shim(str(shim)) == [str(exe.resolve())]


def test_plain_executable_is_not_treated_as_shim(tmp_path):
    """真正的可执行文件不该被当成外壳去解析。"""
    exe = tmp_path / "codex.exe"
    exe.write_bytes(b"MZ")
    assert ai_bridge._resolve_shim(str(exe)) is None


def test_capabilities_tells_where_it_looked_when_missing(monkeypatch):
    """没找到 CLI 时要说清「找过哪些地方」。干甩一句「未安装」的结果是
    用户明明装了却无从下手（朋友的商店版 codex 就是这样）。"""
    monkeypatch.setattr(ai_bridge, "_cli_candidates", lambda name: [])
    ai_bridge.invalidate_capabilities()
    caps = ai_bridge.capabilities(refresh=True)
    for name in ("codex", "claude"):
        info = caps["providers"][name]
        assert info["installed"] is False
        assert info["searched"], "必须报出找过的目录"
    ai_bridge.invalidate_capabilities()


# ---------------- 渲染解释器探测 ----------------------------------------------

def test_worker_python_candidates_cover_common_windows_installs(monkeypatch):
    """python.org、conda、以及 PATH 里的 python 都要在候选里。
    独立应用没有「自己的解释器」可用，全靠这份清单把用户已有环境翻出来。"""
    monkeypatch.setattr(os, "name", "nt", raising=False)
    monkeypatch.setattr(sys, "platform", "win32", raising=False)
    monkeypatch.setattr(pool, "is_frozen", lambda: True)
    # 用户配置的读取会经 pathlib（config_dir 在 nt 分支上构造 WindowsPath），
    # 这里测的是候选清单本身，把它短路掉
    monkeypatch.setattr(pool.config, "worker_python", lambda: None)
    # shutil.which 在 os.name 被改成 nt 之后会去调只有 Windows 才有的 _winapi
    monkeypatch.setattr(pool.shutil, "which", lambda *a, **k: None)
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\u\AppData\Local")
    # glob 命中与否取决于跑测试的这台机器，所以盯的是「有没有去这些地方找」
    probed: list[str] = []
    monkeypatch.setattr(pool, "_glob", lambda pat: probed.append(pat) or [])

    cands = " | ".join(c for c in pool._candidate_pythons() if c).lower()
    assert "anaconda3" in cands and "miniconda3" in cands   # conda
    globbed = " | ".join(probed).lower()
    assert r"programs\python" in globbed                    # python.org 安装器
    assert globbed.count("python*") >= 2                    # 还兜了 C:\ 根


def test_frozen_app_never_probes_its_own_executable(monkeypatch):
    """打包成独立应用时 sys.executable 是 Magplot 自己，不是解释器。
    拿它去 `-c "import matplotlib"` 会以莫名其妙的参数把应用再启动一次。"""
    monkeypatch.setattr(pool, "is_frozen", lambda: True)
    assert sys.executable not in pool._candidate_pythons()
    monkeypatch.setattr(pool, "is_frozen", lambda: False)
    assert sys.executable in pool._candidate_pythons()


# ---------------- 一键诊断包 --------------------------------------------------

def test_diagnostics_bundle_redacts_secrets_and_home(client, tmp_path, monkeypatch):
    """诊断包会被用户贴进 issue 或发到群里：密钥和个人目录一个都不许漏。"""
    import io
    import zipfile

    from magplot.engine import ai_providers, diagnostics

    ai_providers.save({"label": "Kimi", "agent": "claude", "api_key": "sk-abcdef123456",
                       "base_url": "https://api.moonshot.cn/anthropic"})
    monkeypatch.setattr(diagnostics, "_log_tail",
                        lambda n=400: [f"用户目录 {os.path.expanduser('~')}/paper",
                                       "Authorization: Bearer sk-abcdef123456"])

    resp = client.get("/api/diagnostics/bundle")
    assert resp.status_code == 200
    assert resp.mimetype == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(resp.data))
    assert set(z.namelist()) >= {"report.json", "app.log", "README.txt"}

    blob = "\n".join(z.read(n).decode("utf-8") for n in z.namelist())
    assert "sk-abcdef123456" not in blob        # 密钥
    assert os.path.expanduser("~") not in blob  # 个人主目录
    report = json.loads(z.read("report.json"))
    assert report["magplot"]["version"]
    assert "platform" in report["system"]
    assert report["ai_endpoints"][0]["has_key"] is True   # 有没有 key 要报，key 本身不报


def test_diagnostics_bundle_survives_missing_log(client, monkeypatch):
    """日志文件还没生成时也要出得来包——排障工具自己不能先炸。"""
    from magplot.engine import diagnostics

    monkeypatch.setattr(diagnostics, "_log_path", lambda: Path("/nonexistent/app.log"))
    assert client.get("/api/diagnostics/bundle").status_code == 200


# ---------------- 内置渲染 runtime（Windows 桌面版）----------------------------
#
# 「干净的 Windows 电脑上装完就能渲染」是产品承诺，而验证它的地方只有 CI 的
# Windows runner。下面这些是**不需要真 Windows 也能钉死**的部分——恰好也是
# 历来最容易出错的部分：路径解析在非目标平台上直接崩、`._pth` 写成正斜杠、
# 往安装目录里写缓存。

def test_runtime_path_logic_never_instantiates_a_foreign_pathlib():
    """`Path(...)` 按 os.name 分派 Posix/Windows 实现，在另一个平台上构造
    直接抛 UnsupportedOperation——真踩过：加了内置 runtime 之后，
    两个「模拟 Windows」的老用例当场全红。

    engine/runtime.py 的定位逻辑因此全程 os.path 拼字符串。这条用例盯住它，
    别哪天有人图省事又把 pathlib 写回去。
    """
    from magplot.engine import runtime as rt

    src = Path(rt.__file__).read_text(encoding="utf-8")
    body = src.split("# 定位", 1)[1].split("# manifest", 1)[0]
    # 只看真代码：注释和 docstring 里当然要提到 Path(...) 说明为什么不用它
    code = "\n".join(ln for ln in body.splitlines()
                     if ln.strip() and not ln.lstrip().startswith("#"))
    assert "Path(" not in code, "定位逻辑里不允许出现 pathlib"

    # 直接验行为：把 os.name 改成 nt，整条链路都不许炸
    import os as _os
    old = _os.name
    try:
        _os.name = "nt"
        rt._candidate_roots()
        rt.status()
        assert rt.runtime_python(r"C:\Magplot\runtime").endswith(r"\python.exe")
    finally:
        _os.name = old


def test_bundled_runtime_lives_under_the_onedir_internal_folder(tmp_path, monkeypatch):
    """PyInstaller onedir 的布局是 `Magplot.exe` + `_internal\\`，
    spec 的 datas 落在 `_internal\\runtime`。安装程序按 recursesubdirs 收，
    免安装 zip 直接打包整个目录——两条发行路径都指望这个落点。"""
    import json as _json

    from magplot.engine import runtime as rt

    internal = tmp_path / "_internal" / "runtime"
    internal.mkdir(parents=True)
    # 解释器落点必须问 runtime_python()：Windows 是 runtime\python.exe，
    # POSIX 是 runtime/bin/python3。以前硬编码 bin/python3，这个「Windows
    # 回归」用例反而只在 macOS/Linux 上绿——真 Windows 上一跑就穿帮。
    py = Path(rt.runtime_python(str(internal)))
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text("#!/bin/sh\n")
    (internal / "runtime-manifest.json").write_text(_json.dumps({
        "schema": 1, "python": {"version": "3.13.15"},
        "packages": {"numpy": "2.5.2"}}), encoding="utf-8")

    monkeypatch.setattr(rt, "is_frozen", lambda: True)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "Magplot.exe"))
    assert rt.runtime_root() == str(internal)


def test_bundled_worker_writes_no_cache_into_the_install_directory(monkeypatch):
    """安装目录常在 `C:\\Program Files\\Magplot`（普通用户没写权限），
    而 Python 默认会往 site-packages 旁边写 __pycache__、matplotlib 会往
    `~/.matplotlib` 写字体缓存。前者会报权限错，后者卸载后留垃圾。"""
    from magplot.engine import config as cfg
    from magplot.engine import runtime as rt

    # 真正的保证是命令行的 -B：embeddable 靠 ._pth 定路径，而 CPython 找到
    # ._pth 就 use_environment = 0，PYTHON* 那条路在这里不可靠
    assert "-B" in rt.child_args()
    env = rt.child_env({"PATH": r"C:\Windows\system32"})
    data = str(cfg.data_dir())
    # MPLCONFIGDIR 不是 PYTHON* 变量，matplotlib 直接读 os.environ，一定生效
    assert env["MPLCONFIGDIR"].startswith(data)
    assert env["PATH"] == r"C:\Windows\system32", "不该动用户原有的 PATH"


def test_windows_desktop_missing_runtime_says_reinstall_not_install_python(
        tmp_path, monkeypatch):
    """朋友那台机器上如果 runtime 没打进去，弹「请先安装 Python 3.10 以上」
    是纯粹的误导——他什么都没做错，是我们的包不完整。"""
    from magplot.engine import runtime as rt

    monkeypatch.setattr(rt, "is_frozen", lambda: True)
    monkeypatch.setattr(pool, "is_frozen", lambda: True)
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "Magplot.exe"))
    monkeypatch.delenv("MAGPLOT_RUNTIME_DIR", raising=False)
    monkeypatch.setattr(pool, "_prioritized_candidates", lambda: [])
    pool.reset_worker_python()
    try:
        with pytest.raises(pool.WorkerError) as exc:
            pool.find_worker_python()
        assert exc.value.code == rt.CODE_MISSING
        assert "Python 3.10" not in str(exc.value)
    finally:
        pool.reset_worker_python()


# ---------------- 子进程卫生：黑框与解码 --------------------------------------
#
# 桌面版是 GUI 子系统进程（console=False），自己没有控制台。它每 spawn 一个
# 控制台子系统的子进程（python.exe / pip / codex），Windows 就现分配一个新
# 控制台并显示出来——用户每渲染一张图都看见黑框闪一下。macOS 上完全看不到
# 这个现象，所以只能靠静态扫描钉死。

def _subprocess_spawns(path: Path) -> list[tuple[str, int, ast.Call]]:
    """文件里所有 `subprocess.Popen` / `subprocess.run` 调用节点。

    连 `import subprocess as sp` 这种别名一起认——只匹配字面量 `subprocess.`
    的话，换个写法就能绕过这条用例，那它就白写了。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    aliases = {"subprocess"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "subprocess":
                    aliases.add(a.asname or a.name)

    found = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("Popen", "run")
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in aliases):
            found.append((f"{path.name}:{node.lineno}", node.lineno, node))
    return found


def _spawn_sites() -> list[tuple[str, ast.Call]]:
    """全后端（engine/ 各模块 + app.py）的 spawn 点。

    app.py 必须一起扫：审计只点了 engine/ 的六处，而 `/api/diagnostics` 里
    还藏着第七处（`import subprocess as sp` 的别名写法）——正是「只扫一个
    目录」这种局部视野让它漏了两轮。
    """
    engine = Path(pool.__file__).parent
    files = sorted(engine.glob("*.py")) + [Path(m.__file__)]
    return [(where, call)
            for py in files
            for where, _lineno, call in _subprocess_spawns(py)]


def test_every_backend_subprocess_hides_the_console_window():
    """每个 spawn 都必须传 creationflags。

    非 Windows 上常量为 0，等同于不传——所以「全都传」没有代价，而漏一个
    就是用户可见的黑框。审计当时六处漏传（外加 app.py 里没点出来的第七处），
    靠人眼逐个核对找出来的；这条用例接手这件事。
    """
    checked = []
    for where, call in _spawn_sites():
        kwargs = {kw.arg for kw in call.keywords}
        assert "creationflags" in kwargs, (
            f"{where} 的 subprocess 调用漏了 creationflags="
            "CREATE_NO_WINDOW（Windows 上会闪黑框）")
        checked.append(where)
    # 一个都没扫到 = 匹配逻辑坏了，别让空断言冒充通过
    assert len(checked) >= 9, f"只扫到 {checked}，AST 匹配逻辑可能失效了"


def test_every_backend_subprocess_that_decodes_pins_utf8():
    """凡是 `text=True` 的 spawn 都要钉死 UTF-8。

    不钉就跟随系统区域编码，cp936 下读到中文路径 / pip 进度条 / worker 回来的
    µ、⁻¹ 直接抛 UnicodeDecodeError。`text=True` 才需要——不解码的调用
    （只看 returncode）拿的是 bytes，钉了反而是噪音。
    """
    for where, call in _spawn_sites():
        kwargs = {kw.arg for kw in call.keywords}
        if "text" not in kwargs and "universal_newlines" not in kwargs:
            continue
        assert "encoding" in kwargs and "errors" in kwargs, (
            f"{where} 用了 text=True 却没钉 encoding/errors，cp936 下会解码失败")


def test_create_no_window_has_exactly_one_definition():
    """常量散成好几份就会各自漂移（ai_bridge 曾自带一份）。

    唯一出处是 runtime.py——CLAUDE.md 把它定为 Windows 平台判断的唯一出处。
    """
    from magplot.engine import runtime as rt

    engine = Path(pool.__file__).parent
    definers = [py.name for py in sorted(engine.glob("*.py"))
                if any(ln.startswith("CREATE_NO_WINDOW")
                       for ln in py.read_text(encoding="utf-8").splitlines())]
    assert definers == ["runtime.py"], f"重复定义：{definers}"

    # 值本身：Windows 上是 CREATE_NO_WINDOW，别处必须是 0（等同于不传）
    expected = 0x08000000 if os.name == "nt" else 0
    assert rt.CREATE_NO_WINDOW == expected


def test_upgrade_pins_utf8_so_pip_output_cannot_explode(monkeypatch):
    """cp936 环境下 pip 的输出（进度条、中文路径）用系统编码一解码就抛
    UnicodeDecodeError，而 apply_upgrade 只接 OSError/TimeoutExpired——
    异常会原样逃出去变成 500，用户点「立即升级」拿到一串 traceback。
    """
    from magplot.engine import updater

    seen = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(updater, "_fetch_latest_release", lambda: None)
    monkeypatch.setattr(updater, "upgrade_command", lambda r: ["pip", "install", "-U", "x"])
    monkeypatch.setattr(updater.subprocess, "run", fake_run)

    out = updater.apply_upgrade()
    assert out["ok"] is True and out["restart_required"] is True
    assert seen.get("encoding") == "utf-8"
    assert seen.get("errors") == "replace"


# ---------------- 换名盖不掉正被读的文件（os.replace → WinError 5） ----------

def test_render_cache_yields_when_the_target_is_locked_by_a_reader(tmp_path,
                                                                   monkeypatch):
    """Windows：目标正被 `send_file` 读着时 `os.replace` 报 WinError 5。

    POSIX 的 rename 盖得掉一个开着的文件，Windows 盖不掉（werkzeug 的
    `open(path, "rb")` 没带 FILE_SHARE_DELETE）。真机现象：16 个并发
    `/api/render` 撞一次就有人拿到 500，而图其实好好地躺在磁盘上。
    """
    figs = _figs(tmp_path)
    src = figs / "Fig1.pdf"
    m.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = m.CACHE_DIR / "locked-target.png"

    m._write_render_cache(src, 200, cached)          # 先有一份完整的
    good = cached.read_bytes()
    assert good.startswith(b"\x89PNG\r\n\x1a\n")

    def denied(_a, _b):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(m.os, "replace", denied)
    m._write_render_cache(src, 200, cached)          # 退让，不许抛
    assert cached.read_bytes() == good, "已经在那儿的同一张图不该被动过"
    assert not list(m.CACHE_DIR.glob("*.part.png")), "临时文件必须清掉"


def test_a_replace_that_never_succeeds_still_fails_loudly(tmp_path, monkeypatch):
    """退让只对「目标已经是同一张完整的图」成立。

    目标不存在还一直换不过去 = 真出事了（盘满、权限、杀毒软件锁着临时文件），
    这时**必须如实抛出**——伪装成成功，用户得到的是一个永远画不出来的面板。
    """
    figs = _figs(tmp_path)
    m.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = m.CACHE_DIR / "never-lands.png"

    monkeypatch.setattr(m, "_REPLACE_BACKOFF_S", 0.0)   # 别让重试拖慢测试
    monkeypatch.setattr(m.os, "replace",
                        lambda _a, _b: (_ for _ in ()).throw(
                            PermissionError(5, "Access is denied")))
    with pytest.raises(PermissionError):
        m._write_render_cache(figs / "Fig1.pdf", 200, cached)
    assert not list(m.CACHE_DIR.glob("*.part.png")), "失败路径也不许留临时文件"


# ---------------- 关进程慢：poll() 还说活着，握手其实早就失败了 --------------

class _ZombiePopen:
    """起得来、握不上手、还迟迟不肯退——Windows 关进程的那个窗口。

    `stdin` 一写就 EINVAL（真机上 hello 就是这么失败的），`stdout` 立刻 EOF，
    而 `poll()` 永远回 None：进程对象看着还活着。
    """

    instances: list = []

    def __init__(self, *_a, **_kw):
        self.pid = 4321 + len(self.instances)
        self.stdout = io.StringIO("")
        self.stdin = self
        self.killed = False
        _ZombiePopen.instances.append(self)

    # stdin
    def write(self, _line):
        raise OSError(22, "Invalid argument")

    def flush(self):
        pass

    # 进程
    def poll(self):
        return None                       # ← 关键：永远说「我还活着」

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return 0


def test_workerd_that_dies_while_poll_still_says_alive_gets_disabled(monkeypatch):
    """起来就崩的 workerd 必须停用（回退 Python 池），不能无限重启。

    「进程对象还在」不等于「起来了」——Windows 上进程拆除比 POSIX 慢，
    hello 已经失败而 `poll()` 还是 None。少了握手这一层，重启计数一次都不加，
    `_MAX_RESTARTS` 永远到不了：每次渲染白等一轮 spawn + 握手，用户只觉得
    「越来越慢」，日志里一个字都没有。CI 的 windows-latest 上实测过。
    """
    _ZombiePopen.instances = []
    monkeypatch.setattr(workerd_client.subprocess, "Popen", _ZombiePopen)

    c = workerd_client.WorkerdClient("magplot-workerd")
    for _ in range(6):
        try:
            c.ensure_started()
        except workerd_client.WorkerdError:
            pass
        if c.disabled:
            break

    assert c.disabled, "反复起来就崩必须停用 workerd"
    assert len(_ZombiePopen.instances) <= workerd_client._MAX_RESTARTS + 1, \
        "重启次数不该超过上限"
    # 半启动的都被收掉了：不收就是每重启一次泄漏一个子进程
    assert all(z.killed for z in _ZombiePopen.instances[:-1])
