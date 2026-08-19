"""Patch 规范化与内容寻址哈希——**唯一权威实现**（纯标准库）。

override patch 是「谁（gid）的哪个属性（prop）改成什么（value）」的全量列表。
同一份修改可以有无数种等价写法：条目顺序不同、同一个 (gid, prop) 写了两遍、
夹杂着前端没清干净的垃圾条目。要让「这两次渲染是不是同一件事」可判定
（缓存命中、幂等重放、supervisor 的请求去重），必须先把它压成唯一形态。

规范化的三条规则（**改任何一条都是破坏性变更**）：

1. **形状校验**：条目必须是对象，`gid`/`prop` 为非空字符串，`value` 是 JSON 值
   （null / bool / 整数 / 有限浮点 / 字符串 / 数组 / 键为字符串的对象）。
   不合规的条目**不静默丢弃**——`canonicalize_with_diagnostics()` 把它们连同
   原因一起交出来，调用方才有机会把「前端发了脏数据」报出来。
2. **去重 last-wins**：同一个 (gid, prop) 只留最后一条（与 `overrides.apply`
   建 `new` 表时的语义一致：后写的赢）。
3. **排序**：按 (gid, prop) 字典序。这只决定**身份**，不决定应用顺序——
   几何优先级（size_mm → position → 其余）是 `overrides.apply` 内部的事，
   worker 应用的永远是**请求里那份原始列表**，不是这里排过序的。

`canonical_json()` 是确定性序列化：`sort_keys` + 无空格分隔符 +
`ensure_ascii=False`（UTF-8 原样出字），浮点走 Python 的最短往返 repr。
`patch_hash()` 就是它的 SHA-256。

**Rust supervisor（tavotto-workerd）必须逐字节复现这里的输出**，
golden vectors 在 `tests/golden/patch_vectors.json`；契约与已知的跨语言坑
（`1e-07` 的指数写法、int/float 之分、整数区间）见
`docs/adr/0003-worker-protocol-v1.md`。改任何一侧都必须同步另一侧 + 向量文件。

本模块被 Flask 父进程（`pool.py`）与 worker 子进程（`worker.py` 直接
`import patchspec`）共用，**只许纯标准库**，且不得使用相对 import
（worker 是把 engine 目录塞进 sys.path 后平铺 import 的）。
"""
from __future__ import annotations

import hashlib
import json
import math

#: 规范条目只有这三个键——多出来的字段一律丢弃（它们不参与身份判定；
#: 真要加新字段，那是协议版本升级，不是这里悄悄放行）。
_FIELDS = ("gid", "prop", "value")

#: 整数的安全区间 = i64。Python 整数是任意精度，JSON 也不限位数，但 Rust 侧
#: 的 serde_json 默认只认 i64/u64——放行一个 10^30 会让两边的序列化当场分叉。
#: override 的值是颜色 / 字号 / 分数坐标，撞不到这条线；撞到了就是脏数据。
_INT_MIN = -(2 ** 63)
_INT_MAX = 2 ** 63 - 1

#: 嵌套深度上限。value 合法的嵌套只有 pos_frac / endpoints_frac 这类一两层的
#: 数组，给到 32 已经绰绰有余；设上限是为了让恶意/损坏输入撞墙而不是递归爆栈。
_MAX_DEPTH = 32


def _value_problem(value: object, depth: int = 0) -> str:
    """value 不是合法 JSON 值时回一句机器可读的原因，合法回空串。"""
    if depth > _MAX_DEPTH:
        return "value_too_deep"
    if value is None or isinstance(value, (str, bool)):
        return ""
    if isinstance(value, int):      # bool 已在上面拦掉（bool 是 int 的子类）
        return "" if _INT_MIN <= value <= _INT_MAX else "int_out_of_range"
    if isinstance(value, float):
        # NaN / Infinity 不是 JSON 值。Python 的 json 默认会写出裸 `NaN`，
        # 任何严格解析器（serde_json 在内）都读不了——必须在入口挡掉。
        return "" if math.isfinite(value) else "non_finite_float"
    if isinstance(value, list):
        for item in value:
            problem = _value_problem(item, depth + 1)
            if problem:
                return problem
        return ""
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                return "non_string_object_key"
            problem = _value_problem(item, depth + 1)
            if problem:
                return problem
        return ""
    # tuple / set / bytes / numpy 标量……都走不到 JSON 里去（真要用先转成
    # 列表或数字，别指望这里替调用方猜）
    return f"unsupported_type:{type(value).__name__}"


