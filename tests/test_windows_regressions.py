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
  * 只装了桌面版时外部程序找不到 CLI（GUI 子系统的 exe 没有 stdout）
  * 测试自己的 id 太长撑爆环境变量（32767 上限）
  * 开发工具往控制台打中文（本地代码页 ≠ UTF-8）

跨平台可跑：拿不到真实 Windows 语义的地方就直接测**那段逻辑本身**
（monkeypatch 出同样的失败），而不是假装在 Windows 上跑。
"""

import ast
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath

import pymupdf
import pytest

from tavotto import app as m
from tavotto.engine import ai_agents, ai_bridge, pool, workerd_client


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
    (figs / "tavotto_registry.json").write_text(
        json.dumps(
            {
                "version": 1,
                "scripts": {
                    "fig1.py": {"entry": "main", "cost": "light", "notes": "", "stems": ["Fig1"]},
                },
            }
        ),
        encoding="utf-8",
    )
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
        script_sha1 = ""  # 空 = 会话没记指纹，前置的脚本检查自然跳过
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
    _fake_workers(monkeypatch, figs, tmp_path, b"%PDF-1.4\n")  # 假装导出成功
    _lock(monkeypatch, "Fig1.pdf")

    resp = client.post("/api/engine/update_source", json={"id": "Fig1.pdf", "patches": []})
    assert resp.status_code == 409
    body = resp.get_json()
    assert body["code"] == "file_locked"
    assert body["file"] == "Fig1.pdf"
    assert "关闭" in body["error"]  # 告诉用户该去做什么
    # 半成品不许留在图库里
    assert not list(figs.glob(".*updating"))
    assert (figs / "Fig1.pdf").is_file()  # 原文件完好


def test_write_back_rolls_back_when_the_second_target_is_locked(client, tmp_path, monkeypatch):
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

    body = client.post(
        "/api/engine/update_source", json={"id": "Fig1.pdf", "patches": []}
    ).get_json()
    assert body["file"] == "Fig1.png"
    assert body["rolled_back"] == ["Fig1.pdf"] and body["rollback_failed"] == []
    assert body["updated"] == []  # 回滚成功 = 没有文件停在「已被换掉」
    assert (figs / "Fig1.pdf").read_bytes() == before_pdf
    assert not list(figs.glob(".*updating"))


# ---------------- 路径：盘符、反斜杠、中文与空格 ------------------------------


def test_browse_accepts_backslashes_and_chinese_spaces(client, tmp_path):
    """路径可以手输/粘贴，用户粘过来的就是资源管理器那种反斜杠写法。"""
    target = tmp_path / "我的 论文" / "figures"
    target.mkdir(parents=True)
    for raw in (str(target), str(target).replace("/", "\\") if os.name == "nt" else str(target)):
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

    **取材有两个坑，都不是产品缺陷，但都会让这条红成别的形状：**

    ① **`tries` 给大**（默认 20 是产品值，这里不是在量它）。判据的主语是
    "它会不会让开"，不是"20 个够不够"。用默认值时这条在全量套件里会偶发红：
    这里拿的是**临时端口**，而内核是**顺序**发的——`busy + 1 … busy + 20`
    精确地就是接下来要发出去的那 20 个号；而 `resolve_port` 在扫描之前先做
    一次 **1.5 秒超时**的 `tavotto_is_serving()` 探测（对端 listen 了但从不
    accept），那 1.5 秒足够同进程的池 / 子进程 / 后台线程把它们全用掉。
    真实入参是固定的 5089（不在临时端口区间），这条链一条都不成立。

    ② **要一个离天花板还有余量的端口**：顺延不越过 `MAX_PORT`，`busy` 落在
    顶端 `tries` 个之内时它无处可去、只能退回 `preferred`——那时红的是
    "没让开"，与本判据无关。

    天花板本身**不在这条用例里量**：它依赖抢得到那几个高位端口，而那是概率
    性的，CI 上大概率 skip，而 skip 在报告里长得和通过一模一样。那条不变式由
    下面两条确定性用例看着。
    """
    tries = 200
    s = socket.socket()
    try:
        for _ in range(20):
            s.bind(("127.0.0.1", 0))
            s.listen(1)
            busy = s.getsockname()[1]
            if busy + tries <= m.MAX_PORT:
                break
            s.close()
            s = socket.socket()
        else:  # pragma: no cover - 连要 20 次都落在顶端，只可能是端口耗尽
            pytest.skip("要不到一个离端口天花板还有余量的临时端口")
        chosen = m.resolve_port(busy, tries=tries)
        assert chosen is not None and chosen != busy
        assert m.port_is_free(chosen), f"让开了，但让到一个也不空闲的端口: {chosen}"
    finally:
        s.close()


def test_the_port_probe_raises_above_the_ceiling_instead_of_saying_busy():
    """**这条是下一条的前提，先把它钉住。**

    `port_is_free()` 只 catch `OSError`，而 `bind()` 收到 65535 以上的号抛的是
    `OverflowError`——所以越界不是"返回 False"，是**一个没人接的异常**。哪天
    CPython 改成抛 `OSError`（或者有人给 `port_is_free` 加上 catch），下面那条
    clamp 的用例就不再是在量真问题了，这里会先红出来提醒。

    不占任何端口，任何平台任何时刻都跑。
    """
    with pytest.raises(OverflowError):
        m.port_is_free(m.MAX_PORT + 1)


def test_resolve_port_never_probes_above_the_ceiling(monkeypatch):
    """顺延**绝不越过 `MAX_PORT`**。

    越过去的表现恰恰是这个函数自己承诺不许发生的那件事：`bind(65536)` 抛
    `OverflowError`，而 `port_is_free()` 不 catch 它——`preferred` 落在顶端
    `tries` 个之内、且那几个都被占着时，`resolve_port` **当场崩掉**。
    默认参数下窗口是 65516–65535；`tries` 调大窗口就跟着变大。

    **刻意不占真端口**：那需要抢到 65530–65535 那几个号，而它们在临时端口
    区间里、归谁每次都不一样——CI 上大概率 skip，而一条从没执行过的门禁不会
    保持正确，何况 skip 在报告里长得和通过一模一样。这里用替身把"全占满"
    做成确定性的（`tests/test_projects.py` 里已经是这个写法），并让替身在越界
    时**抛真实的那个异常**——不然量到的只是"循环范围写对了"，不是"崩不掉"。
    """
    probed: list[int] = []

    def fake_port_is_free(p: int) -> bool:
        if p > m.MAX_PORT:  # 与真 bind() 同一种失败
            raise OverflowError("bind(): port must be 0-65535.")
        probed.append(p)
        return False  # 全占满：确定性地走完整个扫描循环

    monkeypatch.setattr(m, "port_is_free", fake_port_is_free)
    monkeypatch.setattr(m, "tavotto_is_serving", lambda p: False)

    chosen = m.resolve_port(m.MAX_PORT - 5, tries=20)

    assert chosen == m.MAX_PORT - 5, "扫不动了要退回 preferred（与'全占满了'同一条出口）"
    assert probed, "一个端口都没探——判据其实没跑到扫描循环"
    # **两条边都要钉**。只写 `<= MAX_PORT` 的话，把 clamp 写成
    # `min(..., MAX_PORT)`（少一个 +1）照样绿——那会**悄悄丢掉 65535 这个合法
    # 端口**：不崩、不报错，只是永远不用它。变异测试里这一条正是漏网的那个。
    assert max(probed) == m.MAX_PORT, (
        f"扫描没覆盖到 65535（最高只探到 {max(probed)}）——合法端口被 clamp 吃掉了"
    )
    # `preferred` 自己那次探测也在 probed 里（`resolve_port` 的第一行），
    # 所以区间是 [preferred, MAX_PORT] 而不是 (preferred, MAX_PORT]
    assert probed == list(range(m.MAX_PORT - 5, m.MAX_PORT + 1)), f"扫描区间不对: {probed}"


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
        "    ax.set_xlabel('波长 / µm⁻¹')\n"  # 中文 + µ + 上标：cp936 的经典雷区
        "    fig.savefig('CJK_1.pdf')\n",
        encoding="utf-8",
    )

    proc = subprocess.Popen(
        [
            worker_py,
            str(WORKER_PY),
            "--script",
            str(figs / "fig_cjk.py"),
            "--figures-dir",
            str(figs),
            "--out-dir",
            str(tmp_path / "out"),
            "--sandbox",
            str(tmp_path / "sandbox"),
            "--entry",
            "main",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "gbk:replace", "PYTHONUTF8": "0", "LC_ALL": "C"},
    )
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


def test_every_entry_point_reconfigures_stdout_to_utf8():
    """启动信息与子命令的输出都有中文；stdout 一旦不是真控制台就退回系统
    区域编码，print 会 UnicodeEncodeError 直接打死进程（用户看到「启动即崩」，
    调用方拿到的是 traceback 而不是那行 JSON）。

    实现是**唯一一份**（`engine/cli.py::use_utf8_streams`），三个入口各自
    在做任何输出之前调它一次。少一个都会在某条安装形态上复现：
      * `tavotto/cli_entry.py` —— pip/pipx 的 console script 与 `python -m tavotto`
      * `packaging/entry.py`   —— 冻结产物（Tavotto.exe / tavotto-cli.exe）
      * `app.py::main`         —— 主入口自己
    """
    repo = Path(__file__).resolve().parent.parent
    impl = (repo / "src" / "tavotto" / "engine" / "cli.py").read_text(encoding="utf-8")
    assert 'reconfigure(encoding="utf-8"' in impl
    for rel in ("src/tavotto/cli_entry.py", "packaging/entry.py", "src/tavotto/app.py"):
        src = (repo / rel).read_text(encoding="utf-8")
        assert "use_utf8_streams()" in src, f"{rel} 没有把 stdout 钉成 UTF-8"


