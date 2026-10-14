"""桌面拥有的运行遇到活图路由冲突(Codex #1022 P1):不许报成功,也不许发布被拒的描述符。"""

from __future__ import annotations

import pytest

from support import desktopkit as dk, nativekit
from tavotto import app as m
from tavotto.engine import nativesession, pool, preparation, runcodes

pytestmark = nativekit.needs_user_python

SCRIPT = dk.FIGURE_HEADER + "fig, ax = plt.subplots()\nax.plot([0, 1], [0, 1])\n"


def test_route_conflict_fails_the_run_and_ends_the_new_process(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "fig.py").write_text(SCRIPT, encoding="utf-8")

    # 另一条还活着的会话(这里用已到屏障的桌面会话扮演「终端里的那一条」)占着同一个 (项目, 脚本, stem)
    spec_launch = dk.launch_for(tmp_path, SCRIPT, name="fig.py")
    holder = dk.desktopproc.start(spec_launch)
    nativekit.wait_state(holder, [nativesession.BARRIER])
    holder.ensure_built()
    assert nativesession.REGISTRY.bind_assets(holder) == []
    holder.spec = None  # 不是本脚本的桌面运行:重新运行时的「旧图让位」不会收它,冲突必须由路由判

    plan = preparation.plan_for(
        project_id="p",
        project_root=str(proj),
        asset_id="",
        stem="",
        script="fig.py",
        entry="__main__",
        original_artifact=None,
        target=preparation.TARGET_SCRIPT,
        execution="native",
    )
    # 两条会话的路由键要对上:holder 的项目根是 tmp_path/proj,脚本相对路径同为 fig.py
    with pytest.raises(pool.WorkerError) as err:
        m._native_preparation_runner(plan)
    assert err.value.code == runcodes.NATIVE_ASSET_CONFLICT
    new = [s for s in nativesession.REGISTRY.list() if s is not holder]
    assert len(new) == 1
    dk.wait_until(lambda: new[0].owned.finished, what="新进程被结束")
    # 旧图仍是该路由的持有者,没有被抢走
    stem = holder.stems[0]
    assert nativesession.REGISTRY.route_for(str(proj), "fig.py", stem) is holder
    holder.cancel_owned()
