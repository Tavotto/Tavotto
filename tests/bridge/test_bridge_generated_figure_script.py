"""数据出图 v2 生成的「短脚本 + 旁边的公共模块」在 **native bridge**（用户自己的 Python）里也能 import 到同目录的公共模块。

判据的主语：**用户的解释器进程**里，cwd 与脚本目录**不是同一处**时，脚本所在目录是否在 `sys.path` 里
（safe worker 一侧由 `tests/test_ai_data_template.py` 的真 worker 对拍覆盖）。两条入口的约定相同：脚本目录在 `sys.path[0]`
（`docs/rules/backend/execution-entries.md`，与 `python 脚本.py` 逐字相同），所以生成的脚本不需要自己改 `sys.path`。
"""

from __future__ import annotations

import pytest

from support.bridgekit import write
from tavotto.engine import ai_data_figure as adf, ai_data_recipe as R, ai_data_template as T

pytestmark = pytest.mark.usefixtures("clean_env")


def test_native_bridge_imports_the_sibling_runtime_module(tmp_path, bridge_session):
    proj = tmp_path / "proj" / "tavotto_figures"
    elsewhere = tmp_path / "elsewhere"  # cwd 故意不是脚本目录
    elsewhere.mkdir()
    rows = ["g,v"] + [f"{g},{i + k}" for g in ("a", "b") for i in range(6) for k in (0, 1)]
    write(proj / "d.csv", "\n".join(rows) + "\n")
    entry = adf.profile_folder(proj).tables[0]
    recipe = R.FigureRecipe.from_dict(
        {
            "table": "d.csv",
            "family": "comparison",
            "layers": [
                {"mark": "jitter"},
                {"mark": "hline_summary", "summary": "mean_sd", "role": "overlay"},
            ],
            "x": "g",
            "y": ["v"],
            "why": "说明",
        }
    )
    assert R.validate_recipe(adf.profile_folder(proj), recipe, confirmed=R.Confirmed()) == []
    out = T.render_recipe_script(entry, recipe, data_rel="d.csv", stem="fig")
    assert "sys.path" not in out.script  # 不自己改 sys.path：两条入口都把脚本目录放在 sys.path[0]
    write(proj / "fig.py", out.script)
    (proj / out.runtime_name).write_bytes(out.runtime)
    with bridge_session(proj / "fig.py", cwd=str(elsewhere)) as sess:
        sess.wait_event("barrier")
        sess.ensure_built()
        man = sess.override("fig", [])["manifest"]
        got = {}
        for el in man["elements"]:
            fp = el.get("data_fingerprint")
            if isinstance(fp, dict):
                label = next(
                    (f.get("value") for f in el.get("editable", []) if f.get("prop") == "label"),
                    None,
                )
                got[label] = fp
        values = adf.inspect_data_source(proj / "d.csv")
        expected = T.expected_fingerprints(values, recipe)
        assert expected
        for e in expected:
            assert got[e["label"]]["parts"][e["part"]] == e["digest"], e["label"]
        sess.resume()
        sess.wait_event("exit")
