"""`tests/support/gh_expr.py`（GitHub 表达式极小求值器）的看护。

它是 `tests/test_merge_queue_workflows.py::TestPullRequestEventTypes` 的尺子。尺子量错，
真值表就会在错的语义上恒绿——所以这里钉的全是那张表**依赖**的语义：merge_group 下读
不到的字段是 null 而不是异常、`null == 'unlabeled'` 是 false、`contains` 对数组逐项比、
`&&` / `||` 返回操作数、认不出的语法**必须抛**而不是算成 false。
"""

from __future__ import annotations

import pytest

from support import gh_expr as G

PR = {
    "event_name": "pull_request",
    "workflow": "CI",
    "ref": "refs/pull/7/merge",
    "event": {
        "action": "unlabeled",
        "label": {"name": "full-ci"},
        "pull_request": {"number": 7, "labels": [{"name": "Docs"}]},
    },
}
MG = {"event_name": "merge_group", "workflow": "CI", "event": {"merge_group": {"head_sha": "abc"}}}


class TestPathsAndNull:
    def test_missing_path_is_null_not_an_error(self):
        assert G.evaluate("github.event.label.name", MG) is None
        assert G.evaluate("github.event.pull_request.labels.*.name", MG) == []

    def test_null_compared_to_a_word_is_false_but_to_empty_string_is_true(self):
        """GitHub 的跨类型比较先转数字：null → 0、'' → 0、'unlabeled' → NaN。"""
        assert G.evaluate("github.event.action == 'unlabeled'", MG) is False
        assert G.evaluate("github.event.action == ''", MG) is True
        assert G.evaluate("github.event.action != 'unlabeled'", MG) is True

    def test_object_filter_maps_over_the_array(self):
        assert G.evaluate("github.event.pull_request.labels.*.name", PR) == ["Docs"]

    def test_unknown_context_root_raises(self):
        with pytest.raises(G.UnsupportedExpression):
            G.evaluate("env.FOO == 'x'", PR)


class TestOperators:
    def test_string_equality_ignores_case(self):
        assert G.evaluate("github.event.label.name == 'FULL-CI'", PR) is True

    def test_and_or_return_operands_like_javascript(self):
        assert G.evaluate("github.event.action && 'yes'", PR) == "yes"
        assert G.evaluate("github.event.label.name || 'fallback'", PR) == "full-ci"
        assert G.evaluate("github.event.label.name || 'fallback'", MG) == "fallback"
        assert G.evaluate("github.event.label.name && 'x'", MG) is None

    def test_not_and_parentheses(self):
        assert G.evaluate("!(github.event_name == 'push')", PR) is True
        assert G.evaluate("!github.event.action", MG) is True

    def test_truthiness_follows_github(self):
        assert G.truthy(None) is False and G.truthy(False) is False
        assert G.truthy(0) is False and G.truthy("") is False
        assert G.truthy("false") is True and G.truthy([]) is True


class TestContains:
    def test_array_search_compares_items_case_insensitively(self):
        assert G.evaluate("contains(github.event.pull_request.labels.*.name, 'docs')", PR) is True
        assert (
            G.evaluate("contains(github.event.pull_request.labels.*.name, 'full-ci')", PR) is False
        )

    def test_null_search_is_empty_string(self):
        assert G.evaluate("contains(github.event.pull_request.labels.*.name, 'x')", MG) is False
        assert G.evaluate("contains(github.event.action, 'x')", MG) is False

    def test_string_search_is_a_substring_test(self):
        assert G.evaluate("contains(github.ref, 'pull/')", PR) is True


class TestStrictness:
    @pytest.mark.parametrize(
        "expr",
        [
            "startsWith(github.ref, 'refs/')",  # 不支持的函数
            "github.event_name == 'a' ||",  # 残缺
            "github.event.pull_request.labels[0].name",  # 下标语法未实现
            "github.run_number > 3",  # 关系运算未实现
            "contains(github.ref)",  # 参数个数
            "",  # 空
        ],
    )
    def test_unsupported_syntax_raises_instead_of_guessing(self, expr):
        with pytest.raises(G.UnsupportedExpression):
            G.evaluate(expr, PR)

    def test_nested_interpolation_is_rejected_by_evaluate_but_handled_by_render(self):
        tpl = "ci-${{ github.workflow }}-${{ github.event_name }}-${{ github.event.pull_request.number || github.event.merge_group.head_sha || github.ref }}"
        with pytest.raises(G.UnsupportedExpression):
            G.evaluate(tpl, PR)
        assert G.render(tpl, PR) == "ci-CI-pull_request-7"
        assert G.render(tpl, MG) == "ci-CI-merge_group-abc"


class TestRender:
    def test_booleans_render_lowercase_and_null_renders_empty(self):
        assert G.render("${{ github.event_name == 'pull_request' }}", PR) == "true"
        assert G.render("${{ github.event_name == 'push' }}", PR) == "false"
        assert G.render("[${{ github.event.label.name }}]", MG) == "[]"

    def test_folded_block_lines_are_joined(self):
        folded = "github.event_name == 'merge_group'\n      || (github.event_name == 'pull_request'\n          && contains(github.event.pull_request.labels.*.name, 'docs'))\n"
        assert G.evaluate(folded, PR) is True
        assert G.evaluate(folded, MG) is True
