"""`scripts/dev/data_figure_bench.py`：手动跑分入口。这里只用假回答自测；`--real` 的护栏（要显式上限、没有连接就不发请求）也只测到「拒绝」这一步，
绝不对真模型发请求。子进程跑，数据 / 配置目录指到临时目录。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "dev" / "data_figure_bench.py"


def bench(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src"),
        "TAVOTTO_DATA_DIR": str(tmp_path / "d"),
        "TAVOTTO_CONFIG_DIR": str(tmp_path / "c"),
        "TAVOTTO_NO_TELEMETRY": "1",
        "PYTHONUTF8": "1",
    }
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=120,
        cwd=tmp_path,
    )


def test_fake_good_answers_score_full_marks_and_write_a_report(tmp_path):
    out = tmp_path / "report.json"
    done = bench(tmp_path, "--fake", "good", "--out", str(out))
    assert done.returncode == 0, done.stderr
    report = json.loads(out.read_text(encoding="utf-8"))
    s = report["summary"]
    assert report["mode"] == "fake:good" and s["cases"] >= 12
    assert s["pass_rate"] == 1.0 and s["fallback_rate"] == 0 and s["violation_rate"] == 0
    assert s["requests"] == s["cases"] and s["cost_usd"] is None


def test_fake_mixed_answers_lower_the_score(tmp_path):
    out = tmp_path / "report.json"
    done = bench(
        tmp_path,
        "--fake",
        "mixed",
        "--wire",
        "openai-chat",
        "--only",
        "DF-01,DF-04",
        "--out",
        str(out),
    )
    assert done.returncode == 0, done.stderr
    s = json.loads(out.read_text(encoding="utf-8"))["summary"]
    assert s["cases"] == 2 and 0 < s["pass_rate"] < 1


def test_real_mode_refuses_without_an_explicit_call_cap(tmp_path):
    for args in (["--real"], ["--real", "--max-calls", "3"]):
        done = bench(tmp_path, *args)
        assert done.returncode == 2 and "没有发出任何请求" in done.stderr


def test_real_mode_without_a_connection_sends_nothing(tmp_path):
    done = bench(tmp_path, "--real", "--max-calls", "100")
    assert done.returncode == 2 and "没有可用的连接" in done.stderr


def test_max_calls_counts_real_http_requests_not_complete_calls(tmp_path):
    """Codex 复现形状：404 换协议 + 非 JSON 重试 = 一条用例 3 个请求。上限按实际请求数硬拦，到顶如实报 stopped_early。"""
    import sys as _sys

    _sys.path.insert(0, str(ROOT / "tests" / "support"))
    import data_figure_cases as D

    from tavotto.engine import ai_data_model

    case = D.load_cases()[0]
    folder = D.materialize(case, tmp_path)
    profile, _ = D.inputs_of(case, folder)
    conn = {**D.fake_conn("openai-chat"), "fallbacks": [("anthropic", "https://fake.invalid")]}
    t = D.FakeTransport(
        [
            404,
            D.reply_body("anthropic", "不是 JSON"),
            D.reply_body("anthropic", D.good_answer(case)),
        ]
    )
    out = ai_data_model.propose_recipes(profile, conn=conn, post=t, max_requests=2)
    assert out.call.reason == "over_budget" and out.call.requests == 2 == len(t.calls)


def test_script_stops_early_when_the_request_cap_is_reached(tmp_path):
    out = tmp_path / "r.json"
    done = bench(tmp_path, "--fake", "good", "--max-calls", "3", "--out", str(out))
    assert done.returncode == 0, done.stderr
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["stopped_early"] is True and report["requests_used"] == 3
    assert len(report["cases"]) == 3


# --------------------------------------------------------------------------- --key-file（只用假传输；绝不联网）
KEY = "sk-test-SECRET-KEY-0123456789abcdef"


def load_bench():
    import importlib.util

    spec = importlib.util.spec_from_file_location("data_figure_bench_inproc", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def key_file(tmp_path, content=KEY + "\n", mode=0o600):
    p = tmp_path / "eval.key"
    p.write_text(content, encoding="ascii")
    p.chmod(mode)
    return p


def test_key_file_flag_combinations_are_refused_without_any_request(tmp_path):
    kf = key_file(tmp_path)
    for args in (
        ["--key-file", str(kf), "--model", "gpt-5"],  # 没有 --real
        ["--real", "--key-file", str(kf), "--max-calls", "50"],  # 没有 --model
        ["--real", "--key-file", str(kf), "--model", "gpt-5"],  # 没有 --max-calls
        ["--model", "gpt-5"],  # 没有 --key-file
        [
            "--real",
            "--key-file",
            str(kf),
            "--model",
            "gpt-5",
            "--max-calls",
            "50",
            "--effort",
            "max",
        ],
    ):
        done = bench(tmp_path, *args)
        assert done.returncode == 2, args
        assert KEY not in done.stdout + done.stderr


@pytest.mark.skipif(os.name == "nt", reason="权限位是 POSIX 的概念")
@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o666])
def test_key_file_wider_than_600_is_refused_and_says_why(tmp_path, mode):
    kf = key_file(tmp_path, mode=mode)
    done = bench(tmp_path, "--real", "--key-file", str(kf), "--model", "gpt-5", "--max-calls", "50")
    assert done.returncode == 2 and "权限太宽" in done.stderr and "没有发出任何请求" in done.stderr
    assert KEY not in done.stdout + done.stderr


@pytest.mark.parametrize("content", ["", "two\nlines\n", "has space\n", "sk-密钥\n"])
def test_malformed_key_files_are_refused_without_echoing_content(tmp_path, content):
    kf = tmp_path / "bad.key"
    kf.write_text(content, encoding="utf-8")
    kf.chmod(0o600)
    done = bench(tmp_path, "--real", "--key-file", str(kf), "--model", "gpt-5", "--max-calls", "50")
    assert done.returncode == 2 and "没有发出任何请求" in done.stderr
    assert content.strip() not in done.stdout + done.stderr or not content.strip()


class Recorder:
    """假的 `post_json`：记下请求（含头里的 key——那是它该收到的），回录好的回答；`mode` 控制失败形状。"""

    def __init__(self, answers, mode="ok"):
        self.answers, self.mode, self.calls = answers, mode, []

    def __call__(self, url, headers, body, *, timeout, max_bytes=None):
        self.calls.append({"url": url, "headers": dict(headers), "body": body})
        if self.mode == "leaky":  # 异常文字里带了 key：任何输出都不许出现它
            raise RuntimeError(f"boom {headers.get('Authorization')}")
        if self.mode == "401":
            return (
                401,
                {},
                json.dumps({"error": f"bad key {headers.get('Authorization')}"}).encode(),
            )
        text = self.answers.pop(0)
        return (
            200,
            {},
            json.dumps(
                {
                    "status": "completed",
                    "output": [
                        {"type": "message", "content": [{"type": "output_text", "text": text}]}
                    ],
                    "usage": {"input_tokens": 1500, "output_tokens": 900},
                }
            ).encode(),
        )


def run_inproc(tmp_path, monkeypatch, capsys, recorder, *extra, case="RC-01", suite="roles"):
    mod = load_bench()
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "d0"))
    monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(tmp_path / "c0"))
    import sys as _sys

    _sys.path.insert(0, str(ROOT / "src"))
    from tavotto.engine import ai_data_roles, ai_structured

    ai_data_roles.reset_for_tests()  # 进程内多次跑：负缓存 / 任务登记不能串到下一条用例
    monkeypatch.setattr(ai_structured, "post_json", recorder)
    kf = key_file(tmp_path)
    out = tmp_path / "report.json"
    saved = (_sys.stdout, _sys.stderr)
    try:
        rc = mod.main(
            [
                "--suite", suite, "--real", "--key-file", str(kf), "--model", "gpt-5",
                "--max-calls", "4", "--only", case, "--out", str(out), *extra,
            ]
        )  # fmt: skip
    finally:
        _sys.stdout, _sys.stderr = saved
    cap = capsys.readouterr()
    return rc, cap.out + cap.err, (out.read_text(encoding="utf-8") if out.exists() else "")


def test_key_file_run_with_a_fake_transport_reports_usage_and_never_leaks_the_key(
    tmp_path, monkeypatch, capsys, caplog
):
    import sys as _sys

    _sys.path.insert(0, str(ROOT / "tests" / "support"))
    import data_figure_roles as DR

    case = next(c for c in DR.load_role_cases() if c["id"] == "RC-01")
    rec = Recorder([json.dumps(case["model_answer"])])
    caplog.set_level("DEBUG")
    rc, text, report = run_inproc(tmp_path, monkeypatch, capsys, rec)
    assert rc == 0, text
    for blob in (text, report, caplog.text):
        assert KEY not in blob
    data = json.loads(report)
    assert data["mode"] == "real" and data["requests_used"] == 1 == len(rec.calls)
    assert data["connection"] == {
        "source": "key-file",
        "wire": "openai-responses",
        "model": "gpt-5",
        "effort": "low",
        "base_url_host": "api.openai.com",
    }
    s = data["summary"]
    assert (s["input_tokens"], s["output_tokens"]) == (1500, 900)
    assert s["cost_usd"] == pytest.approx((1500 * 1.25 + 900 * 10.0) / 1_000_000)  # 按定价表
    call = rec.calls[0]
    assert call["url"] == "https://api.openai.com/v1/responses"
    assert call["headers"]["Authorization"] == f"Bearer {KEY}"  # key 只出现在发给服务商的头里
    assert call["body"]["model"] == "gpt-5" and call["body"]["reasoning"] == {"effort": "low"}
    assert "输出（含推理）" in text
    # 没写应用配置 / provider 存储
    assert not (tmp_path / "c0").exists() or not any((tmp_path / "c0").rglob("*"))


def _role_rec():
    import sys as _sys

    _sys.path.insert(0, str(ROOT / "tests" / "support"))
    import data_figure_roles as DR

    case = next(c for c in DR.load_role_cases() if c["id"] == "RC-01")
    return Recorder([json.dumps(case["model_answer"])])


def test_auto_small_switches_model_and_effort_only_when_asked(tmp_path, monkeypatch, capsys):
    rec = _role_rec()
    rc, text, report = run_inproc(tmp_path, monkeypatch, capsys, rec, "--auto-small")
    assert rc == 0, text
    assert rec.calls[0]["body"]["model"] == "gpt-6-luna"
    assert rec.calls[0]["body"]["reasoning"] == {"effort": "none"}
    data = json.loads(report)
    assert data["connection"]["effort"] is None  # 临时连接不带 effort：自动策略自己定


def test_explicit_effort_none_is_sent_as_given_and_model_is_not_switched(
    tmp_path, monkeypatch, capsys
):
    rec = _role_rec()
    rc, text, _report = run_inproc(tmp_path, monkeypatch, capsys, rec, "--effort", "none")
    assert rc == 0, text
    assert rec.calls[0]["body"]["model"] == "gpt-5"
    assert rec.calls[0]["body"]["reasoning"] == {"effort": "none"}


def test_auto_small_flag_combinations_are_refused(tmp_path):
    kf = key_file(tmp_path)
    base = ["--real", "--key-file", str(kf), "--model", "gpt-5", "--max-calls", "50"]
    for args in (
        [*base, "--auto-small", "--effort", "low"],  # 与 --effort 互斥
        ["--auto-small"],  # 没有 --real
        [*base, "--suite", "recipes", "--auto-small"],  # 只给 roles
    ):
        done = bench(tmp_path, *args)
        assert done.returncode == 2, args
        assert KEY not in done.stdout + done.stderr


@pytest.mark.parametrize("mode", ["leaky", "401"])
def test_key_never_appears_in_outputs_even_when_errors_echo_it(
    tmp_path, monkeypatch, capsys, caplog, mode
):
    caplog.set_level("DEBUG")
    rc, text, report = run_inproc(tmp_path, monkeypatch, capsys, Recorder([], mode))
    assert rc == 0
    for blob in (text, report, caplog.text):
        assert KEY not in blob
    assert json.loads(report)["summary"]["failures"]  # 失败如实记下，只是不带 key


def test_key_file_recipes_suite_uses_the_same_connection_and_hard_cap(
    tmp_path, monkeypatch, capsys
):
    import sys as _sys

    _sys.path.insert(0, str(ROOT / "tests" / "support"))
    import data_figure_cases as D

    case = next(c for c in D.load_cases() if c["id"] == "DF-01")
    rec = Recorder([D.good_answer(case)])
    rc, text, report = run_inproc(tmp_path, monkeypatch, capsys, rec, case="DF-01", suite="recipes")
    assert rc == 0 and KEY not in text + report
    assert json.loads(report)["connection"]["effort"] == "low" and len(rec.calls) == 1
    assert rec.calls[0]["body"]["reasoning"] == {"effort": "low"}


def test_scrub_and_report_writer_replace_the_key_as_a_last_line_of_defence(tmp_path):
    import io
    import types

    mod = load_bench()
    buf = io.StringIO()
    mod._Scrub(buf, KEY).write(f"leak {KEY} here")
    assert KEY not in buf.getvalue() and "***" in buf.getvalue()
    kf = key_file(tmp_path)
    out = tmp_path / "r.json"
    mod._write_report(types.SimpleNamespace(out=str(out), key_file=str(kf)), {"x": f"oops {KEY}"})
    assert KEY not in out.read_text(encoding="utf-8")


def test_arm_rules_refuses_real_and_key_file_before_touching_any_credential(tmp_path):
    missing = tmp_path / "nope.key"  # 不存在：若先读 key 文件，报错会是「读不到」而不是 arm 冲突
    for args in (
        ["--suite", "roles", "--arm", "rules", "--real", "--max-calls", "50"],
        [
            "--suite",
            "roles",
            "--arm",
            "rules",
            "--real",
            "--key-file",
            str(missing),
            "--model",
            "gpt-5",
            "--max-calls",
            "50",
        ],
    ):
        done = bench(tmp_path, *args)
        assert done.returncode == 2 and "--arm rules" in done.stderr, args
        assert "没有可用的连接" not in done.stderr and "读不到" not in done.stderr


def test_arm_rules_makes_zero_transport_calls_and_resolves_no_connection(
    tmp_path, monkeypatch, capsys
):
    import sys as _sys

    mod = load_bench()
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "d0"))
    monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(tmp_path / "c0"))
    _sys.path.insert(0, str(ROOT / "src"))
    from tavotto.engine import ai_data_roles, ai_structured

    ai_data_roles.reset_for_tests()
    rec = Recorder([])
    monkeypatch.setattr(ai_structured, "post_json", rec)

    def boom():
        raise AssertionError("resolve_connection must not run")

    monkeypatch.setattr(ai_structured, "resolve_connection", boom)
    out = tmp_path / "r.json"
    saved = (_sys.stdout, _sys.stderr)
    try:
        rc = mod.main(
            ["--suite", "roles", "--arm", "rules", "--only", "RC-01,RC-02", "--out", str(out)]
        )
    finally:
        _sys.stdout, _sys.stderr = saved
    capsys.readouterr()
    assert rc == 0 and rec.calls == []
    report = json.loads(out.read_text(encoding="utf-8"))
    assert (
        report["mode"] == "rules-only"
        and report["requests_used"] == 0
        and report["connection"] is None
    )


# --------------------------------------------------------------------------- roles 套件：--repeats / --cases / 统计 / 门
def roles_report(tmp_path, *args):
    out = tmp_path / "roles.json"
    done = bench(tmp_path, "--suite", "roles", *args, "--out", str(out))
    assert done.returncode == 0, done.stderr + done.stdout
    return json.loads(out.read_text(encoding="utf-8")), done.stdout


def test_repeats_with_good_fake_answers_count_every_run_and_every_request(tmp_path):
    rep, text = roles_report(tmp_path, "--fake", "good", "--repeats", "3", "--only", "RC-01,RC-19")
    st = rep["stats"]
    assert rep["repeats"] == 3 and (st["cases"], st["runs"], st["repeats"]) == (2, 6, 3)
    assert st["completion_rate"] == 1.0 and st["all_runs_correct_share"] == 1.0
    assert st["per_case"]["RC-01"]["done"] == 3 and st["rules_broken_runs"] == 0
    assert st["requests"] == 6 == rep["requests_used"] and st["requests_per_table"] == 1.0
    assert st["latency_ms"]["per_call"]["n"] == 6 and st["latency_ms"]["per_table"]["n"] == 6
    assert len(rep["cases"]) == 6 and {c["attempt"] for c in rep["cases"]} == {1, 2, 3}
    assert all(len(c["calls"]) == 1 for c in rep["cases"])
    assert "完成率 100.0%" in text and "每次运行都用独立的空缓存" in text and "每张表平均" in text


def test_mixed_fake_answers_make_the_per_case_counts_vary(tmp_path):
    rep, _ = roles_report(tmp_path, "--fake", "mixed", "--repeats", "2", "--only", "RC-01,RC-19")
    st = rep["stats"]
    # RC-01 规则会错：good 那次对、trap（什么都不改）那次错；RC-19 规则本来就对：两次都对
    assert (st["per_case"]["RC-01"]["done"], st["per_case"]["RC-19"]["done"]) == (1, 2)
    assert st["completion_rate"] == 0.75 and st["all_runs_correct_share"] == 0.5
    assert st["rules_broken_runs"] == 0 and st["not_worse_than_rules_rate"] == 1.0


def test_cases_flag_selects_the_set_and_the_report_names_it(tmp_path):
    hold, _ = roles_report(tmp_path, "--fake", "good", "--cases", "holdout")
    assert hold["case_set"] == "holdout" and hold["stats"]["cases"] == 30
    assert {c["id"][:3] for c in hold["cases"]} == {"HO-"}
    both, _ = roles_report(tmp_path, "--fake", "good", "--cases", "all", "--only", "RC-01,HO-01")
    assert [c["id"] for c in both["cases"]] == ["RC-01", "HO-01"]
    done = bench(tmp_path, "--suite", "roles", "--cases", "holdout", "--only", "RC-01")
    assert done.returncode == 2 and "未知用例" in done.stderr  # 用例集之外的 id 不放行


def test_repeats_and_cases_are_for_the_roles_suite_only_and_bounded(tmp_path):
    for args in (
        ["--repeats", "2"],
        ["--cases", "holdout"],
        ["--suite", "roles", "--repeats", "0"],
    ):
        done = bench(tmp_path, *args)
        assert done.returncode == 2, args


def test_real_cap_must_cover_cases_times_two_times_repeats_before_any_request(tmp_path):
    done = bench(
        tmp_path,
        "--suite",
        "roles",
        "--real",
        "--max-calls",
        "11",
        "--repeats",
        "3",
        "--only",
        "RC-01,RC-02",
    )
    assert done.returncode == 2 and "× 3 = 12" in done.stderr and "没有发出任何请求" in done.stderr
    # 够了才会往下走到解析连接（这里没有连接 → 照旧不发请求）
    ok = bench(
        tmp_path,
        "--suite",
        "roles",
        "--real",
        "--max-calls",
        "12",
        "--repeats",
        "3",
        "--only",
        "RC-01,RC-02",
    )
    assert ok.returncode == 2 and "没有可用的连接" in ok.stderr


def test_the_request_cap_stops_a_repeated_fake_run_early(tmp_path):
    rep, _ = roles_report(
        tmp_path, "--fake", "good", "--repeats", "3", "--only", "RC-01", "--max-calls", "2"
    )
    assert rep["stopped_early"] is True and rep["requests_used"] == 2 and len(rep["cases"]) == 2


def test_real_repeats_bypass_the_cache_and_each_run_sends_its_own_request(
    tmp_path, monkeypatch, capsys
):
    import sys as _sys

    _sys.path.insert(0, str(ROOT / "tests" / "support"))
    import data_figure_roles as DR

    case = next(c for c in DR.load_role_cases() if c["id"] == "RC-01")
    rec = Recorder([json.dumps(case["model_answer"]), json.dumps(case["model_answer"])])
    rc, text, report = run_inproc(tmp_path, monkeypatch, capsys, rec, "--repeats", "2")
    assert rc == 0, text
    assert len(rec.calls) == 2  # 第二次没有读缓存
    data = json.loads(report)
    st = data["stats"]
    assert data["requests_used"] == 2 and st["runs"] == 2 and st["completion_rate"] == 1.0
    assert st["input_tokens"] == 3000 and st["output_tokens"] == 1800
    assert st["cost_per_table_usd"] == pytest.approx((1500 * 1.25 + 900 * 10.0) / 1_000_000)
    assert st["latency_ms"]["per_call"]["n"] == 2 and st["cached_input_tokens"] == 0
    assert KEY not in text + report
    assert not (tmp_path / "d0").exists() or not list(
        (tmp_path / "d0").rglob("ai_data_roles/*.json")
    )


def test_arm_rules_prints_the_gate_table_and_the_hard_zero(tmp_path):
    rep, text = roles_report(tmp_path, "--arm", "rules", "--cases", "all")
    assert (
        rep["mode"] == "rules-only"
        and rep["requests_used"] == 0
        and rep["stats"]["completion_rate"] == 0.0
    )
    gate = rep["gate"]
    assert set(gate["versions"]) == {"strict", "medium", "loose"}
    for v in ("strict", "medium"):
        assert gate["versions"][v]["all"]["skipped_but_rules_wrong"] == 0
    assert gate["rules_right"]["all"]["cases"] == 52
    assert "必须 0" in text and "holdout-plain" in text
