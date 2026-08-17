"""AI 历史（SQLite）与 capabilities 探测、CLI 命令构造。"""
import pytest

from magplot.engine import ai_bridge
from magplot.engine import ai_history


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

def test_capabilities_reports_uninstalled(monkeypatch):
    monkeypatch.setattr(ai_bridge, "_cli_path", lambda name: None)
    caps = ai_bridge.capabilities(refresh=True)
    for name in ("codex", "claude"):
        p = caps["providers"][name]
        assert p["installed"] is False and p["models"] == []


def test_capabilities_provider_specific(monkeypatch):
    monkeypatch.setattr(ai_bridge, "_cli_path", lambda name: f"/fake/{name}")
    monkeypatch.setattr(ai_bridge, "_probe_version", lambda p: "v1.0")
    caps = ai_bridge.capabilities(refresh=True)
    codex, claude = caps["providers"]["codex"], caps["providers"]["claude"]
    assert codex["efforts"]  # codex 有推理强度
    assert claude["efforts"] == []  # claude CLI 不暴露，不假装有
    assert codex["models"] != claude["models"]
    # 缓存后不再探测
    monkeypatch.setattr(ai_bridge, "_probe_version", lambda p: "v2.0")
    assert ai_bridge.capabilities()["providers"]["codex"]["version"] == "v1.0"
    ai_bridge.capabilities(refresh=True)


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
    assert 'model_provider="magplot"' in joined
    assert 'model_providers.magplot.base_url="https://api.deepseek.com/v1"' in joined
    assert 'model_providers.magplot.wire_api="chat"' in joined
    # 密钥走环境变量，命令行里只出现变量名
    assert env == {"MAGPLOT_CODEX_API_KEY": "sk-d"}
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
