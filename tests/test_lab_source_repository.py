"""reusable 在**别的仓库**的上下文里跑时，脚本眼里的「我在哪个仓库」——合同。

`_lab-qualification.yml` 是可复用 workflow，2026-09-16 起由私有仓库 `Tavotto/ci-infra`
跨仓库调用（ADMIN_HANDOFF_RUNNER_POOL.md F 组）。reusable 的 job 在**调用方**上下文里
跑：`GITHUB_REPOSITORY` / `GITHUB_SHA` / `GITHUB_REF` 说的都是 ci-infra，而 checkout
出来、正在被验的代码是 `Tavotto/Tavotto@inputs.sha`。PR A 合入后第一次真实派发
（ci-infra run 35112349056）链路机械上全通，红在两处 ambient 读取：

* `tests/test_distribution_metrics.py::test_missing_token_fails_loudly…`——用例没钉
  `GITHUB_REPOSITORY`，采集器看到 `Tavotto/ci-infra` 按「fork 没配 secret」退 0，
  `assert 0 == 2`；dev 机（没设）与公开仓库 runner（`Tavotto/Tavotto`）都只是碰巧绿。
* `scripts/ci/upgrade_acceptance.py` 的 `REPO_SLUG` 同样读 `GITHUB_REPOSITORY`，
  nightly 及以上会去 ci-infra 找 N-1 发行档（这次没跑到那一步）。

修法是**一个出口**：reusable 的 `qualify` job env 设 `TAVOTTO_SOURCE_REPOSITORY`
（`GITHUB_*` 前缀是保留的，覆盖不了），`scripts/ci/_common.source_repository()`
按「显式源仓库 > 运行仓库 > 默认」解析，读仓库名的脚本一律走它。本文件钉三件事：

1. reusable 那侧的键值（在 tests/test_release_workflow_contract.py，与另两处钉法同住）；
2. 脚本那侧：出口的形状、`REPO_SLUG` 走出口、reusable 调的所有脚本里**只有**出口读
   这一族变量（正面形式：名字只出现在允许读它的那两个函数里）；
3. 用例那侧：每个调 `collector.main` 的用例都显式钉了 `GITHUB_REPOSITORY`。

判据的主语（AGENTS.md）：**被验的代码来自哪个仓库**，不是「workflow 跑在哪个仓库」。
两者在同仓库调用时相等，所以这一族缺陷在公开仓库的 runner 上永远不会发作。
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CI_DIR = ROOT / "scripts" / "ci"
REUSABLE = ROOT / ".github" / "workflows" / "_lab-qualification.yml"
METRICS_TESTS = ROOT / "tests" / "test_distribution_metrics.py"
sys.path.insert(0, str(CI_DIR))

import _common  # noqa: E402

PUBLIC_REPO = "Tavotto/Tavotto"
#: 跨仓库调用时运行 workflow 的那个仓库——第一次真实派发时环境里的值。
RUNNING_REPO = "Tavotto/ci-infra"

#: reusable 直接调的脚本——**闭集**。从 reusable 的 `run:` 行里枚举出来与它对比，
#: 多一个少一个都红：新脚本要么进这张表（并接受下面的唯一出口判据），要么别进 reusable。
EXPECTED_SCRIPTS = {
    "scripts/ci/lab_preflight.py",
    "scripts/ci/cleanup.py",
    "scripts/ci/runtime_pins.py",
    "scripts/build_frontend.py",
    "scripts/ci/lab_acceptance.py",
    "scripts/ci/upgrade_acceptance.py",
    "scripts/ci/visual_regression.py",
    "scripts/ci/compat_matrix.py",
    "scripts/ci/soak.py",
    "scripts/ci/benchmark.py",
    "scripts/ci/mutation.py",
    "scripts/ci/summarize.py",
}

#: 「我在哪个仓库 / 哪个提交」这一族。跨仓库调用时它们说的全是调用方，
#: 所以除了 `_common` 里的出口，谁都不许各自去读。
REPO_IDENTITY_VARS = {
    "GITHUB_REPOSITORY",
    "GITHUB_REPOSITORY_OWNER",
    "GITHUB_SHA",
    "GITHUB_REF",
    "GITHUB_REF_NAME",
    "GITHUB_SERVER_URL",
}
#: 允许读这一族变量的 (文件, 函数)。`run_metadata` 读 SHA/REF 前先问
#: `running_in_source_repository`——那两个函数就是出口本身。
ALLOWED_READERS = {
    ("_common.py", "source_repository"),
    ("_common.py", "running_in_source_repository"),
    ("_common.py", "run_metadata"),
}


# ---------------------------------------------------------------- 枚举被判对象
def _scripts_called_by_the_reusable() -> set[str]:
    """reusable 的 `run:` 行里出现的脚本路径（整行注释剥掉——判据不许被自己的说明满足）。"""
    lines = [
        ln
        for ln in REUSABLE.read_text(encoding="utf-8").splitlines()
        if not ln.lstrip().startswith("#")
    ]
    return set(re.findall(r"scripts/(?:ci/)?[A-Za-z_][A-Za-z0-9_]*\.py", "\n".join(lines)))


def _local_imports(path: Path) -> set[Path]:
    """脚本 `import X` / `from X import …` 里指向仓库自己的模块的那些。

    这些脚本把 `scripts/ci/` 与 `scripts/` 都插进了 sys.path（`smoke_app` / `bench_render`
    住在上一级），两处都要找——只找一处的话 smoke_app 会安静地掉出被判集合。
    """
    out: set[Path] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module]
        for name in names:
            for base in (CI_DIR, ROOT / "scripts"):
                cand = base / f"{name.split('.')[0]}.py"
                if cand.is_file():
                    out.add(cand)
    return out


def _reusable_script_closure() -> set[Path]:
    """被判对象：reusable 调的脚本 + 它们（传递地）import 的 scripts/ci 模块。"""
    found = _scripts_called_by_the_reusable()
    assert found == EXPECTED_SCRIPTS, (
        f"reusable 调的脚本集合变了：多了 {sorted(found - EXPECTED_SCRIPTS)}，"
        f"少了 {sorted(EXPECTED_SCRIPTS - found)}——更新 EXPECTED_SCRIPTS，新脚本也接受唯一出口判据"
    )
    todo = [ROOT / rel for rel in found]
    seen: set[Path] = set()
    while todo:
        p = todo.pop()
        if p in seen:
            continue
        seen.add(p)
        todo.extend(_local_imports(p) - seen)
    assert CI_DIR / "_common.py" in seen, "闭包里没有 _common.py——import 解析失效了"
    return seen


def _identity_constants(path: Path) -> list[tuple[str, int]]:
    """文件里每个**恰好等于**这一族变量名的字符串常量：(所在函数名或 '<module>', 行号)。

    按 AST 常量判而不是 grep：注释不在 AST 里；docstring 是常量但整段不等于变量名。
    反过来，`os.environ["GITHUB_SHA"]` / `.get("GITHUB_SHA")` / `"GITHUB_SHA" in os.environ`
    / 先赋给变量再读——形式再多，那个名字总得以常量出现一次。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: list[tuple[str, int]] = []

    def visit(node: ast.AST, owner: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, child.name)
            else:
                if isinstance(child, ast.Constant) and child.value in REPO_IDENTITY_VARS:
                    hits.append((owner, child.lineno))
                visit(child, owner)

    visit(tree, "<module>")
    return hits


