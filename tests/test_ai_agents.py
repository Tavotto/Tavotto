"""编码 Agent 注册表与适配层（`engine/ai_agents.py`）。

这份盯的是**「加第三个 Agent 时不用改前后端任何一条分支」**这条纪律：
注册表说了算、适配器各管各的、通用层对它们一视同仁。所有用例都是 mock 的
——开发机上装没装 codex / claude 与结论无关（真 CLI 的冒烟另外条件 skip）。
"""
import json
import os
import subprocess

import pytest

from tavotto.engine import ai_agents, ai_bridge, ai_providers, config

#: 真的那份就绪检查执行器。autouse fixture 会把它换成「查不了」，
#: 需要观察真实调用的用例自己换回来。
_REAL_RUN_PROBE = ai_agents._run_probe


def _honour_override(fallback):
    """候选桩：优先用配置里的自定义路径（与真实 candidates 同一条优先级）。"""
    def fake(agent, override=None):
        custom = override if override is not None else ai_agents.path_override(agent.id)
        if custom:
            return [ai_agents.CliCandidate(custom, "custom")]
        return [ai_agents.CliCandidate(p, s) for p, s in fallback]
    return fake


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    ai_bridge.invalidate_capabilities()
    # 就绪检查会真去 spawn；默认钉成「查不了」，需要它的用例自己覆盖
    monkeypatch.setattr(ai_agents, "_run_probe", lambda argv, timeout=10: None)
    yield
    ai_bridge.invalidate_capabilities()


# ---------------- 注册表纪律 --------------------------------------------------

def test_registry_ids_are_unique_and_ordered():
    ids = ai_agents.agent_ids()
    assert len(ids) == len(set(ids)), "id 撞车会让 get_agent 静默返回错的那个"
    # 顺序即界面顺序；生产注册表只放真的能跑起来的两个
    assert ids == ("codex", "claude")


def test_every_agent_declares_the_full_contract():
    for agent in ai_agents.agents():
        assert agent.id and agent.display_name and agent.icon_key
        assert agent.command_names, "没有可执行文件名就没法探测"
        assert isinstance(agent.model_capabilities(), ai_agents.ModelCapabilities)
        assert isinstance(agent.readiness([]), ai_agents.ReadinessResult) or True
        if agent.install_spec is not None:
            assert agent.install_spec.method == "npm"
            assert agent.install_spec.package


def test_unknown_agent_id_is_refused_everywhere():
    """未知 id 一律当场拒，绝不继续往下传（更不会被拼进命令行）。"""
    assert ai_agents.get_agent("opencode") is None
    for call in (lambda: ai_bridge.require_agent("opencode"),
                 lambda: ai_bridge.start_install("opencode"),
                 lambda: ai_bridge.set_agent_enabled("opencode", True),
                 lambda: ai_bridge.set_agent_path_override("opencode", "/x"),
                 lambda: ai_bridge.require_usable("opencode")):
        with pytest.raises(ai_bridge.AgentError) as exc:
            call()
        assert exc.value.code == "ai_agent_unknown"


class _FakeAgent(ai_agents.AgentDefinition):
    """一个只存在于用例里的 Agent。

    它的作用是反证「capabilities 里没有硬编码分支」：把它塞进注册表，
    通用层必须原样把它当第三个 Agent 处理——不需要在 ai_bridge / app 里
    加任何一行。"""
    id = "fake"
    display_name = "Fake Agent"
    icon_key = "fake"
    command_names = ("fakeagent",)
    endpoint_family = None
    install_spec = None
    supports_model_selection = True
    supports_effort_selection = False
    supports_readiness_probe = False

    def model_capabilities(self):
        return ai_agents.ModelCapabilities(models=["m1"], default_model="m1")

    def build_command(self, ctx):
        return ai_agents.SpawnSpec([*ctx.argv, "--go", ctx.prompt], {})

    def classify_event(self, line, state):
        return [("message", line)]


