"""把一次 pytest collection **按文件**切成 N 片（CI03a）。纯函数，不碰 pytest 对象。

接线在 `tests/conftest.py`（`--shard K/N` / `--shard-manifest PATH`）；不带 `--shard`
时那两个钩子一个字节都不改 collection。这里只回答三个问题：

* **分给谁**：`assign()`——文件按 (权重 desc, 路径 asc) 排好，依次放进当前累计最轻
  的片，同轻时片号小的先。同一份 collection 在任何机器上得到同一份分配。
* **重多少**：`load_weights()` + `default_weight()`——权重表
  `tests/support/shard_weights.json` 由 CI00 的逐文件计时派生；表里没有的文件按表中
  各文件权重的第 75 百分位算。取 p75 而不是 0 或均值：新写的重文件不会被当成零、
  也不会因为计时表没它而漏掉——它只是被保守地估重，落到哪一片都仍然被跑到。
* **切对了没有**：`verify()`——并集 == 全集、两两不交、每片非空、nodeid 无重复。
  主语是 **nodeid 集合**，不是计数：两片各漏一条再各多一条，计数照样对得上。
  任何一条不成立都抛 `ShardError`，conftest 把它转成 `pytest.UsageError`（rc 4）——
  **不是**静默跑全集（那样 CI 上两片各跑一遍全量，慢一倍且没人知道），也**不是**
  静默跑空集（那样一片绿着什么都没验）。

不拆文件的理由：29 个文件有 module 级 fixture（CI_BASELINE.md §7），文件内共享
worker / figure 状态，拆开会把「同一文件里靠前一条暖的缓存」这类顺序依赖摊到两个
进程里。文件是 pytest 里最小的、fixture 生命周期自洽的单位。
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

#: 权重表的落点。与本模块同目录：它是测试基础设施的一部分，不是文档。
WEIGHTS_PATH = Path(__file__).with_name("shard_weights.json")

#: 权重表 JSON 的顶层必填键。`weights` 之外的两个是给人读的出处——缺了就说明这份表
#: 不知道是哪台机器、哪一天量的，那样的数字没法在下次重平衡时判断「是旧了还是错了」。
_REQUIRED_TOP_LEVEL_KEYS = ("source", "measured", "weights")

_SPEC = re.compile(r"\s*(\d+)\s*/\s*(\d+)\s*")


class ShardError(ValueError):
    """选择器自己的错：参数、权重表、分配结果不满足合同。

    调用方（conftest）把它转成 `pytest.UsageError`——退出码 4，和「pytest.ini 写错键」
    同一档：这是**配置**坏了，不是用例红了，两者不该长得一样。
    """


def parse_spec(spec: str) -> tuple[int, int]:
    """`"K/N"` → `(K, N)`；N ≥ 1、1 ≤ K ≤ N，否则 `ShardError`。"""
    m = _SPEC.fullmatch(spec or "")
    if not m:
        raise ShardError(f"--shard 要写成 K/N（例如 1/2），收到 {spec!r}")
    shard, shards = int(m.group(1)), int(m.group(2))
    # 一条检查管两件事（N ≥ 1 蕴含在 1 ≤ K ≤ N 里）：拆成两条的话第二条永远
    # 杀不死——变异反证时删掉任一条另一条都还在挡，判据看起来像有两层，其实是一层
    if not 1 <= shard <= shards:
        raise ShardError(f"--shard 的 K 必须在 1..N 之内（N ≥ 1），收到 {spec!r}")
    return shard, shards


def load_weights(path: Path = WEIGHTS_PATH) -> dict[str, float]:
    """读权重表：`{"source": …, "measured": …, "weights": {"tests/x.py": 秒}}`。

    形状不对一律抛，不退回默认权重——一张读不出来的表意味着分配会与上一次不同，
    而「两片各自算出的分配不一致」正是并集自验要抓的事；与其让它在自验那步以一个
    看不懂的「漏了 37 个文件」出现，不如在这里说清楚是表坏了。
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ShardError(f"读不到权重表 {path}：{exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ShardError(f"权重表 {path} 不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise ShardError(f"权重表 {path} 顶层必须是对象，收到 {type(data).__name__}")
    missing = [k for k in _REQUIRED_TOP_LEVEL_KEYS if k not in data]
    if missing:
        raise ShardError(f"权重表 {path} 缺顶层键 {missing}")
    table = data["weights"]
    if not isinstance(table, dict) or not table:
        raise ShardError(f"权重表 {path} 的 weights 必须是非空对象")
    out: dict[str, float] = {}
    for key, value in table.items():
        if (
            not isinstance(key, str)
            or not key.endswith(".py")
            or "\\" in key
            or key.startswith("/")
        ):
            raise ShardError(f"权重表 {path} 里的键必须是相对 POSIX 路径的 .py 文件：{key!r}")
        # bool 是 int 的子类，`True` 会静默变成 1.0——明确拒绝
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ShardError(f"权重表 {path} 里 {key} 的权重必须是数字，收到 {value!r}")
        if not math.isfinite(value) or value < 0:
            raise ShardError(f"权重表 {path} 里 {key} 的权重必须是有限的非负数，收到 {value!r}")
        out[key] = float(value)
    return out


def default_weight(weights: Mapping[str, float]) -> float:
    """表里没有的文件用这个：表中各文件权重的第 75 百分位（nearest-rank）。"""
    values = sorted(weights.values())
    if not values:
        raise ShardError("权重表为空，算不出默认权重")
    return values[math.ceil(0.75 * len(values)) - 1]


def assign(
    files: Iterable[str],
    weights: Mapping[str, float],
    shards: int,
    default: float,
) -> tuple[list[list[str]], list[float]]:
    """贪心（LPT）：文件按 (权重 desc, 路径 asc) 排，依次放进当前累计最轻的片。

    返回 `(每片的文件列表, 每片的估计秒数)`。同一输入在任何机器上得到同一输出：
    排序键全是确定的，平局按片号小的。
    """
    if shards < 1:
        raise ShardError(f"片数必须 ≥ 1，收到 {shards}")
    ordered = sorted(set(files), key=lambda f: (-weights.get(f, default), f))
    buckets: list[list[str]] = [[] for _ in range(shards)]
    loads: list[float] = [0.0] * shards
    for f in ordered:
        target = min(range(shards), key=lambda i: (loads[i], i))
        buckets[target].append(f)
        loads[target] += weights.get(f, default)
    return buckets, loads


@dataclass(frozen=True)
class Plan:
    """一次分片的完整结果：**全部 N 片**都在这里，不只是自己那一片。

    自验要看全部 N 片（并集、不交），所以每个 shard 进程都算全部再只跑自己的。
    """

    shard: int
    shards: int
    files: tuple[tuple[str, ...], ...]  # 每片的文件，按权重 desc
    nodeids: tuple[tuple[str, ...], ...]  # 每片的 nodeid，保持 collection 顺序
    estimated_seconds: tuple[float, ...]
    default_weight: float
    unknown_files: tuple[str, ...]  # 权重表里没有、用了默认权重的文件

    @property
    def selected_files(self) -> tuple[str, ...]:
        return self.files[self.shard - 1]

    @property
    def selected_nodeids(self) -> tuple[str, ...]:
        return self.nodeids[self.shard - 1]


def plan(
    nodeids_by_file: Mapping[str, Sequence[str]],
    weights: Mapping[str, float],
    shard: int,
    shards: int,
) -> Plan:
    """由「文件 → 该文件的 nodeid（collection 顺序）」算出全部 N 片。"""
    if not 1 <= shard <= shards:
        raise ShardError(f"片号 {shard} 不在 1..{shards} 之内")
    default = default_weight(weights)
    buckets, loads = assign(nodeids_by_file.keys(), weights, shards, default)
    file_index = {f: i for i, f in enumerate(nodeids_by_file)}
    per_shard_nodeids: list[tuple[str, ...]] = []
    for bucket in buckets:
        ids: list[str] = []
        # 片内文件按 collection 顺序回放，文件内顺序原样：模块级 fixture 与
        # 「靠前一条暖缓存」都不受分片影响
        for f in sorted(bucket, key=file_index.__getitem__):
            ids.extend(nodeids_by_file[f])
        per_shard_nodeids.append(tuple(ids))
    unknown = tuple(sorted(f for f in nodeids_by_file if f not in weights))
    return Plan(
        shard=shard,
        shards=shards,
        files=tuple(tuple(b) for b in buckets),
        nodeids=tuple(per_shard_nodeids),
        estimated_seconds=tuple(loads),
        default_weight=default,
        unknown_files=unknown,
    )


def verify(the_plan: Plan, all_nodeids: Sequence[str]) -> None:
    """并集 == 全集、两两不交、每片非空、nodeid 无重复；任一条不成立抛 `ShardError`。

    主语是 nodeid **集合**。四条各挡一种失效：

    * 全集里有重复 nodeid → 同一条用例会被算进两片或漏掉一片，先在源头拒绝；
    * 两片有交集（或片内重复）→ 同一条被跑两遍，「并集相等」抓不到它；
    * 并集 ≠ 全集 → 漏片（少）或凭空多出（多），逐条点名；
    * 某片为空 → 那一片的 job 会绿着什么都不验（N 大于文件数时必然如此，
      也是错：要的是分片，不是空转）。
    """
    full = list(all_nodeids)
    if len(set(full)) != len(full):
        dupes = sorted({n for n in full if full.count(n) > 1})
        raise ShardError(f"collection 里有重复 nodeid（{len(dupes)} 条）：{dupes[:5]}…")
    union: list[str] = []
    for ids in the_plan.nodeids:
        union.extend(ids)
    if len(set(union)) != len(union):
        dupes = sorted({n for n in union if union.count(n) > 1})
        raise ShardError(f"分片之间有交集或片内重复（{len(dupes)} 条）：{dupes[:5]}…")
    missing = sorted(set(full) - set(union))
    extra = sorted(set(union) - set(full))
    if missing or extra:
        raise ShardError(
            f"分片并集 ≠ 全集：漏掉 {len(missing)} 条 {missing[:5]}…，"
            f"多出 {len(extra)} 条 {extra[:5]}…"
        )
    for i, ids in enumerate(the_plan.nodeids, start=1):
        if not ids:
            n_files = sum(len(b) for b in the_plan.files)
            raise ShardError(
                f"第 {i}/{the_plan.shards} 片为空（全集 {n_files} 个文件、{len(full)} 条 nodeid）"
                "——一片空转的 job 会绿着什么都不验"
            )


# ---------------------------------------------------------------------------
# 权重表的重算：从 junit 产物来
# ---------------------------------------------------------------------------
# CI 的每一片都把 junit.xml 与 manifest 一起上传（ci.yml backend-fast /
# backend-platforms）。把同一次 run 里同一个 os 的所有片的 junit 喂给这里，就是那个
# os 上的新权重表；本机跑一次全量 `--junitxml` 也行。**表只影响平衡，不影响覆盖**：
# 表旧了两片会一重一轻，但并集自验照样成立。


def file_of_classname(classname: str, root: Path) -> str | None:
    """junit 的 `classname`（`tests.bridge.test_x.TestY`）→ `tests/bridge/test_x.py`。

    类名与模块名在 classname 里长得一样（都是点分段），所以从最长前缀往回试，
    取第一个在 `root` 下真实存在的 `.py`。找不到返回 None，由调用方决定怎么报。
    """
    parts = classname.split(".")
    for n in range(len(parts), 0, -1):
        candidate = "/".join(parts[:n]) + ".py"
        if (root / candidate).is_file():
            return candidate
    return None


def weights_from_junit(xml_paths: Iterable[Path], root: Path) -> dict[str, float]:
    """把若干 junit.xml 里每条 testcase 的 `time` 按文件累加（秒，保留两位）。

    `time` 是 pytest 的 `junit_duration_report=total`（setup+call+teardown），与
    CI00 的 `durations_by_file.csv` 同一口径——只是 CSV 只累了 ≥ 5 ms 的阶段。
    classname 对不回文件的 testcase 直接抛：那说明喂进来的 junit 不是这棵树产的。
    """
    totals: dict[str, float] = {}
    for xml_path in xml_paths:
        tree = ET.parse(xml_path)
        for case in tree.iter("testcase"):
            classname = case.get("classname", "")
            file = file_of_classname(classname, root)
            if file is None:
                raise ShardError(f"{xml_path}：classname {classname!r} 对不回 {root} 下的任何 .py")
            totals[file] = totals.get(file, 0.0) + float(case.get("time", "0") or 0.0)
    if not totals:
        raise ShardError("junit 里一条 testcase 都没有")
    return {k: round(v, 2) for k, v in sorted(totals.items())}


def main(argv: Sequence[str] | None = None) -> int:
    """`python tests/support/shard.py --from-junit a.xml b.xml --source … --measured … > shard_weights.json`"""
    # Windows 上 stdout 被重定向成管道 / 文件时退回系统区域编码（cp1252 / cp936），而这个
    # 入口打的是 `ensure_ascii=False` 的中文 JSON——第一行就 UnicodeEncodeError，退出码 1。
    # 与 tests/support/ 其它子进程入口同一写法（tests/test_windows_regressions.py 扫着）。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="从 junit.xml 重算 tests/support/shard_weights.json")
    ap.add_argument("--from-junit", nargs="+", required=True, type=Path, metavar="XML")
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    ap.add_argument("--source", required=True, help="这些 junit 从哪来（run id / job / 本机命令）")
    ap.add_argument("--measured", required=True, help="哪天、哪台机器、哪个 HEAD 量的")
    args = ap.parse_args(argv)
    table = weights_from_junit(args.from_junit, args.root)
    doc = {
        "source": args.source,
        "measured": args.measured,
        "unit": "seconds (setup+call+teardown per testcase, summed per file)",
        "default_rule": "文件不在表里时取表中各值的第 75 百分位（nearest-rank）",
        "weights": table,
    }
    sys.stdout.write(json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
