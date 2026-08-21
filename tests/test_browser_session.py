"""浏览器 playground 引擎适配层（engine/browser.py）的行为契约。

`browser.py` 在生产里跑在 Pyodide（WebAssembly）里；这里 spawn
`pool.find_worker_python()` 的科学栈 CPython 直接跑**同一份代码**——
语义（捕获 / manifest / override / 还原）与解释器无关，Pyodide 特有的
部分（加载、超时、Worker 生死）由 web 侧的 vitest 与 Playwright 盖。

三块内容：
  * fixture 矩阵（ADR 0007 §测试）：常见 matplotlib 脚本形态逐一过
    「跑通 → 捕获 → manifest 有语义元素 → 真实 override 应用 → 空列表还原」；
  * 错误分诊：每类失败都要有稳定 code，绝不让用户读裸 traceback 猜原因；
  * 源文件完整性：写进虚拟 FS 的那个文件**读回来**的 sha256 与输入一致，
    改完图仍然一致，被人动过一个字节就必须报出来（篡改钩子只在测试驱动里）；
  * 跨进程哈希一致：browser 响应里的 patch_hash 必须等于父进程
    `tavotto.engine.patchspec` 对同一列表算出的值（同一份实现跑在两个
    解释器里，这是 §49「不许移植第二份规范化」的看护）。
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tavotto.engine import patchspec, pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）")

ROOT = Path(__file__).resolve().parent.parent
ENGINE_DIR = ROOT / "src" / "tavotto" / "engine"
RUNTIME_LOCK = json.loads(
    (ROOT / "packaging" / "playground-runtime.json").read_text(encoding="utf-8"))
SUPPORTED_ROOTS = RUNTIME_LOCK["import_roots"]

#: 子进程驱动：stdin 收 JSON 请求列表，stdout 末行吐 JSON 响应列表。
#: 只经 `browser.handle`——测试走的就是 JS 侧唯一会走的那扇门。
_DRIVER = """
import json, sys
sys.path.insert(0, sys.argv[1])
import browser
reqs = json.load(sys.stdin)
out = []
for r in reqs:
    # `__tamper` 只存在于这个测试驱动里——产品代码**不给**任何改工作区
    # 源文件的入口，否则「完整性校验」就成了自证的摆设。
    if r.get("cmd") == "__tamper":
        with open(r["path"], "a", encoding="utf-8") as f:
            f.write(r["append"])
        out.append({"ok": True})
        continue
    out.append(json.loads(browser.handle(json.dumps(r))))
sys.stdout.write("\\n" + json.dumps(out))
"""


def drive(reqs: list[dict], tmp_path: Path) -> list[dict]:
    """一个全新解释器跑一串命令——正好对应「一个 Worker 一个会话」。"""
    for r in reqs:
        if r.get("cmd") == "load":
            r.setdefault("workspace", str(tmp_path / "ws"))
    proc = subprocess.run(
        [WORKER_PY, "-c", _DRIVER, str(ENGINE_DIR)],
        input=json.dumps(reqs), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180)
    assert proc.returncode == 0, f"驱动进程失败:\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _module_available(name: str) -> bool:
    return subprocess.run([WORKER_PY, "-c", f"import {name}"],
                          capture_output=True).returncode == 0


def roles(manifest: dict) -> set[str]:
    return {el["role"] for el in manifest["elements"]}


def field_value(manifest: dict, gid: str, prop: str):
    for el in manifest["elements"]:
        if el["gid"] == gid:
            for f in el.get("editable", []):
                if f["prop"] == prop:
                    return f["value"]
    raise AssertionError(f"manifest 里没有 {gid}.{prop}")


# ---------------------------------------------------------------- fixture 矩阵

#: (名字, 脚本, 必须出现的角色, 一条真实 override 的 (gid, prop, value))
FIXTURES = [
    ("line_savefig", """
import matplotlib.pyplot as plt
fig, ax = plt.subplots(figsize=(3, 2))
ax.plot([0, 1, 2], [1, 0, 2])
ax.set_title("Line")
fig.savefig("out.pdf")
""", {"title", "line"}, ("axes_0.title", "fontsize", 15)),
    ("multiline_legend_show", """
import numpy as np
import matplotlib.pyplot as plt
t = np.linspace(0, 6, 60)
plt.figure(figsize=(3.2, 2.4))
plt.plot(t, np.sin(t), label="sin")
plt.plot(t, np.cos(t), "--", label="cos")
plt.xlabel("t")
plt.title("Waves")
plt.legend()
plt.show()
""", {"title", "line", "legend", "axis_label"}, ("axes_0.lines_0", "linewidth", 2.5)),
    ("scatter", """