def _brief(entry: object, limit: int = 200) -> str:
    """诊断里回显条目本体——只截一小段，别把整份脏数据塞进日志。"""
    text = repr(entry)
    return text if len(text) <= limit else text[:limit] + "…"


def canonicalize_with_diagnostics(
    patches: object,
) -> tuple[list[dict], list[dict]]:
    """规范化并同时交出被剔除的条目。

    回 `(canonical, dropped)`；`dropped` 每项形如
    `{"index": 3, "reason": "bad_gid", "entry": "…"}`（`index` 为 -1 表示
    整个 patches 就不是列表）。**剔除永远是可见的**——静默丢一条 patch，
    用户看到的是「我改了颜色但没生效」，没有任何线索。
    """
    if not isinstance(patches, (list, tuple)):
        return [], [{"index": -1, "reason": "patches_not_a_list",
                     "entry": _brief(patches)}]

    dropped: list[dict] = []
    merged: dict[tuple[str, str], object] = {}
    for i, entry in enumerate(patches):
        if not isinstance(entry, dict):
            dropped.append({"index": i, "reason": "not_an_object",
                            "entry": _brief(entry)})
            continue
        gid, prop = entry.get("gid"), entry.get("prop")
        if not isinstance(gid, str) or not gid:
            dropped.append({"index": i, "reason": "bad_gid",
                            "entry": _brief(entry)})
            continue
        if not isinstance(prop, str) or not prop:
            dropped.append({"index": i, "reason": "bad_prop",
                            "entry": _brief(entry)})
            continue
        if "value" not in entry:
            dropped.append({"index": i, "reason": "missing_value",
                            "entry": _brief(entry)})
            continue
        problem = _value_problem(entry["value"])
        if problem:
            dropped.append({"index": i, "reason": problem,
                            "entry": _brief(entry)})
            continue
        merged[(gid, prop)] = entry["value"]     # last-wins

    canonical = [{"gid": gid, "prop": prop, "value": value}
                 for (gid, prop), value in sorted(merged.items())]
    return canonical, dropped


def canonicalize(patches: object) -> list[dict]:
    """规范化后的 patch 列表（不关心被剔除了什么时用这个）。"""
    return canonicalize_with_diagnostics(patches)[0]


def canonical_json(patches: object) -> str:
    """确定性序列化：同一份修改的任何等价写法都得到同一个字符串。

    * `sort_keys=True` —— 键序固定为 gid < prop < value；**对嵌套对象同样
      生效**（value 里的 `{"b":…,"a":…}` 会被写成 `{"a":…,"b":…}`），
      所以 `json.dumps(canonicalize(x))` 不等于这里的输出，别自己拼
    * `separators=(",", ":")` —— 不留任何空白
    * `ensure_ascii=False` —— 中文 / µ / ⁻¹ 原样出字（编码时统一 UTF-8）
    * `allow_nan=False` —— 非有限浮点已在规范化时剔除，这里是第二道保险
    """
    return json.dumps(canonicalize(patches), sort_keys=True,
                      separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def patch_hash(patches: object) -> str:
    """`"sha256:" + sha256(canonical_json(...).encode("utf-8")).hexdigest()`。

    带算法前缀是为了将来能换算法而不必猜这串十六进制是什么。
    """
    digest = hashlib.sha256(canonical_json(patches).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"
