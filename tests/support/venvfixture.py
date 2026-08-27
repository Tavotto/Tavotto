"""建一个**像用户项目那样**的真 venv——两组用例共用的唯一出处。

`test_project_env.py`（Session 7）与 `test_dependency_repair*.py`（7B）都要
「项目自己带的 `.venv`」这个夹具。创建那一步有两个不显眼但会决定成败的细节，
写两份的话其中一份迟早漏掉一个：

1. **`--system-site-packages`**：matplotlib 直接用宿主那份，CI 不必联网装
   几百 MB 的科学栈。
2. **必须把宿主的 `tavotto` 遮掉**（见下）。
"""
import subprocess
from pathlib import Path

#: 遮蔽用的替身。真实用户的 venv 里就是**没有** Tavotto，`import tavotto`
#: 抛的正是 ModuleNotFoundError——替身照着那个形状抛，而不是抛个别的。
_MASK = (
    '# 由 tests/support/venvfixture.py 写入：把宿主解释器上的 Tavotto 遮掉，\n'
    '# 让这个 venv 长得跟真实用户的项目环境一样（那里面没有 Tavotto）。\n'
    'raise ModuleNotFoundError("No module named \'tavotto\'", name="tavotto")\n'
)


def site_packages(venv: Path) -> Path:
    found = (sorted(venv.glob("lib/python*/site-packages"))
             or sorted(venv.glob("Lib/site-packages")))
    return found[0]


def mask_tavotto(venv: Path) -> Path:
    """把宿主解释器上的 `tavotto` 从这个 venv 里遮掉。

    **为什么必须做**：`--system-site-packages` 会把**基础解释器**的
    site-packages 带进来，而 CI 的 backend-fast 正是
    `pip install -e ".[dev]"` 装进那个基础解释器的。于是夹具 venv 里
    `import tavotto` 成功——「项目 venv 里没有 Tavotto 也能起 worker」那条
    用例的**前提当场失效**（它在 CI 上红过一次，就是这么红的：本地跑 pytest
    的是 `.venv`，而**从 venv 建 venv 继承的是基础解释器的 site-packages，
    不是父 venv 的**，所以本地永远复现不出来）。

    做法是往 venv **自己的** site-packages 写一个同名替身——它排在继承来的
    那份**前面**（`prove_shadow` 用 matplotlib 这个确实存在于上游的名字
    验过：遮蔽前 OK、遮蔽后 ImportError、移除后又 OK）。

    worker 那条路不受影响：`engine/worker.py` 是 `sys.path.insert(0, HERE)`
    的**平铺** import（`figcapture` / `manifest` / `overrides`），从头到尾
    不 `import tavotto`——那正是本轮要证明的事。
    """
    shim = site_packages(venv) / "tavotto.py"
    shim.write_text(_MASK, encoding="utf-8")
    return shim


def make_project_venv(root: Path, name: str = ".venv", *,
                      python: str | None = None) -> Path:
    """在 `root` 下建一个能执行、且**不含 Tavotto** 的 venv。"""
    venv = root / name
    subprocess.run([python, "-m", "venv", "--system-site-packages", str(venv)],
                   check=True, capture_output=True, timeout=300)
    mask_tavotto(venv)
    return venv
