"""AI 历史（SQLite）与 capabilities 探测、CLI 命令构造。"""
import pytest

from tavotto.engine import ai_bridge
from tavotto.engine import ai_history


@pytest.fixture
def db(tmp_path):
    return tmp_path / "hist.sqlite3"


def _start(db, sid="s1", **kw):
    ai_history.record_start({
        "id": sid, "project": "/p", "provider": "codex",
        "prompt": kw.pop("prompt", "改图例"), "target": kw.pop("target", "整张图"),
        "script": "fig9.py", **kw,
    }, db_path=db)


def test_roundtrip_and_end(db):
    _start(db, model="gpt-5-codex", effort="high")
    ai_history.record_end("s1", "done", diff="+x", changed=True,
                          transcript=[{"kind": "message", "text": "ok"}], db_path=db)
    row = ai_history.get("s1", db_path=db)
    assert row["status"] == "done" and row["changed"] is True
    assert row["model"] == "gpt-5-codex" and row["effort"] == "high"
    assert row["transcript"] == [{"kind": "message", "text": "ok"}]
    assert row["ended_ms"] is not None
    assert row["revert_available"] is False  # 没有快照文件


def test_mark_interrupted_running(db):
    _start(db, sid="r1")
    _start(db, sid="r2")
    ai_history.record_end("r2", "done", db_path=db)
    assert ai_history.mark_interrupted_running(db_path=db) == 1
    assert ai_history.get("r1", db_path=db)["status"] == "interrupted"
    assert ai_history.get("r2", db_path=db)["status"] == "done"


def test_list_search_filter_pagination(db):
    for i in range(5):
        _start(db, sid=f"s{i}", prompt=f"任务{i} 图例" if i % 2 else f"任务{i} 配色")
        ai_history.record_end(f"s{i}", "done" if i < 3 else "failed", db_path=db)
    out = ai_history.list_sessions("/p", limit=2, db_path=db)
    assert out["total"] == 5 and len(out["sessions"]) == 2
    out = ai_history.list_sessions("/p", query="图例", db_path=db)
    assert out["total"] == 2
    out = ai_history.list_sessions("/p", status="failed", db_path=db)
    assert out["total"] == 2
    # 项目隔离
    assert ai_history.list_sessions("/other", db_path=db)["total"] == 0


def test_pin_delete_purge(db):
    _start(db, sid="p1")
    _start(db, sid="p2")
    assert ai_history.set_pinned("p1", True, db_path=db) is True
    # 保留期 0 天：未固定的删掉、固定的保留
    assert ai_history.purge(0, db_path=db) == 1
    assert ai_history.get("p1", db_path=db) is not None
    assert ai_history.get("p2", db_path=db) is None
    assert ai_history.delete("p1", db_path=db) is True
    assert ai_history.delete("p1", db_path=db) is False


# ---------------- capabilities 与命令构造 -------------------------------------

@pytest.fixture(autouse=True)
def _clean_caps_cache():
    """capabilities 缓存是模块级全局：本文件的用例在 monkeypatch 生效期间
    重建过它，跑完必须清掉，否则假探测结果会串进后面的诊断包用例。"""
    yield
    ai_bridge.invalidate_capabilities()


def test_capabilities_reports_uninstalled(monkeypatch):
    monkeypatch.setattr(ai_bridge, "_cli_candidates", lambda name: [])
    monkeypatch.setattr(ai_bridge, "_RESOLVE_CACHE", {})
    caps = ai_bridge.capabilities(refresh=True)
    for name in ("codex", "claude"):
        p = caps["providers"][name]
        assert p["installed"] is False and p["models"] == []
        # 未安装时给出一键安装的可行性信息（npm 在不在由测试机决定，不断言）
        assert p["install"]["method"] == "npm" and p["install"]["package"]


def test_capabilities_provider_specific(monkeypatch):
    monkeypatch.setattr(ai_bridge, "_cli_candidates", lambda name: [f"/fake/{name}"])
    monkeypatch.setattr(ai_bridge, "_probe_version", lambda p: "v1.0")
    monkeypatch.setattr(ai_bridge, "_RESOLVE_CACHE", {})
    caps = ai_bridge.capabilities(refresh=True)
    codex, claude = caps["providers"]["codex"], caps["providers"]["claude"]
    assert codex["efforts"]  # codex 有推理强度
    assert claude["efforts"] == []  # claude CLI 不暴露，不假装有
    assert codex["models"] != claude["models"]
    # 缓存后不再探测
    monkeypatch.setattr(ai_bridge, "_probe_version", lambda p: "v2.0")
    assert ai_bridge.capabilities()["providers"]["codex"]["version"] == "v1.0"
    ai_bridge.capabilities(refresh=True)


def test_broken_candidate_falls_through(monkeypatch):
    """第一个候选启动不了（WindowsApps 执行别名的典型故障）要落到下一个；
    全都启动不了时必须报未安装 + broken_path，绝不能拿着坏路径宣布已安装。"""
    monkeypatch.setattr(ai_bridge, "_RESOLVE_CACHE", {})
    monkeypatch.setattr(
        ai_bridge, "_cli_candidates",
        lambda name: [rf"C:\Users\x\AppData\Local\Microsoft\WindowsApps\{name}.exe",
                      rf"C:\Users\x\AppData\Roaming\npm\{name}.cmd"])
    monkeypatch.setattr(ai_bridge, "_resolve_shim", lambda p: None)
    monkeypatch.setattr(
        ai_bridge, "_probe_version",
        lambda argv: "v9.9" if "npm" in argv[0] else None)
    caps = ai_bridge.capabilities(refresh=True)
    codex = caps["providers"]["codex"]
    assert codex["installed"] is True
    assert "npm" in codex["path"] and codex["version"] == "v9.9"

    # 全部候选都启动不了：未安装 + 指出坏在哪
    ai_bridge.invalidate_capabilities()
    monkeypatch.setattr(ai_bridge, "_probe_version", lambda argv: None)
    caps = ai_bridge.capabilities(refresh=True)
    codex = caps["providers"]["codex"]
    assert codex["installed"] is False
    assert "WindowsApps" in codex["broken_path"]