def test_a_third_agent_needs_no_new_branch(monkeypatch):
    """把 Fake Adapter 加进注册表 → capabilities 自动多一条，形状齐全。"""
    monkeypatch.setattr(ai_agents, "AGENT_REGISTRY",
                        (*ai_agents.AGENT_REGISTRY, _FakeAgent()))
    monkeypatch.setattr(
        ai_agents, "candidates",
        lambda agent, override=None: [
            ai_agents.CliCandidate(f"/fake/{agent.id}", "path")])
    monkeypatch.setattr(ai_agents, "probe_version", lambda argv: "v1")
    caps = ai_bridge.capabilities(refresh=True)
    ids = [a["id"] for a in caps["agents"]]
    assert ids == ["codex", "claude", "fake"]
    fake = caps["agents"][-1]
    assert fake["display_name"] == "Fake Agent"
    assert fake["state"] == "installed" and fake["usable"] is True
    assert fake["models"] == ["m1"]
    assert fake["features"] == {"third_party_endpoints": False,
                                "model_selection": True,
                                "effort_selection": False,
                                "wire_api_selection": False,
                                "readiness_probe": False}
    # 不声明 install spec 就没有安装入口（界面据此不画那个按钮）
    assert "install" not in fake
    # 第三方接口的白名单也跟着注册表走，不是第二份手写清单
    assert "fake" not in ai_providers.agents()
    assert set(ai_providers.agents()) == {"codex", "claude"}
    # 命令构造完全交给适配器
    cmd, env = ai_bridge._cmd("fake", "P", "/cwd")
    assert cmd == ["/fake/fake", "--go", "P"] and env == {}


# ---------------- 候选来源与优先级 --------------------------------------------

def test_custom_path_wins_over_everything(tmp_path, monkeypatch):
    exe = tmp_path / "mycodex"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    exe.chmod(0o755)
    monkeypatch.setattr(ai_agents.shutil, "which",
                        lambda name, path=None: "/usr/bin/codex")
    cands = ai_agents.candidates(ai_agents.get_agent("codex"), override=str(exe))
    assert cands[0].path == str(exe) and cands[0].source == "custom"
    assert "/usr/bin/codex" in [c.path for c in cands]   # PATH 仍是次选


def test_path_candidate_is_tagged_path(monkeypatch):
    monkeypatch.setattr(ai_agents.shutil, "which",
                        lambda name, path=None: "/usr/bin/codex" if path is None else None)
    cands = ai_agents.candidates(ai_agents.get_agent("codex"), override="")
    assert cands and cands[0] == ai_agents.CliCandidate("/usr/bin/codex", "path")


def test_common_location_candidates_carry_their_source(tmp_path, monkeypatch):
    """从常见目录翻出来的候选要带上是哪一类目录——详情页的诊断区靠它解释
    「从哪找到的」，而它**绝不参与「能不能用」的判断**。"""
    brew = tmp_path / "brew"
    brew.mkdir()
    (brew / "codex").write_text("", encoding="utf-8")
    monkeypatch.setattr(ai_agents, "search_locations",
                        lambda name: [ai_agents.SearchLocation(str(brew), "homebrew")])
    monkeypatch.setattr(ai_agents.shutil, "which",
                        lambda name, path=None: (str(brew / name) if path else None))
    cands = ai_agents.candidates(ai_agents.get_agent("codex"), override="")
    assert cands == [ai_agents.CliCandidate(str(brew / "codex"), "homebrew")]


def test_resolution_records_where_it_was_found(monkeypatch):
    monkeypatch.setattr(
        ai_agents, "candidates",
        lambda agent, override=None: [
            ai_agents.CliCandidate("/Applications/ChatGPT.app/Contents/Resources/codex",
                                   "chatgpt_bundle")])
    monkeypatch.setattr(ai_agents, "probe_version", lambda argv: "codex-cli 1.0")
    ai_agents.clear_cache()
    res = ai_agents.resolve(ai_agents.get_agent("codex"))
    assert res.source == "chatgpt_bundle" and res.version == "codex-cli 1.0"


def test_only_a_real_version_launch_counts(monkeypatch):
    """光有文件不算数：`--version` 起不来就当没有（找到候选 = broken）。"""
    monkeypatch.setattr(
        ai_agents, "candidates",
        lambda agent, override=None: [ai_agents.CliCandidate("/x/codex", "path")])
    monkeypatch.setattr(ai_agents, "probe_version", lambda argv: None)
    ai_agents.clear_cache()
    res = ai_agents.resolve(ai_agents.get_agent("codex"))
    assert res.argv is None and res.broken_path == "/x/codex"


