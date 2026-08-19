"""discover 静态扫描：三种入口方言、f-string↔磁盘产物比对、冲突不自动裁决、
merge 时现有注册表优先。全部纯 AST，不执行任何脚本。"""
import json

import pytest

from magplot.engine import discover, registry


@pytest.fixture
def figs(tmp_path):
    return tmp_path


def _script(figs, name, code):
    (figs / name).write_text(code, encoding="utf-8")


def _touch(figs, *names):
    for n in names:
        (figs / n).write_bytes(b"")


MAIN_SCRIPT = '''\
from paper_style import save

def main():
    fig = build()
    save(fig, "FigA_alpha")
    for k in "ab":
        save(fig, f"FigA_beta_{k}")

if __name__ == "__main__":
    main()
'''


def test_main_dialect_with_fstring_resolved_against_disk(figs):
    _script(figs, "fig_a.py", MAIN_SCRIPT)
    _touch(figs, "FigA_beta_a.pdf", "FigA_beta_b.png")
    rep = discover.discover(figs)
    info = rep["scripts"]["fig_a.py"]
    assert info["entry"] == "main"  # 有 main 优先于 __main__
    assert info["stems"] == ["FigA_alpha", "FigA_beta_a", "FigA_beta_b"]
    assert info["unresolved"] == []


def test_render_dialect_and_ext_stripped(figs):
    _script(figs, "fig_b.py", 'def render():\n    fig.savefig("out/FigB_g.pdf")\n')
    info = discover.discover(figs)["scripts"]["fig_b.py"]
    assert info["entry"] == "render"
    assert info["stems"] == ["FigB_g"]  # 目录前缀与扩展名都剥掉


def test_inline_main_dialect(figs):
    _script(figs, "fig_c.py",
            'import matplotlib.pyplot as plt\n'
            'if __name__ == "__main__":\n'
            '    plt.savefig("FigC_d.png")\n')
    assert discover.discover(figs)["scripts"]["fig_c.py"]["entry"] == "__main__"


def test_unresolved_fstring_reported_not_guessed(figs):
    _script(figs, "fig_d.py", 'def main():\n    save(fig, f"FigD_{k}")\n')
    info = discover.discover(figs)["scripts"]["fig_d.py"]
    assert info["stems"] == []
    assert info["unresolved"] == ["FigD_*"]


def test_paper_style_and_helpers_skipped(figs):
    _script(figs, "paper_style.py", 'def save(fig, stem):\n    fig.savefig(stem)\n')
    _script(figs, "paper_style 2.py",  # macOS 复制产生的副本也不是产图脚本
            'def main():\n    save(fig, "Stray")\n')
    _script(figs, "_helper.py", 'def main():\n    save(fig, "X")\n')
    _script(figs, "data_module.py", 'def load():\n    return 1\n')  # 不产图
    assert discover.discover(figs)["scripts"] == {}


def test_conflict_excluded_from_draft_and_reported(figs):
    _script(figs, "one.py", 'def main():\n    save(fig, "Shared")\n    save(fig, "One_x")\n')
    _script(figs, "two.py", 'def main():\n    save(fig, "Shared")\n')
    cfg, rep = discover.build_draft(figs)
    assert rep["conflicts"] == {"Shared": ["one.py", "two.py"]}
    assert cfg["scripts"]["one.py"]["stems"] == ["One_x"]
    assert "two.py" not in cfg["scripts"]  # 只剩冲突 stem → 整个不进草稿


def test_draft_loads_into_registry(figs):
    _script(figs, "fig_a.py", 'def main():\n    save(fig, "FigA_1")\n')
    cfg, _ = discover.build_draft(figs)
    discover.write_config(figs, cfg)
    registry.load(figs)
    assert registry.for_stem("FigA_1")["cost"] == "medium"  # 草稿默认值
    registry.load_data({"scripts": {}}, source="<test-reset>")


