"""不依赖 pytest 的「离线世界」零件：造本地 wheel、让受管环境能在离线机器上建出来。

分出来是因为两个消费者要同一份：

* 单元 / 集成用例（`support.dependency_repair` 的 `offline_managed_env` 夹具，monkeypatch 版）；
* 真浏览器 e2e 的被测进程（`web/e2e/` 起 `tests/support/e2e_offline_app.py`，进程内直接 setattr）。
  CI 的 posix-e2e 只装了 Tavotto 与 matplotlib，没有 pytest——所以这里一个 `import pytest` 都不能有。
"""

import base64
import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

from tavotto.engine import depplan, deprepair, managedenv

#: 只存在于测试造出来的 wheel 里的纯 Python 包。名字刻意不像任何真包——
#: 它必须在宿主解释器里 import 不到，否则整组用例会假绿。
FIXTURE_IMPORT = "tavotto_test_missing_dep"
FIXTURE_DIST = "tavotto-test-missing-dep"
FIXTURE_VERSION = "1.0"


def _site_packages_of(python) -> "list[str]":
    """问**那个解释器自己**要它的 site-packages。

    不能从当前进程推——`base` 与跑测试的解释器不一定是同一个。
    """
    # 要的是**那个解释器实际 import 得到东西的地方**，不是「全局 site 目录」。
    # 漏了 user site 就会漏掉 `pip install --user` 装的科学栈（Debian/Ubuntu
    # 的系统 Python 上很常见）：探测那一步因为继承了 user site 而成功，挂进
    # 新环境的却一条都没有——而新 venv 把 user site 关掉了
    # （实测 `site.ENABLE_USER_SITE = False`），于是 matplotlib 消失。
    # `getsitepackages` 在个别 virtualenv 造的环境里不存在，取不到就跳过。
    code = (
        "import json,os,site,sysconfig\n"
        "p=set()\n"
        "g=getattr(site,'getsitepackages',None)\n"
        "if g: p.update(g())\n"
        "for k in ('purelib','platlib'): p.add(sysconfig.get_paths()[k])\n"
        "if getattr(site,'ENABLE_USER_SITE',False):\n"
        "    u=site.getusersitepackages()\n"
        "    p.update([u] if isinstance(u,str) else u)\n"
        "print(json.dumps(sorted(d for d in p if d and os.path.isdir(d))))\n"
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


def apply_offline_managed_env(setattr, base_python) -> None:
    """让受管环境**能在离线机器上建出来**（`setattr(obj, name, value)` 由调用方给：
    pytest 用 `monkeypatch.setattr`，e2e 的被测进程直接用内建 `setattr`）。放宽了什么、没放宽什么见
    `support.dependency_repair.offline_managed_env` 的说明。"""
    setattr(managedenv, "BASE_PACKAGES", ())
    # U04 起受管环境按代建（`create_generation_venv`），每一代的需求集合以 adapter 约束
    # （matplotlib / numpy）打底——离线 CI 里那两样来自宿主的 site-packages 而不是 pip，
    # 所以这里同样把 adapter 换成空表。生产上那两样各有单元用例逐字钉着
    # （`test_dependency_plan.py::test_adapter_requirements_mirror_the_worker_extra`）。
    setattr(depplan, "ADAPTER_REQUIREMENTS", ())
    # 新的一代「必然会有」的 distribution（`fresh_venv_facts(provided=)`）：生产上由 adapter 装进去，
    # 这里由上面的 `.pth` 嫁接给——计划照样不把 matplotlib / numpy 列进要装的集合
    setattr(depplan, "adapter_distributions", lambda: ("matplotlib", "numpy"))
    original = managedenv.create_venv
    original_generation = managedenv.create_generation_venv

    def _make_and_graft(root, target, base):
        try:
            root.parent.mkdir(parents=True, exist_ok=True)
        except (
            OSError
        ) as exc:  # 与真实 `create_generation_venv` 同一形状：建不了目录回 (False, 原因)
            return False, str(exc)
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

    def _with_host_stack(target_project, base):
        root = managedenv.venv_dir(target_project)
        target = managedenv.venv_python(target_project)
        if target.is_file():
            return True, ""
        return _make_and_graft(root, target, base)

    def _generation_with_host_stack(target_project, generation, base):
        root = managedenv.generation_dir(target_project, generation)
        target = managedenv.generation_python(target_project, generation)
        if root.exists():
            import shutil

            shutil.rmtree(root, ignore_errors=True)
        return _make_and_graft(root, target, base)

    assert original is managedenv.create_venv  # 换的是同一个出处
    assert original_generation is managedenv.create_generation_venv
    setattr(managedenv, "create_venv", _with_host_stack)
    setattr(managedenv, "create_generation_venv", _generation_with_host_stack)
    setattr(deprepair, "_base_python", base_python)
    setattr(deprepair, "_base_python_known", True)


def build_wheel(
    dest: Path,
    *,
    name: str = FIXTURE_DIST,
    import_name: str = FIXTURE_IMPORT,
    version: str = FIXTURE_VERSION,
    requires: tuple[str, ...] = (),
    provides_extras: tuple[str, ...] = (),
    body: str = "",
) -> Path:
    """手工造一个纯 Python wheel（不联网、不需要 build backend）。

    wheel 就是一个约定好目录结构的 zip。自己拼出来比 `pip wheel` 快得多，
    也不需要网络——而「不联网」正是这组用例能进 CI 的前提。

    `requires` 是 `Requires-Dist` 行（可带 `; extra == "x"` 的 marker——U04 用它造
    「extra 拉进另一个包」的形状），`provides_extras` 对应 `Provides-Extra`；`body` 是模块
    体里额外的源码（默认只有 `VALUE` / `NAME` 两个常量）。
    """
    dest.mkdir(parents=True, exist_ok=True)
    dist = name.replace("-", "_")
    info = f"{dist}-{version}.dist-info"
    metadata = (
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\nSummary: Tavotto test fixture\n"
    )
    for extra in provides_extras:
        metadata += f"Provides-Extra: {extra}\n"
    for req in requires:
        metadata += f"Requires-Dist: {req}\n"
    payload = {
        f"{import_name}.py": f'VALUE = 42\nNAME = "{import_name}"\n{body}',
        f"{info}/METADATA": metadata,
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