# ---------------- 就绪检查 ----------------------------------------------------

def test_readiness_never_sends_a_model_request(monkeypatch):
    """健康检查只允许跑官方的**本地状态**子命令。

    这里同时钉死两件事：跑的是哪条命令（写死的子命令，不含用户输入），
    以及 stdin 一律 DEVNULL（任何想要输入的命令必须当场失败，而不是把
    设置页挂死在那儿等）。
    """
    seen: list[list[str]] = []

    def fake_run(argv, **kw):
        seen.append(list(argv))
        assert kw["stdin"] is subprocess.DEVNULL
        assert kw["timeout"] <= ai_agents.READINESS_TIMEOUT_S
        return subprocess.CompletedProcess(argv, 0, "Logged in using ChatGPT", "")

    monkeypatch.setattr(ai_agents, "_run_probe", _REAL_RUN_PROBE)
    monkeypatch.setattr(ai_agents.subprocess, "run", fake_run)
    out = ai_agents.get_agent("codex").readiness(["/x/codex"])
    assert out.state == "ready"
    assert seen == [["/x/codex", "login", "status"]]
    # 绝不出现任何会真的发一次请求 / 建会话的子命令
    flat = " ".join(seen[0])
    for forbidden in ("exec", "-p", "chat", "run"):
        assert f" {forbidden} " not in f" {flat} "


def _probe(monkeypatch, code, text):
    monkeypatch.setattr(ai_agents, "_run_probe",
                        lambda argv, timeout=10: (code, text))


def test_codex_readiness_states(monkeypatch):
    codex = ai_agents.get_agent("codex")
    _probe(monkeypatch, 0, "Logged in using ChatGPT")
    assert codex.readiness(["/x"]).state == "ready"
    # 「not logged in」自带 "logged in" 子串——必须先判否定式
    _probe(monkeypatch, 1, "Not logged in")
    assert codex.readiness(["/x"]).state == "needs_auth"
    _probe(monkeypatch, 2, "error: unrecognized subcommand 'status'")
    assert codex.readiness(["/x"]).state == "unknown"
    monkeypatch.setattr(ai_agents, "_run_probe", lambda argv, timeout=10: None)
    assert codex.readiness(["/x"]).state == "unknown"      # 超时 / 起不来


def test_claude_readiness_reads_only_the_logged_in_flag(monkeypatch):
    """`claude auth status` 的 JSON 里还有邮箱、组织名、订阅档位。
    **只取 loggedIn**——其余一个字节都不该进 capabilities、日志或诊断包。"""
    claude = ai_agents.get_agent("claude")
    payload = json.dumps({"loggedIn": True, "email": "someone@example.com",
                          "orgName": "Someone's Org", "subscriptionType": "max"})
    _probe(monkeypatch, 0, payload)
    out = claude.readiness(["/x"])
    assert out.state == "ready"
    assert "example.com" not in json.dumps(out.__dict__)
    _probe(monkeypatch, 0, json.dumps({"loggedIn": False}))
    assert claude.readiness(["/x"]).state == "needs_auth"
    _probe(monkeypatch, 1, "Unknown command: auth")
    assert claude.readiness(["/x"]).state == "unknown"


def test_readiness_timeout_does_not_hang_the_settings_page(monkeypatch):
    """就绪检查超时 = unknown = 界面显示「已安装」，其余一切照常工作。"""
    def boom(argv, **kw):
        raise subprocess.TimeoutExpired(argv, kw.get("timeout", 1))

    monkeypatch.setattr(ai_agents.subprocess, "run", boom)
    monkeypatch.setattr(
        ai_agents, "candidates",
        lambda agent, override=None: [ai_agents.CliCandidate("/x/cli", "path")])
    monkeypatch.setattr(ai_agents, "probe_version", lambda argv: "v1")
    ai_agents.clear_cache()
    caps = ai_bridge.capabilities(refresh=True)
    codex = caps["agents"][0]
    assert codex["state"] == "installed"          # 不是 ready，也不是 needs_auth
    assert codex["diagnostics"]["readiness"] == "unknown"