@pytest.mark.parametrize("argv", [["doctor"], ["doctor", "--json"], ["--help"]])
def test_cli_entry_survives_a_non_utf8_console(tmp_path, argv):
    """**子命令的输出全是中文，而分派发生在 UTF-8 重配之前就会当场崩。**

    `app.main()` 一开头就把 stdout/stderr reconfigure 成 utf-8，正是为了
    Windows 上「stdout 不是真控制台就退回系统区域编码」这件事。把子命令
    分派提前到 `tavotto/cli_entry.py`（为了不为一次交接付 Flask 的冷启动）
    之后，`doctor` 跑在重配**之前**：cp1252/cp936 的控制台上第一句
    `print(f"* Tavotto …（交接协议 v1）")` 直接 UnicodeEncodeError，
    退出码 1，调用方（安装器、Codex 插件）拿到的是一堆 traceback。

    只在 Windows 上发作，本机与 Linux 全绿——所以用 `PYTHONIOENCODING`
    把那台机器搬过来（与本文件里 worker 那条同一手法）。
    """
    env = {
        **os.environ,
        "PYTHONIOENCODING": "cp1252",
        "TAVOTTO_CONFIG_DIR": str(tmp_path / "cfg"),
        "TAVOTTO_DATA_DIR": str(tmp_path / "data"),
        "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "src"),
    }
    proc = subprocess.run(
        [sys.executable, "-m", "tavotto", *argv],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=120,
    )
    assert "UnicodeEncodeError" not in proc.stderr, proc.stderr
    assert proc.returncode == 0, proc.stderr


def test_packaging_entry_points_reconfigure_stdout_to_utf8():
    """打包/冒烟脚本的日志带中文与 ✓✗↓；Windows 管道 stdout 默认 cp1252/cp936，
    不 reconfigure 的话第一条日志就 UnicodeEncodeError 打死整个构建
    （windows-exe-smoke 首跑连撞两处：build_worker_runtime 的「↓」、
    tavotto.spec 的中文 print）。新加的入口脚本都要沿用 build_frontend.py
    的同一段写法。"""
    repo = Path(__file__).resolve().parent.parent
    for rel in (
        "packaging/tavotto.spec",
        "scripts/build_frontend.py",
        "scripts/build_desktop.py",
        "scripts/build_worker_runtime.py",
        "scripts/smoke_app.py",
        "scripts/smoke_desktop.py",
        # Codex 插件的交接脚本：Codex 调它时 stdout 就是管道，
        # 输出的 JSON 带中文（hint / tavotto open 回来的错误）
        "codex-plugin/skills/tavotto-figure/scripts/handoff.py",
        # 这两个的结论全是中文，而 pytest 与 CI 都是捕获着调它们的
        "scripts/gen_preflight_vectors.py",
        "scripts/build_mcp_widget.py",
    ):
        src = (repo / rel).read_text(encoding="utf-8")
        assert 'reconfigure(encoding="utf-8"' in src, (
            f"{rel} 没做 stdout reconfigure，Windows 管道下中文日志会打死进程"
        )