import numpy as np
import matplotlib.pyplot as plt
rng = np.random.default_rng(7)
fig, ax = plt.subplots(figsize=(3, 2.2))
ax.scatter(rng.random(30), rng.random(30))
fig.savefig("Scatter.png")
""", {"scatter"}, ("axes_0.scatter_0", "alpha", 0.4)),
    ("annotation", """
import matplotlib.pyplot as plt
fig, ax = plt.subplots(figsize=(3, 2))
ax.plot([0, 1, 2], [0, 2, 1])
ax.annotate("peak", xy=(1, 2), xytext=(1.4, 1.5),
            arrowprops=dict(arrowstyle="->"))
fig.savefig("Anno.pdf")
""", {"line", "text"}, ("axes_0.texts_0", "fontsize", 11)),
    ("custom_ticks", """
import matplotlib.pyplot as plt
fig, ax = plt.subplots(figsize=(3, 2))
ax.plot([0, 1, 2, 3], [3, 1, 2, 0])
ax.set_xticks([0, 1.5, 3])
ax.set_xlabel("x")
ax.set_ylabel("y")
fig.savefig("Ticks.pdf")
""", {"ticks", "axis_label"}, ("axes_0.xticks", "fontsize", 7)),
    ("fill_between", """
import numpy as np
import matplotlib.pyplot as plt
t = np.linspace(0, 4, 80)
fig, ax = plt.subplots(figsize=(3, 2))
ax.plot(t, np.sin(t))
ax.fill_between(t, np.sin(t) - 0.2, np.sin(t) + 0.2, alpha=0.3)
fig.savefig("Band.pdf")
""", {"line", "fill"}, ("axes_0.lines_0", "color", "#aa3311")),
    ("colorbar", """
import numpy as np
import matplotlib.pyplot as plt
fig, ax = plt.subplots(figsize=(3, 2.4))
im = ax.imshow(np.arange(20.0).reshape(4, 5), cmap="viridis")
fig.colorbar(im, ax=ax)
fig.savefig("Heat.pdf")
""", {"image", "colorbar"}, ("axes_0.images_0", "cmap", "plasma")),
]

if WORKER_PY and _module_available("pandas"):
    FIXTURES.append(("pandas_plot", """
import pandas as pd
import matplotlib.pyplot as plt
df = pd.DataFrame({"a": [1.0, 3.0, 2.0], "b": [2.0, 1.0, 3.0]})
ax = df.plot(figsize=(3, 2), title="Frame")
ax.figure.savefig("Frame.pdf")
""", {"title", "line", "legend"}, ("axes_0.title", "text", "Renamed")))

if WORKER_PY and _module_available("scipy"):
    FIXTURES.append(("scipy_data", """
import numpy as np
from scipy import signal
import matplotlib.pyplot as plt
t = np.linspace(0, 1, 200)
fig, ax = plt.subplots(figsize=(3, 2))
ax.plot(t, signal.sawtooth(2 * np.pi * 4 * t))
ax.set_title("Sawtooth")
fig.savefig("Saw.pdf")
""", {"title", "line"}, ("axes_0.title", "color", "#2266aa")))


@pytest.mark.parametrize(("name", "src", "want_roles", "patch"),
                         FIXTURES, ids=[f[0] for f in FIXTURES])
def test_fixture_runs_edits_and_reverts(name, src, want_roles, patch, tmp_path):
    gid, prop, value = patch
    patches = [{"gid": gid, "prop": prop, "value": value}]
    # 先单独 load 一次拿 stem（savefig 的文件名只有跑过才知道），
    # 再起一个全新会话跑完整的 load → open → 编辑 → 还原 序列
    (load,) = drive([{"cmd": "load", "filename": f"{name}.py", "source": src}],
                    tmp_path)
    assert load["ok"], load
    assert load["figures"], f"{name}: 没捕获到 figure"
    stem = load["figures"][0]["stem"]

    load, opened, edited, reverted = drive([
        {"cmd": "load", "filename": f"{name}.py", "source": src},
        {"cmd": "open", "stem": stem},
        {"cmd": "render", "stem": stem, "patches": patches},
        {"cmd": "render", "stem": stem, "patches": []},
    ], tmp_path)
    assert opened["ok"], opened
    man = opened["manifest"]
    assert want_roles <= roles(man), f"{name}: 缺角色 {want_roles - roles(man)}"
    assert opened["svg"].lstrip().startswith("<?xml") or "<svg" in opened["svg"][:500]
    assert man["size_mm"][0] > 0 and man["size_mm"][1] > 0
    # manifest 元素坐标都是 figure 分数（0..1 区间附近）
    for el in man["elements"]:
        assert len(el["bbox"]) == 4

    assert edited["ok"], edited
    assert edited["warnings"] == [], f"{name}: override 被拒 {edited['warnings']}"
    assert edited["render_revision"] > opened["render_revision"]
    # 改动真的落进了 manifest（值往返）
    got = field_value(edited["manifest"], gid, prop)
    if isinstance(value, (int, float)):
        assert float(got) == pytest.approx(float(value))
    elif prop == "color":
        assert str(got).lower() == value.lower()
    else:
        assert got == value

    # 空列表 = 全量还原（undo 的基础）
    assert reverted["ok"], reverted
    assert reverted["warnings"] == []
    assert field_value(reverted["manifest"], gid, prop) == \
        field_value(opened["manifest"], gid, prop)

    # §49：browser 侧的 patch_hash 与父进程 patchspec 同源同值
    assert edited["patch_hash"] == patchspec.patch_hash(patches)
    assert reverted["patch_hash"] == patchspec.patch_hash([])


# ---------------------------------------------------------------- 执行语义

def test_script_sees_own_argv_and_file(tmp_path):
    src = """
