"""项目本地 Python 环境：发现、体检、记住（Compatibility Bridge Session 7）。

内置 runtime 缺第三方依赖时（`import lmfit` / `import ovito` 而内置环境里
没有），用户原本的出路是「去设置里手填一条解释器路径」——门槛太高。绝大多数
科研项目自己就带着一个能跑通的环境（`.venv/`），本模块负责把它找出来、验证
它真的能跑 Tavotto 的 worker，然后交给 `pool` 作为**该项目**的渲染解释器。

**本模块只做发现与体检，不做决策**：谁压过谁是 `pool.resolve_worker_python`
那条唯一优先级链的事，本模块不碰它。完整设计见
`docs/adr/0018-project-python-environment-resolution.md`。

**纯标准库**：被 `engine/pool.py` import，而 pool 被 Flask import
（进程与依赖边界见 `src/tavotto/AGENTS.md`）。

## 硬约束：以完整解释器为单位切换，绝不混装 site-packages

绝不允许把 `<项目>/.venv/lib/**/site-packages` 塞进内置 runtime 的 `sys.path`
或 `PYTHONPATH`。用户 venv 里的 numpy / scipy / h5py / rdkit / opencv / torch
是编译扩展，绑死 CPython ABI、Python minor 版本、NumPy ABI 与系统动态库：

    内置 Python 3.13 + venv 里的 cp311 扩展   → import 即崩
    内置 numpy 2.x  + venv 里对 numpy 1.x 编译的 scipy → 不可预测状态

后者尤其危险——它不一定当场崩，可能只是算出错的数。所以环境切换的单位只能是
**整个解释器**：

    <项目>/.venv/bin/python → engine/worker.py → 用户脚本

而不是「内置解释器 import 用户 venv 里的包」。这条由
`tests/test_project_env.py::test_never_mixes_site_packages` 结构性看护。

## 为什么第一版只认本地 venv

`.venv` / `venv` / `env` 三种目录名覆盖 stdlib venv、virtualenv 与 uv venv，
它们的共同点是**目录里就有一个真正的解释器**，不需要 shell activation、不需要
解析任何工具自己的元数据。Poetry / Conda / pyenv / pixi / hatch 都要先问它们
自己的 CLI 才知道环境在哪（`conda env list` 可能几秒），而且环境往往在项目
之外——那是另一个安全模型。等真实用户数据表明有需要再加。
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
from pathlib import Path

from . import config, runtime

LOG = logging.getLogger("tavotto.projectenv")

# ---------------------------------------------------------------------------
# 稳定错误码（协议契约：code 不许改，文案随便改）。
# 前端文案在 web/src/i18n/locales/*/errors.json 的 backend.* 下（中英各一份）。
# ---------------------------------------------------------------------------
ERROR_NOT_FOUND = "project_env_not_found"
ERROR_MODULE_MISSING = "project_env_module_missing"
ERROR_NO_MATPLOTLIB = "project_env_no_matplotlib"
ERROR_UNSUPPORTED_PYTHON = "project_env_unsupported_python"
ERROR_MULTIPLE = "multiple_project_environments"
ERROR_UNUSABLE = "project_env_unusable"

#: 找哪些目录名。顺序即优先级（同一层同时存在时按这个顺序裁决）。
VENV_DIRNAMES = (".venv", "venv", "env")

#: 体检子进程的超时。冷启动一个解释器 + import matplotlib 在机械硬盘上可以
#: 十几秒；给足，超时按「环境不可用」处理而不是当作缺包。
PROBE_TIMEOUT_S = 60.0

#: 支持口径。**唯一权威是 `docs/support-matrix.json` 与 `pyproject.toml`**，
#: 这里是运行时镜像（那两份文件不随 wheel 发布，运行时读不到）。
#: `tests/test_support_matrix.py::test_project_env_mirrors_the_matrix` 逐条对拍，
#: 改了矩阵不改这里当场变红。
PYTHON_MIN = (3, 10)
PYTHON_MAX_EXCLUSIVE = (3, 14)
PYTHON_TESTED = ((3, 10), (3, 11), (3, 12), (3, 13))
MPL_MIN = (3, 8)
MPL_MAX_EXCLUSIVE = (3, 12)

#: 支持等级。`unsupported` 拒绝自动使用；`unverified_but_compatible` 允许用，
#: 但环境状态里如实标出来（不声称验证过）。
SUPPORT_VERIFIED = "verified"
SUPPORT_UNVERIFIED = "unverified_but_compatible"
SUPPORT_UNSUPPORTED = "unsupported"

#: 顶级模块名的合法形状。**体检要在目标解释器里 import 这个名字**，所以它
#: 绝不能是任意字符串——`missing_module()` 从 traceback 里抠出来的东西终究
#: 来自用户脚本，直接拼进 `-c` 就是一条注入路径。
_MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def valid_module_name(name: str) -> bool:
    """能不能安全地拿去 import。只认**顶级**模块名（不含点）。

    `missing_module()` 产出的本来就是顶级名（`No module named 'lmfit'` 里
    带点的那种也只取第一段），这里是第二道门：不合形状一律不体检，
    宁可少一次自动切换，也不把用户脚本里的字符串送进子进程命令行。
    """
    return bool(name) and bool(_MODULE_RE.match(name))


# --------------------------------------------------------------- 发现
def _interpreter_names() -> tuple[str, ...]:
    """venv 里解释器的相对路径候选（按平台）。

    **不走 `activate`**：那是给交互 shell 用的脚本，解析它等于重建一套 shell
    语义；解释器实体一直就在固定位置。
    """
    if os.name == "nt":
        return ("Scripts\\python.exe", "Scripts\\pythonw.exe")
    return ("bin/python", "bin/python3")


def interpreter_of(venv_dir: str | Path, *, root: str | Path | None = None) -> str | None:
    """venv 目录 → 里面的解释器路径；不是有效 venv 回 None。

    **不能只看目录名**：项目里叫 `env/` 的目录经常是「环境配置」「环境变量
    样例」这类东西。判据是 `pyvenv.cfg` 存在（stdlib venv / virtualenv / uv
    三家都写它）**且**解释器文件真的在。

    给了 `root` 就把候选**钉死在它之内**（`contained_path`）。生产上的每个
    调用点都该传——回来的这条路径下游是要拿去 spawn 的。
    """
    base_str = str(venv_dir) if root is None else contained_path(root, venv_dir)
    if base_str is None:
        # 给了 root 却逃出去了：这个候选一开始就不该被看见
        return None
    base = Path(base_str)
    if not (base / "pyvenv.cfg").is_file():
        return None
    for rel in _interpreter_names():
        # `contained_file` 判的是**父目录**——解释器本身是软链接，不能 realpath。
        cand = contained_file(base, rel)
        if cand is None:
            continue
        try:
            if Path(cand).is_file():
                return cand
        except OSError:
            continue
    return None


def contained_path(root: str | Path, candidate: str | Path) -> str | None:
    """把 `candidate` **钉死在** `root` 之内，回已 realpath 的绝对路径；逃出去回 None。

    这是本模块（以及 app 的项目环境端点）**唯一**允许把用户派生路径交给
    文件系统或子进程的入口。两步缺一不可，顺序也不能反：

    1. **先 realpath**——软链接、`..`、`.` 全部在这一步落地。只做字符串
       归一（`normpath`）的话，`<项目>/.venv -> /etc` 这种软链接看着在项目
       内、实体在项目外。
    2. **再按路径前缀判**——比较的是 realpath 之后的两条绝对路径，
       `real == real_root` 或以 `real_root + os.sep` 开头才算数。
       用 `+ os.sep` 而不是裸 `startswith`：否则 `/a/project-evil` 会被
       `/a/project` 判成「在里面」。

    `_within()` 回的是布尔、给发现流程做过滤；这一个回**净化后的路径本身**，
    调用方拿它去 open/spawn——「判过了」与「用的是判过的那一个」是两件事，
    分开写就还有把前者的结论用在后者之外的机会。
    """
    try:
        real_root = os.path.realpath(os.fspath(root))
        real = os.path.realpath(os.path.join(real_root, os.fspath(candidate)))
    except (OSError, ValueError):
        return None
    if real != real_root and not real.startswith(real_root + os.sep):
        return None
    return real


def contained_file(root: str | Path, candidate: str | Path) -> str | None:
    """把一个**文件**候选钉在 `root` 之内：判它的**父目录**，回拼好的路径。

    与 `contained_path` 的差别只有一条，但这条不认就会把功能判死：
    **绝不 realpath 文件本身**。`venv/bin/python` 在 POSIX 上就是一条指向
    基础解释器的软链接（`/opt/homebrew/.../python3.13`），跟着它走的话
    **每一个** venv 都会被判成「在项目外」。

    目录不是软链接，判目录既挡得住 `..` 逃逸与「软链接目录指到根外」，
    又不会把合法的解释器误伤掉。这个坑本模块踩过两次（`project_relative`
    的注释记过第一次），所以收成一个函数——第三个调用点直接用它。
    """
    rel = Path(candidate)
    holder = contained_path(root, rel.parent if rel.name else rel)
    if holder is None:
        return None
    return str(Path(holder) / rel.name) if rel.name else holder


def _within(root: Path, path: Path) -> bool:
    """`path` 是否在 `root` 之内（**按 realpath 判**）。

    按 realpath 而不是按字符串前缀：项目里放一条指向别处的软链接
    （`.venv -> ~/envs/paper`）时，字符串看着在项目内，实体在项目外。
    发现的范围必须是用户交给 Tavotto 的那棵目录树，不能顺着软链接跳出去。
    """
    try:
        real_root = root.resolve(strict=False)
        real_path = path.resolve(strict=False)
    except OSError:
        return False
    return real_path == real_root or real_root in real_path.parents


def discover(figures_dir: str | Path, script: str | None = None) -> list[str]:
    """从脚本所在目录逐级向上找到项目根，收集本地 venv 目录。

    返回**按优先级排好序**的 venv 目录路径（可能为空）。规则（写进 ADR 与
    用例，不许随实现漂移）：

    1. 离脚本最近的那一层优先（`paper/src/plots/.venv` 压过 `paper/.venv`）；
    2. 同一层里按 `.venv` → `venv` → `env`；
    3. 仍然并列时按规范化路径字典序——只是为了「同一台机器上每次都给同一个
       答案」，不是什么语义。

    **搜索范围严格限制在项目根内**：不上溯到项目之外（那是别人的项目），
    不顺软链接跳出去（`_within`）。项目根就是 Tavotto 打开的图库目录
    `figures_dir`——用户交给我们的边界只有这一条。
    """
    root = Path(figures_dir)
    start = (root / script).parent if script else root
    if not _within(root, start):
        # 脚本在项目外（理论上更早就该被 `script_path_outside_project` 拦下）
        start = root
    found: list[str] = []
    seen: set[str] = set()
    cur = start
    while True:
        layer: list[str] = []
        for name in VENV_DIRNAMES:
            cand = cur / name
            if not _within(root, cand):
                continue
            if interpreter_of(cand, root=root):
                layer.append(str(cand))
        for p in sorted(
            layer, key=lambda s: (VENV_DIRNAMES.index(Path(s).name), os.path.normcase(s))
        ):
            if p not in seen:
                seen.add(p)
                found.append(p)
        if _same_dir(cur, root) or not _within(root, cur.parent) or _same_dir(cur.parent, cur):
            break
        cur = cur.parent
    return found


def _same_dir(a: Path, b: Path) -> bool:
    try:
        return a.resolve(strict=False) == b.resolve(strict=False)
    except OSError:
        return os.path.normcase(str(a)) == os.path.normcase(str(b))


# --------------------------------------------------------------- 体检
#: 体检脚本。**在目标解释器里跑**，只用标准库 + matplotlib，输出单行 JSON。
#:
#: `import figcapture, manifest, overrides` 那一段是本轮最关键的一条检查：
#: 它证明这个环境**不需要安装 Tavotto 本体**也能起 worker——`engine/worker.py`
#: 是 `sys.path.insert(0, HERE)` 的平铺 import，Tavotto 自己把 worker 代码
#: 交给用户的解释器执行，绝不往用户 venv 里 pip install 任何东西。
_PROBE_SRC = r"""
import json, platform, sys
out = {"executable": sys.executable, "prefix": sys.prefix,
       "python_version": platform.python_version(),
       "version_info": list(sys.version_info[:3]),
       "arch": platform.machine(), "matplotlib_version": None,
       "tavotto_worker_ok": False, "requested_module": None,
       "requested_module_ok": None, "error": None}
