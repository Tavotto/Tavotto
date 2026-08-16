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
