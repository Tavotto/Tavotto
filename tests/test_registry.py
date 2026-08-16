"""注册表加载与校验：重复 stem 报错、方言/成本枚举、文件缺失。"""
import json

import pytest

from magplot.engine import registry


@pytest.fixture(autouse=True)
def _isolate():
    """registry 是模块级状态；每个用例前后清空，避免串味。"""
    registry.load_data({"scripts": {}}, source="<test-reset>")
    yield
    registry.load_data({"scripts": {}}, source="<test-reset>")


VALID = {"version": 1, "scripts": {
    "fig_a.py": {"entry": "main", "cost": "light", "notes": "",
                 "stems": ["FigA_1", "FigA_2"]},
    "fig_b.py": {"entry": "render", "cost": "heavy", "notes": "3d",
                 "stems": ["FigB_1"]},
}}


def test_load_from_file_and_lookup(tmp_path):
    registry.registry_path(tmp_path).write_text(
        json.dumps(VALID, ensure_ascii=False), encoding="utf-8")
    registry.load(tmp_path)
    assert registry.loaded()
    assert registry.for_stem("FigA_2") == {
        "script": "fig_a.py", "entry": "main", "cost": "light", "notes": ""}
    assert registry.for_stem("FigB_1")["notes"] == "3d"
    assert registry.for_stem("nope") is None
    assert registry.all_scripts() == ["fig_a.py", "fig_b.py"]
    assert registry.stems_of("fig_a.py") == ["FigA_1", "FigA_2"]


def test_missing_file_raises_filenotfound(tmp_path):
    with pytest.raises(FileNotFoundError):
        registry.load(tmp_path)


def test_duplicate_stem_raises_with_both_scripts():
    bad = {"scripts": {
        "one.py": {"entry": "main", "cost": "light", "stems": ["X"]},
        "two.py": {"entry": "main", "cost": "light", "stems": ["X"]}}}
    with pytest.raises(RuntimeError, match="one.py.*two.py"):
        registry.load_data(bad)


def test_invalid_entry_and_cost_rejected():
    with pytest.raises(RuntimeError, match="entry 非法"):
        registry.load_data({"scripts": {"a.py": {"entry": "run", "stems": []}}})
    with pytest.raises(RuntimeError, match="cost 非法"):
        registry.load_data({"scripts": {"a.py": {"entry": "main",
                                                 "cost": "huge", "stems": []}}})


def test_invalid_json_raises_runtime(tmp_path):
    registry.registry_path(tmp_path).write_text("{oops", encoding="utf-8")
    with pytest.raises(RuntimeError, match="合法 JSON"):
        registry.load(tmp_path)


def test_defaults_entry_main_cost_medium():
    registry.load_data({"scripts": {"a.py": {"stems": ["S"]}}})
    assert registry.for_stem("S") == {
        "script": "a.py", "entry": "main", "cost": "medium", "notes": ""}