# ---------------------------------------------------------------- ② 脚本那侧
def test_the_enumeration_sees_the_scripts_it_should():
    """先证明尺子是活的：闭集非空、含 upgrade_acceptance、闭包里有 _common 与 smoke_app。"""
    closure = _reusable_script_closure()
    names = {p.name for p in closure}
    assert "upgrade_acceptance.py" in names and "_common.py" in names and "smoke_app.py" in names, (
        sorted(names)
    )


def test_source_repository_prefers_the_explicit_source_then_the_running_repo_then_the_default():
    """出口的**形状**（AST，正面形式）：`TAVOTTO_SOURCE_REPOSITORY or GITHUB_REPOSITORY or 默认`。

    顺序是判据的一部分：把两个 `get` 调换，ci-infra 里 `GITHUB_REPOSITORY` 非空、
    永远轮不到显式源仓库——而同仓库调用与本地都照旧绿。
    """
    tree = ast.parse((CI_DIR / "_common.py").read_text(encoding="utf-8"))
    fn = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "source_repository"
    )
    ret = fn.body[-1]
    assert isinstance(ret, ast.Return) and isinstance(ret.value, ast.BoolOp), ast.unparse(ret)
    assert isinstance(ret.value.op, ast.Or), ast.unparse(ret)
    values = ret.value.values
    assert len(values) == 3, ast.unparse(ret)

    def env_key(call: ast.AST) -> str | None:
        if (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "get"
            and ast.unparse(call.func.value) == "os.environ"
            and call.args
            and isinstance(call.args[0], ast.Constant)
        ):
            return call.args[0].value
        return None

    assert env_key(values[0]) == "TAVOTTO_SOURCE_REPOSITORY", ast.unparse(values[0])
    assert env_key(values[1]) == "GITHUB_REPOSITORY", ast.unparse(values[1])
    assert isinstance(values[2], ast.Name) and values[2].id == "DEFAULT_SOURCE_REPOSITORY", (
        ast.unparse(values[2])
    )
    assert _common.DEFAULT_SOURCE_REPOSITORY == PUBLIC_REPO


