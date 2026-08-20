"""后端错误响应的结构纪律（1.0 审计 P1-02）。

约定在 app.py 的 API 段首：**每一个** `{"error": ...}` 响应都带稳定的
`code`（多数还带 `params`）。前端 `backendErrorText` 按 code 查当前语言的
文案；没有 code 的响应在英文界面上就是一句突兀的中文。

这里做的是**结构扫描**而不是逐端点打请求：错误分支很多只有特定环境才走得
到（Windows 文件锁、磁盘满、损坏的 zip），静态扫描是唯一能全覆盖的一层。
新增一个无 code 的错误响应会当场红——要么补 code，要么把它加进 allowlist
并写明为什么（目前 allowlist 为空，希望保持下去）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "tavotto"
WEB_ERRORS = (Path(__file__).resolve().parents[1] / "web" / "src" / "i18n"
              / "locales")

#: 明知没有 code 的位置：`"<文件名>:<行号>"`。新增前先想清楚——
#: 每一条都意味着英文用户会在那儿看到中文。
#: desktop.py:98 的「非桌面模式」404 由 ADR 0008 分支（release-gate/
#: p0-browser-auth）整块搬进 security.py 并带上 no_session_mode，
#: 合并后这条自然消失；不在这里改它是为了不与那个分支冲突。
ALLOWLIST: set[str] = {"desktop.py:98"}

#: 会返回用户可见 JSON 错误的模块（不存在的跳过：security.py 随 ADR 0008
#: 的分支进来，两个分支各自独立绿、合并后自动都在扫描范围内）
SCANNED = [n for n in ("app.py", "security.py", "desktop.py")
           if (SRC / n).is_file()]


def _error_blocks(text: str):
    """每个 `jsonify({"error": ...})` 调用的文本块（到第一个 `})` 为止——
    本仓库的错误响应都是字面量 dict，这个粗粒度够用且改坏会立刻可见。"""
    for m in re.finditer(r'jsonify\(\{"error"', text):
        window = text[m.start():m.start() + 600]
        end = window.find("})")
        yield text[:m.start()].count("\n") + 1, window[:end if end != -1 else 600]


def collect_missing():
    missing = []
    for name in SCANNED:
        text = (SRC / name).read_text(encoding="utf-8")
        for line, block in _error_blocks(text):
            if '"code"' in block:
                continue
            key = f"{name}:{line}"
            if key not in ALLOWLIST:
                missing.append((key, block.splitlines()[0]))
    return missing


def test_every_error_response_carries_a_stable_code():
    missing = collect_missing()
    assert not missing, (
        "以下错误响应没有稳定 code（英文界面会在这里冒中文）：\n"
        + "\n".join(f"  {k}  {frag}" for k, frag in missing))


def _codes_in_sources() -> set[str]:
    codes = set()
    for name in SCANNED:
        text = (SRC / name).read_text(encoding="utf-8")
        codes.update(re.findall(r'"code":\s*"([a-z0-9_]+)"', text))
    return codes


def test_frontend_has_translations_for_backend_codes():
    """两门语言的 `errors:backend.*` 必须覆盖后端字面量声明的每个 code。

    覆盖不到的 code 前端会回退中文原文——那正是审计说的「错误尾部泄漏中文」。
    动态 code（worker 的 `exc.code`、runtime 的 `st.get("code")`）不在字面量
    扫描范围内，它们各自有专门文案与用例。
    """
    # 这些 code 刻意没有文案：它们不是用户可见的失败（前端把这类调用整个吞掉
    # 或只做分诊），出现在界面上本身就是 bug。会话 guard 那几个的拒绝对象是
    # 攻击页面/畸形请求，正常界面路径由 main.tsx 的专用启动页兜住
    # （boot.desktopSessionFailed / boot.sessionUnauthenticated），401 的
    # session_auth_required 另有 backend 文案。
    non_ui = {"telemetry_rejected", "invalid_telemetry_event",
              "bad_nonce", "bad_host", "bad_origin", "bad_secret",
              "no_session_mode", "desktop_auth_required"}
    declared = _codes_in_sources() - non_ui
    for locale in ("zh-CN", "en-US"):
        data = json.loads((WEB_ERRORS / locale / "errors.json")
                          .read_text(encoding="utf-8"))
        have = set(data.get("backend", {}))
        missing = sorted(declared - have)
        assert not missing, f"{locale}/errors.json backend 缺文案: {missing}"