def test_codex_handoff_json_survives_cp1252_stdout():
    """Codex 插件的交接脚本在非 UTF-8 stdout 下必须照样吐出那行 JSON。

    实测（CI 的 windows-latest 腿）：Codex 调这个脚本时 stdout 是管道，Windows 上
    于是退回 cp1252/cp936；输出里有中文（这里走的是「路径不存在」那条），
    第一次 print 直接 UnicodeEncodeError——**调用方看到的是脚本挂了，
    而不是那行说明该怎么修的 JSON**，整条交接在 Windows 上等于不可用。
    """
    repo = Path(__file__).resolve().parent.parent
    script = repo / "codex-plugin/skills/tavotto-figure/scripts/handoff.py"
    r = subprocess.run(
        [sys.executable, str(script), str(repo / "不存在的图.pdf")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        env={**os.environ, "PYTHONIOENCODING": "cp1252"},
    )
    assert r.returncode == 2, r.stderr  # 2 = 路径不对，不是 1（崩了）
    assert "路径不存在" in json.loads(r.stdout.strip().splitlines()[-1])["error"]


def test_maintenance_scripts_report_under_cp1252_stdout(tmp_path):
    """两个新维护脚本在非 UTF-8 stdout 下必须照样把结论说出来。

    它们都是**被捕获着调用**的（`tests/test_preflight.py` spawn 校验器、
    CI 的 frontend job 跑画布同步门禁），输出又全是中文。不钉 UTF-8 的话
    Windows 上第一次 print 就 UnicodeEncodeError——退出码变成 1，
    于是「向量对不上」和「画布产物过期」这两个门禁在 Windows 腿上**永远是红的，
    而且红的原因与它们要看护的事毫无关系**。空转的门禁比没有门禁更坏。
    """
    repo = Path(__file__).resolve().parent.parent
    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}

    # 校验器：向量与实现一致时退 0，并把那句中文结论说出来
    r = subprocess.run(
        [sys.executable, str(repo / "scripts/gen_preflight_vectors.py")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        env=env,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Python 实现一致" in r.stdout

    # 画布同步门禁：--check 不需要 Node，纯指纹比对
    r = subprocess.run(
        [sys.executable, str(repo / "scripts/build_mcp_widget.py"), "--check"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        env=env,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "画布产物与源码一致" in r.stdout


def test_widget_fingerprint_is_the_same_on_windows_and_posix():
    """画布同步门禁的指纹**必须跨平台一致**，否则它在 Windows 腿上永远是红的。

    CI 的 windows-latest 腿实测（本 PR 连撞两轮），三处差异各占一份：

      * **路径分隔符**——`str(Path("web/src/a.ts"))` 在 Windows 上是
        `web\\src\\a.ts`；
      * **行尾**——GitHub 的 Windows runner 默认 `core.autocrlf=true`，
        检出的文本文件是 CRLF；
      * **遍历顺序**——`sorted(Path)` 在 Windows 上比的是**小写化**后的字符串
        （大小写不敏感），`Zebra.ts` 与 `apple.ts` 的先后在两个平台正好相反。

    「永远红的门禁」与「空转的门禁」一样坏：它报的不是它要看护的那件事，
    看的人学会的是忽略它。

    这条在 macOS/Linux 上照样跑得出来——`PureWindowsPath` 是纯路径，
    不像 `WindowsPath` 那样在别的平台上构造就抛 UnsupportedOperation。
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import build_mcp_widget

    # 一份「Windows 视角」：反斜杠 + CRLF + 大小写不敏感的那个顺序
    windows = build_mcp_widget.digest(
        [
            (PureWindowsPath(r"web\src\lib\apple.ts"), b"a\r\nb\r\n"),
            (PureWindowsPath(r"web\src\lib\Zebra.ts"), b"z\r\n"),
        ]
    )
    # 一份「POSIX 视角」：正斜杠 + LF + 大小写敏感的那个顺序
    posix = build_mcp_widget.digest(
        [
            (PurePosixPath("web/src/lib/Zebra.ts"), b"z\n"),
            (PurePosixPath("web/src/lib/apple.ts"), b"a\nb\n"),
        ]
    )
    assert windows == posix, "同一份源码在两个平台上算出了不同的指纹"


def test_widget_fingerprint_still_notices_a_real_change():
    """上一条是「别乱报」，这条是「别不报」——规范化不能规范到什么都一样。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import build_mcp_widget

    base = [(PurePosixPath("web/src/a.ts"), b"x\n")]
    assert build_mcp_widget.digest(base) != build_mcp_widget.digest(
        [(PurePosixPath("web/src/a.ts"), b"y\n")]
    ), "内容变了却算出同一个指纹"
    assert build_mcp_widget.digest(base) != build_mcp_widget.digest(
        [(PurePosixPath("web/src/b.ts"), b"x\n")]
    ), "文件名变了却算出同一个指纹"


def test_codex_handoff_pins_utf8_on_every_decoding_spawn():
    """它读的是 `tavotto open` 的中文 JSON 与用户脚本的 traceback。

    `text=True` 不钉 encoding 就跟随系统区域编码，cp936 下解码当场抛——
    与 `test_every_backend_subprocess_that_decodes_pins_utf8` 同一条纪律，
    只是那条只扫 engine/ 与 app.py，够不到插件目录。
    """
    repo = Path(__file__).resolve().parent.parent
    script = repo / "codex-plugin/skills/tavotto-figure/scripts/handoff.py"
    tree = ast.parse(script.read_text(encoding="utf-8"))
    checked = 0
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run"
        ):
            continue
        kwargs = {kw.arg for kw in node.keywords}
        if "text" not in kwargs:
            continue  # 只看 returncode 的那次不解码
        assert "encoding" in kwargs and "errors" in kwargs, (
            f"handoff.py 第 {node.lineno} 行 text=True 却没钉 encoding/errors"
        )
        checked += 1
    assert checked >= 2, "一处都没扫到 = 匹配逻辑坏了，别让空断言冒充通过"


def test_runtime_build_log_survives_cp1252_stdout():
    """runtime 构建脚本的日志带「↓」（U+2193）；Windows 上管道 stdout 默认
    cp1252/cp936，第一条下载日志就 UnicodeEncodeError 打死整个构建
    （GitHub CI windows-exe-smoke 实测）。log() 必须自己兜底。"""
    scripts = Path(__file__).resolve().parent.parent / "scripts"
    r = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "import build_worker_runtime as brt; brt.log('↓ https://example.invalid')",
            str(scripts),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env={**os.environ, "PYTHONIOENCODING": "cp1252"},
    )
    assert r.returncode == 0, r.stderr


# ---------------- AI CLI 的 Windows 落点 --------------------------------------


def test_cli_search_dirs_cover_windows_install_locations(monkeypatch):
    """Windows 上 PATH 最不可靠：npm 全局目录要重开终端才进 PATH，从桌面
    快捷方式启动的进程拿到的又是启动那一刻的旧环境块。"""
    monkeypatch.setattr(os, "name", "nt", raising=False)
    locs = ai_agents.search_locations("codex")
    dirs = " | ".join(loc.path for loc in locs).lower()
    assert "npm" in dirs
    # 微软商店版 codex 的真身在受 ACL 保护的 WindowsApps 包体里，
    # 能用的入口是这个执行别名目录——少了它，商店版就是「系统找不到」
    assert "microsoft\\windowsapps" in dirs
    assert "winget" in dirs and "scoop" in dirs
    # 来源标签要如实分辨这几类落点（详情页的诊断区按来源解释「从哪找到的」）
    by_source = {loc.source for loc in locs}
    assert {"npm_global", "windows_alias", "common_location"} <= by_source


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
        '@ECHO off\r\n"%dp0%\\node_modules\\@openai\\codex\\bin\\codex.exe" %*\r\n',
        encoding="utf-8",
    )
    assert ai_agents.resolve_shim(str(shim)) == [str(exe.resolve())]


def test_plain_executable_is_not_treated_as_shim(tmp_path):
    """真正的可执行文件不该被当成外壳去解析。"""
    exe = tmp_path / "codex.exe"
    exe.write_bytes(b"MZ")
    assert ai_agents.resolve_shim(str(exe)) is None


def test_executable_bit_gate_is_a_noop_on_windows(tmp_path, monkeypatch):
    """Windows 没有可执行位语义：`os.access(X_OK)` 对任何存在的文件都为真。

    自定义可执行文件的校验里那道「可执行位」闸因此在 Windows 上**等于没有**
    ——这不是缺陷，是那个平台上没东西可查。真正兜底的是「拿它跑一次
    `--version`」，两个平台上都是它说了算。

    这条用例是 PR #128 在 merge queue 的 windows-latest 腿上红出来的：
    原来的断言（无可执行位 → 拒）写的是 POSIX 语义，在 Windows 上必红。
    修法不是把断言删掉，而是把**两边各自的真实行为**都钉住。
    """
    from tavotto.engine import ai_agents as agents

    codex = agents.get_agent("codex")
    plain = tmp_path / "codex"
    plain.write_text("不是可执行文件", encoding="utf-8")
    try:
        plain.chmod(0o644)  # POSIX 上摘掉可执行位；Windows 上无效
    except OSError:
        pass

    # 模拟 Windows：X_OK 恒真
    monkeypatch.setattr(agents.os, "access", lambda *a, **k: True)
    monkeypatch.setattr(agents, "probe_version_detailed", lambda argv: (None, "launch_failed"))
    res = agents.validate_executable(codex, str(plain))
    # 那道闸放行了，但结论一样是「拒」——由启动验证兜住
    assert res.argv is None and res.error == "launch_failed"

    # 反过来：真能起来的就该过（否则这条用例用「恒拒」也能假绿）
    monkeypatch.setattr(agents, "probe_version_detailed", lambda argv: ("codex-cli 1.0", None))
    ok = agents.validate_executable(codex, str(plain))
    assert ok.argv is not None and ok.version == "codex-cli 1.0"


def test_capabilities_tells_where_it_looked_when_missing(monkeypatch):
    """没找到 CLI 时要说清「找过哪些地方」。干甩一句「未安装」的结果是
    用户明明装了却无从下手（朋友的商店版 codex 就是这样）。"""
    monkeypatch.setattr(ai_agents, "candidates", lambda agent, override=None: [])
    monkeypatch.setattr(ai_agents, "_run_probe", lambda argv, timeout=10: None)
    ai_bridge.invalidate_capabilities()
    caps = ai_bridge.capabilities(refresh=True)
    assert [a["id"] for a in caps["agents"]] == ["codex", "claude"]
    for info in caps["agents"]:
        assert info["installed"] is False
        assert info["state"] == "not_installed"  # 没装不是「坏了」
        assert info["diagnostics"]["searched"], "必须报出找过的目录"
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
    assert "anaconda3" in cands and "miniconda3" in cands  # conda
    globbed = " | ".join(probed).lower()
    assert r"programs\python" in globbed  # python.org 安装器
    assert globbed.count("python*") >= 2  # 还兜了 C:\ 根


def test_frozen_app_never_probes_its_own_executable(monkeypatch):
    """打包成独立应用时 sys.executable 是 Tavotto 自己，不是解释器。
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

    from tavotto.engine import ai_providers, diagnostics

    ai_providers.save(
        {
            "label": "Kimi",
            "agent": "claude",
            "api_key": "sk-abcdef123456",
            "base_url": "https://api.moonshot.cn/anthropic",
        }
    )
    monkeypatch.setattr(
        diagnostics,
        "_log_tail",
        lambda n=400: [
            f"用户目录 {os.path.expanduser('~')}/paper",
            "Authorization: Bearer sk-abcdef123456",
        ],
    )

    resp = client.get("/api/diagnostics/bundle")
    assert resp.status_code == 200
    assert resp.mimetype == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(resp.data))
    assert set(z.namelist()) >= {"report.json", "app.log", "README.txt"}

    blob = "\n".join(z.read(n).decode("utf-8") for n in z.namelist())
    assert "sk-abcdef123456" not in blob  # 密钥
    assert os.path.expanduser("~") not in blob  # 个人主目录
    report = json.loads(z.read("report.json"))
    assert report["tavotto"]["version"]
    assert "platform" in report["system"]
    assert report["ai_endpoints"][0]["has_key"] is True  # 有没有 key 要报，key 本身不报


def test_diagnostics_bundle_survives_missing_log(client, monkeypatch):
    """日志文件还没生成时也要出得来包——排障工具自己不能先炸。"""
    from tavotto.engine import diagnostics

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
    from tavotto.engine import runtime as rt

    src = Path(rt.__file__).read_text(encoding="utf-8")
    body = src.split("# 定位", 1)[1].split("# manifest", 1)[0]
    # 只看真代码：注释和 docstring 里当然要提到 Path(...) 说明为什么不用它
    code = "\n".join(
        ln for ln in body.splitlines() if ln.strip() and not ln.lstrip().startswith("#")
    )
    assert "Path(" not in code, "定位逻辑里不允许出现 pathlib"

    # 直接验行为：把 os.name 改成 nt，整条链路都不许炸
    import os as _os

    old = _os.name
    try:
        _os.name = "nt"
        rt._candidate_roots()
        rt.status()
        assert rt.runtime_python(r"C:\Tavotto\runtime").endswith(r"\python.exe")
    finally:
        _os.name = old


def test_bundled_runtime_lives_under_the_onedir_internal_folder(tmp_path, monkeypatch):
    """PyInstaller onedir 的布局是 `Tavotto.exe` + `_internal\\`，
    spec 的 datas 落在 `_internal\\runtime`。安装程序按 recursesubdirs 收，
    免安装 zip 直接打包整个目录——两条发行路径都指望这个落点。"""
    import json as _json

    from tavotto.engine import runtime as rt

    internal = tmp_path / "_internal" / "runtime"
    internal.mkdir(parents=True)
    # 解释器落点必须问 runtime_python()：Windows 是 runtime\python.exe，
    # POSIX 是 runtime/bin/python3。以前硬编码 bin/python3，这个「Windows
    # 回归」用例反而只在 macOS/Linux 上绿——真 Windows 上一跑就穿帮。
    py = Path(rt.runtime_python(str(internal)))
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text("#!/bin/sh\n")
    (internal / "runtime-manifest.json").write_text(
        _json.dumps(
            {"schema": 1, "python": {"version": "3.13.15"}, "packages": {"numpy": "2.5.2"}}
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(rt, "is_frozen", lambda: True)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "Tavotto.exe"))
    assert rt.runtime_root() == str(internal)


def test_bundled_worker_writes_no_cache_into_the_install_directory(monkeypatch):
    """安装目录常在 `C:\\Program Files\\Tavotto`（普通用户没写权限），
    而 Python 默认会往 site-packages 旁边写 __pycache__、matplotlib 会往
    `~/.matplotlib` 写字体缓存。前者会报权限错，后者卸载后留垃圾。"""
    from tavotto.engine import config as cfg, runtime as rt

    # 真正的保证是命令行的 -B：embeddable 靠 ._pth 定路径，而 CPython 找到
    # ._pth 就 use_environment = 0，PYTHON* 那条路在这里不可靠
    assert "-B" in rt.child_args()
    env = rt.child_env({"PATH": r"C:\Windows\system32"})
    data = str(cfg.data_dir())
    # MPLCONFIGDIR 不是 PYTHON* 变量，matplotlib 直接读 os.environ，一定生效
    assert env["MPLCONFIGDIR"].startswith(data)
    assert env["PATH"] == r"C:\Windows\system32", "不该动用户原有的 PATH"


def test_windows_desktop_missing_runtime_says_reinstall_not_install_python(tmp_path, monkeypatch):
    """朋友那台机器上如果 runtime 没打进去，弹「请先安装 Python 3.10 以上」
    是纯粹的误导——他什么都没做错，是我们的包不完整。"""
    from tavotto.engine import runtime as rt

    monkeypatch.setattr(rt, "is_frozen", lambda: True)
    monkeypatch.setattr(pool, "is_frozen", lambda: True)
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "Tavotto.exe"))
    monkeypatch.delenv("TAVOTTO_RUNTIME_DIR", raising=False)
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
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("Popen", "run")
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in aliases
        ):
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
    return [(where, call) for py in files for where, _lineno, call in _subprocess_spawns(py)]


#: `creationflags` 允许的两个值——**闭集**。每个 spawn 必须说清自己属于哪一类：
#:
#: * `CREATE_NO_WINDOW` —— **GUI 拥有的隐藏子进程**（桌面版起 python/pip/codex）。
#:   不传就是用户可见的黑框；
#: * `INHERIT_CONSOLE` —— **CLI 拥有的控制台子进程**（`tavotto run` 起用户的
#:   Python，ADR 0021 §1）。它必须留在用户的终端上，加 `CREATE_NO_WINDOW`
#:   会让 `input()` 当场 EOF、Ctrl+C 送不到。
#:
#: 值都是 0（非 Windows）/ 一个是 0（`INHERIT_CONSOLE` 在所有平台都是 0），
#: 所以这条判据**不改变任何运行时行为**——它要的是"源码里写得出这个 spawn
#: 属于哪一类"。
#: 判据按**常量名**判（`value.rsplit(".", 1)[-1]`），不按整串：模块别名各处
#: 不同（`runtime.` / `engine_runtime.` / 裸名），把别名也钉进闭集只会让这条
#: 用例在下一次改 import 时假红。
_ALLOWED_CREATIONFLAGS = {"CREATE_NO_WINDOW", "INHERIT_CONSOLE"}
#: 只认**具名常量**（可带模块前缀）。字面量 `0`、`A | B` 这类表达式一律拒——
#: 那是绕过声明的写法。
_CREATIONFLAGS_SHAPE = re.compile(r"^(?:\w+\.)?[A-Z_]+$")


def test_every_backend_subprocess_declares_its_console_ownership():
    """每个 spawn 都必须传 `creationflags`，而且只能是那两个具名常量之一。

    原来这条只要求"传了 creationflags"，因为那时后端只有一类子进程：桌面版
    起的隐藏子进程，漏传 = 用户可见的黑框（审计当时六处漏传，外加 app.py 里
    没点出来的第七处）。

    2026-08-28 多了第二类：`tavotto run` 起的**用户自己的 Python**，它跑在
    用户的终端里，加 `CREATE_NO_WINDOW` 恰恰是**错的**。所以判据从"传了没有"
    升级成"**声明的是哪一类**"——两类都写得出名字，谁都不能靠"漏传"或者
    "抄了旁边那一行"蒙混过去。

    这不是给新代码开豁免：`INHERIT_CONSOLE` 的值就是 0，与"不传"运行时等价；
    它买到的是**可读性与可审查性**——下一个人在一个 GUI 路径里看到
    `INHERIT_CONSOLE` 会知道那不对，而看到一个空白的 kwargs 不会。
    """
    checked, console_owned = [], []
    for where, call in _spawn_sites():
        kwargs = {kw.arg: ast.unparse(kw.value) for kw in call.keywords if kw.arg}
        assert "creationflags" in kwargs, (
            f"{where} 的 subprocess 调用漏了 creationflags——"
            "GUI 拥有的子进程用 CREATE_NO_WINDOW，CLI 拥有的用 INHERIT_CONSOLE"
        )
        value = kwargs["creationflags"]
        assert _CREATIONFLAGS_SHAPE.match(value), (
            f"{where} 的 creationflags={value!r} 不是具名常量——"
            "只认 CREATE_NO_WINDOW / INHERIT_CONSOLE（可带模块前缀）"
        )
        assert value.rsplit(".", 1)[-1] in _ALLOWED_CREATIONFLAGS, (
            f"{where} 的 creationflags={value!r} 不在闭集里 {sorted(_ALLOWED_CREATIONFLAGS)}"
        )
        checked.append(where)
        if value.endswith("INHERIT_CONSOLE"):
            console_owned.append(where)
    # 一个都没扫到 = 匹配逻辑坏了，别让空断言冒充通过
    assert len(checked) >= 10, f"只扫到 {checked}，AST 匹配逻辑可能失效了"
    # **CLI 拥有的那一类只有一处**：`tavotto run` 起用户的 Python。多出来一处
    # 就要有人解释为什么——这不是可以顺手复制的写法。
    assert [w.split(":")[0] for w in console_owned] == ["runcli.py"], console_owned


def test_the_user_python_is_never_detached_from_the_terminal():
    """反方向：`tavotto run` 起的用户 Python **绝不能**带 `CREATE_NO_WINDOW`。

    上面那条防的是"漏了声明"；这条防的是"有人好心把它'修'成和别处一样"。
    Windows 上加了它的表现全都是静默的：`input()` 立刻 EOF、Ctrl+C 送不到、
    `print` 去了一个没人看的地方——而 macOS 上一切正常，看不出来。
    """
    from tavotto.engine import runcli

    src = Path(runcli.__file__).read_text(encoding="utf-8")
    body = src.split("def _spawn_user_python", 1)[1].split("\ndef ", 1)[0]
    body = body.split('"""', 2)[-1]  # docstring 里恰恰会解释"为什么不用它"
    assert "CREATE_NO_WINDOW" not in body, (
        "tavotto run 起的用户 Python 带上了 CREATE_NO_WINDOW——"
        "那会把它从用户的终端上摘下来（ADR 0021 §1）"
    )
    assert "INHERIT_CONSOLE" in body


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
            f"{where} 用了 text=True 却没钉 encoding/errors，cp936 下会解码失败"
        )


