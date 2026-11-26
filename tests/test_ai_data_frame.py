"""数据出图：规范里的 `axis_policy.enclosed_spines` 是判据，不是画法。

False = 这份规范不要求四周边框（期刊底、自由排版都是 False），不等于「去掉上、右边框」：
选了期刊之后图仍按具名默认画四周边框。
"""

import copy

import pytest

from tavotto.engine import ai_data_template as T, profiles


@pytest.mark.parametrize("flag", [False, None, "no", 0])
def test_not_required_keeps_the_default_frame(flag):
    spec = copy.deepcopy(profiles.load("lab-publication-v1"))
    spec["axis_policy"]["enclosed_spines"] = flag
    assert T.style_from_profile(spec)["enclosed_spines"] is True


def test_required_stays_enclosed():
    spec = copy.deepcopy(profiles.load("lab-publication-v1"))
    spec["axis_policy"]["enclosed_spines"] = True
    assert T.style_from_profile(spec)["enclosed_spines"] is True


def test_rc_lines_draw_all_four_spines_when_not_required():
    spec = copy.deepcopy(profiles.load("lab-publication-v1"))
    spec["axis_policy"]["enclosed_spines"] = False
    rc = "\n".join(T._rc_lines(T.style_from_profile(spec), False))
    assert '"axes.spines.top": True' in rc and '"axes.spines.right": True' in rc
