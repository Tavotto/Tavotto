"""建一个**像用户项目那样**的真 venv——两组用例共用的唯一出处。

`test_project_env.py`（Session 7）与 `test_dependency_repair*.py`（7B）都要
「项目自己带的 `.venv`」这个夹具。创建那一步有三个不显眼但会决定成败的细节，
写两份的话其中一份迟早漏掉一个：

1. **matplotlib 直接用宿主那份**，CI 不必联网装几百 MB 的科学栈。
   注意 `--system-site-packages` **单独不够**：它带进来的是**基础解释器**的
   site-packages，不是「交给夹具的那个解释器」的（见 `inherit_host_site()`）。
2. **必须把宿主的 `tavotto` 遮掉**（见 `mask_tavotto()`）。
3. **建完当场验一遍前提，不成立就分档报出来**（见 `verify()`）——夹具的前提
   失效时，红的是十几条**看起来在测别的东西**的用例，而错误信息指向产品。
"""

import json
import os
import subprocess
from pathlib import Path

#: 遮蔽用的替身。真实用户的 venv 里就是**没有** Tavotto，`import tavotto`
#: 抛的正是 ModuleNotFoundError——替身照着那个形状抛，而不是抛个别的。
_MASK = (
    "# 由 tests/support/venvfixture.py 写入：把宿主解释器上的 Tavotto 遮掉，\n"
    "# 让这个 venv 长得跟真实用户的项目环境一样（那里面没有 Tavotto）。\n"
    'raise ModuleNotFoundError("No module named \'tavotto\'", name="tavotto")\n'
)

#: 把宿主 import 得到的东西接进新 venv 的那个 `.pth`。文件名带前缀是为了让
#: 排障的人一眼看出它是谁写的——venv 里出现一个来路不明的 `.pth` 比缺一个
#: 更难查。
_HOST_SITE_PTH = "_tavotto_fixture_host_site.pth"

# --------------------------------------------------------------- 分档结论
#: 夹具前提不成立的四种**不同**答案。合并成一句「环境有问题」就等于把
#: 「这台机器缺 matplotlib」和「夹具没把它带进来」变成同一个结论，而这两件事
#: 该找的人、该动的东西完全不同。
PREMISE_HOST_NO_MATPLOTLIB = "fixture_host_no_matplotlib"
PREMISE_NOT_INHERITED = "fixture_venv_did_not_inherit_matplotlib"
PREMISE_MASK_INEFFECTIVE = "fixture_venv_tavotto_not_masked"
PREMISE_VENV_UNUSABLE = "fixture_venv_unusable"


class VenvFixtureError(AssertionError):
    """夹具的前提不成立。`code` 是上面四档之一，别再往里合并新的含义。"""

    def __init__(self, code: str, message: str):
        super().__init__(f"[{code}] {message}")
        self.code = code


# --------------------------------------------------------------- 探测
#: 在**宿主解释器**里跑：它到底能 import 到哪些目录，以及它自己有没有 matplotlib。
#:
#: 取的是**已经解析好的 `sys.path`**，不是 `site.getsitepackages()`：宿主自己
#: 也可能是一层夹具 venv（它的包是靠本文件写的 `.pth` 接进来的），
#: `getsitepackages()` 只会回它自己那个空壳目录，而 `sys.path` 里是真的都在。
#:
#: **刻意剔掉 `PYTHONPATH` 带进来的目录**：跑 pytest 时父进程带着
#: `PYTHONPATH=<仓库>/src`，把它烧进夹具 venv 等于让「用户的项目环境」里凭空
#: 出现一份 Tavotto 源码——那正是这个夹具要证明不存在的东西。
_HOST_SRC = r"""
import json, os, sys
skip = set()
for p in (os.environ.get("PYTHONPATH") or "").split(os.pathsep):
    if p:
        try:
            skip.add(os.path.abspath(p))
        except OSError:
            pass
dirs = []
for p in sys.path:
    if not p:
        continue
    try:
        if os.path.isdir(p) and os.path.abspath(p) not in skip:
            dirs.append(p)
    except OSError:
        continue
try:
    import matplotlib
    mpl = matplotlib.__version__
except Exception:
    mpl = None
sys.stdout.write(json.dumps({
    "dirs": dirs, "prefix": sys.prefix, "base_prefix": sys.base_prefix,
    "version": "%d.%d.%d" % sys.version_info[:3], "matplotlib": mpl}))
"""

