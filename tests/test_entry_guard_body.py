"""`if __name__ == "__main__":` 守卫里多做了事时按 `__main__` 跑（Guardian cl-b941e8b82f）。

函数入口是 `getattr(module, entry)()`：模块不以 `__main__` 名义执行，守卫整段不跑。守卫里 `main()` 之前的
`plt.style.use` / `rcParams` / 种子 / 全局赋值于是全丢，图静默按默认样式出。判据唯一出处
`discover.guard_does_more_than`，三处消费同一把尺：

* 静态起草 `_entry_of`、试运行候选 `entry_candidates_of`：守卫多做了事 -> `__main__` 排第一；
  守卫只有 `main()` / `sys.exit(main())` / `raise SystemExit(main())` / `plt.show()` / `pass` -> 保持函数入口；
* 登记入口对照源码 `entryfresh.is_stale`：已登记 `entry=main`、守卫调了它还多做了事 -> 改登记为 `__main__`；
  守卫没调到登记的函数（用户手选了别的）不动；
* 端到端（真 worker）：守卫里改 rcParams 的脚本，Tavotto 出的图与 `python script.py` 一致。
"""

import ast
import json

import pytest

from tavotto.engine import discover, entryfresh, pool as engine_pool, probe

HEAD = "import sys\nimport matplotlib.pyplot as plt\n\n\ndef main():\n    plt.plot([1, 2, 3])\n    plt.savefig('a.pdf')\n\n\n"


def _guarded(*body: str, orelse: str | None = None) -> str:
    text = HEAD + 'if __name__ == "__main__":\n' + "".join(f"    {line}\n" for line in body)
    if orelse is not None:
        text += f"else:\n    {orelse}\n"
    return text


def _decide(source: str) -> tuple[str | None, list[str]]:
    tree = ast.parse(source)
    return discover._entry_of(tree), discover.entry_candidates_of(tree)


EXTRA_BODIES = [
    pytest.param(("plt.style.use('grayscale')", "main()"), id="style-before"),
    pytest.param(("plt.rcParams['lines.linewidth'] = 3", "main()"), id="rcparams-before"),
    pytest.param(("main()", "plt.gcf().suptitle('x')"), id="edit-after"),
    pytest.param(("import numpy as np", "np.random.seed(0)", "main()"), id="seed"),
    pytest.param(("main()", "main()"), id="called-twice"),
    pytest.param(("plot_all()",), id="calls-another-function"),
    pytest.param(("main(sys.argv[1:])",), id="main-with-args"),
]

SAME_BODIES = [
    pytest.param(("main()",), id="bare"),
    pytest.param(("sys.exit(main())",), id="sys-exit"),
    pytest.param(("raise SystemExit(main())",), id="raise-systemexit"),
    pytest.param(("main()", "plt.show()"), id="show-after"),
]


class TestDrafting:
    @pytest.mark.parametrize("body", EXTRA_BODIES)
    def test_guard_doing_more_runs_as_main_module(self, body):
        entry, candidates = _decide(_guarded(*body))
        assert entry == "__main__"
        assert candidates[0] == "__main__"

    @pytest.mark.parametrize("body", SAME_BODIES)
    def test_guard_that_only_calls_the_entry_keeps_the_function(self, body):
        entry, candidates = _decide(_guarded(*body))
        assert entry == "main"
        assert candidates[:2] == ["main", "__main__"]

    def test_an_else_branch_counts_as_more(self):
        assert _decide(_guarded("main()", orelse="pass"))[0] == "__main__"

    def test_reversed_comparison_is_the_same_guard(self):
        source = HEAD + 'if "__main__" == __name__:\n    plt.style.use("grayscale")\n    main()\n'
        assert _decide(source)[0] == "__main__"

    def test_no_guard_keeps_the_function(self):
        assert _decide(HEAD)[0] == "main"

    def test_other_name_comparisons_are_not_judged(self):
        source = HEAD + 'if __name__ != "__main__":\n    plt.style.use("grayscale")\n'
        assert _decide(source)[0] == "main"

    def test_custom_entry_tier_uses_the_same_predicate(self):
        source = (
            "import matplotlib.pyplot as plt\n\n\ndef plot():\n    plt.plot([1])\n    plt.savefig('a.pdf')\n\n\n"
            'if __name__ == "__main__":\n    plt.style.use("grayscale")\n    plot()\n'
        )
        assert _decide(source)[0] == "__main__"

    def test_probe_candidates_put_main_module_first(self, tmp_path):
        (tmp_path / "s.py").write_text(
            _guarded("plt.style.use('grayscale')", "main()"), encoding="utf-8"
        )
        assert probe.entry_candidates(tmp_path, "s.py")[0] == "__main__"
        assert probe.entry_candidates(tmp_path, "s.py", static_only=True)[0] == "__main__"