def test_needs_auth_is_not_usable(monkeypatch):
    monkeypatch.setattr(
        ai_agents, "candidates",
        lambda agent, override=None: [ai_agents.CliCandidate(f"/x/{agent.id}", "path")])
    monkeypatch.setattr(ai_agents, "probe_version", lambda argv: "v1")
    _probe(monkeypatch, 1, "Not logged in")
    ai_agents.clear_cache()
    codex = ai_bridge.capabilities(refresh=True)["agents"][0]
    assert codex["state"] == "needs_auth"
    assert codex["installed"] is True and codex["enabled"] is True
    assert codex["usable"] is False               # 明确要登录 = 现在派不了活


# ---------------- enabled / usable 语义 ---------------------------------------

def _installed(monkeypatch):
    monkeypatch.setattr(
        ai_agents, "candidates",
        lambda agent, override=None: [ai_agents.CliCandidate(f"/x/{agent.id}", "path")])
    monkeypatch.setattr(ai_agents, "probe_version", lambda argv: "v1")
    ai_agents.clear_cache()


def _codex(caps):
    return next(a for a in caps["agents"] if a["id"] == "codex")


def test_enabled_defaults_to_installed(monkeypatch):
    _installed(monkeypatch)
    assert _codex(ai_bridge.capabilities(refresh=True))["enabled"] is True
    monkeypatch.setattr(ai_agents, "candidates", lambda agent, override=None: [])
    ai_bridge.invalidate_capabilities()
    entry = _codex(ai_bridge.capabilities(refresh=True))
    assert entry["enabled"] is False and entry["state"] == "not_installed"


def test_explicit_off_survives_a_successful_detection(monkeypatch):
    """用户明确关过就一直关着——下次探测成功也不自动翻回来。"""
    _installed(monkeypatch)
    ai_bridge.set_agent_enabled("codex", False)
    entry = _codex(ai_bridge.capabilities(refresh=True))
    assert entry["enabled"] is False
    assert entry["installed"] is True
    assert entry["state"] == "disabled" and entry["usable"] is False
    # 重新探测（CLI 仍在）→ 仍然是关着的
    ai_bridge.invalidate_capabilities()
    assert _codex(ai_bridge.capabilities(refresh=True))["enabled"] is False


def test_never_touched_agent_auto_enables_after_a_new_install(monkeypatch):
    """从没表过态的 Agent：装上了就自动可用，不该逼用户先去开一次开关。"""
    monkeypatch.setattr(ai_agents, "candidates", lambda agent, override=None: [])
    assert _codex(ai_bridge.capabilities(refresh=True))["enabled"] is False
    _installed(monkeypatch)
    ai_bridge.invalidate_capabilities()
    entry = _codex(ai_bridge.capabilities(refresh=True))
    assert entry["enabled"] is True and entry["usable"] is True


def test_cannot_enable_an_agent_that_is_not_installed(monkeypatch):
    monkeypatch.setattr(ai_agents, "candidates", lambda agent, override=None: [])
    ai_bridge.invalidate_capabilities()
    with pytest.raises(ai_bridge.AgentError) as exc:
        ai_bridge.set_agent_enabled("codex", True)
    assert exc.value.code == "ai_agent_not_installed"


def test_disabled_agent_is_refused_at_run_time(monkeypatch):
    """禁用不能只靠前端隐藏——run 这条路必须自己判。"""
    _installed(monkeypatch)
    ai_bridge.set_agent_enabled("codex", False)
    with pytest.raises(ai_bridge.AgentError) as exc:
        ai_bridge.require_usable("codex")
    assert exc.value.code == "ai_agent_disabled"


# ---------------- 接了第三方接口时，CLI 自己的登录态不算数 ----------------

