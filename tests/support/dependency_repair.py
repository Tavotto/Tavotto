"""受控依赖修复（ADR 0019）两组用例共用的 fixture 与工具。

分出来是因为「单元 / 分支」那组与「真安装端到端」那组要用同一套东西：
同一个 fixture 包、同一个手工 wheel、同一份状态清理。抄两份的话，其中一份
迟早与另一份漂移，而漂移最先表现为**假绿**（一组用例用着另一套前提）。

以 pytest 插件的形式挂进去（`pytest_plugins = ("support.dependency_repair",)`），
不是 `from ... import fixture`——后者会让 fixture 名与用例参数名互相遮蔽。
"""

import base64
import hashlib
import json
import subprocess
import time
import zipfile
from pathlib import Path

import pytest

from support import venvfixture
from tavotto.engine import deprepair, managedenv, pool as engine_pool, projectenv

try:
    WORKER_PY = engine_pool.find_worker_python()
except engine_pool.WorkerError:
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

#: 只存在于测试造出来的 wheel 里的纯 Python 包。名字刻意不像任何真包——
#: 它必须在宿主解释器里 import 不到，否则整组用例会假绿。
FIXTURE_IMPORT = "tavotto_test_missing_dep"
FIXTURE_DIST = "tavotto-test-missing-dep"
FIXTURE_VERSION = "1.0"

SCRIPT = f"""\
import {FIXTURE_IMPORT}          # 只有装过修复包的环境里才有
import matplotlib.pyplot as plt

fig, ax = plt.subplots()
ax.plot([1, 2, 3], [4, 5, 6])
ax.set_title("Original Title")     # 修好之后要能改它（端到端的编辑那一段）
fig.savefig("Fig1.pdf")
"""


# --------------------------------------------------------------- fixture
@pytest.fixture
def clean_state():
    """模块级状态（计划、轮次、已试过、解析缓存）用例之间必须清干净。

    留着上一个用例的结论，下一个用例会在「已经修过一轮」的状态下开始——
    轮次上限那条首当其冲变成假绿。**不设 autouse**：本模块是以插件形式挂
    进去的，autouse 会波及整个 session 里的每一条用例。各用例文件自己套一层
    三行的 autouse 包装。
    """
    deprepair.reset_state()
    projectenv.reset_cache()
    engine_pool.reset_worker_python()
    yield
    deprepair.reset_state()
    projectenv.reset_cache()
    engine_pool.reset_worker_python()


@pytest.fixture
def project(tmp_path):
    """图库目录 + 一个 import 了 fixture 包的脚本。

    退出前把项目关掉：走 `client` 的用例会 `open_project()`，那会给这个目录
    起 watcher，而目录里可能建着真 venv（几千个文件）——留着不收，整个
    pytest 进程剩下的时间都在监视一堆已经删掉的临时目录。
    """
    from tavotto import app as m

    figs = tmp_path / "figs"
    figs.mkdir()
    (figs / "figure.py").write_text(SCRIPT, encoding="utf-8")
    yield figs
    for pid in [p for p, ctx in list(m.PROJECTS.items()) if str(ctx.path) == str(figs)]:
        m.close_project(pid, wait=True)
    engine_pool.shutdown_all(str(figs), wait=True)


@pytest.fixture
def client():
    from tavotto import app as m

    m.app.config["TESTING"] = True
    return m.app.test_client()


@pytest.fixture
def wheelhouse(tmp_path, monkeypatch):
    """一个本地 wheel 仓库，并让 pip **只**从它取包。

    `PIP_FIND_LINKS` / `PIP_NO_INDEX` 是 pip 自己的配置——用它们而不是给
    `pip install` 加参数，正好也验证了「index 用那个环境自己的配置，Tavotto
    不覆盖也不绕过」这条决策：安装命令一个字节都不用为测试改动。
    """
    house = tmp_path / "wheelhouse"
    build_wheel(house)
    monkeypatch.setenv("PIP_FIND_LINKS", str(house))
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    return house