def test_source_repository_resolves_each_tier(monkeypatch):
    """行为：三档各走一次，并且第一档**压过**非空的第二档（ci-infra 的处境）。"""
    monkeypatch.delenv("TAVOTTO_SOURCE_REPOSITORY", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    assert _common.source_repository() == PUBLIC_REPO
    monkeypatch.setenv("GITHUB_REPOSITORY", RUNNING_REPO)
    assert _common.source_repository() == RUNNING_REPO
    monkeypatch.setenv("TAVOTTO_SOURCE_REPOSITORY", PUBLIC_REPO)
    assert _common.source_repository() == PUBLIC_REPO


def test_upgrade_acceptance_takes_its_repository_from_the_single_outlet():
    """`REPO_SLUG = source_repository()`（AST），且每个 `{API}/repos/…` URL 的仓库段都是它。

    第一条是「走出口」；第二条是「没有第二个来源」——再多一个 f-string 里手写仓库名，
    N-1 发行档就有了一个不受 TAVOTTO_SOURCE_REPOSITORY 管的来路。
    """
    tree = ast.parse((CI_DIR / "upgrade_acceptance.py").read_text(encoding="utf-8"))
    assigns = [
        n
        for n in tree.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "REPO_SLUG" for t in n.targets)
    ]
    assert len(assigns) == 1, f"REPO_SLUG 应恰好赋值一次，实际 {len(assigns)}"
    value = assigns[0].value
    assert (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id == "source_repository"
        and not value.args
    ), f"REPO_SLUG 没走 _common.source_repository()：{ast.unparse(value)}"

    api_urls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.JoinedStr)
        and n.values
        and isinstance(n.values[0], ast.FormattedValue)
        and isinstance(n.values[0].value, ast.Name)
        and n.values[0].value.id == "API"
    ]
    assert len(api_urls) >= 3, f"GitHub API 的 f-string 少于预期：{len(api_urls)}"
    for js in api_urls:
        parts = [ast.unparse(v.value) for v in js.values if isinstance(v, ast.FormattedValue)]
        assert "REPO_SLUG" in parts, f"这条 API URL 的仓库段不是 REPO_SLUG：{ast.unparse(js)}"


def test_only_the_outlet_reads_repository_identity_variables():
    """**唯一出口**：reusable 调的脚本及其 import 闭包里，这一族变量名只出现在允许的函数里。

    正面形式（「名字只在这两处」），不是「不许出现 os.environ.get(…)」——后者会被
    `os.getenv` / 下标 / 先存变量再读绕开，而名字总得以常量出现一次。
    """
    closure = _reusable_script_closure()
    offenders = []
    for path in sorted(closure):
        for owner, lineno in _identity_constants(path):
            if (path.name, owner) not in ALLOWED_READERS:
                offenders.append(f"{path.relative_to(ROOT)}:{lineno} 在 {owner}()")
    assert not offenders, (
        "这些地方各自读了「我在哪个仓库」——跨仓库调用时读到的是 ci-infra；改走 "
        "_common.source_repository / run_metadata：\n  " + "\n  ".join(offenders)
    )
    # 出口本身必须真的在读：不然「没有别人读」只是因为整族都没人读了
    assert {owner for owner, _ in _identity_constants(CI_DIR / "_common.py")} >= {
        "source_repository",
        "running_in_source_repository",
        "run_metadata",
    }


