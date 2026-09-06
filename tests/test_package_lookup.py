"""设置 → 包管理的「查找」（ADR 0038 的 2026-09-07 修订）。

查找是这一页上**唯一会主动出网**的动作，所以看护的重点与安装那组不同：

* **谁触发**：只有端点被调用时才出网；界面上没有自动触发的路径（前端侧的
  判据在 `web/src/components/settings/PackagesSettings.test.tsx`）。
* **发出去的是什么**：argv 逐字节钉住，只有一个包名，且包名进 argv 之前过
  两次同一道白名单语法（原样一次、PEP 503 归一之后再一次）。
* **回来的是什么**：响应里只有包名、版本表、以及「配没配自定义源」这一个
  三档布尔——没有地址、没有路径、没有 pip 的原始输出。
* **失败怎么说**：四个稳定 code，每一个对应一个不同的下一步。

**这里一次网络请求都不发**：`deprepair._run_lookup` 是唯一的执行点，所有
用例都把它换掉。真实联网只在开发时手工验证过一次（结果写在 ADR 里）。
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

from tavotto import security
from tavotto.engine import deprepair, depresolve, managedenv

pytest_plugins = ("support.dependency_repair",)

DEPREPAIR_SRC = Path(deprepair.__file__)

#: `pip index versions lmfit` 在真机上的输出（2026-09-07 实测原样抄回来）。
#: **用真实形状而不是自己捏一个**：捏出来的形状会产生假红或假绿，而它看起来
#: 与真的一模一样（[[simulated-input-shape-lies]]）。
REAL_FOUND = (
    "lmfit (1.3.4)\n"
    "Available versions: 1.3.4, 1.3.3, 1.3.2, 1.3.1, 1.3.0, 1.2.2\n"
    "  INSTALLED: 1.3.2\n"
    "  LATEST:    1.3.4\n"
)
#: 索引上真的没有这个名字（`--retries 1`，网络健康）。实测原样。
REAL_NOT_FOUND = "ERROR: No matching distribution found for tavotto-no-such-pkg-xyz\n"
#: 索引连不上。实测原样——注意**最后一行与上面那条逐字相同**，唯一的差别是
#: 前面多了一行连接失败的重试警告。整个 offline 判据就系在这一行上。
REAL_OFFLINE = (
    "WARNING: Retrying (Retry(total=0, connect=None, read=None, redirect=None, status=None)) "
    "after connection broken by 'NewConnectionError('<pip._vendor.urllib3.connection."
    "HTTPConnection object at 0x1097f1be0>: Failed to establish a new connection: "
    "[Errno 61] Connection refused')': /simple/lmfit/\n"
    "ERROR: No matching distribution found for lmfit\n"
)


@pytest.fixture(autouse=True)
def _clean(clean_state):
    yield


@pytest.fixture
def managed_env(tmp_path):
    """一份「形状对」的受管环境：manifest + 一个空的解释器文件。

    查找不跑它，只需要 `managedenv.python_of()` 认得出它。
    """
    managedenv.write_manifest(tmp_path, managedenv.new_manifest(tmp_path, "/x/py"))
    python = managedenv.venv_python(tmp_path)
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("", encoding="utf-8")
    # `new_manifest` 建出来是 incomplete，而 `python_of()` 只认 ready。少了这一行
    # 用例照样跑，只是全都悄悄走了「退到基础解释器」那条分支——判据还在，量的
    # 却不是它想量的那个对象。
    managedenv.mark_ready(tmp_path)
    assert managedenv.python_of(tmp_path), "夹具没做出一个 python_of 认得出的环境"
    return python


@pytest.fixture
def no_index(monkeypatch):
    """自定义源那一问不起子进程（它自己有独立的用例）。"""
    monkeypatch.setattr(deprepair, "custom_package_index", lambda python: False)


@pytest.fixture
def recorded(monkeypatch):
    """把 `_run_lookup` 换成一个记账的假货，回一份可配置的结果。"""

    calls: list[list[str]] = []
    box = {"rc": 0, "text": REAL_FOUND, "timed_out": False}

    def _fake(argv):
        calls.append(list(argv))
        return box["rc"], box["text"], box["timed_out"]

    monkeypatch.setattr(deprepair, "_run_lookup", _fake)
    return calls, box


# ===========================================================================
# 一、发出去的是什么
# ===========================================================================
def test_lookup_argv_is_pinned():
    """查找命令逐字节钉住。

    每一个参数都在守一件具体的事，改任何一个都要有人先改这条断言：

    * `--disable-pip-version-check`：它自己会再发一次网络请求，实测把一次
      查找从 0.7 s 拖到 10.6 s；
    * `--no-input`：私有源要密码时子进程里没人能回答，只会挂着；
    * **`--retries 1` 是 offline 判据的一部分**：`--retries 0` 时 pip 连不上
      索引也只打一句 `No matching distribution found`，与「这个包不存在」
      逐字相同（2026-09-07 实测），离线就再也认不出来了；
    * `--timeout 3`：与 8 s 的墙上预算配套（0.3 启动 + 3 × 2 次 ≈ 6.3 s）；
    * 包名在**最后**，且首字符必然是字母数字，不可能被 pip 当成选项。
    """
    assert deprepair.pip_index_argv("/env/bin/python", "lmfit") == [
        "/env/bin/python",
        "-m",
        "pip",
        "index",
        "versions",
        "--disable-pip-version-check",
        "--no-input",
        "--retries",
        "1",
        "--timeout",
        "3",
        "lmfit",
    ]


def test_lookup_runs_a_list_argv_and_never_a_shell(monkeypatch):
    """`shell=False`（默认）+ list argv + stdin 关掉。

    判据同时问了两个维度：**调用点**传的是不是 list、有没有 `shell`；以及
    **源码里** `_run_lookup` 内部有没有别的执行方式（走 AST，不是子串——
    注释与 docstring 都能满足子串）。
    """
    seen: dict = {}

    def _fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        raise subprocess.TimeoutExpired(argv, 1)

    monkeypatch.setattr(deprepair.subprocess, "run", _fake_run)
    rc, text, timed_out = deprepair._run_lookup(deprepair.pip_index_argv("/py", "lmfit"))
    assert timed_out is True and rc != 0 and text == ""
    assert isinstance(seen["argv"], list)
    assert "shell" not in seen["kwargs"], "查找绝不能经 shell"
    assert seen["kwargs"]["stdin"] is subprocess.DEVNULL
    assert seen["kwargs"]["timeout"] == deprepair.LOOKUP_TIMEOUT_S

    tree = ast.parse(DEPREPAIR_SRC.read_text(encoding="utf-8"))
    fn = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_run_lookup"
    )
    called = {
        ast.unparse(n.func)
        for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }
    assert called == {"subprocess.run"}, f"_run_lookup 里出现了别的执行方式：{called}"


@pytest.mark.parametrize(
    "hostile",
    [
        "-r requirements.txt",
        "--index-url http://evil.example/simple",
        "https://evil.example/x.whl",
        "git+https://evil.example/x",
        "file:///etc/passwd",
        "../../etc/passwd",
        "pkg @ https://evil.example/x.whl",
        "pkg[extra]",
        "pkg; os_name=='nt'",
        "pkg && rm -rf /",
        "pkg`whoami`",
        "$(whoami)",
        "",
        "   ",
    ],
)
def test_a_hostile_name_dies_before_any_subprocess(tmp_path, monkeypatch, hostile):
    """敌意串在语法那一关就死，`_run_lookup` 一次都不会被调。"""

    def _boom(argv):
        raise AssertionError("语法不合法的名字不该走到子进程")

    monkeypatch.setattr(deprepair, "_run_lookup", _boom)
    with pytest.raises(deprepair.RepairError) as err:
        deprepair.lookup_package(tmp_path, hostile)
    assert err.value.code == deprepair.ERROR_REQUIREMENT_INVALID


def test_a_version_specifier_is_refused(tmp_path, monkeypatch):
    """查找只接受**名字**：`lmfit>=1.3` 是安装的输入，不是查找的输入。"""
    monkeypatch.setattr(
        deprepair, "_run_lookup", lambda argv: pytest.fail("带版本的串不该走到子进程")
    )
    with pytest.raises(deprepair.RepairError) as err:
        deprepair.lookup_package(tmp_path, "lmfit>=1.3")
    assert err.value.code == deprepair.ERROR_REQUIREMENT_INVALID


def test_the_name_is_normalised_before_it_reaches_pip(tmp_path, managed_env, recorded, no_index):
    """PEP 503 归一在**我们这一侧**做。

    pip 自己也会归一，但它把用户原样输入的那个名字回显出来
    （`SciKit_Learn (1.9.0)`，2026-09-07 实测）。归一化交给 pip 的话，界面上
    显示的名字与安装时用的身份就会是两个东西。
    """
    calls, box = recorded
    box["text"] = "scikit-learn (1.9.0)\nAvailable versions: 1.9.0, 1.8.0\n"
    out = deprepair.lookup_package(tmp_path, "SciKit_Learn")
    assert calls[0][-1] == "scikit-learn", "进 argv 的必须是归一后的名字"
    assert out["name"] == "scikit-learn"
    assert out["name"] == depresolve.normalize_distribution("SciKit_Learn")


# ===========================================================================
# 二、失败：四个 code 各对应一个不同的下一步
# ===========================================================================
def test_offline_is_reported_when_the_index_cannot_be_reached(
    tmp_path, managed_env, recorded, no_index
):
    _, box = recorded
    box.update(rc=1, text=REAL_OFFLINE)
    with pytest.raises(deprepair.RepairError) as err:
        deprepair.lookup_package(tmp_path, "lmfit")
    assert err.value.code == deprepair.ERROR_LOOKUP_OFFLINE


def test_not_found_is_reported_when_the_index_answered_and_had_no_such_name(
    tmp_path, managed_env, recorded, no_index
):
    _, box = recorded
    box.update(rc=1, text=REAL_NOT_FOUND)
    with pytest.raises(deprepair.RepairError) as err:
        deprepair.lookup_package(tmp_path, "tavotto-no-such-pkg-xyz")
    assert err.value.code == deprepair.ERROR_LOOKUP_NOT_FOUND


def test_the_network_verdict_wins_over_the_not_found_verdict():
    """两句话同时出现时算**离线**——这是一档刻意的不对称。

    `pip index versions` 在「连不上索引」与「索引上没有这个名字」两种情况下
    的最后一行逐字相同，只有前者多一行重试警告。于是网络抖了一下、重试却成功
    并确实查到「没有这个包」时，我们会报 offline。

    选这个方向是因为**反向的错误代价更高**：把「网断了」说成「PyPI 上没有这个
    名字」会让用户去改一个本来就对的包名；反过来只是让他重试一次。
    """
    both = REAL_OFFLINE  # 它本身就同时含两句
    assert "no matching distribution" in both.lower()
    assert deprepair.classify_lookup_failure(both) == deprepair.ERROR_LOOKUP_OFFLINE
    assert deprepair.classify_lookup_failure(REAL_NOT_FOUND) == deprepair.ERROR_LOOKUP_NOT_FOUND


def test_timeout_is_its_own_code_and_comes_from_a_structural_signal(
    tmp_path, managed_env, recorded, no_index
):
    """超时是**结构化信号**（`_run_lookup` 的第三个返回值），不是匹配一句中文。

    `_run()` 把超时压成 `(1, "超时（8s）")`；拿判据去匹配那句散文的话，改一个
    字或翻译一次判据就静默失效了。
    """
    _, box = recorded
    box.update(rc=1, text="", timed_out=True)
    with pytest.raises(deprepair.RepairError) as err:
        deprepair.lookup_package(tmp_path, "lmfit")
    assert err.value.code == deprepair.ERROR_LOOKUP_TIMEOUT


def test_an_unreadable_answer_is_a_failure_not_an_empty_result(
    tmp_path, managed_env, recorded, no_index
):
    """退出码 0 **不等于**拿到了要的东西。

    `pip index` 的输出不是契约。它哪天换了格式，解析不出版本表时必须报
    `lookup_failed`——回一个 `versions: []` 的「找到了」会让界面显示一个空的
    版本下拉，用户以为这个包没有任何版本可装。
    """
    _, box = recorded
    box.update(rc=0, text="lmfit (1.3.4)\n(pip 换了输出格式)\n")
    with pytest.raises(deprepair.RepairError) as err:
        deprepair.lookup_package(tmp_path, "lmfit")
    assert err.value.code == deprepair.ERROR_LOOKUP_FAILED


def test_without_any_python_lookup_fails_instead_of_pretending(tmp_path, monkeypatch):
    monkeypatch.setattr(managedenv, "python_of", lambda p: None)
    monkeypatch.setattr(deprepair, "base_python", lambda: None)
    monkeypatch.setattr(deprepair, "_run_lookup", lambda argv: pytest.fail("没有解释器还起子进程"))
    with pytest.raises(deprepair.RepairError) as err:
        deprepair.lookup_package(tmp_path, "lmfit")
    assert err.value.code == deprepair.ERROR_LOOKUP_FAILED


def test_the_four_lookup_codes_are_a_closed_set():
    """四档闭集——端点的状态表按它写，多一个少一个都在这里红。"""
    assert deprepair.LOOKUP_ERROR_CODES == (
        "package_lookup_not_found",
        "package_lookup_offline",
        "package_lookup_timeout",
        "package_lookup_failed",
    )


# ===========================================================================
# 三、回来的是什么
# ===========================================================================
def test_a_found_package_keeps_pips_version_order(tmp_path, managed_env, recorded, no_index):
    """版本表按 pip 给的顺序原样保留，`latest` 是第一个。

    **不自己排序**：比较版本号要 PEP 440 的规则（`1.3.10` > `1.3.9`，
    `1.0rc1` < `1.0`），而那正是 pip 已经做过的事，自己再排一遍只会排错。
    """
    out = deprepair.lookup_package(tmp_path, "lmfit")
    assert out["versions"] == ["1.3.4", "1.3.3", "1.3.2", "1.3.1", "1.3.0", "1.2.2"]
    assert out["latest"] == "1.3.4"


def test_installed_answers_about_the_target_environment_only(
    tmp_path, recorded, no_index, monkeypatch
):
    """`installed` 的主语是**这个项目的受管环境**，不是随便哪个解释器。

    受管环境不在时我们退到基础解释器去查版本表（那部分与解释器无关），但
    「已经装了哪一版」是与解释器强相关的——拿基础解释器的答案回答这一页的
    问题就是量错了对象（[[name-the-subject-of-the-predicate]]）。
    """
    monkeypatch.setattr(managedenv, "python_of", lambda p: None)
    monkeypatch.setattr(deprepair, "base_python", lambda: "/base/python")
    out = deprepair.lookup_package(tmp_path, "lmfit")
    assert out["versions"], "版本表与解释器无关，照样要有"
    assert out["installed"] == "", "环境不在时不许拿基础解释器的答案冒充"


def test_installed_comes_from_the_managed_env_when_there_is_one(
    tmp_path, managed_env, recorded, no_index
):
    out = deprepair.lookup_package(tmp_path, "lmfit")
    assert out["installed"] == "1.3.2"


@pytest.mark.parametrize(
    ("answer", "expected"),
    [(True, "custom_index"), (False, "pypi"), (None, "unknown")],
)
def test_source_has_three_values_because_unknown_is_one_of_them(
    tmp_path, managed_env, recorded, monkeypatch, answer, expected
):
    """「问不出来」是独立一档，不能并进「就是 pypi」。

    并进去的话，一个连 `pip config list` 都跑不起来的环境会被界面说成
    「走的是官方 PyPI」——那是我们并不知道的事。
    """
    monkeypatch.setattr(deprepair, "custom_package_index", lambda python: answer)
    assert deprepair.lookup_package(tmp_path, "lmfit")["source"] == expected


def test_the_response_carries_no_paths_no_urls_and_no_pip_output(
    tmp_path, managed_env, recorded, monkeypatch
):
    """响应里只有包名、版本、三档 source。

    pip 的输出可能带 index 地址甚至凭据（`https://user:token@…`）。查找的
    响应**结构上**就不含它：既没有 `log` 字段，也不回显任何一行原文。
    """
    _, box = recorded
    box["text"] = (
        "Looking in indexes: https://user:token@pypi.internal.example/simple\n" + REAL_FOUND
    )
    monkeypatch.setattr(deprepair, "custom_package_index", lambda python: True)
    out = deprepair.lookup_package(tmp_path, "lmfit")
    assert set(out) == {"name", "versions", "latest", "installed", "source"}
    blob = repr(out)
    for leak in ("http", "token", "simple", str(tmp_path), str(managed_env)):
        assert leak not in blob, f"响应里漏出了 {leak}"


def test_the_version_table_is_bounded(tmp_path, managed_env, recorded, no_index):
    _, box = recorded
    many = ", ".join(f"1.0.{i}" for i in range(deprepair.LOOKUP_MAX_VERSIONS + 50))
    box["text"] = f"x (1.0.0)\nAvailable versions: {many}\n"
    out = deprepair.lookup_package(tmp_path, "lmfit")
    assert len(out["versions"]) == deprepair.LOOKUP_MAX_VERSIONS


def test_parsing_reads_only_the_two_prefixes_it_knows():
    versions, installed = deprepair.parse_index_versions(REAL_FOUND)
    assert versions[0] == "1.3.4" and installed == "1.3.2"
    assert deprepair.parse_index_versions("nothing useful here") == ([], "")
    # 没装过的包没有 INSTALLED 行
    assert deprepair.parse_index_versions("x (1.0)\nAvailable versions: 1.0\n") == (["1.0"], "")


# ===========================================================================
# 四、端点
# ===========================================================================
def test_lookup_is_registered_and_is_not_public():
    """这条路由不在公开面上。

    **未认证 401 的实际请求由 `test_browser_auth.py` 的枚举用例覆盖**——它遍历
    整个 `url_map`，新端点自动进它的范围。这里钉的是它没有被加进公开清单：
    公开清单的内容本身在那份文件里也被逐条钉住。
    """
    from tavotto import app as m

    paths = {str(r.rule) for r in m.app.url_map.iter_rules()}
    assert "/api/engine/packages/lookup" in paths
    assert "/api/engine/packages/lookup" not in security._PUBLIC_PATHS
    assert not "/api/engine/packages/lookup".startswith(security._PUBLIC_PREFIXES)


def test_lookup_endpoint_returns_the_payload(client, tmp_path, managed_env, recorded, no_index):
    from tavotto import app as m

    pid = m.open_project(str(tmp_path))["id"]
    try:
        resp = client.get(f"/api/engine/packages/lookup?pj={pid}&name=LMFit")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["name"] == "lmfit" and body["latest"] == "1.3.4"
        assert resp.headers["Cache-Control"] == "no-store"
    finally:
        m.close_project(pid, wait=True)


@pytest.mark.parametrize(
    ("rc", "text", "timed_out", "code", "status"),
    [
        (1, REAL_NOT_FOUND, False, "package_lookup_not_found", 404),
        (1, REAL_OFFLINE, False, "package_lookup_offline", 503),
        (1, "", True, "package_lookup_timeout", 504),
        (0, "unreadable", False, "package_lookup_failed", 502),
    ],
)
def test_lookup_endpoint_status_matches_the_next_step(
    client, tmp_path, managed_env, recorded, no_index, rc, text, timed_out, code, status
):
    """每个 code 一个状态码，且响应里**同时**有 code 与中文原文（老前端的回退）。"""
    from tavotto import app as m

    _, box = recorded
    box.update(rc=rc, text=text, timed_out=timed_out)
    pid = m.open_project(str(tmp_path))["id"]
    try:
        resp = client.get(f"/api/engine/packages/lookup?pj={pid}&name=lmfit")
        assert resp.status_code == status
        body = resp.get_json()
        assert body["code"] == code
        assert body["error"], "code 之外仍要有人可读的原文"
    finally:
        m.close_project(pid, wait=True)


def test_lookup_endpoint_refuses_a_bad_name_with_400(client, tmp_path, monkeypatch):
    from tavotto import app as m

    monkeypatch.setattr(deprepair, "_run_lookup", lambda argv: pytest.fail("不该出网"))
    pid = m.open_project(str(tmp_path))["id"]
    try:
        resp = client.get(f"/api/engine/packages/lookup?pj={pid}&name=-r%20evil.txt")
        assert resp.status_code == 400
        assert resp.get_json()["code"] == deprepair.ERROR_REQUIREMENT_INVALID
    finally:
        m.close_project(pid, wait=True)


def test_lookup_without_a_project_is_refused(client, monkeypatch):
    from tavotto import app as m

    monkeypatch.setattr(m, "DEFAULT_PROJECT", None)
    monkeypatch.setattr(deprepair, "_run_lookup", lambda argv: pytest.fail("不该出网"))
    resp = client.get("/api/engine/packages/lookup?name=lmfit")
    assert resp.status_code == 409
    assert resp.get_json()["code"] == "no_project"


def test_lookup_never_writes_to_the_managed_environment(tmp_path, managed_env, recorded, no_index):
    """查找是只读的：跑完之后环境目录里的文件与跑之前逐字节相同。"""
    before = {p: p.read_bytes() for p in managedenv.env_dir(tmp_path).rglob("*") if p.is_file()}
    deprepair.lookup_package(tmp_path, "lmfit")
    after = {p: p.read_bytes() for p in managedenv.env_dir(tmp_path).rglob("*") if p.is_file()}
    assert before == after