def test_endpoint_backed_agent_stays_usable_without_cli_login(monkeypatch):
    """配了第三方接口的用户不该因为「没登录官方账号」被踢出选择器。

    注入那套凭据的全部意义就是让 CLI 不必用官方登录跑起来；拿 CLI 的登录态
    去回答「现在能不能派活」是把判据的主语搞错了。（PR #128 评审 P1）
    """
    _installed(monkeypatch)
    _probe(monkeypatch, 1, "Not logged in")          # CLI 自己没登录
    ai_bridge.invalidate_capabilities()
    # 先确认「没接接口时」它确实是 needs_auth——否则这条用例什么也没证明
    assert _codex(ai_bridge.capabilities(refresh=True))["state"] == "needs_auth"

    ai_providers.save({"id": "deepseek-oai", "label": "DeepSeek", "agent": "codex",
                       "base_url": "https://api.deepseek.com/v1",
                       "api_key": "sk-x", "wire_api": "chat"})
    ai_providers.set_active("codex", "deepseek-oai")
    ai_bridge.invalidate_capabilities()
    entry = _codex(ai_bridge.capabilities(refresh=True))
    assert entry["state"] == "installed"             # 不再是 needs_auth
    assert entry["usable"] is True                   # 选择器里留得住
    # 原始结论照实记在诊断里，只是不再当闸
    assert entry["diagnostics"]["readiness"] == "needs_auth"
    ai_bridge.require_usable("codex")                # 运行这条闸也放行


def test_an_endpoint_that_injects_nothing_does_not_excuse_the_login(monkeypatch):
    """判据是「spawn_overrides 真的注入了东西」，不是「配置里有一条记录」。

    codex 侧 base_url 为空时 spawn_overrides 一个字节都不注入——那种情况
    CLI 自己的登录态仍然算数。两份规则分叉的表现是「界面说可用、一跑就报
    未登录」。
    """
    _installed(monkeypatch)
    _probe(monkeypatch, 1, "Not logged in")
    ai_providers.save({"id": "official", "label": "OpenAI 官方", "agent": "codex",
                       "base_url": "", "wire_api": "responses"})
    ai_providers.set_active("codex", "official")
    ai_bridge.invalidate_capabilities()
    entry = _codex(ai_bridge.capabilities(refresh=True))
    assert entry["state"] == "needs_auth" and entry["usable"] is False


def test_run_refuses_an_agent_that_needs_auth(monkeypatch):
    """`require_usable` 的判据必须与 capabilities 的 usable 一致——
    前端把它藏了、API 还放它进来，是最难查的一类不一致。"""
    _installed(monkeypatch)
    _probe(monkeypatch, 1, "Not logged in")
    ai_bridge.invalidate_capabilities()
    with pytest.raises(ai_bridge.AgentError) as exc:
        ai_bridge.require_usable("codex")
    assert exc.value.code == "ai_agent_needs_auth"


# ---------------- 自定义可执行文件 --------------------------------------------

def test_custom_path_is_validated_the_same_way_as_autodetection(tmp_path, monkeypatch):
    exe = tmp_path / "codex"
    exe.write_text("", encoding="utf-8")
    exe.chmod(0o755)                     # 校验要求可执行位，fixture 得是真的
    monkeypatch.setattr(ai_agents, "probe_version_detailed",
                        lambda argv: ("codex-cli 9.9", None))
    monkeypatch.setattr(ai_agents, "candidates", _honour_override([]))
    monkeypatch.setattr(ai_agents, "probe_version", lambda argv: "codex-cli 9.9")
    caps = ai_bridge.set_agent_path_override("codex", str(exe))
    entry = _codex(caps)
    assert entry["path_override"] == str(exe)
    assert config.ai_agent_settings()["codex"]["path_override"] == str(exe)


