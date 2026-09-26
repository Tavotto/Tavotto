"""manifest 的 `value_original`：字段上有 override 时，override 之前**脚本的值**（ADR 0081 §十三）。

前端靠它判断「脚本重跑后，这一项脚本自己改过没有」：样式写的 override 在脚本改过的那一项上
让位（用户 2026-09-25 裁决：重跑后脚本赢）。所以它必须与 `value` **同一个口径**——前端记下的
基线是绑定那一刻 manifest 的 `value`，两边读法不同就会把「脚本没改」误判成「改了」，
样式的 override 每一轮都被拆掉。

钉的是：

* 样式管得到的**每一个** (角色, 属性)（表从 `web/src/lib/stylePresets.ts` 读，不手抄第二份）上，
  `value_original` 等于没有 override 时 manifest 的 `value`——一次全写（绑定）与逐条写各一遍；
* 元素之间的联动不串味：色条外框就是色条轴的边框，同一轮先改了边框的话，外框采到的不能是改后的值
  （实测踩到过，采样挪到了「这一轮任何 setter 动手之前」）；
* 没有 override 时不发；撤掉 override 之后跟着消失；
* rebase（native 屏障 / 脚本重跑）清空 originals 后按**新的**脚本值重采。

本进程不 import matplotlib：探针跑在 worker 的解释器里。
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "src" / "tavotto" / "engine"
PRESETS = ROOT / "web" / "src" / "lib" / "stylePresets.ts"


def _style_table() -> dict[str, list[str]]:
    """`STYLE_ROLE_PROPS` + `PALETTE_PROP`：样式能写的 (角色, 属性) 全集，照前端源码读。"""
    src = PRESETS.read_text(encoding="utf-8")
    block = re.search(r"export const STYLE_ROLE_PROPS[^=]*=\s*\{(.*?)\n\}", src, re.S)
    assert block, "STYLE_ROLE_PROPS 找不到了：判据量不到对象"
    table: dict[str, list[str]] = {}
    for role, props in re.findall(r"^\s*(\w+):\s*\[([^\]]*)\]", block.group(1), re.M):
        table[role] = re.findall(r"'([^']+)'", props)
    palette = re.search(r"const PALETTE_PROP[^=]*=\s*\{(.*?)\n\}", src, re.S)
    assert palette, "PALETTE_PROP 找不到了"
    for role, prop in re.findall(r"^\s*(\w+):\s*'([^']+)'", palette.group(1), re.M):
        table.setdefault(role, []).append(prop)
    assert len(table) >= 10 and "fontsize" in table["title"], table
    return table


FIGURE = """
import numpy as np
fig, (ax, ax2) = plt.subplots(1, 2, figsize=(6, 3))
ax.plot([0, 1, 2], [0, 1, 0], lw=1.5, marker="o", ms=4, label="a")
ax.scatter([0.5, 1.5], [0.5, 0.2], label="s")
ax.errorbar([0, 1], [1, 2], yerr=[0.1, 0.2], capsize=2, label="e")
ax.bar([0, 1], [1, 2], edgecolor="k", linewidth=0.5, label="b")
ax.set_title("T", fontsize=10); ax.set_xlabel("x", fontsize=9); ax.set_ylabel("y")
ax.text(0.5, 0.5, "note", fontsize=7)
ax.legend(fontsize=8)
im = ax2.imshow(np.arange(9).reshape(3, 3)); fig.colorbar(im, ax=ax2)
"""

PROBE = """
import json, sys
sys.path.insert(0, ENGINE)
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
import manifest, overrides

def build(st):
    st.fig.canvas.draw()
    return manifest.build_manifest(st, "s")

def field(m, gid, prop):
    el = next((e for e in m["elements"] if e["gid"] == gid), None)
    return None if el is None else next((f for f in el["editable"] if f["prop"] == prop), None)

def alt(f):
    v, t = f["value"], f["type"]
    if t == "number":
        return round(v + 1.25, 2) if f.get("max") is None or v + 1.25 <= f["max"] else round(v - 0.5, 2)
    if t == "color":
        return "#123456" if v != "#123456" else "#654321"
    if t == "bool":
        return not v
    if t == "enum":
        bad = f.get("options_unavailable") or []
        return next(o for o in f["options"] if o != v and o not in bad)
    raise ValueError(t)

def same(a, b):
    num = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool)
    return abs(a - b) < 0.005 if num(a) and num(b) else a == b
"""


def _run(body: str, *args: str) -> dict:
    code = f"ENGINE = {str(ENGINE)!r}\n" + PROBE + body
    r = subprocess.run(
        [WORKER_PY, "-c", code, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    assert r.returncode == 0, r.stderr[-1500:]
    return json.loads(r.stdout.strip().splitlines()[-1])


ALL_STYLE_PROPS = (
    FIGURE
    + """
TABLE = json.loads(sys.argv[1])
st = overrides.FigState(fig); manifest.instrument(st)
m0 = build(st)
patches, base = [], {}
for e in m0["elements"]:
    for prop in TABLE.get(e["role"], []):
        f = field(m0, e["gid"], prop)
        if f is None or f["value"] is None:
            continue
        assert "value_original" not in f, (e["gid"], prop)
        patches.append({"gid": e["gid"], "prop": prop, "value": alt(f)})
        base[(e["gid"], prop)] = f["value"]
out = {"n": len(patches), "roles": sorted({e["role"] for e in m0["elements"] if e["role"] in TABLE}),
       "bad": [], "warn": []}
def check(m, keys):
    for gid, prop in keys:
        f = field(m, gid, prop)
        got = "<absent>" if f is None else f.get("value_original", "<absent>")
        if not same(got, base[(gid, prop)]):
            out["bad"].append([gid, prop, base[(gid, prop)], got])