def _help_output(env_overrides: dict[str, str | None]) -> str:
    env = dict(os.environ)
    for key in ("TAVOTTO_SOURCE_REPOSITORY", "GITHUB_REPOSITORY"):
        env.pop(key, None)
    for key, value in env_overrides.items():
        if value is not None:
            env[key] = value
    out = subprocess.run(
        [sys.executable, str(CI_DIR / "upgrade_acceptance.py"), "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        env=env,
    )
    assert out.returncode == 0, out.stderr[-800:]
    return out.stdout


def test_upgrade_acceptance_help_names_the_source_repository_under_ci_infra():
    """端到端：ci-infra 的环境形状下 `--help` 印出的 N-1 来源是 Tavotto/Tavotto。

    三组对照放在一条里：(1) ci-infra 形状（两个都设）→ 公开仓库；(2) 只有运行仓库 →
    印运行仓库，证明尺子看得见这一维、修法是「优先」不是把名字写死；(3) 都没有 → 默认。
    """
    both = _help_output(
        {"GITHUB_REPOSITORY": RUNNING_REPO, "TAVOTTO_SOURCE_REPOSITORY": PUBLIC_REPO}
    )
    assert PUBLIC_REPO in both and RUNNING_REPO not in both, both
    running_only = _help_output({"GITHUB_REPOSITORY": RUNNING_REPO})
    assert RUNNING_REPO in running_only, running_only
    neither = _help_output({})
    assert PUBLIC_REPO in neither, neither


def _git_head() -> str:
    return subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        encoding="utf-8",
        timeout=20,
    ).stdout.strip()


def test_run_metadata_describes_the_code_under_test_not_the_running_repository(monkeypatch):
    """报告里的 `repository` / `sha` / `ref` 是**被验的代码**的。

    跨仓库：`GITHUB_SHA` / `GITHUB_REF` 是 ci-infra 的，sha 要落到 checkout 的 HEAD、
    ref 留空（不知道就说不知道，别写一个别的仓库的分支名）；summarize 把 sha 印成
    「Commit」，写错了读 ci-infra run 的人会去 Tavotto/Tavotto 找一个不存在的提交。
    同仓库与本地：逐字不变。
    """
    monkeypatch.chdir(ROOT)
    fake_sha = "f" * 40
    head = _git_head()
    assert head and head != fake_sha

    # 跨仓库调用（ci-infra 的处境）
    monkeypatch.setenv("TAVOTTO_SOURCE_REPOSITORY", PUBLIC_REPO)
    monkeypatch.setenv("GITHUB_REPOSITORY", RUNNING_REPO)
    monkeypatch.setenv("GITHUB_SHA", fake_sha)
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    meta = _common.run_metadata("nightly")
    assert meta["repository"] == PUBLIC_REPO
    assert meta["sha"] == head, f"跨仓库时 sha 应是 checkout 的 HEAD，实际 {meta['sha']!r}"
    assert meta["ref"] == "", f"跨仓库时 ref 无从得知，应留空，实际 {meta['ref']!r}"

    # 同仓库调用（release.yml 并行期）：GITHUB_* 照用
    monkeypatch.setenv("GITHUB_REPOSITORY", PUBLIC_REPO)
    meta = _common.run_metadata("release")
    assert meta["repository"] == PUBLIC_REPO
    assert meta["sha"] == fake_sha
    assert meta["ref"] == "refs/heads/main"

    # 本地手工跑：什么都没设
    for key in ("TAVOTTO_SOURCE_REPOSITORY", "GITHUB_REPOSITORY", "GITHUB_SHA", "GITHUB_REF"):
        monkeypatch.delenv(key, raising=False)
    meta = _common.run_metadata()
    assert meta["repository"] == PUBLIC_REPO
    assert meta["sha"] == head
    assert meta["ref"] == ""