def _site_packages_of(python) -> "list[str]":
    """问**那个解释器自己**要它的 site-packages。

    不能从当前进程推——`base` 与跑测试的解释器不一定是同一个。
    """
    code = (
        "import json,site,sysconfig;"
        "print(json.dumps(sorted({*site.getsitepackages(), sysconfig.get_paths()['purelib']})))"
    )
    out = subprocess.run(
        [str(python), "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    if out.returncode != 0:
        return []
    return json.loads(out.stdout)


def _graft_host_site_packages(base, new_python) -> "tuple[bool, str]":
    """把宿主的 site-packages 挂进刚建好的环境（`.pth`）。

    判据的主语是「新环境能不能 import matplotlib」，所以挂完**当场验一次**
    ——只写文件不验证的话，路径错了会一路静默到用例的断言上，而那时的报错
    指向的是被测代码。
    """
    host = _site_packages_of(base)
    if not host:
        return False, f"问不出 {base} 的 site-packages"
    target_sites = _site_packages_of(new_python)
    if not target_sites:
        return False, f"问不出 {new_python} 的 site-packages"
    pth = Path(target_sites[0]) / "_tavotto_test_host_stack.pth"
    pth.parent.mkdir(parents=True, exist_ok=True)
    pth.write_text("\n".join(host) + "\n", encoding="utf-8")
    probe = subprocess.run(
        [str(new_python), "-c", "import matplotlib"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    if probe.returncode != 0:
        return False, f"挂了宿主 site-packages 仍 import 不到 matplotlib：{probe.stderr}"
    return True, ""


@pytest.fixture
def offline_managed_env(monkeypatch):
    """让受管环境**能在离线 CI 里建出来**，同时说清楚这里放宽了什么。

    生产上新建的受管环境是严格隔离的（不带 `--system-site-packages`）并且要
    `pip install matplotlib`——那一步必然联网。CI 不联网，所以这组用例里：

    * 基础栈换成空表（不下载任何东西）；
    * 新环境显式挂上**宿主解释器的 site-packages**（一个 `.pth`），matplotlib
      用宿主那份，于是 worker 自检仍然是**真跑一次**。

    挂 `.pth` 而不是用 `--system-site-packages`：后者继承的是 `base` 的**基础
    解释器**，而不是 `base` 自己。`base` 本身是 venv 时（CI 上一直如此——
    整套测试就跑在一个 venv 里，matplotlib 装在那个 venv 里），新环境拿到的
    是系统解释器的 site-packages，里面没有 matplotlib，于是 worker 自检失败、
    修复被判 `dependency_import_still_failed`。这条前提**静默不成立**：本机
    基础解释器碰巧装了 matplotlib 就一路绿，实验室 runner 上就从没绿过。

    被放宽的那两条**另有单元用例逐字节钉住**
    （`test_dependency_repair.py::test_managed_venv_creation_is_isolated_and_minimal`）
    ——否则「离线 fixture 好使」会掩盖「生产上建出来的环境不隔离」。
    这组用例真正证明的是：位置、项目作用域、**真 pip 把包装进了那个环境**、
    `sys.prefix` 是它、manifest 记账、重建装得回去。
    """
    monkeypatch.setattr(managedenv, "BASE_PACKAGES", ())
    original = managedenv.create_venv

    def _with_host_stack(target_project, base):
        root = managedenv.venv_dir(target_project)
        target = managedenv.venv_python(target_project)
        if target.is_file():
            return True, ""
        root.parent.mkdir(parents=True, exist_ok=True)
        out = subprocess.run(
            [base, "-m", "venv", str(root)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
        if not target.is_file():
            return False, out.stderr
        ok, why = _graft_host_site_packages(base, target)
        return ok, why or out.stderr

    assert original is managedenv.create_venv  # 换的是同一个出处
    monkeypatch.setattr(managedenv, "create_venv", _with_host_stack)
    monkeypatch.setattr(deprepair, "_base_python", WORKER_PY)
    monkeypatch.setattr(deprepair, "_base_python_known", True)


# --------------------------------------------------------------- 工具
def build_wheel(
    dest: Path,
    *,
    name: str = FIXTURE_DIST,
    import_name: str = FIXTURE_IMPORT,
    version: str = FIXTURE_VERSION,
) -> Path:
    """手工造一个纯 Python wheel（不联网、不需要 build backend）。

    wheel 就是一个约定好目录结构的 zip。自己拼出来比 `pip wheel` 快得多，
    也不需要网络——而「不联网」正是这组用例能进 CI 的前提。
    """
    dest.mkdir(parents=True, exist_ok=True)
    dist = name.replace("-", "_")
    info = f"{dist}-{version}.dist-info"
    payload = {
        f"{import_name}.py": f'VALUE = 42\nNAME = "{import_name}"\n',
        f"{info}/METADATA": (
            f"Metadata-Version: 2.1\nName: {name}\n"
            f"Version: {version}\n"
            f"Summary: Tavotto test fixture\n"
        ),
        f"{info}/WHEEL": (
            "Wheel-Version: 1.0\nGenerator: tavotto-tests\n"
            "Root-Is-Purelib: true\nTag: py3-none-any\n"
        ),
    }
    records = []
    for path, text in payload.items():
        raw = text.encode("utf-8")
        digest = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode("ascii")
        records.append(f"{path},sha256={digest},{len(raw)}")
    records.append(f"{info}/RECORD,,")
    payload[f"{info}/RECORD"] = "\n".join(records) + "\n"
    whl = dest / f"{dist}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(whl, "w") as z:
        for path, text in payload.items():
            z.writestr(path, text)
    return whl


def real_venv(root: Path, *, name: str = ".venv") -> Path:
    """在 `root` 下建一个**能执行**、且不含 Tavotto 的 venv。

    创建细节（`--system-site-packages` 与遮蔽宿主的 Tavotto）在
    `support.venvfixture` 一处——两组用例共用同一份，免得其中一份漏掉遮蔽
    那一步之后在 CI 上才发现。
    """
    return venvfixture.make_project_venv(root, name, python=WORKER_PY)


site_packages = venvfixture.site_packages


def wait_for(
    plan_id: str,
    states=(deprepair.STATE_DONE, deprepair.STATE_FAILED, deprepair.STATE_CANCELLED),
    timeout: float = 600.0,
) -> dict:
    """等一个异步安装到终态。**轮询有上限**，超时就把最后状态摆出来。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        rec = deprepair.progress(plan_id)
        if rec.get("state") in states:
            return rec
        time.sleep(0.2)
    raise AssertionError(f"安装没有在 {timeout}s 内到终态: {deprepair.progress(plan_id)}")