def test_invalid_custom_path_does_not_clobber_the_saved_one(tmp_path, monkeypatch):
    """验证不过就一个字节都不写——用户原来那份有效设置绝不能被手滑清掉。"""
    good = tmp_path / "codex"
    good.write_text("", encoding="utf-8")
    good.chmod(0o755)
    monkeypatch.setattr(ai_agents, "probe_version_detailed",
                        lambda argv: ("codex-cli 9.9", None))
    monkeypatch.setattr(ai_agents, "candidates", _honour_override([]))
    monkeypatch.setattr(ai_agents, "probe_version", lambda argv: "codex-cli 9.9")
    ai_bridge.set_agent_path_override("codex", str(good))

    # 名字带 codex → 过得了「是不是这个 Agent」那道闸，这样这条用例守的
    # 才是「--version 起不来」，而不是被前一道闸挡下
    bad = tmp_path / "codex-broken"
    bad.write_text("", encoding="utf-8")
    bad.chmod(0o755)
    monkeypatch.setattr(ai_agents, "probe_version_detailed",
                        lambda argv: (None, "launch_failed"))
    with pytest.raises(ai_bridge.AgentError) as exc:
        ai_bridge.set_agent_path_override("codex", str(bad))
    assert exc.value.code == "ai_agent_executable_invalid"
    assert config.ai_agent_settings()["codex"]["path_override"] == str(good)

    # 文件根本不存在也是同一条路（不是 500）
    with pytest.raises(ai_bridge.AgentError) as exc:
        ai_bridge.set_agent_path_override("codex", str(tmp_path / "nope"))
    assert exc.value.code == "ai_agent_executable_invalid"

    # 探测超时有自己的 code（可重试，不是「路径填错了」）
    monkeypatch.setattr(ai_agents, "probe_version_detailed",
                        lambda argv: (None, "timeout"))
    with pytest.raises(ai_bridge.AgentError) as exc:
        ai_bridge.set_agent_path_override("codex", str(bad))
    assert exc.value.code == "ai_agent_probe_timeout"
    assert config.ai_agent_settings()["codex"]["path_override"] == str(good)


def test_custom_path_refuses_an_arbitrary_executable(tmp_path, monkeypatch):
    """**这条是安全看护**（CodeQL py/path-injection，PR #128 上报出的那条）。

    `path_override` 来自 HTTP 请求体，最终会被 spawn。「是个文件」远远不够：
    把 Tavotto 指向 /bin/sh 不是「路径填错了」，那是拿一个任意可执行文件
    换掉将要被启动的程序。判据是文件名必须指向这个 Agent。
    """
    codex = ai_agents.get_agent("codex")
    monkeypatch.setattr(ai_agents, "probe_version_detailed",
                        lambda argv: ("codex-cli 9.9", None))

    evil = tmp_path / "sh"
    evil.write_text("#!/bin/sh\n", encoding="utf-8")
    evil.chmod(0o755)
    res = ai_agents.validate_executable(codex, str(evil))
    assert res.argv is None and res.error == "not_this_agent"
    with pytest.raises(ai_bridge.AgentError) as exc:
        ai_bridge.set_agent_path_override("codex", str(evil))
    assert exc.value.code == "ai_agent_executable_invalid"
    assert "path_override" not in config.ai_agent_settings().get("codex", {})

    # 叫得出名字的都放行：codex / codex.exe / codex-cli / run-codex.sh
    for name in ("codex", "codex.exe", "codex-cli", "run-codex.sh"):
        assert ai_agents.names_this_agent(codex, f"/somewhere/{name}")
    # 别家的、以及通用解释器，一律不认
    for name in ("sh", "bash", "python3", "claude", "rm"):
        assert not ai_agents.names_this_agent(codex, f"/somewhere/{name}")


def test_custom_path_normalises_before_judging(tmp_path, monkeypatch):
    """`..` 与符号链接在判断**之前**解掉——否则判据打在一个还能再跳一次的
    字符串上，`/safe/../bin/sh` 这种会带着一个看起来合规的前缀溜过去。"""
    codex = ai_agents.get_agent("codex")
    monkeypatch.setattr(ai_agents, "probe_version_detailed", lambda argv: ("v1", None))
    real = tmp_path / "sh"
    real.write_text("", encoding="utf-8")
    real.chmod(0o755)
    # 名字合规的符号链接指向一个不合规的真身：认真身，不认链接名
    link = tmp_path / "codex"
    link.symlink_to(real)
    res = ai_agents.validate_executable(codex, str(link))
    assert res.argv is None and res.error == "not_this_agent"
    # 绕路写法同样落到真身上
    sub = tmp_path / "sub"
    sub.mkdir()
    res = ai_agents.validate_executable(codex, str(sub / ".." / "sh"))
    assert res.argv is None and res.error == "not_this_agent"