#: 在**新建的 venv** 里跑，确认两条前提。`-I`：跑测试时父进程带着
#: `PYTHONPATH=<仓库>/src`，不隔离的话 `import tavotto` 会看到仓库源码，
#: 遮蔽有没有生效就量不出来了。
_VERIFY_SRC = r"""
import json, sys
out = {"executable": sys.executable, "prefix": sys.prefix,
       "base_prefix": sys.base_prefix, "matplotlib": None, "tavotto": ""}
try:
    import matplotlib
    out["matplotlib"] = matplotlib.__version__
except Exception as exc:
    out["matplotlib_error"] = "%s: %s" % (type(exc).__name__, exc)
try:
    import tavotto
    out["tavotto"] = getattr(tavotto, "__file__", "?") or "?"
except Exception:
    out["tavotto"] = ""
sys.stdout.write(json.dumps(out))
"""


def _run_json(python: str, source: str, *, isolated: bool) -> dict:
    """在某个解释器里跑一段脚本，把它那行 JSON 读回来；跑不起来回诊断。"""
    cmd = [str(python)] + (["-I"] if isolated else []) + ["-c", source]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"_error": str(exc)[:400]}
    if proc.returncode != 0:
        return {"_error": (proc.stderr or "").strip()[:400]}
    try:
        return json.loads(proc.stdout.strip() or "{}")
    except ValueError:
        return {"_error": (proc.stdout or "").strip()[:400]}


def host_facts(python: str) -> dict:
    """交给夹具的那个解释器的事实：能 import 哪些目录、自己有没有 matplotlib。"""
    return _run_json(python, _HOST_SRC, isolated=False)


def site_packages(venv: Path) -> Path:
    found = sorted(venv.glob("lib/python*/site-packages")) or sorted(venv.glob("Lib/site-packages"))
    return found[0]


def mask_tavotto(venv: Path) -> Path:
    """把宿主解释器上的 `tavotto` 从这个 venv 里遮掉。

    **为什么必须做**：新 venv 会看到宿主的 site-packages（`--system-site-packages`
    带来的那一份，以及 `inherit_host_site()` 接进来的那一份），而 CI 的
    backend-fast 正是 `pip install -e ".[dev]"` 装进那个解释器的。于是夹具 venv 里
    `import tavotto` 成功——「项目 venv 里没有 Tavotto 也能起 worker」那条用例的
    **前提当场失效**（它在 CI 上红过一次，就是这么红的）。

    做法是往 venv **自己的** site-packages 写一个同名替身——它排在继承来的
    那些目录**前面**：`site` 先把 venv 自己的 site-packages 加进 `sys.path`，
    `.pth` 里的路径行是**追加**到它后面的，所以替身一定先被找到。
    遮蔽有没有真的生效不靠推理，`verify()` 每次都量一遍。

    worker 那条路不受影响：`engine/worker.py` 是 `sys.path.insert(0, HERE)`
    的**平铺** import（`figcapture` / `manifest` / `overrides`），从头到尾
    不 `import tavotto`——那正是本轮要证明的事。
    """
    shim = site_packages(venv) / "tavotto.py"
    shim.write_text(_MASK, encoding="utf-8")
    return shim


def inherit_host_site(venv: Path, python: str, facts: dict | None = None) -> Path | None:
    """把**交给夹具的那个解释器**能 import 的目录接进新 venv。

    `--system-site-packages` 带进来的是 `sys.base_prefix` 的 site-packages，
    **不是** `python` 自己的。两者只有在 `python` 就是基础解释器时才重合：

    * GitHub 的 backend-fast：`pip install -e ".[dev]" && pip install matplotlib`
      直接装进 setup-python 的解释器，pytest 也用它——两者重合，一直是绿的。
    * 实验室 runner：整套东西装在一次性 venv 里（`_lab-qualification.yml` 的
      「建一次性验证环境」），base 是 `/usr/bin/python3`（Ubuntu 3.12.3，
      按设计**不**带 matplotlib——科学栈的版本由 `packaging/runtime-lock.json`
      钉在那个 venv 里，装到系统解释器上反而会让像素基线失去意义）。
      于是新建的 venv 一个 matplotlib 都看不到，`probe_environment()` 如实
      报 `project_env_no_matplotlib`，`test_project_env.py` 14 条全红（#225）。

    做法是往新 venv 自己的 site-packages 写一个**只含路径行**的 `.pth`。
    路径行与 `addsitedir()` 有一个决定性的差别：**路径行只把目录追加进
    `sys.path`，不会再去执行那个目录里的 `.pth`**。宿主的 editable 安装
    （`__editable__*.pth` + 一个注册进 `sys.meta_path` 的 finder）因此不会
    跟着进来——夹具要的是「这个环境里有 matplotlib」，不是「宿主装过什么」。

    ABI 上是安全的：新 venv 就是 `python` 自己 `-m venv` 建出来的，同一个
    解释器、同一个 minor、同一套平台标签。这与 ADR 0018「绝不混装
    site-packages」不冲突——那条约束说的是**产品**不许把用户 venv 的
    site-packages 塞进内置 runtime 的 `sys.path`（两个不同的解释器），
    看护它的是 `test_never_mixes_site_packages`，那条用例照旧。
    """
    facts = host_facts(python) if facts is None else facts
    own = os.path.normcase(str(site_packages(venv)))
    dirs: list[str] = []
    for d in facts.get("dirs") or []:
        if not d or os.path.normcase(str(d)) == own:
            continue
        if str(d) not in dirs:
            dirs.append(str(d))
    if not dirs:
        return None
    pth = site_packages(venv) / _HOST_SITE_PTH
    pth.write_text("\n".join(dirs) + "\n", encoding="utf-8")
    return pth