def test_merge_existing_registry_wins(figs):
    """现有注册表的归属永远优先：已登记的 stem 不会被重新分配。"""
    existing = {"version": 1, "scripts": {
        "old.py": {"entry": "render", "cost": "heavy", "notes": "",
                   "stems": ["Kept_1"]}}}
    registry.registry_path(figs).write_text(
        json.dumps(existing), encoding="utf-8")
    # new.py 认领 Kept_1（已登记）+ New_1（新）；old.py 磁盘上已不存在
    _script(figs, "new.py", 'def main():\n    save(fig, "Kept_1")\n    save(fig, "New_1")\n')
    merged, _, changes = discover.merge(figs)
    assert merged["scripts"]["old.py"]["stems"] == ["Kept_1"]  # 原样保留
    assert merged["scripts"]["new.py"]["stems"] == ["New_1"]   # 只拿到新 stem
    assert changes["added_scripts"] == ["new.py"]


def test_merge_appends_new_stems_to_existing_script(figs):
    existing = {"version": 1, "scripts": {
        "fig_a.py": {"entry": "main", "cost": "light", "notes": "",
                     "stems": ["FigA_1"]}}}
    registry.registry_path(figs).write_text(
        json.dumps(existing), encoding="utf-8")
    _script(figs, "fig_a.py", 'def main():\n    save(fig, "FigA_1")\n    save(fig, "FigA_2")\n')
    merged, _, changes = discover.merge(figs)
    assert merged["scripts"]["fig_a.py"]["stems"] == ["FigA_1", "FigA_2"]
    assert merged["scripts"]["fig_a.py"]["cost"] == "light"  # 现有元数据不动
    assert changes["added_stems"] == {"fig_a.py": ["FigA_2"]}


DYNAMIC_NAME_SCRIPT = '''\
from pathlib import Path

OUT = Path(__file__).parent / "panels"

def save_panel(fig, stem):
    fig.savefig(OUT / f"{stem}.pdf")

def main():
    for name in ("A", "B"):
        save_panel(build(), f"Dyn_{name}")
'''


def test_wrapper_function_and_constant_loop_are_resolved(figs):
    """包装函数 + 常量循环不是「动态命名」，是可以算出来的。

    `save_panel(fig, f"Dyn_{name}")` 把 stem 传进包装函数，包装函数再拼
    `OUT / f"{stem}.pdf"`——跨函数传播实参 + 展开常量 for 循环即可还原。
    论文的 supporting_information/panels/ 正是这个写法。
    """
    _script(figs, "build_panels.py", DYNAMIC_NAME_SCRIPT)
    info = discover.discover(figs)["scripts"]["build_panels.py"]
    assert info["stems"] == ["Dyn_A", "Dyn_B"]
    assert info["dynamic_names"] is False
    assert info["save_calls"] == 1          # 调用点算一次，不按循环次数重复计

    cfg, _ = discover.build_draft(figs)
    assert cfg["scripts"]["build_panels.py"]["stems"] == ["Dyn_A", "Dyn_B"]


RUNTIME_NAME_SCRIPT = '''\
import sys
from pathlib import Path

OUT = Path("panels")

def main():
    for path in sorted(Path("data").glob("*.csv")):
        fig = build(path)
        fig.savefig(OUT / f"{path.stem}.pdf")
'''


def test_runtime_only_names_reported_not_guessed(figs):
    """stem 真的只有运行期才知道（来自目录遍历）：报出来，但绝不猜。

    静默跳过的后果是用户拿到一份空的 mm_registry.json，面板上没有 ⚡，
    却完全不知道原因——所以必须留 dynamic_names 让上层引导「试运行探测」。
    """
    _script(figs, "scan_panels.py", RUNTIME_NAME_SCRIPT)
    info = discover.discover(figs)["scripts"]["scan_panels.py"]
    assert info["stems"] == []
    assert info["dynamic_names"] is True
    assert info["save_calls"] == 1

    cfg, _ = discover.build_draft(figs)
    assert "scan_panels.py" not in cfg["scripts"]