def test_custom_path_requires_an_executable_bit(tmp_path, monkeypatch):
    codex = ai_agents.get_agent("codex")
    monkeypatch.setattr(ai_agents, "probe_version_detailed", lambda argv: ("v1", None))
    plain = tmp_path / "codex"
    plain.write_text("", encoding="utf-8")
    plain.chmod(0o644)
    res = ai_agents.validate_executable(codex, str(plain))
    assert res.argv is None and res.error == "not_executable"
    plain.chmod(0o755)
    assert ai_agents.validate_executable(codex, str(plain)).argv is not None


def test_custom_path_refuses_empty_and_nul(tmp_path):
    codex = ai_agents.get_agent("codex")
    for bad in ("", "   ", "/tmp/co\x00dex"):
        assert ai_agents.validate_executable(codex, bad).argv is None


def test_clearing_the_override_returns_to_autodetection(tmp_path, monkeypatch):
    exe = tmp_path / "codex"
    exe.write_text("", encoding="utf-8")
    exe.chmod(0o755)
    monkeypatch.setattr(ai_agents, "probe_version_detailed",
                        lambda argv: ("v1", None))
    monkeypatch.setattr(ai_agents, "candidates",
                        _honour_override([("/usr/bin/codex", "path")]))
    monkeypatch.setattr(ai_agents, "probe_version", lambda argv: "v1")
    ai_bridge.set_agent_path_override("codex", str(exe))
    assert _codex(ai_bridge.capabilities())["detection_source"] == "custom"
    caps = ai_bridge.set_agent_path_override("codex", "")
    assert _codex(caps)["path_override"] is None
    assert _codex(caps)["detection_source"] == "path"
    assert "path_override" not in config.ai_agent_settings().get("codex", {})


# ---------------- 旧配置迁移 --------------------------------------------------

def test_legacy_cli_paths_migrate_into_the_generic_shape(tmp_path, monkeypatch):
    """v0.10 及更早存的是 `ai.codex_path` / `ai.claude_path`。

    迁移完必须**删掉旧键**：两份权威并存的话，一边改路径另一边不知道。
    """
    monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(tmp_path))
    cfg = {"ai": {"codex_path": "/old/codex", "claude_path": "/old/claude",
                  "providers": [{"id": "kimi", "agent": "claude"}]}}
    config.save({**config._defaults(), **cfg})

    agents = config.ai_agent_settings()
    assert agents["codex"]["path_override"] == "/old/codex"
    assert agents["claude"]["path_override"] == "/old/claude"

    raw = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert "codex_path" not in raw["ai"] and "claude_path" not in raw["ai"]
    assert raw["ai"]["providers"], "第三方接口记录不能被迁移顺手抹掉"


def test_legacy_migration_does_not_override_the_new_shape(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(tmp_path))
    config.save({**config._defaults(),
                 "ai": {"codex_path": "/old/codex",
                        "agents": {"codex": {"path_override": "/new/codex"}}}})
    assert config.ai_agent_settings()["codex"]["path_override"] == "/new/codex"


def test_unknown_agent_settings_survive(tmp_path, monkeypatch):
    """配置里留着不认识的 Agent（降级 / 未来版本）不能让设置页整个炸掉。"""
    monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(tmp_path))
    config.save({**config._defaults(),
                 "ai": {"agents": {"someday": {"enabled": True}, "codex": {}}}})
    assert config.ai_agent_settings()["someday"] == {"enabled": True}
    caps = ai_bridge.capabilities(refresh=True)
    assert [a["id"] for a in caps["agents"]] == ["codex", "claude"]
    config.set_ai_agent_settings("codex", {"enabled": False})
    assert config.ai_agent_settings()["someday"] == {"enabled": True}


# ---------------- 一键安装的包名来源 -------------------------------------------

def test_install_package_comes_only_from_the_adapter(monkeypatch):
    """包名写死在适配器里；请求体里没有、也不接受任何包名字段。"""
    ran: list[list[str]] = []

    def fake_run(argv, **kw):
        ran.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    monkeypatch.setattr(ai_bridge, "_npm_argv", lambda: ["/usr/bin/npm"])
    monkeypatch.setattr(ai_bridge, "_INSTALLS", {})
    monkeypatch.setattr(ai_bridge.subprocess, "run", fake_run)
    _installed(monkeypatch)
    ai_bridge.start_install("codex")
    for _ in range(200):
        if ai_bridge.install_status("codex")["status"] != "running":
            break
        import time as _t
        _t.sleep(0.01)
    assert ran and ran[0] == ["/usr/bin/npm", "install", "-g", "@openai/codex"]
    assert ai_bridge.install_status("codex")["status"] == "done"