def interpreter_of(venv: Path) -> str | None:
    """venv 里的解释器；没有回 None。夹具自己判，不绕道产品代码。"""
    for rel in ("bin/python", "bin/python3", "Scripts\\python.exe"):
        cand = venv / rel
        if cand.is_file():
            return str(cand)
    return None


def verify(venv: Path, python: str, facts: dict | None = None) -> dict:
    """建完就地验一遍夹具的前提，不成立**分档**抛出来。

    没有这一步时，前提失效的表现是十几条用例在断言产品的错误码
    （`assert 'project_env_no_matplotlib' == 'project_env_module_missing'`）——
    读的人第一反应是产品坏了，而真正的答案在夹具或那台机器上。四档分开报，
    是因为它们该找的人不同：

    * `fixture_host_no_matplotlib`：**机器/环境侧**——交给夹具的解释器自己就
      没有 matplotlib。夹具无能为力（本轮明确不装任何东西）。
    * `fixture_venv_did_not_inherit_matplotlib`：**夹具侧**——宿主有，没带进来。
    * `fixture_venv_tavotto_not_masked`：**夹具侧**——遮蔽失效。
    * `fixture_venv_unusable`：**环境坏**——新建的 venv 根本起不来。
    """
    facts = host_facts(python) if facts is None else facts
    interpreter = interpreter_of(venv)
    if interpreter is None:
        raise VenvFixtureError(
            PREMISE_VENV_UNUSABLE, f"新建的 venv 里找不到解释器：{venv}（宿主 {python}）"
        )
    seen = _run_json(interpreter, _VERIFY_SRC, isolated=True)
    if "_error" in seen or not seen:
        raise VenvFixtureError(
            PREMISE_VENV_UNUSABLE,
            f"新建的 venv 起不来：{interpreter}（宿主 {python}）；"
            f"{seen.get('_error') or '没有任何输出'}",
        )
    if not seen.get("matplotlib"):
        where = (
            f"宿主 {python}（prefix={facts.get('prefix')}、base={facts.get('base_prefix')}、"
            f"Python {facts.get('version')}）"
        )
        detail = seen.get("matplotlib_error") or ""
        if not facts.get("matplotlib"):
            raise VenvFixtureError(
                PREMISE_HOST_NO_MATPLOTLIB,
                f"{where} 自己就 import 不到 matplotlib，夹具没法凭空造一个出来"
                "（本轮明确不装任何东西）。这是机器/环境侧的事：给跑测试的那个解释器"
                f"装上 matplotlib，或让 TAVOTTO_WORKER_PYTHON 指向一个带它的解释器。"
                f"venv 侧报的是：{detail}",
            )
        raise VenvFixtureError(
            PREMISE_NOT_INHERITED,
            f"{where} 有 matplotlib {facts['matplotlib']}，却没能接进新建的 venv "
            f"{venv}——`inherit_host_site()` 漏掉了宿主的 site-packages。"
            f"venv 侧报的是：{detail}",
        )
    if seen.get("tavotto"):
        raise VenvFixtureError(
            PREMISE_MASK_INEFFECTIVE,
            f"夹具 venv 里 `import tavotto` 还成功（{seen['tavotto']}）——遮蔽失效，"
            "「项目 venv 里没有 Tavotto 也能起 worker」那条就只是碰运气。",
        )
    return seen


def make_project_venv(root: Path, name: str = ".venv", *, python: str | None = None) -> Path:
    """在 `root` 下建一个能执行、有 matplotlib、且**不含 Tavotto** 的 venv。"""
    venv = root / name
    subprocess.run(
        [python, "-m", "venv", "--system-site-packages", str(venv)],
        check=True,
        capture_output=True,
        timeout=300,
    )
    facts = host_facts(str(python))
    inherit_host_site(venv, str(python), facts)
    mask_tavotto(venv)
    verify(venv, str(python), facts)
    return venv