# ---------------------------------------------------------------- ③ 用例那侧
def _calls(fn: ast.AST, *, attr_chain: str | None = None, name: str | None = None) -> bool:
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        if attr_chain and ast.unparse(node.func) == attr_chain:
            return True
        if name and isinstance(node.func, ast.Name) and node.func.id == name:
            return True
    return False


def _pins_github_repository(fn: ast.AST) -> bool:
    """函数体里有 `monkeypatch.setenv/delenv`，其键解析到 `"GITHUB_REPOSITORY"`。

    键可以是字面量，也可以是 `for key in ("…", "GITHUB_REPOSITORY"):` 的循环变量
    （`_run_main` 就是这么写的）。delenv 也算钉——「显式不设」是一个明确的取值。
    """
    looped: dict[str, set] = {}
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.For)
            and isinstance(node.target, ast.Name)
            and isinstance(node.iter, (ast.Tuple, ast.List))
        ):
            looped.setdefault(node.target.id, set()).update(
                e.value for e in node.iter.elts if isinstance(e, ast.Constant)
            )
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("setenv", "delenv")
            and ast.unparse(node.func.value) == "monkeypatch"
            and node.args
        ):
            key = node.args[0]
            if isinstance(key, ast.Constant) and key.value == "GITHUB_REPOSITORY":
                return True
            if isinstance(key, ast.Name) and "GITHUB_REPOSITORY" in looped.get(key.id, ()):
                return True
    return False


def test_every_collector_main_caller_pins_github_repository():
    """采集器按 `GITHUB_REPOSITORY` 判「我是不是本仓库」，所以每个调 `collector.main`
    的用例都得自己说清这一维——直接钉，或经 `_run_main`（它先 delenv 再按实参设）。

    枚举而不是点名：新加的用例自动进被判集合。先自检尺子（那条在 ci-infra 红过的
    用例必须在集合里），再判。
    """
    tree = ast.parse(METRICS_TESTS.read_text(encoding="utf-8"))
    funcs = {
        n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    direct = {name for name, fn in funcs.items() if _calls(fn, attr_chain="collector.main")}
    via_helper = {name for name, fn in funcs.items() if _calls(fn, name="_run_main")}

    assert "test_missing_token_fails_loudly_instead_of_silently_skipping" in direct, sorted(direct)
    assert "_run_main" in direct and via_helper, "helper 形状变了——判据在看错的集合"
    assert _pins_github_repository(funcs["_run_main"]), "_run_main 不再钉 GITHUB_REPOSITORY"

    unpinned = sorted(name for name in direct if not _pins_github_repository(funcs[name]))
    assert not unpinned, (
        "这些用例调了 collector.main 却没钉 GITHUB_REPOSITORY——结论取决于跑在哪台机器上"
        f"（ci-infra 里它是 Tavotto/ci-infra）：{unpinned}"
    )


@pytest.mark.parametrize("running_repo", [RUNNING_REPO, "someone/Tavotto"])
def test_metrics_tests_pass_regardless_of_the_ambient_repository(running_repo):
    """把整个采集器测试文件放进 ci-infra / fork 的环境形状里跑：全绿。

    这是第一次真实派发红掉的那条路径的**直接复现**（修复前 1 failed：`assert 0 == 2`）。
    子进程跑，避免 monkeypatch 与本进程里已 import 的模块状态互相干扰。
    """
    env = dict(os.environ, GITHUB_REPOSITORY=running_repo)
    env.pop("TAVOTTO_METRICS_TOKEN", None)
    # 不再自己加 `-q`：pytest.ini 的 addopts 已有一个，两个叠成 `-qq` 会把「N passed」
    # 那行汇总也吞掉——而那行正是下面用来证明「真的跑了一批」的东西。
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", str(METRICS_TESTS)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        env=env,
        cwd=str(ROOT),
    )
    assert out.returncode == 0, out.stdout[-2000:] + out.stderr[-800:]
    # 退出码 0 也可能是「一条都没收集到」以外的空转形状；数一下真跑了多少条。
    m = re.search(r"(\d+) passed", out.stdout)
    assert m and int(m.group(1)) >= 20, out.stdout[-600:]
