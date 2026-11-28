"""开源科研图标库的离线构建脚本（ADR 0222）。**不进运行时、不进安装包。**

    python -I scripts/iconlib/build_iconlib.py --cache <仓库外的缓存目录> --out <输出目录> [--limit 20]

流程：抓取（限速、可续）→ 许可过滤 → 预检（同 `svgimport` 判据）→ 出处元数据 manifest → 中英索引 index → 审计 audit。
下载的 SVG 是不可信数据：只当字节存盘、只交给 `svgimport` 的受限解析器；缓存目录不是脚本目录，也不会被当代码执行。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import iconlib_index  # noqa: E402
import iconlib_license  # noqa: E402
import iconlib_normalize  # noqa: E402
import iconlib_sources  # noqa: E402


def make_manifest_entry(rec: dict, info: dict, pre: dict) -> dict:
    """候选记录 + 取得信息 + 预检结果 → 一条出处元数据。`modified` 一律 True：入库后我们会分组、出色槽，算改编。"""
    short, url = iconlib_license.LICENSES[rec["license_id"]]
    return {
        "id": rec["id"],
        "title": rec.get("title", ""),
        "category": rec.get("category", ""),
        "source": rec["source"],
        "via": rec.get("via"),
        "source_url": rec.get("source_url"),
        "file_url": info["url"],
        "creator": rec.get("creator"),
        "creator_url": rec.get("creator_url"),
        "license_id": rec["license_id"],
        "license_name": short,
        "license_url": url,
        "copyright_notice": rec.get("copyright_notice"),
        "modified": True,
        "retrieved_at": info["retrieved_at"],
        "sha256": info["sha256"],
        "paths": pre["paths"],
        "bytes": pre["bytes"],
        "size_mm": pre["size_mm"],
        "slots": pre["slots"],
        "groups": pre["groups"],
        "quality": pre["quality"],
        "description": rec.get("description", ""),
        "synonyms": rec.get("synonyms") or [],
        "references": rec.get("references") or [],
    }


def process(rec: dict, fetcher, *, max_bytes: int, max_paths: int, audit: list) -> tuple | None:
    got = fetcher.get(rec["url"])
    if got is None:
        audit.append(
            {"id": rec["id"], "source": rec["source"], "stage": "fetch", "reason": "fetch_failed"}
        )
        return None
    svg, info = got
    pre = iconlib_normalize.precheck(svg, max_bytes=max_bytes, max_paths=max_paths)
    if not pre["ok"]:
        audit.append(
            {
                "id": rec["id"],
                "source": rec["source"],
                "stage": "precheck",
                "reason": ",".join(pre["reasons"]),
                "bytes": pre["bytes"],
                "paths": pre.get("paths"),
            }
        )
        return None
    return svg, info, pre


def run(args: argparse.Namespace) -> int:
    cache = Path(args.cache).resolve()
    out = Path(args.out).resolve()
    repo = Path(__file__).resolve().parents[2]
    for d in (cache, out):
        if repo in (d, *d.parents):
            # 缓存与输出放仓库里会被误提交；明确拒绝
            print(f"拒绝：{d} 在仓库里，请放仓库外", file=sys.stderr)
            return 2
    fetcher = iconlib_sources.Fetcher(cache, delay=args.delay)
    audit: list[dict] = []
    manifest: list[dict] = []
    stats: dict = {}
    wanted = {s.strip() for s in args.sources.split(",") if s.strip()}

    # 先把所有「清单」取齐：任何一个取不到就在**写任何文件之前**非零退出（旧的 manifest / index / audit 原样留着）
    cands = None
    all_ids: list[str] = []
    if "bioicons" in wanted:
        cands = iconlib_sources.bioicons_candidates(fetcher)
        if cands is None:
            print("Bioicons 索引取不到", file=sys.stderr)
            return 1
    if "reactome" in wanted:
        try:
            all_ids = iconlib_sources.reactome_list_ids(fetcher)
        except iconlib_sources.ListingError as e:
            print(f"Reactome 清单取不到：{e}", file=sys.stderr)
            return 1
    (out / "svg").mkdir(parents=True, exist_ok=True)

    if "bioicons" in wanted:
        kept, lic_audit = iconlib_license.filter_records(cands)
        audit += lic_audit
        picked = iconlib_sources.stride_pick(kept, args.limit)
        stats["bioicons"] = {
            "index_total": len(cands),
            "license_kept": len(kept),
            "attempted": len(picked),
        }
        n_ok = 0
        for rec in picked:
            r = process(
                rec, fetcher, max_bytes=args.max_bytes, max_paths=args.max_paths, audit=audit
            )
            if r:
                svg, info, pre = r
                manifest.append(make_manifest_entry(rec, info, pre))
                (out / "svg" / f"{rec['id']}.svg").write_bytes(svg)
                n_ok += 1
        stats["bioicons"]["accepted"] = n_ok

    if "reactome" in wanted:
        # 在非蛋白分类的全部 ID 里等距抽样（不是取开头一段，避免偏向同一批设计者）；蛋白排除在取 XML 后判
        ids = iconlib_sources.stride_pick(all_ids, args.limit + args.limit // 2)
        cands, probe = iconlib_sources.reactome_candidates(fetcher, ids, args.limit)
        probe["listed_ids"] = len(all_ids)
        kept, lic_audit = iconlib_license.filter_records(cands)
        audit += lic_audit
        stats["reactome"] = {"probe": probe, "attempted": len(kept)}
        n_ok = 0
        for rec in kept:
            r = process(
                rec, fetcher, max_bytes=args.max_bytes, max_paths=args.max_paths, audit=audit
            )
            if r:
                svg, info, pre = r
                manifest.append(make_manifest_entry(rec, info, pre))
                (out / "svg" / f"{rec['id']}.svg").write_bytes(svg)
                n_ok += 1
        stats["reactome"]["accepted"] = n_ok

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    index_entries = [iconlib_index.build_entry(m, {**m}) for m in manifest]
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), "utf8")
    (out / "index.json").write_text(
        json.dumps(iconlib_index.build_index(index_entries, now), ensure_ascii=False, indent=1),
        "utf8",
    )
    (out / "audit.json").write_text(
        json.dumps({"stats": stats, "dropped": audit}, ensure_ascii=False, indent=1), "utf8"
    )
    print(
        json.dumps(
            {"stats": stats, "network_calls": fetcher.network_calls, "accepted": len(manifest)},
            ensure_ascii=False,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cache", required=True, help="下载缓存目录（仓库外）")
    ap.add_argument("--out", required=True, help="输出目录（仓库外）")
    ap.add_argument("--sources", default="bioicons,reactome")
    ap.add_argument("--limit", type=int, default=20, help="每库取多少个候选")
    ap.add_argument("--delay", type=float, default=1.0, help="联网请求间隔秒数")
    ap.add_argument("--max-bytes", type=int, default=iconlib_normalize.DEFAULT_MAX_BYTES)
    ap.add_argument("--max-paths", type=int, default=iconlib_normalize.DEFAULT_MAX_PATHS)
    return run(ap.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