if sys.argv[2] == "all":
    out["warn"] = overrides.apply(st, patches)
    check(build(st), list(base))
else:
    for p in patches:
        out["warn"] += overrides.apply(st, [p])
        check(build(st), [(p["gid"], p["prop"])])
        overrides.apply(st, [])
overrides.apply(st, [])
m_clear = build(st)
out["left"] = [[e["gid"], f["prop"]] for e in m_clear["elements"] for f in e["editable"] if "value_original" in f]
print(json.dumps(out))
"""
)


@pytest.mark.parametrize("mode", ["all", "one"])
def test_value_original_matches_the_unoverridden_value_for_every_style_prop(mode):
    """样式能写的每一项：有 override 时 `value_original` == 没 override 时的 `value`。

    `all` 是绑定那一刻（一次写全套，元素之间的联动都在同一轮里发生），`one` 是逐条写。
    撤掉之后 `value_original` 全部消失（没有 override = 字段缺席）。
    """
    got = _run(ALL_STYLE_PROPS, json.dumps(_style_table()), mode)
    # 判据量到了东西：图里摆出的角色都认得出，覆盖面不是空的
    for role in (
        "title",
        "axis_label",
        "ticks",
        "legend",
        "line",
        "errorbar",
        "bar_series",
        "axes",
        "colorbar",
    ):
        assert role in got["roles"], got["roles"]
    assert got["n"] >= 60, got["n"]
    assert not got["warn"], got["warn"]
    assert got["bad"] == [], got["bad"]
    assert got["left"] == [], got["left"]


REBASE = """
fig, ax = plt.subplots()
ax.plot([0, 1], [0, 1])
ax.set_title("T", fontsize=10)
st = overrides.FigState(fig); manifest.instrument(st)
gid = next(e["gid"] for e in build(st)["elements"] if e["role"] == "title")
patch = [{"gid": gid, "prop": "fontsize", "value": 8}]
out = {}
overrides.apply(st, patch)
out["before"] = field(build(st), gid, "fontsize")
# rebase 的顺序（bridge_runner.rebase）：离开屏障时 apply([]) 清空 originals → 脚本改了自己的值
# → 重新 instrument → 重放同一组 patch（第一次碰到这个 key 时按**此刻**的脚本值重采）
overrides.apply(st, [])
ax.title.set_fontsize(12)
manifest.instrument(st)
overrides.apply(st, patch)
out["after"] = field(build(st), gid, "fontsize")
print(json.dumps(out))
"""


def test_rebase_resamples_value_original_from_the_new_script_value():
    got = _run(REBASE)
    assert got["before"]["value"] == 8 and got["before"]["value_original"] == 10, got["before"]
    assert got["after"]["value"] == 8 and got["after"]["value_original"] == 12, got["after"]


NO_READER = """
fig, ax = plt.subplots()
ax.set_title("T", fontsize=10)
st = overrides.FigState(fig); manifest.instrument(st)
gid = next(e["gid"] for e in build(st)["elements"] if e["role"] == "title")
overrides.set_original_reader(None)
overrides.apply(st, [{"gid": gid, "prop": "fontsize", "value": 8}])
f = field(build(st), gid, "fontsize")
print(json.dumps({"value": f["value"], "has": "value_original" in f}))
"""


def test_without_a_reader_the_field_stays_absent_rather_than_guessed():
    """采不到（没登记读法 / 读的时候出错）就不发：缺席 = 不知道，前端按保守路径走（不让位）。"""
    got = _run(NO_READER)
    assert got == {"value": 8.0, "has": False}, got


RESTORE_DEBT = """
fig, ax = plt.subplots()
ax.plot([0, 1], [0, 1])
ax.set_title("T", fontsize=10)
st = overrides.FigState(fig); manifest.instrument(st)
gid = next(e["gid"] for e in build(st)["elements"] if e["role"] == "title")
key = ("text", "fontsize")
out = {}
overrides.apply(st, [{"gid": gid, "prop": "fontsize", "value": 8}])

def boom(*_a, **_k):
    raise RuntimeError("还原坏了")

had, prev = key in overrides._RESTORE, overrides._RESTORE.get(key)
overrides._RESTORE[key] = boom
try:
    out["warn"] = overrides.apply(st, [])
    out["owed"] = (gid, "fontsize") in getattr(st, "unrestored", set())
    out["during"] = field(build(st), gid, "fontsize")
finally:
    if had:
        overrides._RESTORE[key] = prev
    else:
        overrides._RESTORE.pop(key, None)
out["retry_warn"] = overrides.apply(st, [])
out["after"] = field(build(st), gid, "fontsize")
out["left"] = [list(k) for k in st.original_values]
print(json.dumps(out, default=str))
"""


def test_value_original_survives_a_restore_debt_and_goes_with_it():
    """#549 × §十三 的交叉点：撤掉一条 override 时还原抛了（#549：不销账，下一次 apply 重试），
    欠账期间这个键仍在 applied 里、图上仍是 override 的值——`value_original` 必须照样报**脚本原样**，
    否则前端在这段时间里读到「不知道」，样式写的 override 该让位时不让位。还原修好之后它与
    originals 一起销掉，不留一条没人回收的记录。
    """
    got = _run(RESTORE_DEBT)
    assert got["owed"] and any("还原失败" in w for w in got["warn"]), got  # 前提：真的欠着账
    assert got["during"]["value_original"] == 10, got["during"]
    assert not any("还原失败" in w for w in got["retry_warn"]), got["retry_warn"]
    assert "value_original" not in got["after"] and got["after"]["value"] == 10, got["after"]
    assert got["left"] == [], got["left"]
