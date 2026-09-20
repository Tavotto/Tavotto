"""U01.harness：case enrollment 台账、结果装配与校验器（`tests/support/foundation_harness.py`），
以及本阶段**唯一一条 enforced 的真实产品用例** `U01-S1`。

三组用例，三个主语：

1. **台账**——32 个 FO 场景逐一在台账里、与 registry 一致；派生 md 不腐烂；enforced 的
   case 指向本文件里真实存在的函数。
2. **校验器的负例**——错项目 / 错 generation / 空报告 / 重复 ID / 错 SHA 各自红；
   「空集合 ⇒ 通过」被钉死为红。每条都在临时目录里用合成记录构造，不碰产品。
3. **`U01-S1`**——`single_file_csv` 夹具经**真实 HTTP 服务**（`python -m tavotto`，会话认证
   默认开、凭据文件交接）走完：401 默认拒绝 → 准备接口拿到真回执（worker 自报的
   prefix 与独立探针一致）→ 渲染（自动缩放的 ylim 由 truth.json 的数值推出）→ 带一条
   override 的旧后端导出 PDF/PNG → 独立读回（PDF 文字层含改过的字、PNG 尺寸按 IHDR）→
   写一条结果记录（`TAVOTTO_FOUNDATION_RESULTS` 指定目录时进 CI 校验）。终点是旧后端
   （D06），报告里 `backend=pymupdf`，**不是** RenderCore 资格。
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

SUPPORT = Path(__file__).resolve().parent / "support"
if str(SUPPORT) not in sys.path:
    sys.path.insert(0, str(SUPPORT))

import foundation_harness as fh  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PACK = ROOT / "docs" / "implementation" / "tavotto-foundation"
FIXTURE = ROOT / "tests" / "fixtures" / "foundation" / "single_file_csv"
CASE_ID = "U01-S1"

# ================================================================ 一、台账


def test_the_ledger_is_well_formed_and_agrees_with_the_registry():
    ledger = fh.load_ledger()
    registry = json.loads((PACK / "registry.json").read_text(encoding="utf-8"))
    assert fh.ledger_errors(ledger, registry) == []
    scenario_ids = {e["id"] for e in registry["entries"] if e["kind"] == "scenario"}
    assert len(scenario_ids) == 32
    assert scenario_ids <= {c["case_id"] for c in ledger["cases"]}


def test_the_enforced_set_is_exactly_what_u03_promoted_and_each_points_at_a_real_test():
    """台账的 enforced 集合是工程事实（03 §3）：U01 的 U01-S1 + U03 提上来的六条首开场景
    （FO01 / FO02 / FO03 / FO07 / FO15 / FO19）；每条都指向真实存在的用例函数。多一条 / 少一条
    都要有人改这里——enrollment 不是随手加的标签。"""
    ledger = fh.load_ledger()
    enforced = {
        c["case_id"]: c for c in ledger["cases"] if c["enrollment"] == fh.ENROLLMENT_ENFORCED
    }
    assert set(enforced) == {CASE_ID, "FO01", "FO02", "FO03", "FO07", "FO15", "FO19"}
    file_part, func = enforced[CASE_ID]["test"].split("::", 1)
    assert Path(file_part).name == Path(__file__).name
    assert func in globals() and callable(globals()[func])
    for cid, case in enforced.items():
        file_part, func = case["test"].split("::", 1)
        src = (ROOT / file_part).read_text(encoding="utf-8")
        assert f"def {func}(" in src, f"{cid} 指向的用例不存在"
        assert case["fixture"] and (ROOT / case["fixture"]).is_dir(), cid
    counts = {}
    for c in ledger["cases"]:
        counts[c["enrollment"]] = counts.get(c["enrollment"], 0) + 1
    assert counts == {
        "planned": 21,
        "observing": 4,
        "later": 1,
        "enforced": 7,
    }  # 33 条：32 个 FO + U01-S1
    # safe_stop 的 case 也能 enforced，但台账预期必须写明是 safe_stop（校验器据此分开计数）
    assert enforced["FO15"]["expected_product_outcome"] == "safe_stop"


def test_the_derived_markdown_is_current():
    proc = subprocess.run(
        [sys.executable, str(PACK / "tools" / "generate_enrollment.py"), "--check"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr


def test_ledger_check_catches_a_drifted_or_malformed_ledger(tmp_path):
    ledger = fh.load_ledger()
    registry = json.loads((PACK / "registry.json").read_text(encoding="utf-8"))
    drifted = json.loads(json.dumps(ledger))
    next(c for c in drifted["cases"] if c["case_id"] == "FO04")["enrollment"] = "enforced"
    errs = fh.ledger_errors(drifted, registry)
    assert any("FO04" in e and "不一致" in e for e in errs)
    assert any("FO04" in e and "pytest" in e for e in errs)
    missing = json.loads(json.dumps(ledger))
    missing["cases"] = [c for c in missing["cases"] if c["case_id"] != "FO07"]
    assert any("FO07" in e for e in fh.ledger_errors(missing, registry))
    dup = json.loads(json.dumps(ledger))
    dup["cases"].append(dict(dup["cases"][0]))
    assert any("重复" in e for e in fh.ledger_errors(dup, registry))
    dangling = json.loads(json.dumps(ledger))
    row = next(c for c in dangling["cases"] if c["case_id"] == CASE_ID)
    row["test"] = "tests/test_foundation_harness.py::test_that_does_not_exist"
    assert any("没有定义" in e for e in fh.ledger_errors(dangling, registry))


def test_enforced_test_existence_is_judged_by_ast_not_by_substring(tmp_path):
    """Codex（#455 转办，P1）：`def {func}(` 子串会把注释里的名字、别的函数**里面**的嵌套函数
    都当成「用例存在」。判据改成 AST：模块级 FunctionDef，或 `Class::method` 按结构逐级找；
    参数化 id（`func[x]`）剥掉再判。四个负例各自红、两个正例绿。"""
    registry = json.loads((PACK / "registry.json").read_text(encoding="utf-8"))
    test_file = tmp_path / "test_probe.py"
    test_file.write_text(
        "# def test_only_in_a_comment():\n"
        "def outer():\n"
        "    def test_nested_inside_outer():\n"
        "        pass\n"
        "\n\n"
        "def test_module_level():\n"
        "    pass\n"
        "\n\n"
        "class TestGroup:\n"
        "    def test_method(self):\n"
        "        pass\n",
        encoding="utf-8",
    )
    rel = test_file.relative_to(ROOT).as_posix() if test_file.is_relative_to(ROOT) else None
    assert rel is None  # tmp 目录不在仓库里：走 root= 参数

    def ledger_for(target: str) -> dict:
        return {
            "schema_version": 1,
            "capability_version": "u01",
            "cases": [
                {
                    "case_id": "X",
                    "title": "t",
                    "scenario_refs": [],
                    "stage": "U01",
                    "enrollment": "enforced",
                    "lane": "pr",
                    "fixture": "tests/fixtures/foundation/single_file_csv",
                    "test": target,
                }
            ],
        }

    def errors(target: str) -> list[str]:
        return [
            e
            for e in fh.ledger_errors(ledger_for(target), root=tmp_path)
            if "X:" in e and "test_probe" in e
        ]

    assert errors("test_probe.py::test_module_level") == []
    assert errors("test_probe.py::TestGroup::test_method") == []
    assert errors("test_probe.py::test_module_level[param-1]") == []
    for bad in (
        "test_probe.py::test_only_in_a_comment",
        "test_probe.py::test_nested_inside_outer",
        "test_probe.py::TestGroup::test_only_in_a_comment",
        "test_probe.py::test_method",  # 方法不在模块级
        "test_probe.py::TestGroup",  # 类不是用例：最后一级必须是函数
    ):
        assert errors(bad), bad
    del registry


# ================================================================ 二、校验器负例


def _ledger_one(lane="pr", enrollment="enforced"):
    return {
        "schema_version": 1,
        "capability_version": "u01",
        "cases": [
            {
                "case_id": CASE_ID,
                "title": "t",
                "scenario_refs": ["FO01"],
                "stage": "U01",
                "enrollment": enrollment,
                "lane": lane,
                "entry": "http",
                "fixture": "tests/fixtures/foundation/single_file_csv",
                "test": "tests/test_foundation_harness.py::test_u01_s1_first_open_and_export_through_the_public_entry"
                if enrollment == "enforced"
                else None,
                "expected_product_outcome": "automatic",
            }
        ],
    }


def _expected(lane="pr", enrollment="enforced"):
    return fh.expected_instances(_ledger_one(lane, enrollment), lane)


def _record(expected, **over):
    inst = expected["instances"][0]
    fields = dict(
        case_id=inst["case_id"],
        binding=dict(inst["binding"]),
        product_outcome="automatic",
        test_verdict="pass",
        observed={
            "receipt": {"generation": 1},
            "artifacts": [{"origin": "execution", "generation": 1, "kind": "pdf"}],
        },
    )
    fields.update(over)
    return fh.ResultRecord(**fields)


def test_a_matching_record_passes_and_the_reports_say_so(tmp_path):
    exp = _expected()
    fh.write_result(_record(exp), tmp_path / "r")
    report = fh.validate(exp, tmp_path / "r")
    assert report["ok"] is True, report["problems"]
    assert report["valid_count"] == report["expected_count"] == 1
    paths = fh.write_reports(report, exp, tmp_path / "out")
    junit = paths["junit"].read_text(encoding="utf-8")
    assert 'failures="0"' in junit and "closed_set_check" in junit
    assert "OK" in paths["summary"].read_text(encoding="utf-8")
    assert json.loads(paths["json"].read_text(encoding="utf-8"))["ok"] is True


def test_an_empty_expected_set_is_never_a_pass(tmp_path):
    """变异「空集合 ⇒ 通过」必红：没有 enforced case 的 lane，校验一律红。"""
    exp = _expected(enrollment="planned")
    assert exp["instances"] == [] and exp["planned_cases"][0]["case_id"] == CASE_ID
    (tmp_path / "r").mkdir()
    report = fh.validate(exp, tmp_path / "r")
    assert report["ok"] is False
    assert [p["kind"] for p in report["problems"]] == ["empty_expected_set"]


def test_an_empty_report_is_red(tmp_path):
    exp = _expected()
    (tmp_path / "r").mkdir()
    report = fh.validate(exp, tmp_path / "r")
    assert report["ok"] is False
    assert {p["kind"] for p in report["problems"]} == {"empty_report", "missing"}
    # 目录根本不存在也是红，而且说得出为什么
    report = fh.validate(exp, tmp_path / "nope")
    assert report["ok"] is False and any(p["kind"] == "unreadable" for p in report["problems"])


def test_a_duplicate_instance_is_red(tmp_path):
    exp = _expected()
    rec = _record(exp)
    fh.write_result(rec, tmp_path / "r")
    # 同一实例的第二份记录：换个文件名，内容一样
    (tmp_path / "r" / "second.json").write_text(
        json.dumps(rec.to_payload(), ensure_ascii=False), encoding="utf-8"
    )
    report = fh.validate(exp, tmp_path / "r")
    assert report["ok"] is False
    assert [p["kind"] for p in report["problems"]] == ["duplicate"]


def test_a_record_from_another_sha_is_red(tmp_path):
    exp = _expected()
    other = _record(exp, binding={**exp["instances"][0]["binding"], "source_sha": "0" * 40})
    fh.write_result(other, tmp_path / "r")
    report = fh.validate(exp, tmp_path / "r")
    assert report["ok"] is False
    assert {p["kind"] for p in report["problems"]} == {"unexpected", "missing"}
    # 只改 SHA 不改 id 的「旧报告」：id 与绑定对不上，同样红
    forged = _record(exp).to_payload()
    forged["binding"]["source_sha"] = "0" * 40
    shutil.rmtree(tmp_path / "r")
    (tmp_path / "r").mkdir()
    (tmp_path / "r" / "forged.json").write_text(json.dumps(forged), encoding="utf-8")
    report = fh.validate(exp, tmp_path / "r")
    assert any(p["kind"] == "identity_mismatch" for p in report["problems"])


def test_a_record_from_another_project_is_red(tmp_path):
    """错项目 = fixture 身份不同（另一份数据跑出来的结果）。"""
    exp = _expected()
    other = _record(exp, binding={**exp["instances"][0]["binding"], "fixture_sha256": "f" * 64})
    fh.write_result(other, tmp_path / "r")
    report = fh.validate(exp, tmp_path / "r")
    assert report["ok"] is False
    assert any(p["kind"] == "unexpected" for p in report["problems"])


def test_a_record_whose_artifact_came_from_another_generation_is_red(tmp_path):
    exp = _expected()
    stale = _record(
        exp,
        observed={
            "receipt": {"generation": 2},
            "artifacts": [{"origin": "execution", "generation": 1, "kind": "pdf"}],
        },
    )
    fh.write_result(stale, tmp_path / "r")
    report = fh.validate(exp, tmp_path / "r")
    assert report["ok"] is False
    assert [p["kind"] for p in report["problems"]] == ["wrong_generation"]


def test_non_pass_verdicts_are_reported_by_kind_and_never_count(tmp_path):
    exp = _expected()
    for verdict, outcome in (
        ("product_failure", "failed"),
        ("infra_error", "not_run"),
        ("safe_stop", "safe_stop"),
    ):
        shutil.rmtree(tmp_path / "r", ignore_errors=True)
        fh.write_result(_record(exp, test_verdict=verdict, product_outcome=outcome), tmp_path / "r")
        report = fh.validate(exp, tmp_path / "r")
        assert report["ok"] is False
        assert [p["kind"] for p in report["problems"]] == [verdict]
        assert report["valid_count"] == 0 and report["by_verdict"] == {verdict: 1}
    for outcome in ("failed", "not_run"):
        with pytest.raises(ValueError, match="pass"):
            _record(exp, test_verdict="pass", product_outcome=outcome)


def test_a_pass_must_land_on_the_outcome_the_ledger_expected(tmp_path):
    """预期的产品结果在执行前写死（05 §7）：safe_stop 的 case 跑出了 automatic 不是「更好」，
    是没停下来；automatic 的 case 跑出了 safe_stop 也不是通过。safe_stop 的通过单独计数，
    不进自动兼容成功的分子。"""
    ledger = _ledger_one()
    ledger["cases"][0]["expected_product_outcome"] = "safe_stop"
    exp = fh.expected_instances(ledger, "pr")
    assert exp["instances"][0]["expected_product_outcome"] == "safe_stop"
    # 跑成 automatic：不是通过
    fh.write_result(_record(exp, product_outcome="automatic"), tmp_path / "a")
    report = fh.validate(exp, tmp_path / "a")
    assert report["ok"] is False
    assert [p["kind"] for p in report["problems"]] == ["outcome_mismatch"]
    # 跑成 safe_stop 且 verdict=pass：通过，但不计入兼容成功
    fh.write_result(_record(exp, product_outcome="safe_stop"), tmp_path / "b")
    report = fh.validate(exp, tmp_path / "b")
    assert report["ok"] is True
    assert report["passed_by_outcome"] == {"safe_stop": 1}
    assert report["compatibility_successes"] == 0
    # 反向：台账预期 automatic，记录说 safe_stop → 不通过
    exp2 = _expected()
    fh.write_result(_record(exp2, product_outcome="safe_stop"), tmp_path / "c")
    report = fh.validate(exp2, tmp_path / "c")
    assert report["ok"] is False and report["problems"][0]["kind"] == "outcome_mismatch"


def test_the_cli_exit_code_is_the_verdict(tmp_path):
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps(_ledger_one()), encoding="utf-8")
    exp_path = tmp_path / "expected.json"
    cmd = [sys.executable, str(SUPPORT / "foundation_harness.py")]
    run = lambda *a: subprocess.run(  # noqa: E731
        [*cmd, *a], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120
    )
    assert (
        run("expected", "--ledger", str(ledger), "--lane", "pr", "--out", str(exp_path)).returncode
        == 0
    )
    exp = json.loads(exp_path.read_text(encoding="utf-8"))
    (tmp_path / "r").mkdir()
    red = run(
        "validate",
        "--expected",
        str(exp_path),
        "--results",
        str(tmp_path / "r"),
        "--report-dir",
        str(tmp_path / "o"),
    )
    assert red.returncode == 1, red.stdout
    assert (tmp_path / "o" / "foundation-harness-junit.xml").is_file()
    fh.write_result(_record(exp), tmp_path / "r")
    green = run(
        "validate",
        "--expected",
        str(exp_path),
        "--results",
        str(tmp_path / "r"),
        "--report-dir",
        str(tmp_path / "o"),
    )
    assert green.returncode == 0, green.stdout
    assert run("ledger-check").returncode == 0


# ================================================================ 三、U01-S1（真实产品路径）

try:
    from tavotto.engine import pool as _pool

    WORKER_PY = _pool.find_worker_python()
except Exception:  # noqa: BLE001 — 没有科学栈就 skip，而 skip 在 CI 校验步里是红
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)


def _smoke_module():
    spec = importlib.util.spec_from_file_location(
        "_u01_smoke_app", ROOT / "scripts" / "smoke_app.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _call(url: str, payload: dict | None, headers: dict, timeout: float):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", **headers} if data else dict(headers),
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def _png_size(path: Path) -> tuple[int, int]:
    head = path.read_bytes()[:24]
    assert head[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", head[16:24])


def _pdf_text(path: Path) -> str:
    import pymupdf  # 旧后端的读取栈：U01 的终点就是它，这里只读

    with pymupdf.open(str(path)) as doc:
        return "\n".join(page.get_text() for page in doc)


def _independent_prefix(python: str) -> dict:
    out = subprocess.run(
        [
            python,
            "-c",
            "import sys, json; print(json.dumps({'prefix': sys.prefix, 'executable': sys.executable}))",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=True,
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


@needs_worker
def test_u01_s1_first_open_and_export_through_the_public_entry(tmp_path):
    truth = json.loads((FIXTURE / "truth.json").read_text(encoding="utf-8"))
    ledger = fh.load_ledger()
    case = next(c for c in ledger["cases"] if c["case_id"] == CASE_ID)
    binding = fh.binding_from_environment(
        ledger=ledger, entry=case["entry"], fixture=case["fixture"]
    )
    out_dir = fh.results_dir() or (tmp_path / "results")
    evidence: list[str] = []
    observed: dict = {"backend": case.get("backend"), "native_reference_python": WORKER_PY}

    # ---- 假想用户环境：夹具的独立副本 + 用户自己跑过一次脚本（磁盘上有 figure.pdf）
    proj = tmp_path / "proj"
    shutil.copytree(FIXTURE, proj)
    native = subprocess.run(
        [WORKER_PY, "figure.py"],
        cwd=proj,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        env={**os.environ, "MPLCONFIGDIR": str(tmp_path / "mpl")},
    )
    assert native.returncode == 0, native.stderr[-2000:]
    assert (proj / truth["expected_output"]).is_file()
    evidence.append(
        f"native reference: {truth['expected_output']} {(proj / truth['expected_output']).stat().st_size} bytes"
    )

    # ---- 真实公共入口：python -m tavotto，会话认证默认开
    smoke = _smoke_module()
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    work = tmp_path / "work"
    data_dir, config_dir = work / "data", work / "config"
    for d in (data_dir, config_dir, work / "home"):
        d.mkdir(parents=True)
    env = {
        **os.environ,
        "TAVOTTO_DATA_DIR": str(data_dir),
        "TAVOTTO_CONFIG_DIR": str(config_dir),
        "HOME": str(work / "home"),
        "TAVOTTO_NO_UPDATE_CHECK": "1",
        "TAVOTTO_NO_TELEMETRY": "1",
        "TAVOTTO_ALLOW_SHUTDOWN": "1",
    }
    env.pop("TAVOTTO_INSECURE_NO_AUTH", None)
    log = (work / "server.log").open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "tavotto",
            "--port",
            str(port),
            "--no-browser",
            "--figures",
            str(proj),
        ],
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        cwd=str(tmp_path),
    )
    t_start = time.time()
    try:
        deadline = time.time() + 120
        while True:
            assert proc.poll() is None, (
                f"服务在就绪前退出: {(work / 'server.log').read_text('utf-8')[-2000:]}"
            )
            try:
                _call(f"{base}/api/version", None, {}, 5)
                break
            except (urllib.error.URLError, OSError):
                assert time.time() < deadline, "120 s 内 /api/version 仍不可访问"
                time.sleep(0.5)
        observed["app_ready_s"] = round(time.time() - t_start, 2)

        # 默认拒绝（ADR 0008）→ 凭据文件交接（唯一实现在 smoke_app）
        with pytest.raises(urllib.error.HTTPError) as denied:
            _call(f"{base}/api/panels", None, {}, 10)
        assert denied.value.code == 401
        assert smoke.adopt_session_credentials(data_dir, port), "本机会话凭据文件缺失"
        auth = dict(smoke._AUTH)
        evidence.append(
            "auth: unauthenticated /api/panels → 401; credentials adopted from data_dir"
        )

        _, panels = _call(f"{base}/api/panels", None, auth, 30)
        panel = next(p for p in panels["panels"] if p["id"] == truth["expected_output"])
        assert panel["script"] == "figure.py" and panel["capability"]["status"] == "editable"

        # ---- 准备接口：真回执
        t0 = time.time()
        status, prep = _call(f"{base}/api/engine/preparation", {"id": panel["id"]}, auth, 30)
        assert status == 202
        plan = prep["plan"]
        assert plan["static_source"]["source_id"] == truth["expected_output"]
        assert plan["launch_context"]["cwd_origin"] == "sandbox"
        assert plan["dependency_intents"] == []  # 夹具没有声明文件：空列表是事实，不是 unknown
        deadline = time.time() + 300
        while True:
            _, state = _call(f"{base}/api/engine/preparation/{plan['plan_id']}", None, auth, 30)
            if state["result"]["status"] in ("ready", "error", "cancelled"):
                break
            assert time.time() < deadline, state
            time.sleep(0.25)
        observed["prepare_s"] = round(time.time() - t0, 2)
        result = state["result"]
        assert result["status"] == "ready", result
        receipt = result["receipt"]
        assert receipt["completeness"] == "complete"
        assert receipt["descriptors"][0]["stem"] == "figure"
        assert result["created_runtime"] is True and result["existing_runtime"] is None
        observed["receipt"] = receipt
        evidence.append(
            f"receipt {receipt['receipt_id']} generation={receipt['generation']} python_source={receipt['python_source']}"
        )

        # 回执自报的解释器 = 产品实际选的那一个（独立探针）：投影里没有 prefix，
        # 用 /api/engine/environment 报的 python 去问它自己
        _, envst = _call(f"{base}/api/engine/environment", None, auth, 30)
        chosen = envst["project"]["python"]
        chosen_path = chosen if os.path.isabs(chosen) else str(proj / chosen)
        probe = _independent_prefix(chosen_path)
        assert receipt["runtime"]["python_version"] == _independent_prefix_version(chosen_path)
        observed["chosen_python"] = {
            "path_reported": chosen,
            "prefix": probe["prefix"],
            "source": envst["project"]["source"],
        }

        # 二开不重复准备（FO-031 的形状）：再准备一次，报告现有 runtime、不新起
        _, prep2 = _call(f"{base}/api/engine/preparation", {"id": panel["id"]}, auth, 30)
        deadline = time.time() + 60
        while True:
            _, state2 = _call(
                f"{base}/api/engine/preparation/{prep2['plan']['plan_id']}", None, auth, 30
            )
            if state2["result"]["status"] in ("ready", "error", "cancelled"):
                break
            assert time.time() < deadline
            time.sleep(0.1)
        assert state2["result"]["status"] == "ready"
        assert state2["result"]["existing_runtime"] == {
            "generation": receipt["generation"],
            "built": True,
            "control_plane": receipt["control_plane"],
        }
        assert state2["result"]["created_runtime"] is False
        assert state2["result"]["receipt"]["receipt_id"] == receipt["receipt_id"]

        # ---- 渲染：已知数值到达了坐标轴（自动缩放 5% 边距由 truth 推出）
        t0 = time.time()
        _, render = _call(
            f"{base}/api/engine/render", {"id": panel["id"], "patches": []}, auth, 300
        )
        observed["render_s"] = round(time.time() - t0, 2)
        elements = render["manifest"]["elements"]
        axes = next(e for e in elements if e["role"] == "axes")
        ylim = next(f["value"] for f in axes["editable"] if f["prop"] == "ylim")
        ys = truth["y"]
        margin = 0.05 * (max(ys) - min(ys))
        assert ylim == pytest.approx([min(ys) - margin, max(ys) + margin], abs=1e-6)
        observed["plotted_ylim"] = ylim
        evidence.append(f"render: {len(elements)} elements; ylim {ylim} == truth ± 5% margin")

        # ---- 带一条 override 的导出（旧后端终点）：改 y 轴标题，PDF 文字层必须带着它。
        # 不改 x 轴标题：这份夹具（figsize 3.2×2.4）的 xlabel 本来就落在图幅之外
        # （manifest bbox y ≈ 1.012 > 1），原生 figure.pdf 的文字层里也没有它——
        # 那是夹具的性质，预检 `element-outside-figure` 会说，不是本链路的缺陷。
        ylabel = next(
            e
            for e in elements
            if any(f["prop"] == "text" and f.get("value") == "y" for f in e.get("editable", []))
        )
        patch = {"gid": ylabel["gid"], "prop": "text", "value": "y (edited by U01-S1)"}
        t0 = time.time()
        status, export = _call(
            f"{base}/api/export",
            {
                "scope": "original",
                "formats": ["pdf", "png"],
                "filename": "u01-s1",
                "ppi": 150,
                "original": {
                    "figure_id": panel["id"],
                    "overrides": [patch],
                    "source_kind": "figure",
                },
            },
            auth,
            600,
        )
        observed["export_s"] = round(time.time() - t0, 2)
        assert status == 200 and export["status"] == "done", export
        export_dir = Path(export["export_dir"])
        names = {o["format"]: o["name"] for o in export["outputs"] if o["status"] == "done"}
        assert set(names) == {"pdf", "png"}
        pdf_path, png_path = export_dir / names["pdf"], export_dir / names["png"]
        assert export["warnings"] == [], export["warnings"]
        assert "y (edited by U01-S1)" in _pdf_text(pdf_path)
        assert "y = 1.5x + 1" in _pdf_text(pdf_path)
        w, h = _png_size(png_path)
        assert w > 0 and h > 0
        png_out = next(o for o in export["outputs"] if o["format"] == "png")
        assert png_out["dimensions"]["px"] == [w, h]
        # 产物身份：与回执同一代（generation）——校验器的「错 generation」判据的正面样本
        from tavotto.engine import figcapture  # 纯标准库

        artifacts = [
            figcapture.source_artifact_from_file(
                p,
                source_id=receipt["descriptors"][0]["asset_id"],
                origin=figcapture.ORIGIN_EXECUTION,
                receipt_id=receipt["receipt_id"],
                generation=receipt["generation"],
                patch_hash=None,
                receipt_identity=receipt["public_identity"],
            ).to_payload()
            for p in (pdf_path, png_path)
        ]
        observed["artifacts"] = artifacts
        observed["export"] = {"outputs": export["outputs"], "warnings": export["warnings"]}
        evidence.append(
            f"export: {names['pdf']} {pdf_path.stat().st_size} B (text layer has the override), {names['png']} {w}x{h}"
        )

        # 干净退出
        _call(f"{base}/api/shutdown", {}, auth, 30)
        proc.wait(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=30)
        log.close()

    record = fh.ResultRecord(
        case_id=CASE_ID,
        binding=binding,
        product_outcome="automatic",
        test_verdict="pass",
        observed=observed,
        evidence=tuple(evidence),
    )
    path = fh.write_result(record, out_dir)
    assert path.is_file()


def _independent_prefix_version(python: str) -> str:
    out = subprocess.run(
        [python, "-c", "import platform; print(platform.python_version())"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=True,
    )
    return out.stdout.strip()
