"""字体下拉的选项：**列出来的 == 这个运行时画得出来的**。

与同一份纪律的前例（`test_axes_ticks_scale.py` 抬头第 1 条：界面上出现的每一个
scale 选项，`set_[xy]scale` 都必须真吃得下）是同一条——只不过 scale 那边一开始
就从 `matplotlib.scale.get_scale_names()` 现取，字体这边曾经是一张写死的表。

写死的代价在浏览器 playground 上实测到了（issue #110）：Pyodide 只带 DejaVu
三件套，下拉里的 Times New Roman / Arial / Helvetica 一个都画不出来，选中之后
matplotlib 静默回退到 DejaVuSans，而链路全通——override 记下了、图重绘了、界面
报告成功，只有字形没变。这不是浏览器专属：任何没装 msttcorefonts 的 Linux 同理。

本进程不 import matplotlib：探测跑在 worker 的科学栈解释器里。
"""

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

ENGINE_DIR = Path(__file__).resolve().parent.parent / "src" / "tavotto" / "engine"

_DRIVER = """\
import sys
sys.path.insert(0, sys.argv[1])
import matplotlib
matplotlib.use("Agg")
from matplotlib import font_manager

import manifest

GENERIC = list(manifest._GENERIC_FAMILIES)
NAMED = list(manifest._NAMED_FAMILIES)
real = font_manager.findfont

# ── a) 本机现状：三个通用族在最前，列出来的具体字体名都真解析得到 ──────────
opts = manifest._family_options()
assert opts[:3] == GENERIC, opts
for name in opts[3:]:
    assert name in NAMED, name
    # 不许回退：`fallback_to_default=True`（默认）会让任何名字都「成功」，
    # 正是它把「选了没反应」变成「界面报告成功」
    font_manager.findfont(font_manager.FontProperties(family=name),
                          fallback_to_default=False)

# ── b) 一个具体字体都没装（Pyodide / 没装 msttcorefonts 的 Linux）─────────
def _no_named(prop, *a, **kw):
    fam = prop.get_family()[0]
    if fam in NAMED:
        raise ValueError("Failed to find font " + fam)
    return real(prop, *a, **kw)

font_manager.findfont = _no_named
manifest._FONT_PRESENT.clear()
assert manifest._family_options() == GENERIC, manifest._family_options()

# ── c) findfont 对**所有**名字都抛：三个通用族仍然全在 ────────────────────
# 它们不许被探测器判定。`fallback_to_default=False` 下 `sans-serif` 本来就抛
# ValueError（连字符被 fontconfig 语法当成分隔符，ParseException），尽管它当然
# 可用——把通用族一起丢进探测器，下拉会当场少掉最常用的那一项。
font_manager.findfont = lambda *a, **kw: (_ for _ in ()).throw(ValueError("nope"))
manifest._FONT_PRESENT.clear()
assert manifest._family_options() == GENERIC, manifest._family_options()

font_manager.findfont = real
manifest._FONT_PRESENT.clear()
print("OK")
"""


_MACHINE_DRIVER = """\
import sys
sys.path.insert(0, sys.argv[1])
import matplotlib
matplotlib.use("Agg")
from matplotlib import font_manager
from matplotlib.text import Text

import manifest
import overrides

# ── a) 本机字体族：排好序、没有系统内部字体、没有读不出名字的、每一个都
#       不回退地解析得到（列出来的 == 画得出来的，与首选项同一条纪律）──────
fams = manifest.installed_font_families()
assert fams == tuple(sorted(fams, key=lambda n: (n.casefold(), n))), fams[:10]
assert len(fams) == len(set(fams))
assert not any(n.startswith(".") or "?" in n for n in fams), [n for n in fams if n.startswith(".") or "?" in n]
assert "DejaVu Sans" in fams, "matplotlib 自带的 DejaVu 在任何平台都该在"
assert not any(n in manifest._GENERIC_FAMILIES for n in fams), "通用族不是本机字体"
for name in fams:
    font_manager.findfont(font_manager.FontProperties(family=[name]),
                          fallback_to_default=False)
# 整个进程只算一次
assert manifest.installed_font_families() is fams

# ── b) `font_installed` 走列表形式：带连字符的名字不许被当成 fontconfig 模式 ──
real = font_manager.findfont
seen = []
def _record(prop, *a, **kw):
    seen.append(list(prop.get_family()))
    return "/nonexistent/but/found.ttf"
font_manager.findfont = _record
overrides._FONT_PRESENT.clear()
assert overrides.font_installed("DFYanKaiW7-B5") is True, seen
assert seen == [["DFYanKaiW7-B5"]], seen
font_manager.findfont = real
overrides._FONT_PRESENT.clear()

# ── c) `options_unavailable` 的判据是「画不画得出」，不是「在不在首选项里」──
def _fam_field(t):
    return next(f for f in manifest._text_fields(t) if f["prop"] == "fontfamily")
installed = _fam_field(Text(0, 0, "hi", fontfamily="DejaVu Sans"))
assert installed["value"] == "DejaVu Sans"
assert installed["options"][0] == "DejaVu Sans", installed["options"][:4]
assert "options_unavailable" not in installed, installed
missing = _fam_field(Text(0, 0, "hi", fontfamily="No Such Font Zzz"))
assert missing["options_unavailable"] == ["No Such Font Zzz"], missing
overrides._FONT_PRESENT.clear()
print("OK")
"""


def test_machine_font_families_are_listed_once_and_all_drawable():
    """`installed_font_families()`（manifest 顶层 `font_families` 的来源）：本机
    matplotlib 认得的全部字体族——排好序、不含系统内部字体与读不出名字的、
    每一个都不回退地解析得到。顺带钉两条同一纪律的修正：`font_installed`
    用列表形式问（连字符不再被当成 fontconfig 模式），`options_unavailable`
    只在**真画不出**时才发（脚本设了一个装了的字体不再被标成「未安装」）。
    """
    out = subprocess.run(
        [WORKER_PY, "-c", _MACHINE_DRIVER, str(ENGINE_DIR)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().endswith("OK")


def test_the_font_dropdown_only_offers_what_this_runtime_can_draw():
    """三件事一次钉住：通用族无条件保留、具体字体名装了才列、探测不碰通用族。

    判据刻意**不是**「六个都在」——CI 的 ubuntu runner 上 Arial / Times New
    Roman / Helvetica 一个都没装，那条断言只会在 macOS 上绿。要看的是
    「列出来的都解析得到」，它在每个平台上都成立，而且更强。
    """
    out = subprocess.run(
        [WORKER_PY, "-c", _DRIVER, str(ENGINE_DIR)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().endswith("OK")
