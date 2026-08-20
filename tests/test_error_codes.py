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


def _all_error_sources() -> str:
    """会发用户可见错误的全部模块源码（app.py + #24 起的 security.py 等）。"""
    root = ROOT / "src" / "tavotto"
    return "\n".join((root / n).read_text(encoding="utf-8")
                     for n in ("app.py", "security.py", "desktop.py")
                     if (root / n).is_file())

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
    "script_not_in_project": set(),
    "script_name_missing": set(),
    "invalid_entry": {"entry"},
    "registry_update_failed": {"reason"},
    "settings_dir_unusable": {"key", "reason"},
    "invalid_preview_dpi": {"value"},
    "invalid_patches": set(),
    "invalid_width": {"value"},
    "not_parameterizable": set(),
    "sync_different_scripts": set(),
    "unknown_agent": {"agent"},
    "python_missing": set(),
    "interpreter_not_found": {"path"},
    "interpreter_no_matplotlib": {"path"},
    "invalid_consent": set(),
    "name_missing": set(),
    "endpoint_save_failed": {"reason"},
    "endpoint_invalid": {"reason"},
    "ai_start_failed": {"reason"},
    "ai_revert_failed": {"reason"},
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
    src = _all_error_sources()
    assert f'"code": "{code}"' in src, f"后端源码里没有发出 {code}"


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
        idx = src.index(f'"code": "{code}"')
        # code 与 error 在同一个 jsonify 里：往前找最近的 jsonify( 起点
        start = src.rindex("jsonify({", 0, idx)
        assert '"error"' in src[start:idx], f"{code} 所在的响应里没有 error 原文"


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
SCANNED = [n for n in ("app.py", "security.py", "desktop.py")
           if (SRC_DIR / n).is_file()]

#: 刻意没有文案的 code：不是用户可见的失败（前端把这类调用整个吞掉或只做
#: 分诊）。会话 guard 那几个的拒绝对象是攻击页面/畸形请求，正常界面路径由
#: main.tsx 的专用启动页兜住；401 的 session_auth_required 另有 backend 文案。
NON_UI_CODES = {"telemetry_rejected", "invalid_telemetry_event",
                "bad_nonce", "bad_host", "bad_origin", "bad_secret",
                "no_session_mode", "desktop_auth_required"}


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
        text = (SRC_DIR / name).read_text(encoding="utf-8")
        declared.update(re.findall(r'"code":\s*"([a-z0-9_]+)"', text))
    unlisted = sorted(declared - set(USER_VISIBLE_CODES) - NON_UI_CODES)
    assert not unlisted, f"这些 code 没进 USER_VISIBLE_CODES 表：{unlisted}"
