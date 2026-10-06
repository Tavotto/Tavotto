"""真 pip 配置用例只隔离 global 文件的发现，不替换解析器、CLI 或产品探针。

HOME / XDG_CONFIG_HOME 不隔离系统配置，真实 PIP_CONFIG_FILE 也不会屏蔽它。
这个临时 startup hook 只给已安装 pip 的测试解释器；missing-pip / pipx 的
用例不用它。已有 sitecustomize 先原样执行，隔离失败则子进程必须退出。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_STARTUP = """\
import importlib.machinery
import importlib.util
import os
import sys

try:
    here = os.path.realpath(os.path.dirname(__file__))
    search = [p for p in sys.path if os.path.realpath(p or os.curdir) != here]
    spec = importlib.machinery.PathFinder.find_spec("sitecustomize", search)
    if spec is not None:
        if spec.loader is None:
            raise RuntimeError("existing sitecustomize has no loader")
        original = importlib.util.module_from_spec(spec)
        sys.modules["sitecustomize"] = original
        spec.loader.exec_module(original)
        # Preserve the original module and its exports for imports after startup.

    from pip._internal import configuration
    discover = configuration.get_configuration_files

    def fixture_configuration_files():
        files = dict(discover())
        files[configuration.kinds.GLOBAL] = [_GLOBAL_FILE]
        return files

    configuration.get_configuration_files = fixture_configuration_files
    if configuration.get_configuration_files()[configuration.kinds.GLOBAL] != [_GLOBAL_FILE]:
        raise RuntimeError("pip global configuration isolation did not activate")
except BaseException as exc:
    # site.py 吞普通 Exception 并继续执行；SystemExit 才能在 startup 失败关闭。
    raise SystemExit("test pip global configuration isolation failed") from exc
"""


def isolated_pip_globals(
    environ: dict[str, str],
    root: Path,
    *,
    python: str = sys.executable,
    global_file: Path | None = None,
) -> dict[str, str]:
    """返回只给该测试子进程的环境，并真问目标解释器确认 hook 已激活。

    USER / SITE / PIP_CONFIG_FILE 与 PIP_* 全保留；各用例仍自己摆配置。
    global_file 只给正面对照摆合成系统配置；缺省是测试自己的空 global 文件。
    不适用于忽略 PYTHONPATH / site 的 -E、-I、-S，也不用于没有 pip 的解释器。
    """
    startup = root / "pip-isolation-startup"
    startup.mkdir(parents=True, exist_ok=True)
    if global_file is None:
        global_file = root / "pip-isolation-global" / ("pip.ini" if os.name == "nt" else "pip.conf")
        global_file.parent.mkdir(parents=True, exist_ok=True)
        global_file.touch(exist_ok=True)
    else:
        assert global_file.is_file(), "synthetic global configuration must already exist"
    (startup / "sitecustomize.py").write_text(
        f"_GLOBAL_FILE = {str(global_file.resolve())!r}\n" + _STARTUP, encoding="utf-8"
    )
    env = dict(environ)
    paths = [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p]
    env["PYTHONPATH"] = os.pathsep.join([str(startup.resolve()), *paths])
    probe = subprocess.run(
        [
            str(python),
            "-B",
            "-c",
            "import json; from pip._internal import configuration as c; "
            "print(json.dumps(c.get_configuration_files()[c.kinds.GLOBAL]))",
        ],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert probe.returncode == 0, f"pip global isolation startup failed: {probe.stderr}"
    assert json.loads(probe.stdout.strip().splitlines()[-1]) == [str(global_file.resolve())], (
        "target interpreter did not activate pip global isolation"
    )
    return env