def test_create_no_window_has_exactly_one_definition():
    """常量散成好几份就会各自漂移（ai_bridge 曾自带一份）。

    唯一出处是 runtime.py——CLAUDE.md 把它定为 Windows 平台判断的唯一出处。
    """
    from tavotto.engine import runtime as rt

    engine = Path(pool.__file__).parent
    definers = [
        py.name
        for py in sorted(engine.glob("*.py"))
        if any(
            ln.startswith("CREATE_NO_WINDOW") for ln in py.read_text(encoding="utf-8").splitlines()
        )
    ]
    assert definers == ["runtime.py"], f"重复定义：{definers}"

    # 值本身：Windows 上是 CREATE_NO_WINDOW，别处必须是 0（等同于不传）
    expected = 0x08000000 if os.name == "nt" else 0
    assert rt.CREATE_NO_WINDOW == expected


def test_upgrade_pins_utf8_so_pip_output_cannot_explode(monkeypatch):
    """cp936 环境下 pip 的输出（进度条、中文路径）用系统编码一解码就抛
    UnicodeDecodeError，而 apply_upgrade 只接 OSError/TimeoutExpired——
    异常会原样逃出去变成 500，用户点「立即升级」拿到一串 traceback。
    """
    from tavotto.engine import updater

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


def test_render_cache_yields_when_the_target_is_locked_by_a_reader(tmp_path, monkeypatch):
    """Windows：目标正被 `send_file` 读着时 `os.replace` 报 WinError 5。

    POSIX 的 rename 盖得掉一个开着的文件，Windows 盖不掉（werkzeug 的
    `open(path, "rb")` 没带 FILE_SHARE_DELETE）。真机现象：16 个并发
    `/api/render` 撞一次就有人拿到 500，而图其实好好地躺在磁盘上。
    """
    figs = _figs(tmp_path)
    src = figs / "Fig1.pdf"
    m.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = m.CACHE_DIR / "locked-target.png"

    m._write_render_cache(src, 200, cached)  # 先有一份完整的
    good = cached.read_bytes()
    assert good.startswith(b"\x89PNG\r\n\x1a\n")

    def denied(_a, _b):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(m.os, "replace", denied)
    m._write_render_cache(src, 200, cached)  # 退让，不许抛
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

    monkeypatch.setattr(m, "_REPLACE_BACKOFF_S", 0.0)  # 别让重试拖慢测试
    monkeypatch.setattr(
        m.os,
        "replace",
        lambda _a, _b: (_ for _ in ()).throw(PermissionError(5, "Access is denied")),
    )
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
        return None  # ← 关键：永远说「我还活着」

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

    c = workerd_client.WorkerdClient("tavotto-workerd")
    for _ in range(6):
        try:
            c.ensure_started()
        except workerd_client.WorkerdError:
            pass
        if c.disabled:
            break

    assert c.disabled, "反复起来就崩必须停用 workerd"
    assert len(_ZombiePopen.instances) <= workerd_client._MAX_RESTARTS + 1, "重启次数不该超过上限"
    # 半启动的都被收掉了：不收就是每重启一次泄漏一个子进程
    assert all(z.killed for z in _ZombiePopen.instances[:-1])


def test_no_test_id_can_blow_the_windows_env_var_limit():
    """没有哪条用例的 id 长到能撑爆 Windows 的环境变量上限。

    pytest 把当前用例的 id 写进 `PYTEST_CURRENT_TEST`。Windows 的环境变量
    上限是 32767 字符，超了就 `ValueError: the environment variable is longer
    than 32767 characters` —— **在 Linux/macOS 上全绿，只在 Windows 上炸**，
    而且报错和被测的东西毫无关系，得翻半天才看出来是 id 的问题。

    真实来源：`@pytest.mark.parametrize` 直接拿**整个文件的内容**当参数。
    工作流文件慢慢变长，某天加几行就越线（本仓库在 desktop-tauri.yml 上撞过
    一次）。参数化要按名字/路径，内容在用例里自己读。

    这里量的是 pytest 真正生成的 id，不是猜哪些用例可疑——阈值取上限的
    四分之一，给「文件还会长」留足余量，同时任何按内容参数化的写法都会
    立刻越线（那些 id 动辄上万字符）。
    """
    # **不能加 -q**：安静模式把每个文件折叠成一行计数，id 根本不出现，
    # 这条守卫就永远是绿的（第一版正是这么写的，改回原 bug 也没红）
    out = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "--no-header",
            "-p",
            "no:cacheprovider",
            str(Path(__file__).parent),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=Path(__file__).resolve().parents[1],
    )
    assert out.returncode == 0, f"收集用例就失败了:\n{out.stdout[-3000:]}\n{out.stderr[-2000:]}"

    LIMIT = 32767 // 4
    worst = max((ln for ln in out.stdout.splitlines() if "::" in ln), key=len, default="")
    assert len(worst) <= LIMIT, (
        f"有用例 id 长达 {len(worst)} 字符（上限 {LIMIT}）——多半是 parametrize "
        f"直接吃了整个文件的内容。开头：{worst[:200]}"
    )