def test_start_install_reports_missing_npm(monkeypatch):
    """现场没有 npm：结构化 npm_missing，引导装 Node，而不是闷头失败。"""
    monkeypatch.setattr(ai_bridge, "_npm_argv", lambda: None)
    monkeypatch.setattr(ai_bridge, "_INSTALLS", {})
    st = ai_bridge.start_install("codex")
    assert st["status"] == "error" and st["code"] == "npm_missing"
    with pytest.raises(ValueError):
        ai_bridge.start_install("not-a-cli")


def test_cmd_passes_model_and_effort(monkeypatch):
    monkeypatch.setattr(ai_bridge, "_find_cli", lambda name: [f"/fake/{name}"])
    cmd, env = ai_bridge._cmd("codex", "p", "/cwd", model="gpt-5", effort="high")
    assert "-m" in cmd and "gpt-5" in cmd
    assert "-c" in cmd and "model_reasoning_effort=high" in cmd
    assert cmd[-1] == "p"  # prompt 恒在末位
    assert env == {}       # 没选第三方接口就不注入任何环境变量
    cmd, _ = ai_bridge._cmd("claude", "p", "/cwd", model="opus")
    assert "--model" in cmd and "opus" in cmd
    # 不传参数 = 不携带对应 flag
    cmd, _ = ai_bridge._cmd("codex", "p", "/cwd")
    assert "-m" not in cmd


def test_cmd_injects_third_party_endpoint(monkeypatch):
    """第三方接口只经环境变量 / `-c` 临时覆盖注入——绝不改写用户
    自己的 ~/.claude/settings.json 或 ~/.codex/config.toml。"""
    monkeypatch.setattr(ai_bridge, "_find_cli", lambda name: [f"/fake/{name}"])
    claude_ep = {"agent": "claude", "label": "Kimi", "api_key": "sk-k",
                 "base_url": "https://api.moonshot.cn/anthropic"}
    cmd, env = ai_bridge._cmd("claude", "p", "/cwd", model="kimi-k2",
                              endpoint=claude_ep)
    assert env["ANTHROPIC_BASE_URL"] == "https://api.moonshot.cn/anthropic"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-k"
    assert env["ANTHROPIC_MODEL"] == "kimi-k2"
    assert not any(a.startswith("ANTHROPIC") for a in cmd)  # 密钥不进命令行

    codex_ep = {"agent": "codex", "label": "DeepSeek", "api_key": "sk-d",
                "base_url": "https://api.deepseek.com/v1", "wire_api": "chat"}
    cmd, env = ai_bridge._cmd("codex", "p", "/cwd", endpoint=codex_ep)
    joined = " ".join(cmd)
    assert 'model_provider="tavotto"' in joined
    assert 'model_providers.tavotto.base_url="https://api.deepseek.com/v1"' in joined
    assert 'model_providers.tavotto.wire_api="chat"' in joined
    # 密钥走环境变量，命令行里只出现变量名
    assert env == {"TAVOTTO_CODEX_API_KEY": "sk-d"}
    assert "sk-d" not in joined


def test_no_private_paths_in_cli_lookup():
    """CLI 查找里不得出现任何指向某个具体用户主目录的绝对路径。

    只允许 `/opt/homebrew/...`、`/usr/local/...` 这类系统级位置，以及运行时
    由 `Path.home()` 拼出来的路径——写死 `/Users/<某人>` 或 `/home/<某人>`
    在别人机器上必然失效，也会把作者的目录结构随发行版一起公开。
    """
    import inspect
    import re

    src = inspect.getsource(ai_bridge)
    hits = re.findall(r'["\'](/(?:Users|home)/[^"\'/\s]+[^"\']*)["\']', src)
    assert not hits, f"发现写死的用户主目录: {hits}"


def test_build_prompt_normalized(tmp_path):
    """给编程助手的提示词规范化看护：

    1. 「必须用 matplotlib + savefig 出图」是硬性要求——助手改用 PIL/plotly/
       手写 SVG 出的图，live-figure 链路完全无法参数化编辑；
    2. paper_style.py 是图库方言：图库里有才提，没有绝不提——更不允许把某个
       具体图库的私有规范（字体/配色号）硬编码进产品提示词。
    """
    p = ai_bridge._build_prompt("fig1.py", "把线加粗", None, str(tmp_path))
    assert "matplotlib" in p and "savefig" in p
    assert "stem" in p and "出图数量" in p
    assert "paper_style" not in p  # 图库里没有这个文件，不该无中生有

    (tmp_path / "paper_style.py").write_text("save = None\n", encoding="utf-8")
    p2 = ai_bridge._build_prompt("fig1.py", "把线加粗", None, str(tmp_path))
    assert "paper_style.py" in p2
    assert "AMFE" not in p2  # 私有图库的规范细节不得硬编码

    # 上下文照常拼进去
    p3 = ai_bridge._build_prompt(
        "fig1.py", "换配色", {"stem": "Fig1", "gid": "axes_0"}, str(tmp_path))
    assert "Fig1" in p3 and "axes_0" in p3 and "换配色" in p3
