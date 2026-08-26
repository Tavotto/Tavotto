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
    return "\n".join((root / n).read_text(encoding="utf-8")
                     for n in _SOURCE_FILES
                     if (root / n).is_file())


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
    block = app[start:start + 600]
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
NON_UI_CODES = {"telemetry_rejected", "invalid_telemetry_event",
                "bad_nonce", "bad_host", "bad_origin", "bad_secret",
                "no_session_mode", "desktop_auth_required",
                # 一键安装的**进度**状态码，不是错误响应：它们的文案在
                # dialogs:settings.agents.install.error.*（由 pnpm i18n:check
                # 守双语齐全），不该也不能进 errors.backend 那张表。
                "npm_missing", "npm_failed", "installed_but_not_found",
                "spawn_failed"}


def _error_blocks(text: str):
    """每个 `jsonify({"error": ...})` 调用的文本块（到第一个 `})` 为止——
    本仓库的错误响应都是字面量 dict，这个粗粒度够用且改坏会立刻可见。"""
    for m in re.finditer(r'jsonify\(\{"error"', text):
        window = text[m.start():m.start() + 600]
        end = window.find("})")
        yield text[:m.start()].count("\n") + 1, window[:end if end != -1 else 600]


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
    assert not missing, (
        "以下错误响应没有稳定 code（英文界面会在这里冒中文）：\n"
        + "\n".join(f"  {k}  {frag}" for k, frag in missing))


def test_visible_codes_table_covers_every_literal_code():
    """上面那张 USER_VISIBLE_CODES 表必须覆盖源码里的每个字面量 code：
    新增 code 忘了登记的话，「两种语言都有文案」那条就守不到它。"""
    declared: set[str] = set()
    for name in SCANNED:
        declared |= _declared_codes((SRC_DIR / name).read_text(encoding="utf-8"))
    unlisted = sorted(declared - set(USER_VISIBLE_CODES) - NON_UI_CODES)
    assert not unlisted, f"这些 code 没进 USER_VISIBLE_CODES 表：{unlisted}"