import sys, os
import matplotlib.pyplot as plt
assert sys.argv == [__file__], sys.argv
assert os.path.basename(__file__) == "my fig.py", __file__
fig, ax = plt.subplots(figsize=(2, 2))
ax.plot([0, 1], [1, 0])
fig.savefig(os.path.splitext(os.path.basename(__file__))[0] + ".pdf")
"""
    (load,) = drive([{"cmd": "load", "filename": "my fig.py", "source": src}], tmp_path)
    assert load["ok"], load
    assert load["figures"][0]["stem"] == "my fig"


def test_savefig_is_intercepted_not_written(tmp_path):
    src = """
import matplotlib.pyplot as plt
fig, ax = plt.subplots(figsize=(2, 2))
ax.plot([0, 1], [0, 1])
fig.savefig("Real.pdf")
"""
    (load,) = drive([{"cmd": "load", "filename": "f.py", "source": src}], tmp_path)
    assert load["ok"]
    assert not (tmp_path / "ws" / "Real.pdf").exists(), "build 期不许写用户输出文件"


def test_pyplot_fallback_names_do_not_collide(tmp_path):
    src = """
import matplotlib.pyplot as plt
for i in range(3):
    fig, ax = plt.subplots(figsize=(2, 2))
    ax.plot([0, 1], [i, 1])
plt.show()
"""
    (load,) = drive([{"cmd": "load", "filename": "figs.py", "source": src}], tmp_path)
    assert load["ok"]
    stems = [f["stem"] for f in load["figures"]]
    assert len(stems) == len(set(stems)) == 3


def test_stdout_is_captured_and_bounded(tmp_path):
    src = """
import matplotlib.pyplot as plt
for i in range(20000):
    print("line", i)
fig, ax = plt.subplots(figsize=(2, 2))
ax.plot([0, 1], [0, 1])
fig.savefig("F.pdf")
"""
    (load,) = drive([{"cmd": "load", "filename": "f.py", "source": src}], tmp_path)
    assert load["ok"]
    assert load["log"].startswith("[output truncated]")
    assert len(load["log"].encode()) < 80 * 1024
    assert "line 19999" in load["log"]  # 留的是尾部


def test_preview_png_is_state_neutral(tmp_path):
    src = """
import matplotlib.pyplot as plt
fig, ax = plt.subplots(figsize=(2.4, 2))
ax.plot([0, 1, 2], [0, 2, 1])
ax.set_title("Neutral")
fig.savefig("N.pdf")
"""
    load, opened, png, after = drive([
        {"cmd": "load", "filename": "n.py", "source": src},
        {"cmd": "open", "stem": "N"},
        {"cmd": "preview_png", "stem": "N",
         "patches": [{"gid": "axes_0.title", "prop": "fontsize", "value": 30}],
         "width": 300},
        {"cmd": "render", "stem": "N", "patches": []},
    ], tmp_path)
    assert png["ok"] and png["png"], png
    # preview 用的 patches 不许留在常驻 figure 上
    assert field_value(after["manifest"], "axes_0.title", "fontsize") == \
        field_value(opened["manifest"], "axes_0.title", "fontsize")


# ---------------------------------------------------------------- import 分类

def test_classify_maps_roots_to_packages(tmp_path):
    src = """