# ---------------- 只装桌面版：外部程序发现不了 CLI ----------------------------
#
# 朋友的现象：Windows 上装好 Tavotto 桌面程序，Codex 的 Tavotto 插件一直说
# 「没找到 Tavotto」。他没装 Python、没装 Conda、PATH 里没有 tavotto、也没设
# TAVOTTO_CLI——插件当时查的正好就是这三处。
#
# 根因不是「少查了一个目录」，而是**装出来的东西里根本没有能当命令行用的
# 可执行文件**：Tavotto.exe（Tauri 壳）与 sidecar 都是 console=False 打的，
# 没有真终端时 sys.stdout 是 None，packaging/entry.py 会把输出改道进 app.log，
# 调用方 capture_output 拿到的是空 stdout 而不是那行 JSON。
#
# 下面两条是这个 bug 的 Windows 语义定版。**跨平台可跑**（注入假文件系统 +
# 显式 system="win32"），完整的环境矩阵与插件那侧的同源比对在
# tests/test_install_locate.py，真安装产物的验收在 nightly 的「装一遍再冒烟」。

WIN_INSTALL = "C:\\Users\\张三\\AppData\\Local\\Tavotto"
WIN_DESKTOP_EXE = WIN_INSTALL + "\\Tavotto.exe"
WIN_CLI_EXE = WIN_INSTALL + "\\sidecar\\Tavotto\\tavotto-cli.exe"
WIN_ENVIRON = {
    "LOCALAPPDATA": "C:\\Users\\张三\\AppData\\Local",
    "APPDATA": "C:\\Users\\张三\\AppData\\Roaming",
}


def test_desktop_only_windows_install_exposes_a_usable_cli():
    """只装了桌面版的 Windows 机器上，必须找得到一条能当命令行调的入口。

    没有 TAVOTTO_CLI、PATH 里没有 tavotto、没有安装清单（模拟被清掉的情况）
    ——只剩「按已知安装位置找」这一条腿，它必须撑得住。
    """
    from tavotto.engine import locate

    installed = {WIN_DESKTOP_EXE, WIN_CLI_EXE}
    got = locate.find_cli(
        system="win32",
        environ=WIN_ENVIRON,
        isfile=lambda p: p in installed,
        which=lambda name: None,
        reg_dirs=(),
    )
    assert got["cmd"] == [WIN_CLI_EXE], "只装桌面版就找不到 CLI = 那个 bug 回来了"
    assert got["source"] == "install"


def test_the_gui_binary_is_never_offered_as_a_command_line():
    """**绝不能把 Tavotto.exe 当命令行交出去。**

    它是 GUI 子系统的可执行文件：调用方拿不到 stdout，只会看到「命令没有输出」。
    这一版没带 tavotto-cli（v0.7.0 及更早的安装包就是这样）时，正确的回答是
    「装了但缺 CLI，去升级」，而不是拿 Tavotto.exe 顶上，也不是说「没装」。
    """
    from tavotto.engine import locate

    only_gui = {WIN_DESKTOP_EXE}
    got = locate.find_cli(
        system="win32",
        environ=WIN_ENVIRON,
        isfile=lambda p: p in only_gui,
        which=lambda name: None,
        reg_dirs=(),
    )
    assert got["cmd"] is None, "把 GUI exe 当 CLI 交出去了"
    assert got["desktop"] == WIN_DESKTOP_EXE, "得说清楚「装了，只是缺 CLI」"


def test_the_windows_installer_ships_and_registers_that_cli():
    """光有发现逻辑不够：安装器得真的把 tavotto-cli 装进来并登记。

    这三处任何一处漏掉，上面两条仍然全绿，而用户那里照旧「找不到 Tavotto」。
    """
    root = Path(__file__).resolve().parent.parent
    spec = (root / "packaging" / "tavotto.spec").read_text(encoding="utf-8")
    assert 'name="tavotto-cli"' in spec, "安装产物里没有 console 版 CLI"
    assert "console=True" in spec

    nsi = (root / "src-tauri" / "windows" / "installer.nsi").read_text(encoding="utf-8")
    from tavotto.engine import locate

    assert locate.CLI_NAME in nsi, "安装器没提到 tavotto-cli.exe"
    assert "doctor --json --write-manifest" in nsi, "装完没有登记安装清单"


# ---------------- 生成物在 Windows 上被改成 CRLF，逐字节比对当场失败 ----------
#
# 现象：`package (windows-latest)` 与 `windows-exe-smoke` 一起红，报
# 「TypeScript definitions are out of date. Run 'i18next-cli types'」，而
# ubuntu / macOS 全绿，本机怎么跑都复现不了。
#
# 成因：`web/src/i18n/resources.d.ts` 是 `i18next-cli types` 生成的，写出来是
# LF；Git for Windows 默认 `core.autocrlf=true`，检出时把它换成 CRLF。
# `types --ci` 拿磁盘上那份与新生成的**逐字节**比，于是必然不一致——
# 文件内容一个字都没错，只是换行符被 git 改了。
#
# 修法是 .gitattributes 把这类「会被逐字节比对的生成物」钉成 LF。


def _byte_compared_generated_files() -> list[str]:
    """会被逐字节 / 逐指纹比对的生成物。新增一个就往这里加一行。"""
    return [
        # `pnpm i18n:check` 的第一步就是 `i18next-cli types --ci`
        "web/src/i18n/resources.d.ts",
        # `python scripts/build_mcp_widget.py --check` 比的是源码指纹
        "codex-plugin/mcp/widget/canvas.html",
        # CLA 正文：SHA-256 记在 .github/cla-policy.json，判据逐字节核对。
        # 不是生成物，是人写的法律文本——但同样「字节必须确定」，而且
        # 2026-08-28 就是在 Windows 那条腿上红过（policy 是 LF 哈希、
        # 检出成 CRLF），所以同样归这张表管。
        "docs/legal/CLA_INDIVIDUAL.md",
        "docs/legal/CLA_CORPORATE.md",
    ]


