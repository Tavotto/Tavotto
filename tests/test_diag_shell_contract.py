"""壳最小诊断（`src-tauri/src/shell_report.rs`，ADR 0130）与引擎诊断发送（`engine/diagsend.py`）的跨语言契约。

外层协议、幂等键、回执、失败码归并、上传地址白名单、包内容——**两侧不各抄一份 schema**：全部以
`tests/golden/diag_shell_report.json` 这一份样例为准，Rust 测试（`shell_report.rs` 的 `mod tests`）与这里各读一遍。
任何一侧改了外层协议或闭集而没改样例，对应那一侧的测试红；改了样例而没改另一侧，另一侧红。

主语：
* 「Rust 写出的 ZIP 合法」= **标准库 `zipfile` 与服务端同一组判据（`diagsend.zip_problem`）读它**，不是 Rust 自检；
* 「init 请求体的键」= 引擎真发出去、模拟服务端收到的那一份（`diag_fakes`），不是引擎里某个常量；
* 「壳包里没有自由文本」= 样例里每个值都落在闭集 / 固定形状里，且全文搜不到路径、令牌、环境变量的痕迹。
"""

from __future__ import annotations

import base64
import importlib.util
import io
import json
import re
import sys
import zipfile
from pathlib import Path

import pytest

from tavotto.engine import diagnostics, diagsend, telemetry

# 引擎侧发送流程的装置（模拟服务端 + 私有 CA）与 test_diag_send 同源，直接复用它的 fixture
from test_diag_send import (  # noqa: F401  (fixture 经导入进入本模块)
    _isolated,
    init_bodies,
    on,
    pki_files,
    send,
    server_tls,
    srv,
    wait_for,
)

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = json.loads((ROOT / "tests" / "golden" / "diag_shell_report.json").read_text("utf-8"))
SHELL_RS = ROOT / "src-tauri" / "src" / "shell_report.rs"


def _bundle() -> bytes:
    return base64.b64decode(CONTRACT["zip_base64"])


# --------------------------------------------------------------------------- 外层协议常量
def test_outer_protocol_constants_are_the_engines():
    assert CONTRACT["endpoint"] == diagsend.PRODUCTION_ENDPOINT
    assert CONTRACT["schema_version"] == diagnostics.BUNDLE_SCHEMA_VERSION, (
        "引擎升了 bundle schema：壳包的 schema_version 要跟着看一眼（report.json 形状是否仍成立）再改样例"
    )
    assert CONTRACT["category"] in diagsend.CATEGORIES
    assert CONTRACT["api_major"] == diagsend.API_MAJOR
    assert CONTRACT["api_version_header"] == diagsend.API_VERSION_HEADER
    assert CONTRACT["off_switch"] == {
        "hard_off_env": diagsend.ENV_HARD_OFF,
        "enable_env": diagsend.ENV_ENABLE,
    }
    auto = telemetry.AUTO_PROPS
    assert CONTRACT["distribution"] in auto["distribution"]["values"]
    assert CONTRACT["input"]["os"] in auto["platform"]["values"]
    assert CONTRACT["input"]["arch"] in auto["arch"]["values"]
    assert diagsend.PRODUCTION_ENDPOINT in {f"https://{h}" for h in diagsend.ENDPOINT_HOSTS}


def test_failure_vocabulary_and_retryability_are_the_engines():
    assert list(diagsend.FAILURES) == CONTRACT["failure_codes"]
    assert CONTRACT["retryable"] == diagsend.FAILURES


def test_classify_vectors_agree_with_the_engine():
    vectors = CONTRACT["classify_vectors"]
    assert len(vectors) >= 15
    for v in vectors:
        assert diagsend.classify(v["status"], v["server_code"]) == v["client_code"], v


def test_upload_url_vectors_agree_with_the_engine():
    target = diagsend.Target("tavotto.cn", 443, False)
    vectors = CONTRACT["upload_url_vectors"]
    assert len(vectors) >= 14
    for v in vectors:
        try:
            got = diagsend.validate_upload_url(v["url"], target)
        except diagsend._Failure as exc:
            assert v["normalized"] is None, (v, exc.code)
            assert exc.code == "upload_url_rejected"
        else:
            assert got == v["normalized"], v