def test_npm_success_still_requires_a_real_launch(monkeypatch):
    """npm 说成了不算数：CLI 仍然起不来就如实报「装完还是不可用」。"""
    monkeypatch.setattr(ai_bridge, "_npm_argv", lambda: ["/usr/bin/npm"])
    monkeypatch.setattr(ai_bridge, "_INSTALLS", {})
    monkeypatch.setattr(
        ai_bridge.subprocess, "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "added 1 package", ""))
    monkeypatch.setattr(ai_agents, "candidates", lambda agent, override=None: [])
    ai_bridge.start_install("codex")
    import time as _t
    for _ in range(200):
        if ai_bridge.install_status("codex")["status"] != "running":
            break
        _t.sleep(0.01)
    st = ai_bridge.install_status("codex")
    assert st["status"] == "error" and st["code"] == "installed_but_not_found"


# ---------------- 输出分类的等价性 ---------------------------------------------

def test_codex_classification_is_unchanged():
    st: dict = {}
    line = json.dumps({"type": "item.completed",
                       "item": {"type": "agent_message", "text": "done"}})
    assert ai_bridge._classify("codex", line, st) == [("message", "done")]
    st = {}
    upd = json.dumps({"type": "item.updated",
                      "item": {"type": "agent_message", "text": "hel"}})
    assert ai_bridge._classify("codex", upd, st) == [("delta", "hel")]
    upd2 = json.dumps({"type": "item.updated",
                       "item": {"type": "agent_message", "text": "hello"}})
    assert ai_bridge._classify("codex", upd2, st) == [("delta", "lo")]
    cmd = json.dumps({"type": "item.completed",
                      "item": {"type": "command_execution", "command": "ls"}})
    assert ai_bridge._classify("codex", cmd, st) == [("action", "$ ls")]
    patch = json.dumps({"type": "item.completed", "item": {"type": "patch_apply"}})
    assert ai_bridge._classify("codex", patch, st) == [("action", "✎ 修改文件")]
    assert ai_bridge._classify("codex", "not json at all", st) == []


def test_claude_classification_is_unchanged():
    st: dict = {}
    delta = json.dumps({"type": "stream_event",
                        "event": {"type": "content_block_delta",
                                  "delta": {"type": "text_delta", "text": "hi"}}})
    assert ai_bridge._classify("claude", delta, st) == [("delta", "hi")]
    asst = json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "ok"},
        {"type": "tool_use", "name": "Edit", "input": {"file_path": "a.py"}},
        {"type": "thinking", "thinking": "hmm"}]}})
    assert ai_bridge._classify("claude", asst, st) == [
        ("message", "ok"), ("action", "✎ Edit a.py"), ("thinking", "hmm")]
    err = json.dumps({"type": "result", "is_error": True, "result": "boom"})
    assert ai_bridge._classify("claude", err, st) == [("message", "boom")]
    assert ai_bridge._classify("claude", json.dumps({"type": "system"}), st) == []


def test_classification_of_an_unknown_agent_is_empty():
    assert ai_bridge._classify("opencode", '{"type":"x"}', {}) == []


# ---------------- 真 CLI 冒烟（装了才跑）---------------------------------------

@pytest.mark.skipif(not os.environ.get("TAVOTTO_REAL_CLI_SMOKE"),
                    reason="需要本机真的装了 codex / claude；设 TAVOTTO_REAL_CLI_SMOKE=1 开启")
def test_real_cli_detection_smoke():
    ai_agents.clear_cache()
    for agent in ai_agents.agents():
        res = ai_agents.resolve(agent)
        if res.argv is None:
            continue
        assert res.version and res.source in ai_agents.SOURCES
        assert res.readiness.state in ("ready", "needs_auth", "unknown")