def _registered(tmp_path, source: str, entry: str = "main"):
    figs = tmp_path / "figs"
    figs.mkdir()
    (figs / "s.py").write_text(source, encoding="utf-8")
    body = {"version": 1, "scripts": {"s.py": {"entry": entry, "stems": ["a"], "notes": "keep"}}}
    (figs / "tavotto_registry.json").write_text(json.dumps(body), encoding="utf-8")
    return figs


def _entry(figs) -> str:
    return json.loads((figs / "tavotto_registry.json").read_text(encoding="utf-8"))["scripts"][
        "s.py"
    ]


class TestRegisteredEntry:
    def test_registered_main_wrapped_by_a_busy_guard_is_rewritten(self, tmp_path):
        figs = _registered(tmp_path, _guarded("plt.style.use('grayscale')", "main()"))
        assert entryfresh.is_stale(figs / "s.py", "main")
        assert entryfresh.reconcile(figs, "s.py") == {"s.py": "__main__"}
        assert _entry(figs) == {"entry": "__main__", "stems": ["a"], "notes": "keep"}
        assert entryfresh.reconcile(figs, "s.py") == {}  # 稳定：`__main__` 不再过时

    def test_guard_that_only_calls_main_is_not_stale(self, tmp_path):
        figs = _registered(tmp_path, _guarded("sys.exit(main())"))
        assert not entryfresh.is_stale(figs / "s.py", "main")
        assert entryfresh.reconcile(figs, "s.py") == {}

    def test_a_hand_picked_function_the_guard_does_not_call_is_left_alone(self, tmp_path):
        source = _guarded("plt.style.use('grayscale')", "main()") + "\n\ndef other():\n    pass\n"
        figs = _registered(tmp_path, source, entry="other")
        assert not entryfresh.is_stale(figs / "s.py", "other")


RCPARAMS_SCRIPT = """\
import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots(figsize=(3, 2))
    ax.plot([1, 2, 3])
    ax.set_title(f"lw={plt.rcParams['lines.linewidth']}")
    fig.savefig("guard_style.png")


if __name__ == "__main__":
    plt.rcParams["lines.linewidth"] = 3.0
    main()
"""

try:
    WORKER_PY = engine_pool.find_worker_python()
except engine_pool.WorkerError:
    WORKER_PY = None


@pytest.mark.skipif(WORKER_PY is None, reason="找不到装有 matplotlib 的解释器")
def test_guard_rcparams_reach_the_tavotto_figure(tmp_path):
    """`python script.py` 的标题是 lw=3.0；修复前 Tavotto 走 `main` 入口，守卫没跑，标题是默认 lw=1.5。"""
    (tmp_path / "s.py").write_text(RCPARAMS_SCRIPT, encoding="utf-8")
    entry = discover.analyze_script(tmp_path / "s.py", tmp_path)["entry"]
    assert entry == "__main__"
    worker = engine_pool.one_shot("s.py", str(tmp_path), entry)
    try:
        built = worker.ensure_built()
        (stem,) = built["stems"]
        manifest = worker.override(stem, [])["manifest"]
        text = json.dumps(manifest, ensure_ascii=False)
        assert "lw=3.0" in text
        assert "lw=1.5" not in text
    finally:
        engine_pool.discard(worker)
