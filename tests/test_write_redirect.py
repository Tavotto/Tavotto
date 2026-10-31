"""校验运行的写入改道（ADR 0143 修订 2026-10-30）：纯函数与提示词的看护。

真 worker 里的行为（改道、读回、写时复制、项目外仍绊线、C 层写由指纹兜住）在
`test_convert_shadow_validation.py`；MCP 来源的绊线语义不变由 `test_mcp_write_tripwire.py` 看护。
这里不在测试进程里 `install` 改道——它会包装 `builtins.open`，那是 worker 子进程的事。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tavotto.engine import ai_convert, ai_prompt_pack, writetripwire


def test_redirect_entries_round_trip_and_split():
    entries = writetripwire.redirect_entries("/p", "/c/redirected")
    roots, redirect = writetripwire.split_roots(("/c", *entries))
    assert roots == ["/c"] and redirect == ("/p", "/c/redirected")
    assert writetripwire.split_roots(("/a", "/b")) == (["/a", "/b"], None)  # MCP 形状不变


def test_half_a_redirect_pair_is_a_configuration_error():
    with pytest.raises(ValueError):
        writetripwire.split_roots(("/c", writetripwire.REDIRECT_PROJECT_PREFIX + "/p"))


def test_validation_roots_carry_the_redirect_pair_under_the_candidate_dir(tmp_path):
    cand = SimpleNamespace(dir=tmp_path / "cand", project_root=tmp_path / "proj")
    roots = ai_convert.validation_roots(cand)
    plain, redirect = writetripwire.split_roots(roots)
    assert plain == [str(cand.dir)]
    assert redirect == (str(cand.project_root), str(cand.dir / ai_convert.REDIRECT_DIRNAME))


def test_convert_prompts_tell_the_agent_to_keep_output_writes():
    py = ai_prompt_pack.convert_prompt(
        script="a.py", target="a_matplotlib.py", project_root="/p", reference=None, library="pillow"
    )
    r = ai_prompt_pack.convert_prompt(
        script="a.R",
        target="a_matplotlib.py",
        project_root="/p",
        reference=None,
        source_language="r",
    )
    for prompt in (py, r):
        assert "输出文件" in prompt and "不要删除，也不要改成只放内存" in prompt, prompt


def test_write_outside_hint_no_longer_asks_to_drop_project_writes():
    hint = ai_convert.WRITE_OUTSIDE_HINT
    assert "不要删除或改成只放内存" in hint
    assert "额外文件写入和中间数据改用内存" not in hint