import os
import math
import numpy as np
import pandas as pd
from PIL import Image
from scipy import stats
import rdkit
"""
    (out,) = drive([{"cmd": "classify", "source": src,
                     "supported_roots": SUPPORTED_ROOTS}], tmp_path)
    assert out["ok"]
    assert out["unsupported"] == ["rdkit"]
    assert set(out["supported"]) == {"numpy", "pandas", "PIL", "scipy"}
    assert set(out["stdlib"]) == {"os", "math"}
    assert set(out["packages"]) == {"numpy", "pandas", "pillow", "scipy"}


def test_classify_optional_try_import_is_not_blocking(tmp_path):
    src = """
import matplotlib.pyplot as plt
try:
    import seaborn as sns
except ImportError:
    sns = None
"""
    (out,) = drive([{"cmd": "classify", "source": src,
                     "supported_roots": SUPPORTED_ROOTS}], tmp_path)
    assert out["unsupported"] == []
    assert out["optional_unsupported"] == ["seaborn"]


def test_classify_syntax_error_has_code(tmp_path):
    (out,) = drive([{"cmd": "classify", "source": "def broken(:",
                     "supported_roots": SUPPORTED_ROOTS}], tmp_path)
    assert out["ok"] is False and out["code"] == "syntax_error"


# ---------------------------------------------------------------- 错误分诊

@pytest.mark.parametrize(("name", "src", "code"), [
    ("syntax", "def broken(:\n", "syntax_error"),
    ("dynamic_import", "import importlib\nimportlib.import_module('rdkit')",
     "unsupported_import"),
    ("missing_file", "open('data.csv').read()", "missing_file"),
    ("crash", "raise RuntimeError('boom')", "script_error"),
])
def test_load_failures_carry_stable_codes(name, src, code, tmp_path):
    (load,) = drive([{"cmd": "load", "filename": "f.py", "source": src}], tmp_path)
    assert load["ok"] is False
    assert load["code"] == code, load
    if code == "script_error":
        assert "boom" in load["traceback"]
    if code == "missing_file":
        assert "data.csv" in load["filename"]
    if code == "unsupported_import":
        assert load["modules"] == ["rdkit"]


def test_no_figure_is_ok_with_empty_list(tmp_path):
    (load,) = drive([{"cmd": "load", "filename": "f.py",
                      "source": "x = 1 + 1\n"}], tmp_path)
    assert load["ok"] and load["figures"] == []


def test_oversized_source_is_rejected(tmp_path):
    (load,) = drive([{"cmd": "load", "filename": "f.py",
                      "source": "# " + "x" * (300 * 1024)}], tmp_path)
    assert load["ok"] is False and load["code"] == "source_too_large"


def test_commands_before_load_are_bad_request(tmp_path):
    out = drive([{"cmd": "render", "stem": "F", "patches": []},
                 {"cmd": "unheard_of"}], tmp_path)
    assert out[0]["code"] == "bad_request"
    assert out[1]["code"] == "unknown_cmd"


def test_second_load_in_one_session_is_refused(tmp_path):
    src = "import matplotlib.pyplot as plt\nplt.plot([0,1])\nplt.show()\n"
    a, b = drive([{"cmd": "load", "filename": "a.py", "source": src},
                  {"cmd": "load", "filename": "b.py", "source": src}], tmp_path)
    assert a["ok"] and b["ok"] is False and b["code"] == "bad_request"


# ------------------------------------------------------- 源文件完整性

#: 三张图都用得上的最小脚本（有标题，可以真的改一处再回来核对）。
_INTEGRITY_SRC = """
import matplotlib.pyplot as plt
fig, ax = plt.subplots(figsize=(2.6, 2))
ax.plot([0, 1, 2], [1, 0, 2], label="a")
ax.set_title("Integrity")
ax.legend()
fig.savefig("I.pdf")
"""


def test_workspace_source_hash_equals_the_input_and_survives_edits(tmp_path):
    """写进 /workspace 的就是用户给的那份，改完图之后还是那份。

    这是「figure.py · 未改动」这句话的 Worker 侧证据：哈希取自**虚拟 FS 里
    真正被 runpy 执行的那个文件**，不是内存里传进来的字符串。主线程那半边
    （Web Crypto 算原文）在 `web/src/playground/sourceIntegrity.test.ts`。
    """
    want = hashlib.sha256(_INTEGRITY_SRC.encode("utf-8")).hexdigest()
    load, opened, rendered, status = drive([
        {"cmd": "load", "filename": "integrity.py", "source": _INTEGRITY_SRC},
        {"cmd": "open", "stem": "I"},
        {"cmd": "render", "stem": "I",
         "patches": [{"gid": "axes_0.title", "prop": "fontsize", "value": 17}]},
        {"cmd": "source_status"},
    ], tmp_path)

    assert load["ok"], load
    assert load["script"] == "integrity.py"
    assert load["source_sha256"] == want
    assert load["source_bytes"] == len(_INTEGRITY_SRC.encode("utf-8"))
    # 文件真的在工作区里，而且逐字节就是输入
    assert (tmp_path / "ws" / "integrity.py").read_bytes() == _INTEGRITY_SRC.encode("utf-8")

    assert opened["ok"] and rendered["ok"], (opened, rendered)
    # 改了一处真的生效了——「没改源文件」不是因为什么都没做
    assert field_value(rendered["manifest"], "axes_0.title", "fontsize") == 17
    assert status["ok"] and status["sha256"] == want, status
    assert status["script"] == "integrity.py"


def test_tampered_workspace_source_is_reported_not_hidden(tmp_path):
    """有人动了工作区里的源文件 → 哈希必须变，不许还报「未改动」。

    产品代码里没有这条路（篡改指令在测试驱动里）；这条用例证明的是
    **校验本身有效**——一个永远返回相同哈希的实现会在这里红。
    """
    want = hashlib.sha256(_INTEGRITY_SRC.encode("utf-8")).hexdigest()
    load, before, _tampered, after = drive([
        {"cmd": "load", "filename": "integrity.py", "source": _INTEGRITY_SRC},
        {"cmd": "source_status"},
        {"cmd": "__tamper", "path": str(tmp_path / "ws" / "integrity.py"),
         "append": "\n# someone edited this\n"},
        {"cmd": "source_status"},
    ], tmp_path)

    assert load["ok"] and before["ok"]
    assert before["sha256"] == want
    assert after["ok"]
    assert after["sha256"] != want, "改过的文件绝不能算出原来的哈希"
    assert after["bytes"] > before["bytes"]


def test_safe_name_is_stateless_and_answerable_before_any_user_code(tmp_path):
    """`safe_name` 必须在**没有会话、没跑过任何用户代码**的时候就能回答。

    Worker 靠它把工作区里的脚本路径钉死在 JS 那一侧，而钉死这个动作必须发生在
    `load` **之前**——等 load 跑完再从回应里取名字的话，用户脚本可以先留一个
    内容是原样的诱饵文件、再改掉 `_ACTIVE.script_name`，于是摘要算得再独立，
    也只是在给诱饵作证（codex 审查第二轮指出的那条）。

    收紧规则只有 `_safe_script_name` 一份实现，所以这条同时钉住「JS 侧不许
    抄第二份」：抄了就会漂，而漂的表现是「界面核对的文件不是被执行的那个」。
    """
    cases = [
        ("my fig.py", "my fig.py"),
        # 目录部分一律丢掉，路径绝不许穿出工作区
        ("a/b.py", "b.py"),
        ("../../etc/passwd", "figure.py"),
        (".hidden.py", "figure.py"),
        ("x.txt", "figure.py"),
        # 非 ASCII **是保留的**（`str.isalnum()` 对 CJK 为真）。这条是有意
        # 钉住的事实：谁想收紧到纯 ASCII，得先面对「用户的文件名突然变成
        # figure.py」这个后果，而不是顺手改掉这行判据。
        ("\u4e2d\u6587.py", "\u4e2d\u6587.py"),
    ]
    out = drive([{"cmd": "safe_name", "filename": n} for n, _ in cases], tmp_path)
    assert all(o["ok"] for o in out), out
    assert [o["script"] for o in out] == [want for _, want in cases]


def test_source_status_before_load_is_bad_request(tmp_path):
    (out,) = drive([{"cmd": "source_status"}], tmp_path)
    assert out["ok"] is False and out["code"] == "bad_request"


def test_manifest_values_are_plain_json(tmp_path):
    """交给 JS 的结构里绝不能混 numpy 标量——严格 JSON 往返就是判据。"""
    src = """
import numpy as np
import matplotlib.pyplot as plt
fig, ax = plt.subplots(figsize=(2.4, 2))
ax.plot(np.array([0.0, 1.0]), np.array([1.0, 0.5]))
ax.set_xlim(np.float64(0), np.float64(1))
fig.savefig("J.pdf")
"""
    _, opened = drive([{"cmd": "load", "filename": "j.py", "source": src},
                       {"cmd": "open", "stem": "J"}], tmp_path)
    # drive 本身就做了严格 json 解析；这里再断言可以逐字节重序列化
    json.dumps(opened["manifest"], allow_nan=False)
