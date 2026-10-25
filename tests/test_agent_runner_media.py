"""Evidence reaches the model request, never the progress log; writes stay scoped.

The HTTP assertions inspect the receiving server's request bytes. Preview order,
compression and stale append failures check observable results, not source text.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json

import pytest

from tavotto.engine.agent_runner import loop, media, tools, wires
from tavotto.engine.agent_runner.redact import Redactor
from tests.test_agent_runner import WIRE_CASES, FakeModelServer

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def session(tmp_path, **kw):
    (tmp_path / "a.R").write_text("plot(1:3)\n")
    return tools.ConvertSession(
        tools.RunEnvelope(
            "convert_to_editable", tmp_path, "a.R", "convert", output="a_matplotlib.py", **kw
        )
    )


@pytest.mark.parametrize("wire", sorted(WIRE_CASES))
def test_images_reach_each_real_http_request_and_never_events(tmp_path, monkeypatch, wire):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    (tmp_path / "fig.py").write_text("print('source')\n")
    (tmp_path / "reference.png").write_bytes(PNG)
    turns = json.loads(
        json.dumps(WIRE_CASES[wire][0])
        .replace("read_file", "read_image")
        .replace("fig.py", "reference.png")
    )
    srv = FakeModelServer(turns)
    try:
        model = loop.HttpModel(
            wires.WIRES[wire],
            base_url=srv.url,
            api_key="test",
            model="m",
            request_timeout=3,
            retry_base=0,
        )
        events = []
        result = loop.run(
            model,
            tools.FigureEditToolset(),
            tools.RunEnvelope("figure_edit", tmp_path, "fig.py", "view reference"),
            events.append,
        )
        assert result.ok
        body = srv.requests[1][2]
        if wire == "anthropic":
            blocks = body["messages"][-1]["content"][0]["content"]
            got = next(b["source"]["data"] for b in blocks if b["type"] == "image")
        elif wire == "openai-chat":
            assert body["messages"][-2]["role"] == "tool"
            blocks = body["messages"][-1]["content"]
            got = next(
                b["image_url"]["url"].split(",", 1)[1] for b in blocks if b["type"] == "image_url"
            )
        else:
            blocks = body["input"][-1]["output"]
            got = next(
                b["image_url"].split(",", 1)[1] for b in blocks if b["type"] == "input_image"
            )
        assert base64.b64decode(got) == PNG
        assert got not in json.dumps(events) and "reference.png" not in json.dumps(events)
    finally:
        srv.close()


def test_image_outside_and_disguised_text_are_not_disclosed(tmp_path):
    root = tmp_path / "candidate"
    root.mkdir()
    s = session(root)
    (tmp_path / "outside.png").write_bytes(PNG)
    (root / "fake.png").write_text("private plain text")
    (root / "escape.png").symlink_to(tmp_path / "outside.png")
    for path in ("../outside.png", "escape.png", "fake.png"):
        result = s.read_image({"path": path})
        assert result.is_error and not result.images
        assert "private plain text" not in result.content


def test_image_swap_during_open_is_rejected_before_read(tmp_path, monkeypatch):
    s = session(tmp_path)
    image = tmp_path / "ref.png"
    image.write_bytes(PNG)
    original_open = tools.os.open

    def swapped(path, flags, *args, **kw):
        if str(path) == str(image):
            image.unlink()
            image.write_bytes(PNG + b"private")
        return original_open(path, flags, *args, **kw)

    monkeypatch.setattr(tools.os, "open", swapped)
    result = s.read_image({"path": "ref.png"})
    assert result.is_error and result.code == "image_changed" and not result.images


def test_check_images_have_order_and_old_images_are_compressed(tmp_path):
    images = [media.from_bytes(PNG, "page 1"), media.from_bytes(PNG, "page 2")]
    s = session(tmp_path, check=lambda: media.CheckResult(True, "2 pages captured", images))
    assert not s.create_file({"content": "import matplotlib.pyplot as plt\n"}).is_error
    result = s.check({})
    assert [img.label for img in result.images] == ["page 1", "page 2"]
    history = [wires.Msg("tool", results=[result])]
    before = loop.estimate_tokens("", [], history)
    history.append(wires.Msg("tool", results=[wires.ToolResult("next", "read_file", "next")]))
    assert loop.compress(history) == 1
    assert not result.images and "not the current" in result.content
    assert loop.estimate_tokens("", [], history) < before


def test_ipc_images_are_validated_and_capped():
    good = media.from_bytes(PNG, "page").as_dict()
    bad = {**good, "data": "https://example.com/private"}
    mismatch = {**good, "mime_type": "image/jpeg"}
    assert media.from_wire([bad, mismatch]) == []
    assert len(media.from_wire([good] * 20)) == media.MAX_CHECK_IMAGES


def test_chunked_write_is_complete_and_duplicate_append_cannot_change_bytes(tmp_path):
    s = session(tmp_path)
    first = "import matplotlib.pyplot as plt\n"
    assert not s.create_file({"content": first}).is_error
    sha = hashlib.sha256(first.encode()).hexdigest()
    args = {"content": "plt.plot([1, 2])\n", "expected_sha256": sha}
    assert not s.append_file(args).is_error
    dest = tmp_path / "a_matplotlib.py"
    expected = (first + args["content"]).encode()
    assert dest.read_bytes() == expected
    assert s.append_file(args).code == "file_changed"
    assert dest.read_bytes() == expected
    assert (tmp_path / "a.R").read_text() == "plot(1:3)\n"


def test_append_checks_entire_result_for_secrets_and_refuses_other_files(tmp_path):
    secret = "sk-canary-for-append-123456789"
    s = session(tmp_path, redactor=Redactor(secret))
    first = "# " + secret[:10]
    assert not s.create_file({"content": first}).is_error
    sha = hashlib.sha256(first.encode()).hexdigest()
    args = {"content": secret[10:], "expected_sha256": sha}
    assert s.append_file(args).code == "secret_in_content"
    for path, code in (
        ("a.R", "source_readonly"),
        ("other.py", "not_output"),
        ("../x.py", "path_outside"),
    ):
        assert s.append_file({**args, "path": path}).code == code
    assert (tmp_path / "a_matplotlib.py").read_text() == first


def test_text_only_tool_protocol_is_unchanged():
    result = wires.ToolResult("id", "read_file", "plain")
    history = [wires.Msg("tool", results=[result])]
    for wire in wires.WIRES.values():
        request = wire.build(
            base_url="https://example.com",
            api_key="x",
            model="m",
            system="s",
            tools=[],
            history=copy.deepcopy(history),
            effort=None,
            max_output_tokens=100,
        )
        assert "image" not in json.dumps(request.body)


@pytest.mark.parametrize("thinking", ["adaptive", "budget"])
def test_compression_respects_thinking_prefix_binding(tmp_path, thinking):
    from dataclasses import replace

    (tmp_path / "plot.py").write_text("# input\n" * 500)
    requests = []
    prefixes = {}

    def post(_url, _headers, body, *, timeout):
        request = copy.deepcopy(body)
        requests.append(request)
        messages = request["messages"]
        for i, msg in enumerate(messages):
            for block in msg["content"]:
                if block["type"] in ("thinking", "redacted_thinking"):
                    if messages[:i] != prefixes[block["signature"]]:
                        return 400, {}, b'{"error":{"type":"invalid_request_error"}}'
        turn = len(requests)
        if turn < 3:
            signature = "signed-" + str(turn)
            prefixes[signature] = copy.deepcopy(messages)
            content = [
                {"type": "thinking", "thinking": "private reasoning", "signature": signature},
                {
                    "type": "tool_use",
                    "id": "read" + str(turn),
                    "name": "read_file",
                    "input": {"path": "plot.py"},
                },
            ]
            stop = "tool_use"
        else:
            content = [{"type": "text", "text": "done"}]
            stop = "end_turn"
        return 200, {}, json.dumps({"content": content, "stop_reason": stop}).encode()

    model = loop.HttpModel(
        wires.AnthropicWire(),
        base_url="https://example.invalid",
        api_key="test",
        model="test",
        thinking=thinking,
        effort="high",
        post=post,
    )
    envelope = tools.RunEnvelope(
        task_type="figure_edit",
        cwd=tmp_path,
        target="plot.py",
        instruction="inspect",
        limits=replace(tools.Limits(), compress_at_tokens=1),
    )
    events = []
    result = loop.run(model, tools.FigureEditToolset(), envelope, events.append)
    assert result.ok and len(requests) == 3
    last = requests[-1]["messages"]
    if thinking == "adaptive":
        assert result.compressions == 1
        assert not any(
            block["type"] in ("thinking", "redacted_thinking")
            for msg in last
            for block in msg["content"]
        )
        assert requests[-1]["thinking"] == {"type": "adaptive"}
        assert requests[-1]["output_config"] == {"effort": "high"}
    else:
        assert result.compressions == 0
        assert requests[-1]["messages"][: len(requests[-2]["messages"])] == requests[-2]["messages"]
    calls = [b["id"] for m in last for b in m["content"] if b["type"] == "tool_use"]
    results = [b["tool_use_id"] for m in last for b in m["content"] if b["type"] == "tool_result"]
    assert calls == results == ["read1", "read2"]
    assert "private reasoning" not in str(events)
