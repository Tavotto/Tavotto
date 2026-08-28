"""用户可见的后端错误：稳定 code + 参数，文案归前端。

约定写在 `app.py` 的 API 段首。这批用例守两件事：

1. 这些端点**真的**给了 code（漏一个的表现不是报错，而是英文界面里冷不丁
   蹦出一句中文——只有装了英文界面的用户会遇到，本地永远复现不了）；
2. 每个 code 在**两种语言**里都有文案，且占位符与后端给的 params 对得上
   （`{{path}}` 写成 `{{dir}}` 的话，界面上就是一个原样的 `{{path}}`）。

新增用户可见的失败时：app.py 给 code + params → 两份 errors.json 加文案 →
这张表里加一行。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "src" / "tavotto" / "app.py"
LOCALES = ROOT / "web" / "src" / "i18n" / "locales"


#: 会返回用户可见 JSON 错误的模块。`engine/ai_bridge.py` 在列，是因为编码
#: Agent 的失败以 `AgentError("<code>")` 抛出、由 app.py 的一个漏斗转成 JSON
#: ——只扫 app.py 的话这批 code 一个都看不见，而看不见的门禁 = 没有门禁。
_SOURCE_FILES = ("app.py", "security.py", "desktop.py", "engine/ai_bridge.py")


def _all_error_sources() -> str:
    root = ROOT / "src" / "tavotto"
    return "\n".join(
        (root / n).read_text(encoding="utf-8") for n in _SOURCE_FILES if (root / n).is_file()
    )


#: 源码里「声明了一个 code」的两种写法：字面量响应，或带 code 的异常。
_CODE_PATTERNS = (r'"code":\s*"([a-z0-9_]+)"', r'AgentError\(\s*"([a-z0-9_]+)"')


def _declared_codes(text: str) -> set[str]:
    out: set[str] = set()
    for pat in _CODE_PATTERNS:
        out.update(re.findall(pat, text))
    return out


# code → 后端会塞进 params 的键（2026-08-21 起覆盖 app.py 的**全部**字面量
# code——审计 P1-02：错误尾部不许泄漏中文，所以每个 code 都要有两种语言的
# 文案，且占位符与 params 对得上）
USER_VISIBLE_CODES = {
    "no_project": set(),
    "missing_path": set(),
    "mkdir_failed": {"reason"},
    "open_project_failed": {"reason"},
    "invalid_path": set(),
    "dir_missing": {"path"},
    "permission_denied": {"path"},
    "read_failed": {"reason"},
    # --- 2026-08-21 全量补 code 的那批 ---
    "internal_error": {"reason"},
    "export_render_failed": {"id", "reason"},
    "invalid_document": set(),
    "package_file_missing": set(),
    "package_invalid": {"reason"},
    "package_schema_unsupported": set(),
    "scan_failed": {"reason"},
    # --- Compatibility Bridge Session 3：试运行路径校验的三种拒绝 ---
    "script_not_found": {"script"},
    "script_path_outside_project": {"script"},
    "unsupported_script_type": {"script"},
    # --- Compatibility Bridge Session 4：RuntimeFigureAsset（ADR 0013）。
    # runtime_source_writeback_unsupported 刻意不在这里：v1 没有任何改写脚本
    # 源码的端点（裁决出处 runtimeasset.writeback_rejection），码表 + 双语
    # 文案先行、producer 后补的对拍在 tests/test_runtime_asset.py ---
    "runtime_asset_unknown": {"id"},
    "runtime_asset_has_no_original_artifact": set(),
    "runtime_cache_missing": set(),
    # --- Compatibility Bridge Session 5：素材库普通入口 ---
    "probe_in_progress": {"script"},
    "script_name_missing": set(),
    "invalid_entry": {"entry"},
    "registry_update_failed": {"reason"},
    "settings_dir_unusable": {"key", "reason"},
    "invalid_preview_dpi": {"value"},
    "invalid_patches": set(),
    "invalid_width": {"value"},
    "not_parameterizable": set(),
    "sync_different_scripts": set(),
    "python_missing": set(),
    "interpreter_not_found": {"path"},
    "interpreter_no_matplotlib": {"path"},
    "invalid_consent": set(),
    "name_missing": set(),
    "endpoint_save_failed": {"reason"},
    "endpoint_invalid": {"reason"},
    "ai_start_failed": {"reason"},
    "ai_revert_failed": {"reason"},
    # --- 编码 Agent 注册表（ADR 0013）：全部经 AgentError 抛出、
    #     由 app.py 的 _agent_error 一个漏斗转成 JSON ---
    "ai_agent_unknown": {"agent"},
    "ai_agent_disabled": {"agent"},
    "ai_agent_not_installed": {"agent"},
    "ai_agent_needs_auth": {"agent"},
    "ai_agent_not_usable": {"agent"},
    "ai_agent_install_unsupported": {"agent"},
    "ai_agent_executable_invalid": {"path"},
    "ai_agent_probe_timeout": {"path"},
    # --- #24（ADR 0008 会话认证）带来的用户可见 code ---
    "session_auth_required": set(),
    # --- 早已有 code、这次补上文案的存量 ---
    "annotations_need_pdf": set(),
    "desktop_updater_disabled": set(),
    "file_locked": set(),
    "replay_divergence": set(),
    "script_changed": set(),
    "source_changed": set(),
    "stale_write": set(),
    "write_back_disabled": set(),
    "write_back_warnings": set(),
}

pytestmark = pytest.mark.skipif(
    not (LOCALES / "zh-CN" / "errors.json").is_file(),
    reason="没有 web/（wheel/sdist 里不含前端源码）",
)


def _errors(locale: str) -> dict:
    return json.loads((LOCALES / locale / "errors.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("code", sorted(USER_VISIBLE_CODES))
def test_backend_emits_the_code(code: str):
    assert code in _declared_codes(_all_error_sources()), f"后端源码里没有发出 {code}"


@pytest.mark.parametrize("locale", ["zh-CN", "en-US"])
def test_every_code_has_text_in_both_languages(locale: str):
    table = _errors(locale).get("backend", {})
    missing = sorted(set(USER_VISIBLE_CODES) - set(table))
    assert not missing, f"{locale} 的 errors.json 缺这些 code 的文案：{missing}"
    empty = sorted(k for k, v in table.items() if not str(v).strip())
    assert not empty, f"{locale} 里这些 code 的文案是空的：{empty}"


@pytest.mark.parametrize("locale", ["zh-CN", "en-US"])
def test_placeholders_match_the_params_the_backend_sends(locale: str):
    table = _errors(locale)["backend"]
    for code, params in USER_VISIBLE_CODES.items():
        used = set(re.findall(r"\{\{\s*(\w+)\s*\}\}", table[code]))
        assert used == params, f"{locale} {code}：文案用了 {used}，后端给的是 {params}"


def test_english_texts_have_no_chinese():
    bad = {k: v for k, v in _errors("en-US")["backend"].items() if re.search(r"[一-鿿]", v)}
    assert not bad, f"英文文案里还留着中文：{bad}"


def test_error_field_is_still_there_as_the_fallback():
    """
    `error` 里的中文原文是回退，不能因为有了 code 就删掉：装着旧前端的用户、
    以及 curl 调试的人只看得到它。
    """
    src = _all_error_sources()
    for code in USER_VISIBLE_CODES:
        needle = f'"code": "{code}"'
        if needle not in src:
            # 经 AgentError 抛出的那批：原文由 app.py 的唯一漏斗补上，
            # 这里直接看那个漏斗（漏斗少了 error 原文，整批一起红）
            assert f'AgentError("{code}"' in src, f"{code} 既没有响应也没有异常"
            continue
        idx = src.index(needle)
        # code 与 error 在同一个 jsonify 里：往前找最近的 jsonify( 起点
        start = src.rindex("jsonify({", 0, idx)
        assert '"error"' in src[start:idx], f"{code} 所在的响应里没有 error 原文"


def test_the_agent_error_funnel_still_carries_the_original_text():
    """`AgentError` 那批的 `error` 原文全靠 app.py 里那一个漏斗。

    漏斗只有一处，所以单独钉死它——上面那条对它们只能确认「异常存在」。"""
    app = APP.read_text(encoding="utf-8")
    start = app.index("def _agent_error(")
    block = app[start : start + 600]
    assert '"error"' in block and '"code": exc.code' in block
    assert '"params": exc.params' in block


# ---------------------------------------------------------------------------
# 结构扫描（2026-08-21，审计 P1-02）：上面那张表靠人记得补；这两条不靠。
# ---------------------------------------------------------------------------
#: 明知没有 code 的位置（"<文件名>:<行号>"）。新增前先想清楚——每一条都意味着
#: 英文用户会在那儿看到中文。desktop.py:98 的「非桌面模式」404 由 ADR 0008
#: 分支整块搬进 security.py 并带上 no_session_mode，合并后这条自然消失；
#: 不在这里改它是为了不与那个分支冲突。
NO_CODE_ALLOWLIST: set[str] = {"desktop.py:98"}

#: 会返回用户可见 JSON 错误的模块（不存在的跳过：security.py 随 ADR 0008
#: 的分支进来，两个分支各自独立绿、合并后自动都在扫描范围内）
SRC_DIR = ROOT / "src" / "tavotto"
SCANNED = [n for n in _SOURCE_FILES if (SRC_DIR / n).is_file()]

#: 刻意没有文案的 code：不是用户可见的失败（前端把这类调用整个吞掉或只做
#: 分诊）。会话 guard 那几个的拒绝对象是攻击页面/畸形请求，正常界面路径由
#: main.tsx 的专用启动页兜住；401 的 session_auth_required 另有 backend 文案。
NON_UI_CODES = {
    "telemetry_rejected",
    "invalid_telemetry_event",
    "bad_nonce",
    "bad_host",
    "bad_origin",
    "bad_secret",
    "no_session_mode",
    "desktop_auth_required",
    # 一键安装的**进度**状态码，不是错误响应：它们的文案在
    # dialogs:settings.agents.install.error.*（由 pnpm i18n:check
    # 守双语齐全），不该也不能进 errors.backend 那张表。
    "npm_missing",
    "npm_failed",
    "installed_but_not_found",
    "spawn_failed",
}


def _error_blocks(text: str):
    """每个 `jsonify({"error": ...})` 调用的文本块（到第一个 `})` 为止——
    本仓库的错误响应都是字面量 dict，这个粗粒度够用且改坏会立刻可见。"""
    for m in re.finditer(r'jsonify\(\{"error"', text):
        window = text[m.start() : m.start() + 600]
        end = window.find("})")
        yield text[: m.start()].count("\n") + 1, window[: end if end != -1 else 600]


def test_every_error_response_carries_a_stable_code():
    missing = []
    for name in SCANNED:
        text = (SRC_DIR / name).read_text(encoding="utf-8")
        for line, block in _error_blocks(text):
            if '"code"' in block:
                continue
            key = f"{name}:{line}"
            if key not in NO_CODE_ALLOWLIST:
                missing.append((key, block.splitlines()[0]))
    assert not missing, "以下错误响应没有稳定 code（英文界面会在这里冒中文）：\n" + "\n".join(
        f"  {k}  {frag}" for k, frag in missing
    )


def test_visible_codes_table_covers_every_literal_code():
    """上面那张 USER_VISIBLE_CODES 表必须覆盖源码里的每个字面量 code：
    新增 code 忘了登记的话，「两种语言都有文案」那条就守不到它。"""
    declared: set[str] = set()
    for name in SCANNED:
        declared |= _declared_codes((SRC_DIR / name).read_text(encoding="utf-8"))
    unlisted = sorted(declared - set(USER_VISIBLE_CODES) - NON_UI_CODES)
    assert not unlisted, f"这些 code 没进 USER_VISIBLE_CODES 表：{unlisted}"


# ---------------------------------------------------------------------------
# Tavotto Run 的稳定码（ADR 0021）——**上面那张表按形状守不到它们**
# ---------------------------------------------------------------------------
# `_declared_codes` 认的是**字面量**：`{"code": "no_project"}`。而 native 那条
# 面返回的是 `engine_runcodes.NATIVE_*` 常量，一个字面量都没有。于是
# `test_visible_codes_table_covers_every_literal_code` 对它们**结构性地看不见**
# ——不是漏登记，是判据的前提（"用户可见的 code 都以字面量出现在源码里"）在
# runcodes 引入常量式码表的那一刻就不再成立了。
#
# 解法不是给 native 开一张平行的手工表（手工表下一次照样忘），而是**枚举**：
# `app.py` 的 `_NATIVE_STATUS` 就是"这条 HTTP 面会返回哪些码"的闭集出处，
# 逐条要求两种语言都有文案。往那张表里加一行却不加文案 → 当场红。
#
# 反方向同样量：`errors.json` 里不许有这个闭集之外的 `native_*` 键。
# `test_i18n_dead_keys.py` 在这里帮不上忙——它按"源码里出现过这个串"判活，
# 而 `runcodes.py` 的码表让**每一个** native 码看起来都有发射点，包括那些
# 只有 CLI 会打印、界面永远见不到的（`native_desktop_required`、
# `native_attach_timeout`、`no_figure_captured`）。

_NATIVE_STATUS_RE = re.compile(r"engine_runcodes\.([A-Z0-9_]+):\s*\d{3}")


def _native_http_codes() -> set[str]:
    """`app.py` 的 `_NATIVE_STATUS` 键 —— 这条面能返回的码的闭集出处。"""
    from tavotto.engine import runcodes

    text = APP.read_text(encoding="utf-8")
    block = text[text.index("_NATIVE_STATUS = {") : text.index("def _native_error")]
    names = _NATIVE_STATUS_RE.findall(block)
    assert len(names) >= 10, f"没解析到 _NATIVE_STATUS（只拿到 {names}）——判据本身坏了"
    return {getattr(runcodes, n) for n in names}


#: 不在 `_NATIVE_STATUS` 里、但确实会经别的端点到达界面的码。
#: 只有一条：装依赖时撞上活跃 native 会话（envlease 抛 EnvironmentBusy，
#: 依赖修复那条端点转成 JSON）。
_EXTRA_NATIVE_UI_CODES = {"environment_in_use_by_native_session"}


def _ui_native_codes() -> set[str]:
    return _native_http_codes() | _EXTRA_NATIVE_UI_CODES


@pytest.mark.parametrize("locale", ["zh-CN", "en-US"])
def test_every_native_code_has_text_in_both_languages(locale: str):
    table = _errors(locale).get("backend", {})
    missing = sorted(_ui_native_codes() - set(table))
    assert not missing, (
        f"{locale} 的 errors.json 缺这些 Tavotto Run 码的文案：{missing}\n"
        "  （界面会原样显示 `backend.<code>`，而这几条恰好都出现在"
        "用户等着看一句解释的时刻）"
    )
    empty = sorted(c for c in _ui_native_codes() if not str(table[c]).strip())
    assert not empty, f"{locale} 里这些码的文案是空的：{empty}"


@pytest.mark.parametrize("locale", ["zh-CN", "en-US"])
def test_native_placeholders_match_the_backend_message(locale: str):
    """文案里的 `{{x}}` 必须与后端那条消息的 `{x}` 逐个对上。

    对不上的表现不是报错，是界面上出现一个**原样的** `{{seconds}}`——一句
    本地化过、看着很正常、却把占位符泄漏给用户的话。
    """
    from tavotto.engine import runcodes

    table = _errors(locale)["backend"]
    lang = "zh" if locale == "zh-CN" else "en"
    for code in sorted(_ui_native_codes()):
        entry = runcodes.MESSAGES[code]
        backend = set(re.findall(r"\{(\w+)\}", entry.get(lang) or entry["zh"]))
        front = set(re.findall(r"\{\{\s*(\w+)\s*\}\}", table[code]))
        assert front == backend, f"{locale} {code}：文案用了 {front}，后端消息给的是 {backend}"


def test_no_dead_native_keys_in_errors_json():
    """反方向：`errors.json` 里不许有闭集之外的 `native_*` 键。

    只有 CLI 会打印的那几条（`native_desktop_required` / `native_attach_timeout`
    / `no_figure_captured`）放进来就是死键——而 `test_i18n_dead_keys.py` 抓不到
    它们：它按"源码里出现过这个串"判活，`runcodes.py` 的码表让每一条都像活的。
    """
    allowed = _ui_native_codes()
    for locale in ("zh-CN", "en-US"):
        table = _errors(locale).get("backend", {})
        stray = sorted(k for k in table if k.startswith("native_") and k not in allowed)
        assert not stray, (
            f"{locale} 的 errors.json 里有界面到不了的 native 码：{stray}\n"
            "  要么它真的会到界面（那就把它加进 app.py 的 _NATIVE_STATUS），"
            "要么删掉这条文案。"
        )


def test_run_error_payload_carries_params_for_the_frontend():
    """`RunError.payload()` 必须同时给**两套约定**。

    * 顶层平铺字段 = CLI 的 JSON 契约（`tests/native/test_run_codes.py` 钉着）；
    * `params` = 前端错误文案的约定（`lib/api.ts` 的 `backendCodeMsg` 从
      `body.params` 取插值）。

    少了 `params` 的表现很隐蔽：界面照常显示那条错误，只是把 `{{seconds}}`
    原样印出来。
    """
    from tavotto.engine import runcodes

    payload = runcodes.RunError(runcodes.NATIVE_ATTACH_TIMEOUT, seconds=300).payload()
    assert payload["params"] == {"seconds": 300}
    assert payload["seconds"] == 300, "顶层平铺字段是 CLI 的契约，不能因为加了 params 就撤掉"
    assert payload["code"] == runcodes.NATIVE_ATTACH_TIMEOUT
    # params 是副本：调用方改它不该改到异常对象自己的 fields
    payload["params"]["seconds"] = 1
    assert runcodes.RunError(runcodes.NATIVE_ATTACH_TIMEOUT, seconds=300).fields == {"seconds": 300}


# --------------------------------------------------------------------------
# worker 错误必须带状态码
# --------------------------------------------------------------------------
def test_every_worker_error_response_carries_a_failure_status():
    """`_worker_error_payload()` 回的是**裸 dict**——Flask 会把它序列化成
    **HTTP 200**。

    调用方（前端 `jsonFetch`）按状态码判成败，于是一次 bridge / worker 失败
    被当成成功，代码接着去读一个不存在的 `session`。用户看到的是**第二个**
    错误，真正的原因被盖掉了——这正是 "silent wrong" 的标准形状：不是没报，
    是**报错了却说成功**。

    2026-08-28 native 那两处（`build` / `continue`·`detach`·`terminate`）正是
    这么漏的：同一个文件里另外 7 处全是 `, 500`，只有这两处忘了（issue #191）。
    所以这条判据是**枚举**式的：每一处调用都要在同一段里带上一个非 2xx 的
    状态码，加第 10 处时忘了会当场红。

    判据只看源码文本，因为它量的是**响应的形状**，不是某一条端点的行为——
    行为那一半由各端点自己的用例覆盖，而"忘了带状态码"恰恰是那些用例
    看不见的（它们断言的是 body 里的 code，200 与 500 都读得到）。
    """
    src = APP.read_text(encoding="utf-8")
    seen: list[str] = []
    bad: list[str] = []
    for i, line in enumerate(src.splitlines(), start=1):
        if "_worker_error_payload(" not in line:
            continue
        if line.lstrip().startswith(("#", "*", "def ")):
            continue  # 定义处与注释不算调用
        seen.append(f"app.py:{i}")
        if re.search(r"jsonify\(_worker_error_payload\([^)]*\)\)\s*,\s*[45]\d\d", line):
            continue
        bad.append(f"app.py:{i}: {line.strip()}")
    assert not bad, (
        "这些 worker 错误响应没带失败状态码，会以 HTTP 200 发出去：\n  "
        + "\n  ".join(bad)
        + "\n  前端按状态码判成败——200 的失败会被当成成功。"
    )
    # **扫到的条数也要钉**（2026-08-28 实测 11 处）。写成 `>=` 不行：那样它会
    # 跟着系统一起长、却不会跟着系统一起收紧，"悄悄少两处"永远不会响（仓库里
    # 那条"下限判据会向上腐烂"的同族）。
    #
    # **它兑现的是什么，说准**——这一格挡的是"调用点集合被动过了，来个人看
    # 一眼"这条**绊线**：新增一处出口、删掉一处、或者把它挪到别的文件里，
    # 都会红。
    #
    # **它挡不住什么**：在 `app.py` 里包一层（`_native_worker_error()` 内部
    # 再调 `_worker_error_payload`）。那处调用没有消失，只是从路由搬进了
    # wrapper——总数仍是 11，这条照样绿，而上面那条逐行判据也看不见 wrapper
    # 的调用方。真要挡它，判据得改成**数出口**：枚举每条路由的
    # `except pool.WorkerError` 分支、断言它的 `return` 带非 2xx。没这么写是
    # 因为 `app.py` 里的 `except pool.WorkerError` 形态很杂（有的往 checks 里
    # 追加、有的置状态字段、有的 `return None`、有的直接 re-raise），逐条分辨
    # "哪些是响应路径"会引入一堆假红。
    #
    # 写在这里是因为**理由写得比兑现的强，比没有理由更坏**：下一个人会以为
    # 这一格有人守着。
    assert len(seen) == 11, (
        f"`_worker_error_payload` 的调用点从 11 变成了 {len(seen)}：{seen}\n"
        "  新增出口 → 把这个数改成新的实测值，并确认它带了状态码；\n"
        "  变少了 → 确认那处是真的删了，而不是搬到了别的文件里。"
    )