# --------------------------------------------------------------------------- Rust 写出的包
def test_the_rust_written_zip_passes_the_servers_own_checks():
    data = _bundle()
    assert diagsend.zip_problem(data) is None
    info = diagsend.inspect_bundle(data)
    assert info.schema == CONTRACT["schema_version"]
    assert [n for n, _ in info.entries] == CONTRACT["entries"]
    assert dict(info.entries) == {"manifest.json": "manifest", "report.json": "environment"}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert z.testzip() is None, "CRC 不对"
        assert json.loads(z.read("manifest.json")) == CONTRACT["manifest"]
        assert json.loads(z.read("report.json")) == CONTRACT["report"]
        assert all(i.compress_type == zipfile.ZIP_STORED for i in z.infolist())


def test_the_rust_zip_is_accepted_by_the_receiving_service_when_its_checkout_is_here():
    """接收服务的 `zipcheck.inspect_bundle` 是服务端真实的校验（只读 import，不改 infra）。没有 infra 检出就跳过。"""
    path = Path(
        "/Volumes/Projects/tavotto-infra/services/diagnostics-api/diagnostics_api/zipcheck.py"
    )
    if not path.is_file():
        pytest.skip("没有 tavotto-infra 检出（CI 里不会有）")
    # 服务端两个模块只读 import：先把 contract 以 `contract` 之名登记，zipcheck 的相对导入改成绝对；用完撤掉登记
    try:
        for name, file in (("contract", path.with_name("contract.py")), ("infra_zipcheck", path)):
            text = file.read_text("utf-8").replace("from .contract import", "from contract import")
            mod = importlib.util.module_from_spec(
                importlib.util.spec_from_loader(name, loader=None)
            )
            sys.modules[name] = mod
            exec(compile(text, str(file), "exec"), mod.__dict__)  # noqa: S102 — 只读本机检出的服务端校验
        facts = sys.modules["infra_zipcheck"].inspect_bundle(_bundle())
    finally:
        sys.modules.pop("contract", None)
        sys.modules.pop("infra_zipcheck", None)
    assert facts.manifest_schema == CONTRACT["schema_version"]
    assert facts.tavotto_version == CONTRACT["input"]["version"]
    assert "unknown_entry" not in facts.flags and "missing_report" not in facts.flags


def test_the_sample_documents_have_no_free_text_only_closed_sets_and_fixed_shapes():
    i = CONTRACT["input"]
    assert i["stage"] in CONTRACT["stages"] and i["code"] in CONTRACT["codes"]
    assert re.fullmatch(r"[0-9A-Za-z.+_-]{1,32}", i["version"])
    assert isinstance(i["exit_code"], (int, type(None)))
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", i["created_at"])
    for code in CONTRACT["codes"]:
        assert re.fullmatch(r"[a-z_]+", code)
    keys: list[str] = []

    def walk(v):
        if isinstance(v, dict):
            for k, x in v.items():
                keys.append(k)
                walk(x)

    walk(CONTRACT["manifest"])
    walk(CONTRACT["report"])
    assert not set(keys) & set(CONTRACT["forbidden_keys"])
    text = json.dumps([CONTRACT["manifest"], CONTRACT["report"]])
    for needle in ("/Users", "\\\\", "C:", "nonce", "token", "stderr", "PATH=", "http"):
        assert needle not in text, needle
    for doc in (CONTRACT["manifest"], CONTRACT["report"]):
        assert doc["source"] == "shell" and doc["partial"] is True


# --------------------------------------------------------------------------- init 请求体
def test_the_engines_init_body_has_the_same_keys_as_the_shells(request):
    """引擎真发出去的 init 请求体（模拟服务端收到的）与壳样例的键集合相同——一侧加字段另一侧就红。"""
    server = request.getfixturevalue("on")  # 经导入进入本模块的 fixture；按名取，免得形参遮住导入名
    prep = send(_bundle(), note=None, category=CONTRACT["category"])
    wait_for(prep["id"], {"done"})
    body = init_bodies(server)[0]
    assert set(body) == set(CONTRACT["init_keys"])
    assert not set(body) & set(CONTRACT["forbidden_init_keys"])
    assert body["category"] == CONTRACT["category"]
    assert body["bundle_schema"] == CONTRACT["schema_version"]


def test_the_shell_source_pins_the_same_protocol_literals():
    """Rust 测试逐字读样例；这里再钉一道：源码里的端点字面量与样例同一个（防只改一边还能过 include_str）。"""
    if not SHELL_RS.is_file():
        pytest.skip("没有 src-tauri/（wheel/sdist 里不含桌面壳）")
    src = SHELL_RS.read_text("utf-8")
    assert f'pub const PRODUCTION_ENDPOINT: &str = "{CONTRACT["endpoint"]}";' in src
    assert f"pub const BUNDLE_SCHEMA: u32 = {CONTRACT['schema_version']};" in src
