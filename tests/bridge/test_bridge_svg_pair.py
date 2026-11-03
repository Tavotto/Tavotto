"""Native owner-thread export pairs leave the user's figure and rcParams intact."""

import hashlib
import json
import xml.etree.ElementTree as ET

import pymupdf

from support.bridgekit import write


def test_native_pair_is_state_neutral_and_keeps_script_font_policy(tmp_path, bridge_session):
    script = write(
        tmp_path / "paired.py",
        "import json\n"
        'import matplotlib\nmatplotlib.use("Agg")\n'
        "import matplotlib.pyplot as plt\n"
        'plt.rcParams["svg.fonttype"] = "none"\n'
        'with open("runs.txt", "a") as stream:\n    stream.write("run\\n")\n'
        "fig, ax = plt.subplots(figsize=(3, 2))\n"
        "ax.plot([0, 1], [0, 1])\n"
        'ax.set_title("Script title")\n'
        "plt.show()\n"
        'with open("after.json", "w") as stream:\n'
        '    json.dump({"title": ax.title.get_text(), "fonttype": plt.rcParams["svg.fonttype"]}, stream)\n',
    )
    with bridge_session(script, cwd=str(tmp_path)) as session:
        session.wait_event("barrier")
        stem = next(iter(session.ensure_built()["stems"]))
        manifest = session.override(stem, [])["manifest"]
        title = next(e["gid"] for e in manifest["elements"] if e.get("role") == "title")
        hot = [{"gid": title, "prop": "text", "value": "Hot title"}]
        session.override(stem, hot)
        before = session.render_png(stem, 400)
        # BridgeSession returns the raw worker response for render_png.
        from pathlib import Path

        png_before = Path(before["path"]).read_bytes()
        pdf, svg = tmp_path / "pair.pdf", tmp_path / "pair.svg"
        response = session.export(
            stem,
            [{"gid": title, "prop": "text", "value": "Export title"}],
            str(pdf),
            dpi=300,
            svg_path=str(svg),
        )
        assert response["svg"]["sha256"] == hashlib.sha256(svg.read_bytes()).hexdigest()
        assert not ET.fromstring(svg.read_bytes()).findall(".//{http://www.w3.org/2000/svg}text")
        with pymupdf.open(pdf) as doc:
            assert "Export title" in doc[0].get_text()
        assert Path(session.render_png(stem, 400)["path"]).read_bytes() == png_before
        assert (tmp_path / "runs.txt").read_text(encoding="utf-8") == "run\n"
        session.resume()
        session.wait_event("barrier")
        after = json.loads((tmp_path / "after.json").read_text(encoding="utf-8"))
        assert after == {"title": "Script title", "fonttype": "none"}
