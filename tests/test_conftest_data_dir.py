"""conftest 的兜底数据目录：会话里是个新建的临时目录，会话结束就不在了。

以前它从不删。U04 / U05 起真 venv + 真 pip 的用例往 `environments/` 里建受管环境，一次会话 600 MB
上下，系统临时目录里攒了 3 311 个、31 GB，整机 ENOSPC。`pytest_sessionfinish` 里的清理是别的用例
观察不到的（它们跑完了才轮到它），所以这里另起一个子进程跑一个最小会话，结束后看目录还在不在。
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
PROBE_ENV = "TAVOTTO_TEST_DATA_DIR_PROBE"


def test_data_dir_is_a_fresh_temp_dir_during_the_session():
    """会话里 `TAVOTTO_DATA_DIR` 指向一个真实存在的目录。

    被下面那条用例当子进程里的「内层会话」跑时（`TAVOTTO_TEST_DATA_DIR_PROBE` 指向一个文件），
    把这个路径写出去——外层据此断言「内层真的建了目录」，否则「结束后目录不在」对一个从没建过的
    目录恒真。
    """
    data_dir = pathlib.Path(os.environ["TAVOTTO_DATA_DIR"])
    assert data_dir.is_dir()
    probe = os.environ.get(PROBE_ENV)
    if probe:
        pathlib.Path(probe).write_text(str(data_dir), encoding="utf-8")


def test_session_end_removes_the_fallback_data_dir(tmp_path):
    """子进程跑一个只含上面那条用例的会话：它在我们给的临时目录下建了兜底目录，结束后目录不在。"""
    tmp_root = tmp_path / "tmp"
    tmp_root.mkdir()
    probe = tmp_path / "data_dir.txt"
    env = dict(os.environ)
    # 外层会话已经把自己的兜底目录写进 TAVOTTO_DATA_DIR（conftest 是 setdefault），去掉它内层才会新建。
    env.pop("TAVOTTO_DATA_DIR", None)
    # tempfile 在 POSIX 认 TMPDIR、在 Windows 认 TMP / TEMP——三个都指过去，内层的 mkdtemp 才落在这里。
    env.update(TMPDIR=str(tmp_root), TMP=str(tmp_root), TEMP=str(tmp_root))
    env[PROBE_ENV] = str(probe)
    node = f"{pathlib.Path(__file__).relative_to(REPO).as_posix()}::test_data_dir_is_a_fresh_temp_dir_during_the_session"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", node],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    created = pathlib.Path(probe.read_text(encoding="utf-8"))
    # 内层真的在我们的临时根下建了自己的兜底目录（不是沿用别人的、也不是没建）……
    assert created.parent == tmp_root, created
    assert created.name.startswith("tavotto-data-"), created
    # ……而会话一结束它就不在了，临时根下也没有别的残留。
    assert not created.exists(), created
    assert sorted(tmp_root.glob("tavotto-data-*")) == []
