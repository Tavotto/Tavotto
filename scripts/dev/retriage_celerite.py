"""Summarize Celerite diagnostic artifacts without running the scientific script.

Run with an unpacked research-s0 artifact directory and an output directory.
The optional old fidelity directory checks whether the historical PNGs repeat.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/ci"))
import compat_guardian_research as research  # noqa: E402
import compat_matrix  # noqa: E402
import compat_research_sources as sources  # noqa: E402
import pixelcompare  # noqa: E402

CASE = "research_astro_celerite_sho"


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def arrays(figure):
    return [[(line["x"], line["y"]) for line in ax["lines"]] for ax in figure["axes"]]


def styles(figure):
    return [[(line["color"], line["linewidth"]) for line in ax["lines"]] for ax in figure["axes"]]


def numeric_summary(path: Path, case: dict) -> dict:
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    registration = next(row for row in research.load_cases() if row["id"] == CASE)
    project = sources.project_for_case(registration)
    expected = project["files"]["tests/compat/" + registration["script"]]
    if not rows or any(row.get("source_sha256") != expected for row in rows):
        raise ValueError("missing observations, diagnostic error, or unexpected source")
    returned = [row for row in rows if row["phase"] == "script_return"]
    counts = Counter(row["leg"] for row in returned)
    required = {
        "native-execs.jsonl": case["executions"]["native"],
        "tavotto-execs.jsonl": case["executions"]["tavotto"],
        **case["raw"]["diagnostic_execution_counts"],
    }
    if dict(counts) != required or any(not count for count in required.values()):
        raise ValueError("numeric observations do not cover the audited executions")
    seeds = [row for row in rows if row["phase"] == "seed_return"]
    if Counter((r["leg"], r["pid"]) for r in seeds) != Counter(
        (r["leg"], r["pid"]) for r in returned
    ):
        raise ValueError("each execution must have exactly one seed observation")
    reference = next(row for row in returned if row["leg"] == "native-execs.jsonl")
    figures = [row["figure"] for row in rows if "figure" in row]
    return {
        "source_sha256": expected,
        "log_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "records": len(rows),
        "executions_by_leg": dict(counts),
        "figure_observations": len(figures),
        "line_counts": [len(ax["lines"]) for ax in reference["figure"]["axes"]],
        "all_arrays_equal": all(arrays(fig) == arrays(reference["figure"]) for fig in figures),
        "script_line_styles_equal": all(
            styles(row["figure"]) == styles(reference["figure"]) for row in returned
        ),
        "seed_state_count": len({row["rng_sha256"] for row in seeds}),
        "return_state_count": len({row["rng_sha256"] for row in returned}),
        "covariance_identity_count": len({digest(row["last_covariance"]) for row in returned}),
        "numpy_versions": sorted({row["numpy"] for row in returned}),
    }


def pdf_summary(evidence: Path, out: Path) -> dict:
    import pikepdf
    import pypdfium2 as pdfium

    files = {"native": "native-pdf/paper/figures/sho.pdf", "zero_edit": "zero-edit/sho.pdf"}
    facts, operators, text, boxes = {}, {}, {}, {}
    for key, relative in files.items():
        path = evidence / relative
        with pdfium.PdfDocument(path) as doc:
            page = doc[0]
            size = page.get_size()
            bitmap = page.render(scale=compat_matrix.FIDELITY_WIDTH / size[0])
            image = bitmap.to_pil().convert("RGBA")
            image.save(out / f"{key}.png")
            facts[key] = {
                "pdf_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "page_pt": size,
                "pixels": image.size,
                "rgba_sha256": hashlib.sha256(image.tobytes()).hexdigest(),
            }
            textpage = page.get_textpage()
            text[key] = textpage.get_text_range()
            boxes[key] = [textpage.get_charbox(i) for i in range(textpage.count_chars())]
            textpage.close()
            bitmap.close()
            page.close()
        with pikepdf.open(path) as doc:
            stream = list(pikepdf.parse_content_stream(doc.pages[0]))
            operators[key] = {
                op: [str(item.operands) for item in stream if str(item.operator) == op]
                for op in ("w", "RG", "rg", "G", "g", "Tf")
            }
            facts[key]["clip_rectangles_pt"] = list(
                dict.fromkeys(
                    tuple(float(value) for value in item.operands)
                    for item in stream
                    if str(item.operator) == "re"
                )
            )
    metrics = pixelcompare.compare(out / "native.png", out / "zero_edit.png")
    passed, reasons = pixelcompare.verdict(metrics, compat_matrix.FIDELITY_TOLERANCE)
    same_text = text["native"] == text["zero_edit"]
    delta = None
    if same_text and len(boxes["native"]) == len(boxes["zero_edit"]):
        delta = max(
            abs(a - b)
            for aa, bb in zip(boxes["native"], boxes["zero_edit"], strict=True)
            for a, b in zip(aa, bb, strict=True)
        )
    return {
        "reader_version": importlib.metadata.version("pypdfium2"),
        "files": facts,
        "style_operator_sequences_equal": operators["native"] == operators["zero_edit"],
        "text_equal": same_text,
        "maximum_text_coordinate_delta_pt": delta,
        "metrics": metrics,
        "existing_tolerance": compat_matrix.FIDELITY_TOLERANCE,
        "passes_existing_tolerance": passed,
        "reasons": reasons,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--previous-fidelity", type=Path)
    args = parser.parse_args()
    reports = list(args.artifact.glob("research-s0/*/reports/guardian.json"))
    if len(reports) != 1:
        raise ValueError("expected exactly one producing Guardian report")
    report = json.loads(reports[0].read_text())
    if len(report["cases"]) != 1 or report["cases"][0]["id"] != CASE:
        raise ValueError("expected the registered Celerite case")
    case = report["cases"][0]
    evidence = reports[0].parents[1] / "evidence" / CASE
    args.out.mkdir(parents=True, exist_ok=True)
    summary = {
        "schema": "tavotto-celerite-retriage/1",
        "source_commit": report["source"]["commit"],
        "guardian_sha256": hashlib.sha256(reports[0].read_bytes()).hexdigest(),
        "original_fidelity": case["raw"]["detail"]["fidelity"]["metrics"],
        "numeric": numeric_summary(evidence / "numeric.jsonl", case),
        "pdf": pdf_summary(evidence, args.out),
    }
    if args.previous_fidelity:
        summary["historical_png_bytes_reproduced"] = {
            name: (args.previous_fidelity / f"sho.{name}.png").read_bytes()
            == (evidence / "fidelity" / f"sho.{name}.png").read_bytes()
            for name in ("control", "tavotto")
        }
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(args.out / "summary.json")


if __name__ == "__main__":
    main()
