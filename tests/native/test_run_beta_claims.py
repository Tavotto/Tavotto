"""README 对 `tavotto run` 的承诺，逐句指得出兑现它的那行代码。

## 为什么需要这一条

裁决写在 Markdown 里拦不住任何人。`TAVOTTO_RUN_BETA = BLOCKED` 曾经**只**
存在于 `docs/compatibility/COMPATIBILITY_BRIDGE_HANDOFF.md`，全仓代码零处，
而 `run` 已经进了 CLI 的子命令闭集、README 上白纸黑字写着「你确认之前，
一行代码都不会跑」。那段时间里两件事同时为真：

* 用户照 README 敲 `tavotto run -- python figure.py` 会白等五分钟
  （桌面首启的落地 URL 丢掉了交接 ID，确认屏永远不出现，CLI 挂到 attach 超时）；
* 每一条门禁都是绿的——因为没有任何一条门禁知道那句话该由谁兑现。

这句承诺跨三种语言：Rust 的壳把交接 ID 拼进落地 URL、TypeScript 认下它并弹
确认屏、Python 的 CLI 在 attach 成功**之后**才 spawn。三条腿缺任何一条，
那句话就是假的，而**没有任何单语言的用例能看见这一点**——各自的用例都在
各自那侧绿着。这里补的正是这条跨语言的连线。

## 判据的形状

不是「grep 得到关键词就算数」——每条 marker 都指向**恰好那一行**，而且是
这一条链路上曾经真的缺过的那一行。删掉实现 → 这里红，并且报的是**它让
README 的哪一句变成了谎**，不是「找不到某个字符串」。

READMEs 里那句话被删掉时本文件转绿，这是对的：承诺撤回之后就没有谎可言。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent

pytestmark = pytest.mark.skipif(
    not (ROOT / "web" / "src").is_dir(),
    reason="没有 web/（wheel/sdist 里不含前端源码）",
)


@dataclass(frozen=True)
class Leg:
    """一条腿：某个文件里必须还在的那行代码。"""

    path: str
    pattern: str
    why: str


@dataclass(frozen=True)
class Claim:
    zh: str
    en: str
    legs: tuple[Leg, ...]


CLAIMS: tuple[Claim, ...] = (
    Claim(
        zh="**你确认之前，一行代码都不会跑。**",
        en="**Nothing runs until you confirm.**",
        legs=(
            Leg(
                "src-tauri/src/main.rs",
                r'"native=\{\}"',
                "桌面**首启**的落地 URL 必须带上交接 ID。丢掉它 = 确认屏永远不出现，"
                "而 CLI 一直挂到 attach 超时——两边都不报错（这一条真的发生过）",
            ),
            Leg(
                "web/src/lib/openRequest.ts",
                r"params\.get\('native'\)",
                "前端要认下 `?native=`。壳带过来了没人接，效果与没带一样",
            ),
            Leg(
                "web/src/lib/openRequest.ts",
                r"useNativeSessionStore\.getState\(\)\.receive\(",
                "认下之后要排进确认队列，否则 ID 只是被读了一遍",
            ),
            Leg(
                "web/src/components/NativeConfirmDialog.tsx",
                r"blockDismiss",
                "确认屏是**闸**不是提示：点外面 / Esc 不算回答。"
                "随手关掉的表现是那个终端一直挂到 attach 超时",
            ),
        ),
    ),
    Claim(
        zh="写明解释器路径、工作目录和目标",
        en="showing the interpreter path, the\nworking directory and the target",
        legs=(
            Leg(
                "web/src/components/NativeConfirmDialog.tsx",
                r"fields\.interpreter",
                "解释器路径——用户判断「这条命令该不该跑」的第一依据",
            ),
            Leg("web/src/components/NativeConfirmDialog.tsx", r"fields\.cwd", "工作目录"),
            Leg("web/src/components/NativeConfirmDialog.tsx", r"fields\.target", "目标"),
        ),
    ),
)


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


#: 注释行（`//` / `*` / `/*` / `#` 开头）。Rust、TS、Python 三种源码共用。
_COMMENT_LINE = re.compile(r"^\s*(//|\*|/\*|\*/|#)")


def code_only(text: str) -> str:
    """去掉注释行之后的源码。

    **这一步是变异测试逼出来的。** 判据原本直接在整份文件里找 marker，而
    `NativeConfirmDialog.tsx` 的模块注释里正好写着一句「必须做出选择
    （`blockDismiss`）」——于是把那个 prop 整个删掉之后，门禁**照样绿**。

    一条被注释满足的门禁比没有门禁更坏：它让人以为那行代码还在。
    """
    return "\n".join(ln for ln in text.splitlines() if not _COMMENT_LINE.match(ln))


def _readmes() -> dict[str, str]:
    return {name: _read(name) for name in ("README.md", "README.zh-CN.md")}


def _norm(text: str) -> str:
    """比对前把换行折成空格——README 的折行位置不该决定一条门禁的死活。"""
    return re.sub(r"\s+", " ", text)


@pytest.mark.parametrize("claim", CLAIMS, ids=lambda c: c.zh[:16])
def test_each_readme_promise_is_backed_by_code(claim: Claim):
    readmes = _readmes()
    promised = {
        "README.md": _norm(claim.en) in _norm(readmes["README.md"]),
        "README.zh-CN.md": _norm(claim.zh) in _norm(readmes["README.zh-CN.md"]),
    }
    if not any(promised.values()):
        pytest.skip("两份 README 都不再这么承诺了——没有谎可言")
    # 一份撤了一份没撤，本身就是个缺陷：两种语言的用户看到的产品不一样
    assert all(promised.values()), (
        f"这句承诺只在其中一份 README 里：{promised}\n  zh: {claim.zh}\n  en: {claim.en}"
    )
    for leg in claim.legs:
        # **只在代码里找**：注释里提一句不算兑现（见 `code_only` 的说明）
        assert re.search(leg.pattern, code_only(_read(leg.path))), (
            f"{leg.path} 里找不到 /{leg.pattern}/ —— README 的这句话因此不再成立：\n"
            f"  「{claim.zh}」\n"
            f"  这一行的作用：{leg.why}\n"
            f"  要么把实现补回来，要么把 README 的这句承诺撤掉。"
        )


def test_the_cli_only_spawns_after_the_desktop_has_attached():
    """「确认之前一行都不跑」在 CLI 侧的那条腿：**顺序**。

    `wait_for_desktop()` 必须排在 `_spawn_user_python()` 前面。反过来写的话
    行为上的表现是"确认屏还开着，脚本已经在跑了"——而那时用户点「取消」，
    取消的是一件已经发生的事。

    行为本身由 `test_native_api.py` / `test_run_cli_integration.py` 量；这里
    量的是**源码顺序**，因为它是那句承诺里唯一一条单靠读代码就能看出对错的。
    """
    src = code_only(_read("src/tavotto/engine/runcli.py"))
    wait = src.index("relay.wait_for_desktop(")
    spawn = src.index("proc = _spawn_user_python(")
    assert wait < spawn, (
        "`tavotto run` 在桌面 attach 之前就 spawn 了用户的 Python——"
        "README 的「你确认之前，一行代码都不会跑」当场变成谎话"
    )


def test_a_marker_that_only_appears_in_a_comment_does_not_count():
    """`code_only` 自己的看护——**这条是变异测试逼出来的**。

    第一版判据直接在整份文件里找 marker，而 `NativeConfirmDialog.tsx` 的模块
    注释里正好写着一句「必须做出选择（`blockDismiss`）」。于是把那个 prop
    整个删掉之后门禁**照样绿**：一条被注释满足的门禁比没有门禁更坏，它让人
    以为那行代码还在。

    这条用例钉的是"注释不算数"这件事本身，跑不到真文件上——真文件里今天
    两者都在，量不出区别（这正是当初没发现的原因）。
    """
    ts = "\n".join(
        [
            "/**",
            " * 必须做出选择（`blockDismiss`）：点外面和 Esc 都不算回答。",
            " */",
            "export function Dialog() {",
            "  return <RD.Root open />",
            "}",
        ]
    )
    assert "blockDismiss" in ts
    assert "blockDismiss" not in code_only(ts)

    with_prop = ts.replace("  return <RD.Root open />", "  return <RD.Root open blockDismiss />")
    assert "blockDismiss" in code_only(with_prop)


def test_code_only_keeps_ordinary_source_lines():
    """反方向：别把正常代码也删了（那会让整族判据变成恒红）。"""
    src = "\n".join(
        [
            "# 注释",
            "x = 1  # 行尾注释不算注释行",
            "    // rust/ts 的注释",
            "    let y = 2;",
            "  * jsdoc 续行",
        ]
    )
    kept = code_only(src)
    assert "x = 1" in kept and "let y = 2;" in kept
    assert "# 注释" not in kept and "rust/ts 的注释" not in kept and "jsdoc" not in kept