@pytest.mark.parametrize("rel", _byte_compared_generated_files())
def test_byte_compared_artifacts_are_pinned_to_lf(rel):
    """会被逐字节比对的生成物必须钉成 LF，否则只在 Windows 上红。

    两件事都要有：
      1. 仓库里存的就是 LF（生成器写的就是 LF，混进 CRLF 说明有人在 Windows
         上重新生成并提交了）；
      2. `.gitattributes` 里有规则挡住 Windows 检出时的 autocrlf 转换——
         只做第 1 条的话，仓库里干净，Windows 的**工作区**照旧是 CRLF。
    """
    root = Path(__file__).resolve().parent.parent
    path = root / rel
    assert path.is_file(), f"{rel} 不在了——改了路径就同步改这张表"
    assert b"\r\n" not in path.read_bytes(), (
        f"{rel} 里有 CRLF：它是生成物，生成器写的是 LF，"
        f"混进 CRLF 说明有人在 Windows 上重新生成并提交了"
    )

    ga = root / ".gitattributes"
    assert ga.is_file(), (
        "没有 .gitattributes：Git for Windows 默认 core.autocrlf=true，"
        "检出时会把这些生成物换成 CRLF，逐字节比对当场失败"
    )
    rules = [
        ln.strip()
        for ln in ga.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    hit = [r for r in rules if r.split()[0] in (rel, "*") and "eol=lf" in r]
    assert hit, f".gitattributes 没有把 {rel} 钉成 eol=lf；现有规则：{rules}"


def test_no_test_judges_executability_by_filesystem_mode() -> None:
    """用例里不许拿 `os.stat().st_mode` 判「有没有可执行位」。

    Windows 上普通文件的 `st_mode` 恒为 `0o100666`，`& 0o111` **永远是 0**，
    于是这种断言在 Windows 上必然红——而在作者的 macOS 上永远绿。
    2026-08-20 真的红了一次（`scf_bootstrap` 那条）。

    真正决定 Linux/macOS 上 checkout 出来有没有 +x 的、以及 zip 打包时取的，
    是 **git 索引里的 mode**（`100755`）。它跨平台一致，而且判的是正确的东西：

        entry = subprocess.run(["git", "ls-files", "-s", "--", rel], ...)
        assert entry.stdout.split()[0] == "100755"

    这条是 meta 检查：它不测产品行为，它拦住一类**只在别人电脑上失败**的写法。
    """
    import tokenize

    root = Path(__file__).resolve().parent
    offenders = []
    for path in sorted(root.glob("test_*.py")):
        # **按 token 扫，不是按行切 `#`**：docstring 里解释这个坑的文字
        # 会被行级判据当成犯规（第一版就是这么误报的）。字符串与注释都跳过。
        with tokenize.open(path) as fh:
            names = [
                t
                for t in tokenize.generate_tokens(fh.readline)
                if t.type == tokenize.NAME or t.type == tokenize.NUMBER
            ]
        for i, tok in enumerate(names):
            if tok.string != "st_mode":
                continue
            near = {t.string for t in names[max(0, i - 6) : i + 7]}
            if near & {"0o111", "0o100", "S_IXUSR", "S_IXGRP", "S_IXOTH"}:
                offenders.append(f"{path.name}:{tok.start[0]}")

    assert not offenders, (
        "这些断言在 Windows 上恒假（st_mode 没有执行位），"
        "改查 git 索引里的 mode：\n  " + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------------------
# 提交进仓库的生成物：换行不许跟平台走
# ---------------------------------------------------------------------------
def test_compat_baseline_is_written_with_lf_on_every_platform(tmp_path):
    """CompatBench 基线的换行必须钉死 `\\n`。

    `Path.write_text` 默认文本模式（`newline=None`）会把 `\\n` 翻成
    `os.linesep`——Windows 上就是 `\\r\\n`。基线是**提交进仓库、要被逐条读
    diff** 的资产，而 `--update-baseline` 明确是给人在本地跑的：一个 Windows
    开发者重生成一次，149 个 case 全变成整文件 CRLF diff，真正的分类变化就
    淹在噪音里了。而「有人真的读过这份 diff」是整条基线纪律唯一的立足点。

    本机（macOS/Linux）上这条恒真，它的价值全在 ci.yml backend 矩阵的
    windows-latest 那一档——与本文件里其它用例同一个道理。

    姊妹问题（同一类「只在别的平台上成立的不变式」）：`_common` 的 cp1252
    stdout、`browser.py` 把用户脚本写进虚拟 FS 时的 CRLF 翻译。
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "ci"))
    import compat_corpus as CC

    payload = {
        "schema": 1,
        "generated_for": {"target": "bundled", "matplotlib": "3.11.1"},
        "cases": {
            "a": {"classification": "full_support", "stages": {"execute": True, "capture": True}}
        },
    }
    CC.validate_baseline(payload)
    dest = CC.write_baseline(payload, tmp_path / "baseline.json")
    raw = dest.read_bytes()
    assert b"\r\n" not in raw, "基线被写成了 CRLF——review 里会变成整文件 diff"
    assert raw.endswith(b"\n")
    # 写出去的还得是能读回来的合法基线，别为了换行把内容写坏
    CC.validate_baseline(json.loads(raw.decode("utf-8")))


def test_compat_text_writes_all_pin_the_newline():
    """CompatBench 每一处文本写盘都必须钉 `newline="\\n"`。

    上面那条行为用例在 macOS/Linux 上是**恒真**的（`os.linesep` 本来就是
    `\\n`），只有 windows-latest 那一档才抓得到——实测确认过：把
    `newline` 摘掉，本机照样绿。所以还需要这一条**平台无关的静态检查**，
    否则「只在别人电脑上发生」的东西在本机开发时毫无阻力地被写进来。

    覆盖的是「产出物」那几处：提交进仓库的基线、会被 upload-artifact 收走
    并拿去 diff 的报告。临时文件（driver 的请求 JSON）不在此列——它写完就
    被同一个进程读掉，换行是什么无所谓。
    """
    ci = Path(__file__).resolve().parents[1] / "scripts" / "ci"
    targets = {
        "compat_corpus.py": ("path.write_text(",),
        "compat_matrix.py": ("json_path.write_text(",),
    }
    checked = 0
    for name, calls in targets.items():
        lines = (ci / name).read_text(encoding="utf-8").splitlines()
        for i, ln in enumerate(lines):
            if not any(c in ln for c in calls):
                continue
            checked += 1
            block = "\n".join(lines[i : i + 4])
            assert 'newline="\\n"' in block, (
                f"{name}:{i + 1} 的 write_text 没钉换行——Windows 上会写成 "
                f"CRLF，而这是提交进仓库/要被 diff 的产出物"
            )
    assert checked >= 3, (
        f"只扫到 {checked} 处写入，比预期少——写盘的地方挪了位置？"
        f"这条用例要跟着改，别让它安静地什么都不检查"
    )


def test_playground_writes_the_workspace_source_byte_for_byte():
    """playground 往虚拟 FS 写用户脚本必须是**二进制写**，不能用文本模式。

    文本模式在 Windows 上把 `\\n` 翻成 `\\r\\n`，于是磁盘上的字节不再是用户
    交出来的那份，「figure.py · 未改动」那条跨边界哈希比对当场变成永远
    mismatch——而界面把 mismatch 当作**不变式失效**，是要常驻报警的那一档。
    CI 的 windows 腿实测逮到过（`test_workspace_source_hash_...` 与
    `test_tampered_...` 双双红）。

    生产环境（Pyodide 的 Emscripten FS）恰好不翻译换行，所以这个坑在浏览器里
    看不出来，在 macOS/Linux 上跑测试也看不出来——**一个只在别的平台上成立的
    不变式不算不变式**。所以这条不去比哈希（那只在 Windows 上才失败），而是
    按源码判「写法本身对不对」，与 st_mode 那条同一路数：拦住一类只在别人
    电脑上失败的写法。
    """
    import ast

    repo = Path(__file__).resolve().parent.parent
    src = (repo / "src" / "tavotto" / "engine" / "browser.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    modes = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "open"):
            continue
        # open(path, mode) —— 只看写用户源码那一处（第二个实参是字面量）
        if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
            modes.append(node.args[1].value)

    writes = [m for m in modes if "w" in str(m)]
    assert writes, "用例前提失效：browser.py 不再往工作区写脚本了？"
    assert all("b" in str(m) for m in writes), (
        f"browser.py 必须用二进制模式写工作区源文件（现在是 {writes}）——"
        "文本模式会在 Windows 上翻译换行，源文件完整性校验永远 mismatch。"
    )


def test_artist_census_prints_chinese_under_a_legacy_code_page(tmp_path):
    """普查工具的中文表头在**非 UTF-8 控制台**上必须打得出来。

    CI 的 windows 腿实测逮到的：GitHub runner 的控制台是 cp1252，
    `print("元素")` 当场 `UnicodeEncodeError`，工具在别人电脑上根本跑不完
    ——而 macOS / Linux 上永远看不见（那儿默认就是 UTF-8）。中文机器上是
    cp936，同一个坑。

    这里用 `PYTHONIOENCODING` 把子进程的 stdio 强制成旧代码页，所以**任何
    平台都跑得出来**——与本文件其余用例同一条纪律：拿不到真实 Windows 语义
    的地方就直接测那段逻辑本身。

    它只是个诊断工具，但正因为如此才更不能崩：它存在的意义就是别人跑得起来。
    """
    from tavotto.engine import pool

    try:
        worker_py = pool.find_worker_python()
    except pool.WorkerError:
        pytest.skip("找不到装有 matplotlib 的解释器")

    repo = Path(__file__).resolve().parent.parent
    tool = repo / "scripts" / "dev" / "matplotlib_artist_census.py"
    script = tmp_path / "fig.py"
    script.write_text(
        "import matplotlib\n"
        "matplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        "fig, ax = plt.subplots()\n"
        "ax.plot([0, 1], [0, 1])\n"
        "ax.set_title('中文标题')\n"
        "fig.savefig('cp_probe.pdf')\n",
        encoding="utf-8",
    )

    env = dict(os.environ, PYTHONIOENCODING="cp1252")
    # 父进程这一侧指名 UTF-8：工具的 stdout 已经钉成 UTF-8，而 `text=True`
    # 用的是**父进程** locale。两边不一致时 subprocess 的读线程会死在解码上，
    # `communicate()` 把那一路交成 None，报出来的是一句莫名其妙的 TypeError。
    proc = subprocess.run(
        [worker_py, str(tool), "fig.py"],
        cwd=str(tmp_path),
        capture_output=True,
        timeout=300,
        env=env,
        encoding="utf-8",
        errors="replace",
    )
    assert "UnicodeEncodeError" not in (proc.stdout + proc.stderr), (
        f"普查工具在非 UTF-8 控制台上崩了——stdout 必须钉成 UTF-8\n{proc.stdout}\n{proc.stderr}"
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ---------------- 子进程输出的解码（本地代码页 ≠ UTF-8）------------------------


def test_child_output_survives_a_legacy_codepage():
    """真复现：子进程打中文，用**旧代码页**解码就会丢掉整段诊断。

    Windows 上 `text=True` 不给 encoding 时按系统默认（cp1252 / 中文机器
    cp936）解码。我们的 CLI 与脚本大量输出中文，于是读线程当场
    `UnicodeDecodeError` 并死掉——而 `returncode` 不经解码、照样拿得到，
    **用例继续绿**，只是 `stdout` / `stderr` 变成空的。

    **后果不是「没解码成功」，是「诊断在最需要它的时候是空的」**：
    `assert out.returncode == 0, f"...：{out.stderr}"` 这类断言一旦真的失败，
    报错信息也是空的。

    这条**平台无关**：不假装在 Windows 上跑，而是显式指定那个失败的编码，
    直接测那段逻辑本身——与本文件其余用例同一策略。
    """
    # **子进程必须是确定性的 UTF-8 字节生产者。** 写成 `sys.stdout.write(中文)`
    # 是不行的：Windows 旧代码页下子进程的 stdout（管道）按那个代码页编码，
    # 写中文当场 `UnicodeEncodeError`，`returncode != 0`——用例会在它本该保护
    # 的那个平台上直接失败，而且失败原因与被测的「父进程解码」毫无关系。
    # 走 `stdout.buffer.write` 绕开文本层，产出的字节与 locale 无关。
    # （Codex 在 #57 上指出的正是这个：上一版只控制了父进程的解码器。）
    child = "import sys; sys.stdout.buffer.write('渲染环境不可用：缺少 matplotlib'.encode('utf-8'))"

    # ① 按旧代码页解码：诊断没了（丢字节或整段变成替换字符）
    legacy = subprocess.run(  # 复现用：这里就是要那个会丢字节的旧代码页
        [sys.executable, "-c", child],
        capture_output=True,
        text=True,
        encoding="cp1252",
        errors="replace",
        timeout=60,
    )
    assert legacy.returncode == 0, "子进程本身应当成功——失败的只是解码"
    assert "渲染环境不可用" not in legacy.stdout, (
        "这条用例的前提失效了：cp1252 竟然解出了中文，说明构造的复现不成立"
    )

    # ② 按 utf-8 解码：诊断完整
    ok = subprocess.run(
        [sys.executable, "-c", child],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert ok.returncode == 0
    assert "渲染环境不可用：缺少 matplotlib" in ok.stdout, (
        "显式 utf-8 之后诊断必须完整——否则这条修复没有意义"
    )


def test_the_repo_scripts_really_round_trip_chinese_help():
    """端到端：真跑 `lab_preflight.py --help`（帮助文本是中文）并读回来。

    上面那条验的是「解码规则」，这条验的是**我们真正会跑的那条命令**在当前
    写法下拿不拿得到中文——静态判据（有没有 encoding 关键字）证明不了这件事，
    Codex 在 #57 上指出的正是这个缺口。
    """
    script = Path(__file__).resolve().parents[1] / "scripts" / "ci" / "lab_preflight.py"
    out = subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert out.returncode == 0, f"--help 都跑不了：{out.stderr}"
    assert any("一" <= ch <= "鿿" for ch in out.stdout), (
        "帮助文本里一个中文都没读到——要么脚本变了，要么解码又丢了"
    )


def test_no_ci_script_hard_codes_a_posix_only_signal():
    """`signal.SIGKILL` **在 Windows 上不存在**，写死它会 AttributeError。

    2026-08-22 实测：`lab_preflight.reap_stale_processes` 里写了
    `signal.SIGKILL`，于是 `test_self_heal_still_blocks_when_a_process_survives`
    在 CI 的 windows 腿上炸掉：

        AttributeError: module 'signal' has no attribute 'SIGKILL'.
                        Did you mean: 'SIGILL'?

    那段代码**只在实验室的 Linux runner 上真跑**（`find_ci_owned_tavotto`
    没有 `/proc` 就回空表），但**模块要在所有平台上 import 得动、用例要在
    所有平台上跑得了**。「它在生产里跑不到」不是写死平台专属常量的理由——
    本仓库这条纪律的原话是：每个「只在别人电脑上发生」的 bug 先变成这里的
    用例再谈修。

    正确写法是 `getattr(signal, "SIGKILL", signal.SIGTERM)`：Windows 上
    `os.kill(pid, SIGTERM)` 走的是 TerminateProcess，本来就是强制终止，
    退化成它是正确的语义、不是凑合。

    判据按 **AST** 走，不按文本：注释里解释「SIGKILL 在 Windows 上不存在」
    时必然写出这个名字，按子串判会让「把原因写清楚」反而变红
    （这个坑本轮已经踩过四次）。
    """
    import ast

    ci_dir = Path(__file__).resolve().parents[1] / "scripts" / "ci"
    POSIX_ONLY = {"SIGKILL", "SIGSTOP", "SIGCONT", "SIGUSR1", "SIGUSR2", "SIGHUP"}
    offenders = []
    for path in sorted(ci_dir.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            # `signal.SIGKILL` / `_sig.SIGKILL` 这种直接取属性
            if not (isinstance(node, ast.Attribute) and node.attr in POSIX_ONLY):
                continue
            base = node.value
            if not (isinstance(base, ast.Name) and "sig" in base.id.lower()):
                continue
            # `getattr(signal, "SIGKILL", …)` 是**正确写法**，不在此列；
            # 它是 ast.Call，走不到这个分支。
            offenders.append(f"{path.name}:{node.lineno} {base.id}.{node.attr}")
    assert not offenders, (
        "这些地方直接取了 POSIX 专属信号，Windows 上会 AttributeError：\n  "
        + "\n  ".join(offenders)
        + '\n用 getattr(signal, "SIGKILL", signal.SIGTERM) 代替'
    )


def test_artifact_manifest_summary_survives_a_windows_codepage(tmp_path):
    """**产物清单的中文摘要不能因为终端编码而炸掉整条构建腿。**

    GitHub 的 windows runner 上 Python 的 stdout 默认编码是 cp1252
    （中文 Windows 上是 cp936），而这个脚本的摘要是中文的。修复之前
    `print(render_summary(m))` 直接抛
    `UnicodeEncodeError: 'charmap' codec can't encode characters in
    position 4-9`——**产物已经造好了，倒在打印摘要这一步上**。
    2026-08-23 v0.9.2 的 publish=false 演练实测到（run 32617869026），
    desktop / build (windows-latest, nsis) 因此失败。

    判据是**真的用那个编码跑一遍子进程**，不是「源码里有没有 reconfigure」：
    后者换个写法就漏，而且证明不了输出真的写得出去。`PYTHONIOENCODING`
    正是 CPython 用来设定 stdout 编码的那个开关，与 runner 上的默认行为同源。
    """
    import subprocess

    repo = Path(__file__).resolve().parents[1]
    script = repo / "scripts" / "ci" / "artifact_manifest.py"
    wheel = tmp_path / "tavotto-0.9.2-py3-none-any.whl"
    wheel.write_bytes(b"not a real wheel, only needs to exist and hash")
    out = tmp_path / "artifact-manifest.json"

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "cp1252"  # 复现 runner 上的默认编码
    env.pop("GITHUB_STEP_SUMMARY", None)  # 不往真的 step summary 里写

    r = subprocess.run(
        [
            sys.executable,
            str(script),
            "build",
            "--version",
            "0.9.2",
            "--source-sha",
            "a" * 40,
            "--add",
            f"wheel:{wheel.name}:any",
            "--base",
            str(tmp_path),
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )

    assert r.returncode == 0, f"cp1252 下 build 挂了（returncode={r.returncode}）：\n{r.stderr}"
    assert "UnicodeEncodeError" not in r.stderr, r.stderr
    assert out.is_file(), "清单没写出来"


# ---------------- 注册表把 .js 关联成 text/plain（issue #115） ----------------
# Windows 上 mimetypes 从 HKCR 读文件关联，某些机器上 .js 的 Content Type 被
# 第三方软件改成 text/plain。send_from_directory 照猜发出去，而入口脚本是
# <script type="module">——WebView2 按严格 MIME 检查拒绝执行，React 不挂载，
# 桌面版整窗白屏、零报错。资产的 Content-Type 必须与机器级关联无关。


def _broken_registry_guess(_name, strict=True):
    """模拟被改坏的 Windows 注册表：所有扩展名都猜成 text/plain。"""
    return ("text/plain", None)


def test_js_assets_ignore_broken_windows_mime_registry(client, tmp_path, monkeypatch):
    import mimetypes

    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index-abc123.js").write_text("export {}\n", encoding="utf-8")
    (assets / "index-abc123.css").write_text("body{}\n", encoding="utf-8")
    (assets / "logo.svg").write_text("<svg/>\n", encoding="utf-8")
    monkeypatch.setattr(m, "WEB_DIST", tmp_path)
    # werkzeug 的 send_file 在响应那一刻才调 mimetypes.guess_type，
    # 换掉这个函数 == 在一台注册表被改坏的 Windows 上跑
    monkeypatch.setattr(mimetypes, "guess_type", _broken_registry_guess)

    for name, want in [
        ("index-abc123.js", "text/javascript"),
        ("index-abc123.css", "text/css"),
        ("logo.svg", "image/svg+xml"),
    ]:
        r = client.get(f"/assets/{name}")
        assert r.status_code == 200
        assert r.mimetype == want, f"{name} 发成了 {r.mimetype}——严格 MIME 检查下浏览器会拒载"
        # 缓存策略不因强制 MIME 而丢
        assert "immutable" in r.headers.get("Cache-Control", "")


def test_unlisted_asset_extensions_still_use_guessing(client, tmp_path, monkeypatch):
    """白名单之外的类型照旧交给 mimetypes 猜——只接管浏览器严格校验的那几类。"""
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "photo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    monkeypatch.setattr(m, "WEB_DIST", tmp_path)

    r = client.get("/assets/photo.png")
    assert r.status_code == 200
    assert r.mimetype == "image/png"


def test_index_busts_the_poisoned_asset_cache(client, tmp_path, monkeypatch):
    """0.10.x 在注册表改坏的机器上，text/plain 的 .js 已按
    max-age=31536000, immutable 缓存进浏览器；bundle 内容哈希不变时升级后
    浏览器根本不再发资产请求——强制 MIME 的新逻辑够不着老缓存。`/` 是
    no-cache 必回源的，Clear-Site-Data: "cache" 挂在它上面才能把中毒条目
    清掉（值必须带双引号，这是该头的语法不是风格）。"""
    (tmp_path / "index.html").write_text("<!doctype html>", encoding="utf-8")
    monkeypatch.setattr(m, "WEB_DIST", tmp_path)

    r = client.get("/")
    assert r.status_code == 200
    assert r.headers.get("Clear-Site-Data") == '"cache"', (
        "没有这个头，0.10.x 缓存过错误 MIME 的浏览器升级后仍旧白屏"
    )
    assert r.headers.get("Cache-Control") == "no-cache"


# ---------------- 一次性 replay worker：kill 之后没 reap，目录就删不掉 --------
# run 32937999297（merge_group / windows-latest / 3.13）：Windows 全量 pytest
# 里四条写回用例集体炸在 `_replay-…-fig_cbar.py` 上——那是**上一个测试文件**
# （test_colorbar_orientation.py）留下的残留。链路是
#   discard → shutdown → request(shutdown) → EOF → kill（不 wait）
#   → rmtree(ignore_errors=True) 撞 sharing violation → 静默留下整棵树。
# 下面这条用例把那个窗口在任何平台上确定性地复现出来：进程没被 reap 之前，
# 删除就抛 Windows 风格的 PermissionError。


class _LingeringPopen:
    """Windows 关进程的真实时序：kill 只是「发出请求」，wait 才是「它没了」。

    * `poll()` 在被 reap 之前**永远**回 None（进程对象看着还活着）；
    * shutdown 请求立刻读到 EOF（worker 收到就 `raise SystemExit(0)`，
      协议上本来就不回普通信封）；
    * `kill()` 只置一个「信号发出去了」的标记，**不代表进程已经消失**；
    * `wait()` 才把它标成真的退出——文件句柄也是这一刻才还给系统。
    """

    instances: list = []

    def __init__(self, *_a, **_kw):
        self.pid = 9100 + len(self.instances)
        self.stdout = _LingeringPopen._Pipe(eof=True)
        self.stdin = _LingeringPopen._Pipe()
        self.kill_called = False
        self.wait_called = False
        self.reaped = False
        type(self).instances.append(self)  # 子类各记各的（见 _StubbornPopen）

    class _Pipe:
        def __init__(self, eof: bool = False):
            self.eof = eof
            self.closed = False

        def write(self, _line):
            if self.closed:
                raise ValueError("I/O operation on closed file")

        def flush(self):
            pass

        def readline(self):
            return ""  # ← shutdown 之后的 EOF

        def close(self):
            self.closed = True

    def poll(self):
        return 0 if self.reaped else None

    def kill(self):
        self.kill_called = True  # ← 只发信号，进程还在，句柄还占着

    def wait(self, timeout=None):
        self.wait_called = True
        self.reaped = True  # ← 到这一刻它才真的没了
        return 0


def _windows_locked_rmtree(fake: _LingeringPopen, calls: list):
    """未 reap 前删除就是 sharing violation；`ignore_errors=True` 静默留下整棵树。

    第二段是真 `shutil.rmtree` 的语义，不是简化——正因为它静默，旧实现才能
    「删除失败」却让 `discard()` 看起来一切正常。
    """
    real = shutil.rmtree

    def rmtree(path, ignore_errors=False, **kw):
        calls.append(Path(path))
        if not fake.reaped:
            exc = PermissionError(
                13, "The process cannot access the file because it is being used by another process"
            )
            exc.winerror = 32
            if ignore_errors:
                return None  # ← 真 shutil 就是这么把失败吞掉的
            raise exc
        return real(path, ignore_errors=ignore_errors, **kw)

    return rmtree


def test_discard_reaps_the_process_before_deleting_the_replay_dir(tmp_path, monkeypatch):
    """`discard()` 返回时：进程已被 wait 回收、句柄已关、exact base 已消失。

    旧实现在这里必然红：整条关停路径一次 `proc.wait()` 都没有（shutdown 命令
    导致的 EOF 让 `request()` 先把 `_dead` 置上，`finally` 里的
    `if self.alive()` 于是恒假，连那句 `kill()` 都走不到），于是 rmtree 撞在
    还没释放的句柄上，被 `ignore_errors=True` 静默吞掉。
    """
    _LingeringPopen.instances = []
    calls: list[Path] = []
    fake_cls = _LingeringPopen

    monkeypatch.setattr(pool, "ENGINE_CACHE", tmp_path / "engine")
    monkeypatch.setattr(pool.subprocess, "Popen", fake_cls)
    monkeypatch.setattr(pool, "select_worker_python", lambda: ("py-fake", pool.SOURCE_SYSTEM))
    monkeypatch.setattr(workerd_client, "find_workerd", lambda: None)

    figs = _figs(tmp_path)
    (figs / "fig_lock.py").write_text("def main():\n    pass\n", encoding="utf-8")

    w = pool.one_shot("fig_lock.py", str(figs), "main")
    assert isinstance(w, pool.EngineWorker), "这条用例只问 Python 控制面"
    fake = fake_cls.instances[-1]
    base = w.base
    assert base.is_dir() and (base / "worker.log").is_file()
    assert str(base) in pool._oneshot_bases, "一次性目录必须先受 prune 豁免"
    # 删除的锁窗口由「有没有 reap」决定——注册在拿到 fake 之后
    monkeypatch.setattr(pool.shutil, "rmtree", _windows_locked_rmtree(fake, calls))

    pool.discard(w)

    assert fake.wait_called, "kill 之后必须 wait：只发信号不等于进程已经退出"
    assert fake.poll() is not None, "discard() 返回时进程还没被回收"
    assert fake.stdin.closed and fake.stdout.closed, "父进程的管道句柄没关"
    assert w._log.closed, "worker.log 的句柄没关"
    assert not base.exists(), f"exact base 没删掉：{base}（rmtree 调用={calls}）"
    assert str(base) not in pool._oneshot_bases, "删干净了还占着 prune 豁免名额"


def test_discard_logs_the_exact_path_when_the_tree_survives(tmp_path, monkeypatch, caplog):
    """删到底还是删不掉时：不静默、不抛、注销豁免让 prune 还有机会回收。"""
    _LingeringPopen.instances = []
    monkeypatch.setattr(pool, "ENGINE_CACHE", tmp_path / "engine")
    monkeypatch.setattr(pool.subprocess, "Popen", _LingeringPopen)
    monkeypatch.setattr(pool, "select_worker_python", lambda: ("py-fake", pool.SOURCE_SYSTEM))
    monkeypatch.setattr(workerd_client, "find_workerd", lambda: None)

    figs = _figs(tmp_path)
    (figs / "fig_stuck.py").write_text("def main():\n    pass\n", encoding="utf-8")
    w = pool.one_shot("fig_stuck.py", str(figs), "main")
    base = w.base

    attempts: list = []

    def never_deletable(path, ignore_errors=False, **_kw):
        attempts.append(path)
        exc = PermissionError(13, "used by another process")
        exc.winerror = 32
        if ignore_errors:
            return None
        raise exc

    monkeypatch.setattr(pool.shutil, "rmtree", never_deletable)
    monkeypatch.setattr(pool, "_RMTREE_BACKOFF", (0.0, 0.0, 0.0))

    with caplog.at_level("WARNING", logger="tavotto.engine"):
        pool.discard(w)  # 绝不抛：写回的成败与收尾无关

    assert len(attempts) == 3, "有限退让：既不是只试一次，也不是无限重试"
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert str(base) in text, "日志里没有 exact path，线上根本对不上号"
    assert "fig_stuck.py" in text and "PermissionError" in text
    assert str(base) not in pool._oneshot_bases, (
        "删不掉更要注销，否则这棵孤儿目录被永久豁免、prune 再也收不走"
    )


class _StubbornPopen(_LingeringPopen):
    """收到 shutdown 也不退的 worker（死循环脚本）：只有 kill 之后才会消失。

    `shutdown_all(wait=True)` 的兜底分支走的正是这条路——`force_kill()` 必须
    在这里也等到收尸，否则「wait=True」只是个名字：函数返回了，用户机器上
    python.exe 还在，Popen 没回收，句柄还占着目录。
    """

    def wait(self, timeout=None):
        self.wait_called = True
        if not self.kill_called:
            raise subprocess.TimeoutExpired("worker", timeout or 0.0)
        self.reaped = True
        return -9


def test_force_kill_waits_until_the_process_is_actually_gone(tmp_path, monkeypatch):
    """`force_kill()` 返回时进程已经被 reap，目录随即删得掉。

    这条用例专门盯住 kill **之后**的那次 wait：优雅关停走得通时那次 wait 根本
    到不了（进程自然退出就被收了），少了这条覆盖，post-kill 的 reap 会是一道
    没人执行过的门禁。
    """
    _StubbornPopen.instances = []
    calls: list[Path] = []
    monkeypatch.setattr(pool, "ENGINE_CACHE", tmp_path / "engine")
    monkeypatch.setattr(pool.subprocess, "Popen", _StubbornPopen)
    monkeypatch.setattr(pool, "select_worker_python", lambda: ("py-fake", pool.SOURCE_SYSTEM))
    monkeypatch.setattr(workerd_client, "find_workerd", lambda: None)
    monkeypatch.setattr(pool, "_REAP_TIMEOUT", 1.0)

    figs = _figs(tmp_path)
    (figs / "fig_loop.py").write_text("def main():\n    pass\n", encoding="utf-8")
    w = pool.one_shot("fig_loop.py", str(figs), "main")
    fake = _StubbornPopen.instances[-1]
    base = w.base
    monkeypatch.setattr(pool.shutil, "rmtree", _windows_locked_rmtree(fake, calls))

    w.force_kill()

    assert fake.kill_called, "硬杀路径连 kill 都没发"
    assert fake.reaped, "kill 之后没有再 wait 一次：进程还没消失就返回了"
    assert not w.alive(), "被硬杀的会话必须判死，绝不许被 get() 捡回去复用"
    assert fake.stdin.closed and fake.stdout.closed and w._log.closed

    pool.discard(w)  # 幂等：第二次关停不许抛
    assert not base.exists(), f"exact base 没删掉：{base}（rmtree 调用={calls}）"
    assert str(base) not in pool._oneshot_bases


def test_preview_svg_is_written_without_newline_translation():
    """预览 SVG 的**判定量**在 Windows 上不许比别的平台大。

    `svg_bytes` 取自 `stat().st_size`，而 `mode_for_svg_bytes()` 拿它决定
    vector 还是 raster。matplotlib 拿到**路径**时走
    `cbook.to_filehandle` → `open(fname, "w", encoding=…)`——**没有 `newline`
    参数**，于是 Windows 上每个 `\\n` 变成 `\\r\\n`，同一张图的判定量凭空
    大约 **+3.8%**（实测 22511 vs 21688，差值正好是换行数），**更早掉进
    raster**。用户看到的是「同一份项目，在 Windows 上预览掉档了」，而没有
    任何地方会报错。

    **这条判据是源码级的，因为行为级的在 POSIX 上恒绿**——`\\r\\n` 在这里
    根本不会发生，写完再去数字节永远相等。今晚第四条「本机恒绿、单平台红」
    的缺陷，能提前挡住的只有这个形态。

    反证：把那句改回 `state.fig.savefig(<路径>, format="svg")`，本条当场红。
    """
    src = (Path(__file__).resolve().parent.parent / "src/tavotto/engine/figsession.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(src)

    offenders = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "savefig":
            continue
        fmt = next(
            (k.value for k in node.keywords if k.arg == "format"),
            None,
        )
        # 只管文本格式（svg）；png/pdf 走二进制，matplotlib 用 "wb"，不翻译
        if not (isinstance(fmt, ast.Constant) and fmt.value == "svg"):
            continue
        target = node.args[0] if node.args else None
        # 允许的形状：传一个**已经打开的文件对象**（名字里带 fh/handle/buf）
        if isinstance(target, ast.Name) and any(
            k in target.id.lower() for k in ("fh", "handle", "buf")
        ):
            continue
        offenders.append(f"figsession.py:{node.lineno} savefig(…, format='svg') 传的不是文件对象")
    assert not offenders, (
        "写文本格式必须传 `open(..., newline='')` 出来的文件对象，不能传路径"
        "——传路径时 matplotlib 用文本模式打开，Windows 上 `\\n` 会变 `\\r\\n`，"
        "而 `svg_bytes` 是判定 vector/raster 的量：\n  " + "\n  ".join(offenders)
    )

    # 第二侧：确实是用 `newline=""` 打开的（光"传了个文件对象"不够，
    # 传一个默认模式打开的照样翻译）
    assert 'open(svg_path, "w", encoding="utf-8", newline="")' in src, (
        'figsession 写预览 SVG 必须显式 `newline=""`——默认的 universal '
        "newlines 会在 Windows 上把 `\\n` 翻成 `\\r\\n`"
    )