def test_path_algebra_with_suffix_and_join(figs):
    """朋友那份图库的写法：Path / f-string 再 .with_suffix()。

    v0.1.3 只认 save()/savefig() 里的字符串字面量，这类脚本一个 stem 都抽不
    出来，注册表必然是空的（= 全图库不可参数化）。
    """
    _script(figs, "fig_paths.py", '''\
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "output"

LABEL = "map"

def main():
    fig.savefig((OUT / "Fig_kinetics").with_suffix(".pdf"))
    fig.savefig(os.path.join("out", "Fig_%s.png" % LABEL))
    fig.savefig(OUT.joinpath("Fig_{}.png".format("zeta")))
    fig.savefig(Path(__file__).with_suffix(".svg"))
''')
    info = discover.discover(figs)["scripts"]["fig_paths.py"]
    # with_suffix 剥的是任意后缀（.py → .svg），不是只剥图片扩展名
    assert info["stems"] == ["Fig_kinetics", "Fig_map", "Fig_zeta", "fig_paths"]


def test_subdirectories_are_scanned(figs):
    """面板脚本放子目录的图库不能整目录漏掉；脚本键是 POSIX 相对路径。"""
    (figs / "panels").mkdir()
    (figs / ".venv" / "lib").mkdir(parents=True)
    _script(figs, "panels/fig_sub.py", 'def main():\n    save(fig, "Sub_1")\n')
    _script(figs, ".venv/lib/noise.py", 'def main():\n    save(fig, "Noise")\n')
    scripts = discover.discover(figs)["scripts"]
    assert "panels/fig_sub.py" in scripts       # 子目录进来了
    assert all(".venv" not in k for k in scripts)  # 虚拟环境整棵剪掉


def test_custom_entry_name_accepted(figs):
    """入口不必叫 main/render——worker 本来就是 getattr(module, entry)()。"""
    _script(figs, "fig_custom.py",
            'def _draw():\n    return 1\n\n'
            'def plot():\n    fig = _draw()\n    fig.savefig("Custom_1.pdf")\n')
    info = discover.discover(figs)["scripts"]["fig_custom.py"]
    assert info["entry"] == "plot"
    assert info["stems"] == ["Custom_1"]


def test_non_plotting_module_stays_quiet(figs):
    """没有任何 save 调用的模块不该被当成「命名有问题的绘图脚本」报出来。"""
    _script(figs, "helpers_mod.py", 'def main():\n    return compute()\n')
    assert discover.discover(figs)["scripts"] == {}


def test_registry_write_is_atomic(figs, monkeypatch):
    """写到一半被杀，不能把用户的 mm_registry.json 截断成非法 JSON。

    注册表**随图库走**，坏掉的是用户目录里的文件——重装应用也修不回来，
    下次打开这个项目只会看到「注册表不是合法 JSON」。桌面壳强退、OOM、
    断电，Windows 上杀毒软件在写入期间短暂锁定，都会走到这条路径。
    仓库里 baked 基线 / 用户配置 / 握手文件 / 安装清单一律是临时文件 +
    replace，这里曾经是唯一的直写例外。
    """
    from pathlib import Path

    path = registry.registry_path(figs)
    original = json.dumps({"version": 1, "scripts": {}}, ensure_ascii=False, indent=1)
    path.write_text(original, encoding="utf-8")

    real_write = Path.write_text

    def half_then_die(self, data, *a, **kw):
        real_write(self, data[: len(data) // 2], *a, **kw)   # 写一半
        raise OSError("模拟写入过程中进程被杀")

    monkeypatch.setattr(Path, "write_text", half_then_die)
    with pytest.raises(OSError):
        discover.write_config(figs, {"version": 1, "scripts": {
            "a.py": {"entry": "main", "cost": "medium", "notes": "", "stems": ["A"]}}})
    monkeypatch.undo()

    assert path.read_text(encoding="utf-8") == original      # 原件一个字节没动
    assert json.loads(path.read_text(encoding="utf-8"))      # 仍然读得回来