engine_dir = sys.argv[1]
module = sys.argv[2] if len(sys.argv) > 2 else ""
try:
    import matplotlib
    matplotlib.use("Agg")
    out["matplotlib_version"] = matplotlib.__version__
except Exception as exc:
    out["error"] = "matplotlib: %s" % exc
if out["matplotlib_version"]:
    sys.path.insert(0, engine_dir)
    try:
        import figcapture, manifest, overrides  # noqa: F401
        out["tavotto_worker_ok"] = True
    except Exception as exc:
        out["error"] = "worker: %s" % exc
if module:
    out["requested_module"] = module
    try:
        __import__(module)
        out["requested_module_ok"] = True
    except Exception:
        out["requested_module_ok"] = False
sys.stdout.write(json.dumps(out))
"""


def probe_environment(python: str, module: str | None = None) -> dict:
    """在候选解释器里跑一次体检，回机器可读结构。

    只 import、不执行用户脚本、不装任何东西。`module` 给了就顺带确认那个包
    在这个环境里真的 import 得到——**「找到了 .venv」不等于「它能解决问题」**，
    没这一步就会把用户从一个报错切到另一个报错。

    失败一律返回 `ok=False` + `code`，绝不抛异常：调用方在渲染主路径上，
    体检失败只该退回原来的错误，不该让整个请求 500。
    """
    if module and not valid_module_name(module):
        # 不合形状的名字连体检都不做（注入面），当作「没法确认」。
        module = None
    engine_dir = str(Path(__file__).resolve().parent)
    argv = [python, "-I", "-c", _PROBE_SRC, engine_dir]
    if module:
        argv.append(module)
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=PROBE_TIMEOUT_S,
            stdin=subprocess.DEVNULL,
            creationflags=runtime.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        # 起不来：bad executable format（venv 建在另一个架构上）、被杀毒
        # 隔离、动态库缺失、venv 的 home 指向一个已经删掉的解释器……
        return {"ok": False, "code": ERROR_UNUSABLE, "python": python, "detail": str(exc)[:400]}
    if proc.returncode != 0:
        return {
            "ok": False,
            "code": ERROR_UNUSABLE,
            "python": python,
            "detail": (proc.stderr or "").strip()[:400],
        }
    try:
        info = json.loads(proc.stdout.strip() or "{}")
    except ValueError:
        return {
            "ok": False,
            "code": ERROR_UNUSABLE,
            "python": python,
            "detail": (proc.stdout or "").strip()[:400],
        }

    info["python"] = python
    version = tuple(info.get("version_info") or ())[:2]
    if not version or not (PYTHON_MIN <= version < PYTHON_MAX_EXCLUSIVE):
        info.update(ok=False, code=ERROR_UNSUPPORTED_PYTHON, support=SUPPORT_UNSUPPORTED)
        return info
    if not info.get("matplotlib_version") or not info.get("tavotto_worker_ok"):
        # matplotlib 起不来 = 它不是一个绘图环境；worker 模块 import 不了 =
        # 它跑不了 Tavotto（多半是 numpy 缺失或版本对不上）。两者都不该
        # 无感切过去，但要分开报——用户的动作完全不同。
        info.update(ok=False, code=ERROR_NO_MATPLOTLIB)
        return info
    if module and info.get("requested_module_ok") is False:
        info.update(ok=False, code=ERROR_MODULE_MISSING)
        return info
    info.update(ok=True, code="", support=support_status(version, info.get("matplotlib_version")))
    return info


def _mpl_tuple(text: str | None) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in str(text or "").split(".")[:2]:
        digits = "".join(c for c in chunk if c.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def support_status(version: tuple, mpl_version: str | None) -> str:
    """这个环境属于哪一档支持等级。

    Python 与 matplotlib 的边界性质不同，**刻意不对称**：

    * Python 在支持区间外 → `unsupported`，不自动使用。语言版本是硬边界
      （语法、ABI、我们从没跑过的 CI 组合）。
    * matplotlib 在钉版区间外但能 import → `unverified_but_compatible`，
      照用，但如实标注。用户装的 3.12 多半是好的，拒绝它等于把一个能出图的
      环境判死；而 Tavotto 的视觉基线只在钉版上重生成过，所以也不能声称验证过。
    """
    if not (PYTHON_MIN <= tuple(version)[:2] < PYTHON_MAX_EXCLUSIVE):
        return SUPPORT_UNSUPPORTED
    mpl = _mpl_tuple(mpl_version)
    if tuple(version)[:2] not in PYTHON_TESTED:
        return SUPPORT_UNVERIFIED
    if not mpl or not (MPL_MIN <= mpl < MPL_MAX_EXCLUSIVE):
        return SUPPORT_UNVERIFIED
    return SUPPORT_VERIFIED


# --------------------------------------------------- 项目级状态（记住选择）
#: 项目设置里存环境决策的键（`config.project_settings(<项目路径>)` 下）。
SETTINGS_KEY = "environment"

_lock = threading.Lock()
#: 进程内解析缓存：项目路径 → 解释器路径（`""` = 这个项目找过了，没有）。
#: 每次 override / export 都重新起一个 Python 去体检是不可接受的开销。
_resolved: dict[str, str] = {}
#: 本次进程里已经为哪些 (项目, 脚本) 自动切换过。**一次 build 最多自动切一次**
#: ——没有这条，「内置缺包 → 切 venv → venv 也缺 → 切回内置」会来回打转。
_attempted: set[tuple[str, str]] = set()


def _key(figures_dir: str | Path) -> str:
    return os.path.normcase(str(Path(figures_dir).resolve(strict=False)))


def remembered(figures_dir: str | Path) -> str | None:
    """这个项目上次定下来的解释器（先看进程缓存，再看项目设置）。

    持久化的是**项目相对路径**（`.venv/bin/python`）而不是绝对路径：项目
    整个目录被挪走、换台机器同步过去、从 `~/paper` 变成 `/Volumes/T7/paper`
    时，绝对路径当场失效而相对路径照样成立。用户显式挑的项目外解释器
    （conda 环境）才存绝对路径——那本来就不跟着项目走。
    """
    key = _key(figures_dir)
    with _lock:
        cached = _resolved.get(key)
    if cached is not None:
        return cached or None
    stored = (config.project_settings(str(Path(figures_dir))) or {}).get(SETTINGS_KEY)
    if not isinstance(stored, dict):
        return None
    rel = stored.get("python_relative")
    absolute = stored.get("python")
    path = str(Path(figures_dir) / rel) if rel else (absolute or "")
    if not path or not Path(path).is_file():
        return None
    with _lock:
        _resolved[key] = path
    return path


def project_relative(figures_dir: str | Path, python: str) -> str:
    """解释器在项目内时的相对路径；在项目外（或算不出来）回空串。

    **绝不 `resolve()` 解释器本身**：`.venv/bin/python` 在 POSIX 上就是一条
    指向基础解释器的软链接（`/opt/homebrew/.../python3.13`），跟着它走的话
    每一个项目 venv 都会被判成「在项目外」，于是持久化的永远是绝对路径——
    项目一挪地方，记住的决策当场失效。这里要的是**布局意义上**的相对位置。
    """
    try:
        rel = os.path.relpath(os.path.abspath(str(python)), os.path.abspath(str(figures_dir)))
    except (OSError, ValueError):
        return ""
    if os.path.isabs(rel) or rel.split(os.sep)[0] == os.pardir:
        return ""
    return rel


def remember(
    figures_dir: str | Path,
    python: str,
    *,
    automatic: bool,
    trigger: str = "",
    module: str = "",
    health: dict | None = None,
) -> None:
    """记住这个项目该用哪个解释器（进程缓存 + 项目设置持久化）。

    **绝不写全局 `worker.python` 设置**：那会让 A 项目找到的 `.venv` 变成
    B 项目的渲染环境——两个项目各有各的环境正是本轮要解决的事。
    """
    key = _key(figures_dir)
    with _lock:
        _resolved[key] = python
    root = Path(figures_dir)
    payload = {"automatic": bool(automatic), "trigger": trigger or "", "module": module or ""}
    # 把体检当时的事实一并存下来：诊断包要回答「为什么用了这个 Python」，
    # 而生成诊断包时**不该**再去起一个解释器问一遍（那要几十秒，用户点的是
    # 「导出诊断包」不是「重新体检」）。
    for key in ("python_version", "matplotlib_version", "support"):
        if (health or {}).get(key):
            payload[key] = str(health[key])
    rel = project_relative(root, python)
    if rel:
        payload["python_relative"] = rel
    else:
        # 用户显式挑的项目外解释器（conda 环境）——它本来就不跟着项目走。
        payload["python"] = str(python)
    try:
        config.set_project_settings(str(root), {SETTINGS_KEY: payload})
    except OSError as exc:  # 配置目录只读/满：记不住不该让渲染失败
        LOG.warning("项目环境决策未能持久化: %s", exc)


def forget(figures_dir: str | Path) -> None:
    """清掉这个项目的环境决策（用户改回内置环境时）。"""
    key = _key(figures_dir)
    with _lock:
        _resolved.pop(key, None)
        for k in [k for k in _attempted if k[0] == key]:
            _attempted.discard(k)
    try:
        config.set_project_settings(str(Path(figures_dir)), {SETTINGS_KEY: None})
    except OSError:
        pass


def reset_cache(figures_dir: str | Path | None = None) -> None:
    """丢弃进程内解析缓存（改了设置、装完环境、测试之间）。"""
    key = _key(figures_dir) if figures_dir is not None else None
    with _lock:
        if key is None:
            _resolved.clear()
            _attempted.clear()
        else:
            _resolved.pop(key, None)
            for k in [k for k in _attempted if k[0] == key]:
                _attempted.discard(k)


def mark_attempted(figures_dir: str | Path, script: str) -> bool:
    """登记「这一对已经自动切换过一次」；已经登记过回 False。

    重试上限的实现面。用户手动点重试会调 `reset_cache()`，于是可以重新来一轮。
    """
    key = (_key(figures_dir), script)
    with _lock:
        if key in _attempted:
            return False
        _attempted.add(key)
        return True


def resolve_for_missing_dependency(figures_dir: str | Path, script: str, module: str) -> dict:
    """内置环境缺 `module` 时，看看这个项目自己的 venv 能不能顶上。

    回 `{"ok": True, "python": …, "venv": …, "health": {…}}`，或
    `{"ok": False, "code": …, …}`（code 见模块头）。**只做判断不改状态**：
    记住决策与作废 worker 由 `pool` 完成——那边才是解释器决策的权威。
    """
    candidates = discover(figures_dir, script)
    if not candidates:
        return {"ok": False, "code": ERROR_NOT_FOUND, "module": module}
    healthy: list[dict] = []
    failures: list[dict] = []
    for venv in candidates:
        python = interpreter_of(venv)
        if not python:
            continue
        health = probe_environment(python, module)
        health["venv"] = venv
        if health.get("ok"):
            healthy.append(health)
            # 第一个健康的就够了：候选已经按「离脚本最近」排好，继续体检
            # 剩下的只是白白多起几个解释器（每个最多 60s）。
            break
        failures.append(health)
    if not healthy:
        # 全都不健康：把**最靠前那个**的失败原因交出去（它是我们本来会选的
        # 那个，对用户最有解释力），而不是笼统一句「没有可用环境」。
        first = failures[0] if failures else {"code": ERROR_NOT_FOUND}
        return {
            "ok": False,
            "code": first.get("code", ERROR_UNUSABLE),
            "module": module,
            "health": first,
            "venv": first.get("venv", ""),
            "candidates": candidates,
        }
    best = healthy[0]
    return {
        "ok": True,
        "python": best["python"],
        "venv": best["venv"],
        "health": best,
        "module": module,
        "candidates": candidates,
    }


def state(figures_dir: str | Path) -> dict:
    """这个项目当前的环境决策（给环境状态 API 与诊断包）。

    只读已记住的东西，**不做任何体检**（那要起子进程）：界面刷新不该为了
    贴个版本号卡住。
    """
    stored = (config.project_settings(str(Path(figures_dir))) or {}).get(SETTINGS_KEY)
    stored = stored if isinstance(stored, dict) else {}
    python = remembered(figures_dir)
    out = {
        "python": python or "",
        "automatic": bool(stored.get("automatic")),
        "trigger": stored.get("trigger") or "",
        "module": stored.get("module") or "",
        "python_version": stored.get("python_version") or "",
        "matplotlib_version": stored.get("matplotlib_version") or "",
        "support": stored.get("support") or "",
    }
    if python:
        out["python_relative"] = project_relative(figures_dir, python)
    return out
