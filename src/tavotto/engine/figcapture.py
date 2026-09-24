"""Figure 捕获策略：桌面 worker 与浏览器 playground **共用的一份语义**。

两个产品入口跑的是同一批用户脚本（AI 生成的、论文作者手写的），凭什么
「网站 /try 能打开、桌面版说这个脚本不出图」？在 2026-08-21 之前正是这样：
`worker.py` 只认 `Figure.savefig` / `paper_style.save`，而 `browser.py` 多了
一条 pyplot 兜底，于是最常见的那种 AI 输出——

    import matplotlib.pyplot as plt
    plt.plot([1, 2, 3], [4, 5, 6])
    plt.show()

——在浏览器里能编辑，在桌面上连「捕获到 0 张图」都不解释。把兜底那几行抄进
worker 能让症状消失，但两份代码迟早分叉，而分叉的表现正是同一个脚本在两个
入口里产出**不同的 stem**（前端按 stem 索引一切，那是数据级的错位）。所以
策略收在这里，两边各调一次。

三件事这里是唯一出处：

* `savefig_stem()` —— `savefig(路径)` 里那个 stem 怎么取；
* `collect_pyplot_figures()` —— 脚本跑完之后还活着的 pyplot Figure 怎么补进
  捕获表（去重、命名、保序）；
* `install_relative_read_fallback()` —— 相对路径**只读**回退（见下）；
* `unused_imports()` / `install_unused_import_placeholders()` —— 脚本 import 了却从未用到、
  又没装的包不挡图（ADR 0061 §二 2026-09-24 修订；判据父进程与 worker 各调一次）。

## fallback stem 的稳定性

`<脚本名>`、`<脚本名>-2`、`<脚本名>-3`……**按 `plt.get_fignums()` 的顺序**，
不按 figure 号编号。为什么不用 `f"{base}-{num}"`：figure 号是 pyplot 的全局
计数器，脚本中途 `plt.close()` 过一次，号就跳了——同一份脚本换个 matplotlib
版本、或者在同一个解释器里跑第二遍，用户的 override 就挂在一个不存在的
stem 上，界面表现是「打开是空白的，什么都没报错」。序号必须只由**这次捕获
里的第几张**决定。

已经被 savefig 认领的 stem 一律不覆盖，并且按 **Figure 身份**去重：
`fig.savefig("a.pdf")` 之后 figure 还活在 pyplot 里，不去重就会同一张图出现
两个 stem。

## 相对路径只读回退

worker 把 cwd 切到沙盒（挡住脚本用相对路径写/删真实图库），代价是

    pd.read_csv("data.csv")

这类写法——`python figure.py` 时天经地义——在 Tavotto 里必然 FileNotFoundError。
这里给的是**最小且不可能变成写入通道**的修法：只有

    * 只读模式（'r'/'rb'/'rt'，带 '+'、'w'、'a'、'x' 的一律不管），
    * 相对路径，**或指向沙盒内部的绝对路径**（见下），
    * 按真正的 open 会用的那条路径判、确实不存在，
    * 换算到脚本目录之后仍然落在项目根**之内**，

四条同时成立时，才把 `open()` 改指到脚本目录下那一份。写、改、删、重命名
一个字节都不经过这里，所以沙盒作为**写入**边界完全没有松动；越界的读
（`../../../etc/passwd`）直接放行给原来的 open 去报它本来的错，不做「就近找
一个能用的」。

**「先 realpath 再 open」的库同样要救得回来。**

只认裸相对路径是不够的：不少库在 open 之前先把路径解成绝对再交给
`builtins.open`，于是回退看到的是 `<沙盒>/sample.png` 而不是 `sample.png`。
CompatBench 在 minimum 档（Python 3.10 / Pillow 10.4.0）上逮到的就是这个——
`sci_pillow` 的 `Image.open("sample.png")` 挂在 execute，而同一条在 bundled
档（Pillow 12.3.0）全绿：

    10.4.0   filename = os.path.realpath(os.fspath(fp))   ← 先解成绝对
    12.3.0   filename = os.fspath(fp)                      ← 还是相对的

**这不是 Pillow 的毛病**，Pillow 只是撞上来的那一个：h5py、部分 netCDF 绑定、
以及任何自己写了 `os.path.abspath(p)` 的用户脚本都是同一类。

放行判据收得很紧：**只认指向沙盒内部、且按真正的 open 会用的那条路径判确实
不存在的绝对路径**，仍然只读、仍然要落在项目根里。语义上这与相对路径是同一
件事——裸相对路径就是拿 cwd 拼出来的，而 cwd 就是沙盒。沙盒**之外**的绝对
路径一个都不碰：那是用户指名的位置，「就近找一个能用的」在那里是越权。

存在性**必须按真正的 open 会用的那条路径判**，不能拿沙盒根去拼：脚本
`os.chdir()` 进子目录之后自己写出来的中间结果会查不到，读被无声改道到项目里
的原件——比读不到还坏。看护 `TestAbsolutizedRelativeRead`（改道 / 越界不改道 /
写不改道 / 沙盒里那份优先 / chdir 后自己写的优先 / chdir 后仍救得回来）。

**三个入口都要 patch，而且第三个是版本相关的。**

`builtins.open` 与 `io.open` 指向同一个 C 函数，但那是**两个独立的名字
绑定**：`builtins.open = f` 改不到 `io.open`。只补前者的话
`Path("config.json").read_text()` 仍然 FileNotFoundError 而 `open(...)` 好使
——两种等价写法行为不一致。这条是 CompatBench 的 `shape_relative_pathlib`
抓出来的。

补完这两个仍然不够，**而且缺口只在 Python 3.10 上张开**（实测 3.10.20）：

    3.10   pathlib._NormalAccessor.open is io.open  →  True
           但它在**类定义时**就绑好了，`Path.open` 调的是
           `self._accessor.open(...)`，patch `io.open` 对它毫无作用
    3.11+  `_accessor` 被删掉，`Path.open` 改成调用时才查 `io.open`

pyproject 的 `requires-python` 下界正是 3.10，所以这不是理论问题：同一份
脚本在 3.13 上读得到数据、在 3.10 上 FileNotFoundError。所以第三个 patch
打在 **`pathlib.Path.open` 本身**——`read_text` / `read_bytes` 都是
`self.open(...)` 的实例方法查找，打在类上对每个版本都成立，也不必知道
`_accessor` 存不存在。

这三个之外不再扩大：pandas 的 `get_handle`、`numpy.load`、`PIL.Image.open`、
`json.load(open(...))` 全部经过它们。`os.open` / `os.stat` 这类底层调用不管
——覆盖它们要维护一张平台相关的语义表，收益却只是极少数直接玩 fd 的脚本。
覆盖不到的那些由 CompatBench 如实记账，不靠猜。

## CapturedFigureDescriptor（2026-08-25，Compatibility Bridge Session 2）

每张捕获 Figure 的结构化描述也收在这里——它就是捕获语义的一部分：
「这张图叫什么、从哪来、有没有原始产物、能不能写回」由捕获那一刻决定，
worker 与 browser 各自造一份的话，两个入口又会给出两个答案。唯一出处：

* `runtime_asset_id()` —— 稳定身份 `runtime:<script>#<stem>`（ADR 0013 §2）；
* `source_fingerprint()` —— stale hint（见函数 docstring 的诚实边界）；
* `build_descriptor()` —— 工厂：writeback 能力**只能派生，不能指定**；
* `find_original_artifact()` —— 「stem 的原始产物在磁盘哪里」的唯一判据
  （handoff 的 `_first_on_disk` 是它的消费者）。

纯标准库（`matplotlib.pyplot` 由调用方传进来）：worker 与 browser 都在
engine 目录平铺 import 它，Flask 父进程也 import 得动。
"""

from __future__ import annotations

import builtins
import dataclasses
import hashlib
import io
import json
import os
import pathlib
import re

__all__ = [
    "savefig_stem",
    "collect_pyplot_figures",
    "fallback_stems",
    "install_relative_read_fallback",
    "unused_imports",
    "install_unused_import_placeholders",
    "SIDE_EFFECT_FREE_IMPORTS",
    "InputObserver",
    "observed_local_modules",
    "INPUT_OBSERVER_MAX_FILES",
    "OBSERVATION_PARTIAL",
    "MAX_PYPLOT_FALLBACK",
    "SOURCE_SAVEFIG",
    "SOURCE_PYPLOT",
    "PROFILE_SAFE",
    "PROFILE_NATIVE",
    "ARTIFACT_EXTS",
    "DESCRIPTOR_VERSION",
    "CapturedFigureDescriptor",
    "build_descriptor",
    "descriptor_from_payload",
    "runtime_asset_id",
    "source_fingerprint",
    "size_mm_of",
    "find_original_artifact",
    "SOURCE_ARTIFACT_VERSION",
    "ORIGIN_EXECUTION",
    "ORIGIN_STATIC",
    "SourceArtifact",
    "hash_file",
    "source_artifact_from_file",
]

#: 兜底最多补多少张。`for i in range(200): plt.figure()` 是真会出现的写法
#: （扫参数、逐条画），每一张都要 instrument + 出一次预览 SVG——不设上限的
#: 话一次 build 就能把内存和几十秒时间烧光，而用户只是想看第一张。
#: 显式 savefig 的那些**不受这个上限约束**：那是脚本明确宣告的产物。
MAX_PYPLOT_FALLBACK = 8

#: 捕获来源：脚本显式 `savefig()` / `paper_style.save()` 认领的 stem。
#: 这类 stem 在桌面上**可能**对应磁盘上一份真实产物（用户先跑过脚本），
#: 「写回原始文件」只对它们有意义。
SOURCE_SAVEFIG = "savefig"
#: 捕获来源：脚本跑完还活在 pyplot 里、从未存过盘的 Figure。
#: 它**没有原始产物**——渲染 / 编辑 / 导出都成立，写回无从谈起。
SOURCE_PYPLOT = "pyplot"

#: 执行 profile（ADR 0014）。常量放在这里而不是 execspec：worker 与 browser
#: 平铺 import 本模块（execspec 是包内模块，它们够不着），而描述符必须说清
#: 「这次是按哪档语义跑的」。execspec 从这里 re-export，两边同一份。
PROFILE_SAFE = "safe"
PROFILE_NATIVE = "native"
_PROFILES = (PROFILE_SAFE, PROFILE_NATIVE)
_SOURCES = (SOURCE_SAVEFIG, SOURCE_PYPLOT)

#: 已知的图产物扩展名——「什么算一份原始产物」的唯一出处（顺序即优先级）。
#: `discover.OUT_EXTS` 与 `handoff.OUT_EXTS` 是它的镜像别名：静态扫描认产物、
#: 交接找产物、描述符判「有没有原件」必须是同一张表，否则三处各认一套，
#: 表现是「discover 说这是图、写回说没有原件」。
ARTIFACT_EXTS = (".pdf", ".png", ".svg", ".jpg", ".jpeg", ".eps", ".tif", ".tiff")

#: 捕获描述符的 schema 版本。**捕获语义改变时才升**（stem 取法、去重、
#: fingerprint 构成……）；它参与 fingerprint，所以升版 = 所有旧 fingerprint
#: 自然失配 = 「按旧语义捕的图可能已过时」这句 stale hint 如实成立。
DESCRIPTOR_VERSION = 1


def size_mm_of(fig) -> tuple[float, float]:
    """Figure 的物理尺寸（mm，两位小数）。

    与 manifest 的 `size_mm` 同一个公式（inches × 25.4，round 2）。描述符在
    build 阶段就要报尺寸，而 browser 侧那时还没建 manifest——两边都从 Figure
    直接算，公式只有这一份，worker/browser 的描述符才比得齐。
    """
    w_in, h_in = (float(v) for v in fig.get_size_inches())
    return (round(w_in * 25.4, 2), round(h_in * 25.4, 2))


def normalize_relative_script(script: str) -> str:
    """脚本路径规范：项目相对、POSIX 分隔。绝对路径直接拒绝。

    asset id 与 fingerprint 都吃它——混进绝对路径，同一个项目换台机器（或
    换个挂载点）id 就变了，保存重开后 override 挂错身份（FigS3 一族事故）。
    """
    if not isinstance(script, str) or not script.strip():
        raise ValueError("script 必须是非空字符串")
    normalized = script.replace("\\", "/")
    if normalized.startswith("/") or (len(normalized) > 1 and normalized[1] == ":"):
        raise ValueError(f"script 必须是项目相对路径，不能是绝对路径: {script!r}")
    return normalized


def runtime_asset_id(script: str, stem: str) -> str:
    """捕获 Figure 的稳定身份：`runtime:<script 相对路径>#<stem>`（ADR 0013 §2）。

    只由 (脚本相对路径, stem) 决定——项目那一维由「id 存在哪个项目的文档里」
    承担。**刻意不含** PID、临时目录、绝对路径、会话 id、时间戳、entry：

    * 前五者会让重跑 / 重开 / 换机器后 override 挂错身份；
    * entry 不进 id 是 ADR 0013 决策 2：注册表里一个脚本只有一个 entry，
      (script, stem) 已唯一；把 entry 编进去，用户改脚本换入口重新探测后，
      同一张图会变成一个新身份，历史 override 全部变成孤儿。

    id 是**不透明标识**：消费方不得从中反解 script/stem（脚本名里可以有
    `#`），要用就取描述符里那两个独立字段——这就是 stem 冲突的显式处理。
    """
    script = normalize_relative_script(script)
    if not isinstance(stem, str) or not stem:
        raise ValueError("stem 必须是非空字符串")
    return f"runtime:{script}#{stem}"


def source_fingerprint(
    script_bytes: bytes,
    *,
    script: str,
    entry: str,
    profile: str,
    target_kind: str = "script",
    argv: tuple = (),
    passthrough_savefig: bool = False,
    matplotlib_version: str = "",
) -> str:
    """捕获那一刻的来源指纹——**只是 stale hint，不是完备性证明**。

    构成：脚本内容 sha256 + ExecutionSpec 的稳定字段（script/entry/profile/
    target_kind/argv/passthrough_savefig）+ matplotlib 版本 + 描述符 schema
    版本。指纹不同 = 图**可能**过时；指纹相同**不保证**没过时：脚本读的
    CSV / 本地 import 的模块 / 环境变量 / 数据库都不在指纹里——覆盖它们
    要追踪脚本的全部 IO，那是另一个量级的工程，诚实地不声称。

    脚本内容先做换行归一（CRLF/CR → LF）再哈希：worker 从磁盘 `read_bytes`
    （Windows 检出多为 CRLF），browser 拿到的是编辑器里的 `str`（LF）——
    同一份逻辑源码必须同一个指纹，否则描述符对拍在 Windows 上必然分叉。
    行尾不改变 Python 语义，归一不损失 stale hint 的分辨力。
    """
    if profile not in _PROFILES:
        raise ValueError(f"profile 非法: {profile!r}（可选 {_PROFILES}）")
    canon_bytes = script_bytes.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    payload = {
        "descriptor_version": DESCRIPTOR_VERSION,
        "script": normalize_relative_script(script),
        "script_sha256": hashlib.sha256(canon_bytes).hexdigest(),
        "entry": entry,
        "profile": profile,
        "target_kind": target_kind,
        "argv": list(argv),
        "passthrough_savefig": bool(passthrough_savefig),
        "matplotlib_version": matplotlib_version,
    }
    canon = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canon.encode("utf-8")).hexdigest()


def find_original_artifact(project_root: str, stem: str, *, isfile=os.path.isfile) -> str | None:
    """项目根下 stem 的原始产物（相对路径，POSIX）；没有回 None。

    判据与 handoff 交接找产物是同一份（它现在就调这里）：只看项目根一层、
    按 `ARTIFACT_EXTS` 的顺序取第一个存在的。`isfile` 可注入是给测试与
    handoff 的 dry 场景用的。
    """
    for ext in ARTIFACT_EXTS:
        if isfile(os.path.join(project_root, stem + ext)):
            return stem + ext
    return None


@dataclasses.dataclass(frozen=True)
class CapturedFigureDescriptor:
    """一张捕获 Figure 的结构化描述（worker / browser / probe 共用的语义）。

    字段随协议走 JSON（`to_payload` / `descriptor_from_payload`）。前端与
    调用方**不得**从路径是否为空之类的旁证猜语义——来源、有没有原件、
    能不能写回，全部是显式字段。
    """

    asset_id: str
    script: str  # 项目相对路径，POSIX 分隔
    entry: str
    stem: str
    capture_source: str  # SOURCE_SAVEFIG | SOURCE_PYPLOT
    execution_profile: str  # PROFILE_SAFE | PROFILE_NATIVE
    original_artifact: str | None  # 项目相对路径；pyplot 捕获恒 None
    size_mm: tuple[float, float]
    source_fingerprint: str
    can_writeback_artifact: bool  # 只能由工厂派生（见 build_descriptor）
    can_writeback_source: bool  # v1 恒 False（不改写用户脚本，ADR 0013 §7）

    def to_payload(self) -> dict:
        out = dataclasses.asdict(self)
        out["size_mm"] = [float(v) for v in self.size_mm]
        return out


def build_descriptor(
    *,
    script: str,
    entry: str,
    stem: str,
    capture_source: str,
    execution_profile: str,
    size_mm,
    source_fingerprint: str,
    original_artifact: str | None = None,
) -> CapturedFigureDescriptor:
    """描述符工厂——**writeback 能力只能派生，不能指定**。

    * `capture_source == "pyplot"` 的图从没存过盘：`original_artifact` 必须是
      None（给了就抛，不是悄悄清掉——上游把来源标错时要当场炸出来，而不是
      让一个「看起来能写回」的描述符流出去）；
    * `can_writeback_artifact` = savefig 来源 **且** 原件真实在磁盘上。
      磁盘上碰巧躺着同名文件而来源是 pyplot 时它必须是 False——那份文件
      不是这张图写的，往上写回就是覆盖一个不相干的文件；
    * `can_writeback_source`（改写用户脚本）v1 一律 False。
    """
    script = normalize_relative_script(script)
    if not isinstance(entry, str) or not entry:
        raise ValueError(f"entry 必须是非空字符串: {entry!r}")
    if not isinstance(stem, str) or not stem:
        raise ValueError(f"stem 必须是非空字符串: {stem!r}")
    if not isinstance(source_fingerprint, str) or not source_fingerprint:
        raise ValueError("source_fingerprint 必须是非空字符串")
    try:
        w, h = size_mm
    except (TypeError, ValueError) as exc:
        raise ValueError(f"size_mm 必须是 (宽, 高) 两元组: {size_mm!r}") from exc
    if capture_source not in _SOURCES:
        raise ValueError(f"capture_source 非法: {capture_source!r}（可选 {_SOURCES}）")
    if execution_profile not in _PROFILES:
        raise ValueError(f"execution_profile 非法: {execution_profile!r}（可选 {_PROFILES}）")
    if capture_source == SOURCE_PYPLOT and original_artifact is not None:
        raise ValueError(
            "pyplot 捕获的 Figure 没有原始产物，"
            f"original_artifact 必须是 None: {original_artifact!r}"
        )
    if original_artifact is not None:
        original_artifact = original_artifact.replace("\\", "/")
    return CapturedFigureDescriptor(
        asset_id=runtime_asset_id(script, stem),
        script=script,
        entry=entry,
        stem=stem,
        capture_source=capture_source,
        execution_profile=execution_profile,
        original_artifact=original_artifact,
        size_mm=(float(w), float(h)),
        source_fingerprint=source_fingerprint,
        can_writeback_artifact=(capture_source == SOURCE_SAVEFIG and original_artifact is not None),
        can_writeback_source=False,
    )


def descriptor_from_payload(data: dict) -> CapturedFigureDescriptor:
    """协议 payload → 描述符（校验后重建，writeback 能力照样只认派生值）。

    经工厂重建而不是逐字段照抄：payload 里写着 `can_writeback_artifact: true`
    而来源是 pyplot 的话，这里会直接抛——坏数据在边界上死，不进语义层。
    """
    if not isinstance(data, dict):
        raise ValueError("描述符必须是对象")
    desc = build_descriptor(
        script=data.get("script"),
        entry=data.get("entry"),
        stem=data.get("stem"),
        capture_source=data.get("capture_source"),
        execution_profile=data.get("execution_profile"),
        size_mm=tuple(data.get("size_mm") or ()),
        source_fingerprint=data.get("source_fingerprint"),
        original_artifact=data.get("original_artifact"),
    )
    for key in ("asset_id", "can_writeback_artifact", "can_writeback_source"):
        if key in data and data[key] != getattr(desc, key):
            raise ValueError(
                f"描述符字段 {key} 与派生值不一致: {data[key]!r} != {getattr(desc, key)!r}"
            )
    return desc


# ---------------------------------------------------------------------------
# SourceArtifact（统一实施包 U01，ADR 0053）
#
# 「源图产物」= 一份**已经落成字节**的图文件 + 它是谁、从哪来。两种来源：
#   * execution —— 本次执行（safe worker / native）按某组 override 写出来的文件
#     （导出中间件、写回 staging）；
#   * static    —— 磁盘上本来就有的原件（用户脚本自己跑出来的 figure.pdf、或一张
#     没有脚本的 PNG）——「静态源可用」这条产品路径（FO-010）的对象。
#
# 身份三分（04 §3）在这里落成三个**不同的字段**：`bytes_sha256` 是最终文件 hash
# （只描述字节，不掺任何别的身份）；`receipt_identity`（回执的**公开**身份）+
# `patch_hash` 指向产出它的执行语义与 override 组合——语义身份的两个坐标；
# `receipt_id` / `generation` 只是实例元数据（哪一代、哪台机器上的那一次），**不进**
# 语义身份：它们由私有失效键派生，进了就会让同一张图在另一台机器 / 下一代会话上
# 长出另一个「语义」身份（Codex #451 P2）。机器路径本身不进这个结构。
# ---------------------------------------------------------------------------
SOURCE_ARTIFACT_VERSION = 1
ORIGIN_EXECUTION = "execution"
ORIGIN_STATIC = "static"
_ORIGINS = (ORIGIN_EXECUTION, ORIGIN_STATIC)


def hash_file(path) -> tuple[str, int]:
    """文件的 sha256 十六进制 + 字节数。分块读，大 PDF 不整个进内存。"""
    h = hashlib.sha256()
    size = 0
    with open(os.fspath(path), "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


@dataclasses.dataclass(frozen=True)
class SourceArtifact:
    """一份源图产物：source ID、字节 hash、类型 / 大小、实例 / override 身份。

    `source_id` 对 execution 来源是 `runtime_asset_id()`（`runtime:<script>#<stem>`），
    对 static 来源是素材相对路径（POSIX）——两者都是跨机器稳定的身份，不含绝对路径。
    `receipt_id` / `receipt_identity` / `generation` / `patch_hash` 对 static 来源恒为
    `None`：没有执行就没有执行身份，**不许**拿「最近一次会话」去冒充。execution 来源
    两个都要：`receipt_id`（实例）与 `receipt_identity`（公开语义）。
    """

    source_id: str
    origin: str  # ORIGIN_EXECUTION | ORIGIN_STATIC
    kind: str  # 文件类型（扩展名去点、小写）
    bytes_sha256: str
    size_bytes: int
    receipt_id: str | None = None
    generation: int | None = None
    patch_hash: str | None = None
    receipt_identity: str | None = None  # 回执的 public_identity()（语义身份的坐标）

    def __post_init__(self) -> None:
        if not isinstance(self.source_id, str) or not self.source_id:
            raise ValueError("source_id 必须是非空字符串")
        if self.origin not in _ORIGINS:
            raise ValueError(f"origin 非法: {self.origin!r}（可选 {_ORIGINS}）")
        if not isinstance(self.kind, str) or not self.kind or self.kind != self.kind.lower():
            raise ValueError(f"kind 必须是小写扩展名: {self.kind!r}")
        if not re.fullmatch(r"[0-9a-f]{64}", self.bytes_sha256 or ""):
            raise ValueError("bytes_sha256 必须是 64 位十六进制 sha256")
        if not isinstance(self.size_bytes, int) or isinstance(self.size_bytes, bool):
            raise ValueError("size_bytes 必须是整数")
        if self.size_bytes <= 0:
            raise ValueError("size_bytes 必须为正：零字节的文件不是产物")
        if self.origin == ORIGIN_STATIC and (
            self.receipt_id is not None
            or self.generation is not None
            or self.patch_hash is not None
            or self.receipt_identity is not None
        ):
            raise ValueError(
                "static 来源没有执行身份"
                "（receipt_id / receipt_identity / generation / patch_hash 必须为 None）"
            )
        if self.origin == ORIGIN_EXECUTION and not self.receipt_id:
            raise ValueError("execution 来源必须带产出它的 receipt_id")
        if self.origin == ORIGIN_EXECUTION and not self.receipt_identity:
            raise ValueError("execution 来源必须带回执的 receipt_identity（公开身份）")

    def to_payload(self) -> dict:
        out = dataclasses.asdict(self)
        out["source_artifact_version"] = SOURCE_ARTIFACT_VERSION
        return out

    def semantic_identity(self) -> str:
        """公开语义身份：**不含**字节 hash——它回答「这是哪张图的哪一版」，
        字节 hash 回答「文件长什么样」，两者是不同的问题（04 §3）。也**不含**
        `receipt_id` / `generation`：那是实例，不是语义。"""
        payload = {
            "source_id": self.source_id,
            "origin": self.origin,
            "kind": self.kind,
            "receipt_identity": self.receipt_identity,
            "patch_hash": self.patch_hash,
        }
        canon = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(canon.encode("utf-8")).hexdigest()


def source_artifact_from_file(
    path,
    *,
    source_id: str,
    origin: str,
    receipt_id: str | None = None,
    generation: int | None = None,
    patch_hash: str | None = None,
    receipt_identity: str | None = None,
) -> SourceArtifact:
    """磁盘文件 → SourceArtifact（当场读字节算 hash；类型按扩展名）。"""
    sha, size = hash_file(path)
    kind = os.path.splitext(os.fspath(path))[1].lstrip(".").lower()
    return SourceArtifact(
        source_id=source_id,
        origin=origin,
        kind=kind,
        bytes_sha256=sha,
        size_bytes=size,
        receipt_id=receipt_id,
        generation=generation,
        patch_hash=patch_hash,
        receipt_identity=receipt_identity,
    )


def savefig_stem(fname) -> str:
    """`savefig(fname)` 的第一个参数 → stem；不是路径（缓冲区）时返回空串。

    worker 与 browser 以前各写各的（一个用 `Path(...).stem`，一个用
    `os.path.splitext(os.path.basename(...))`）。两者在 `a.tar.gz` 这类
    多后缀上一致，在 `Path("out")/"f.pdf"` 上也一致——但「一致」这件事没有
    任何东西看着它。
    """
    if not isinstance(fname, (str, os.PathLike)):
        return ""  # BytesIO / 文件对象：不是一份产物
    return os.path.splitext(os.path.basename(os.fspath(fname)))[0]


def fallback_stems(taken, script_stem: str, count: int) -> list[str]:
    """给 `count` 张没有 savefig 的 Figure 编出确定性 stem。

    `taken` 是**已被认领**的 stem 集合（savefig 捕获的那些）。返回的名字
    只依赖「这是本次捕获里的第几张」，与 pyplot 的 figure 号无关。
    """
    used = set(taken)
    out: list[str] = []
    base = script_stem or "figure"
    n = 1
    for _ in range(count):
        while True:
            stem = base if n == 1 else f"{base}-{n}"
            n += 1
            if stem not in used:
                break
        used.add(stem)
        out.append(stem)
    return out


def collect_pyplot_figures(
    capture: dict, script_stem: str, plt, limit: int = MAX_PYPLOT_FALLBACK
) -> tuple[list[str], int]:
    """把脚本跑完仍活着、且没被 savefig 认领的 pyplot Figure 补进 `capture`。

    就地修改 `capture`（stem → Figure，保持产出顺序），返回
    `(新增的 stem 列表, 因上限被丢掉的张数)`——调用方据此分辨「这张图有没有
    原始产物」，以及要不要跟用户说「还有 N 张没显示」。

    去重按 `id(figure)`：`fig.savefig("a.pdf")` 之后那张图还在 pyplot 的
    figure 管理器里，不去重就会同一张图挂两个 stem，用户看到两个一模一样的
    面板、改一个另一个不动。

    `plt.get_fignums()` 的顺序即产出顺序（pyplot 的 Gcf 按创建先后维护），
    stem 的序号只由「本次捕获里的第几张」决定，与 figure 号无关。
    """
    seen = {id(f) for f in capture.values()}
    pending = []
    for num in plt.get_fignums():
        fig = plt.figure(num)
        if id(fig) in seen:
            continue
        seen.add(id(fig))
        pending.append(fig)
    dropped = max(0, len(pending) - max(0, int(limit)))
    if dropped:
        pending = pending[: max(0, int(limit))]
    stems = fallback_stems(capture.keys(), script_stem, len(pending))
    for stem, fig in zip(stems, pending):
        capture[stem] = fig
    return stems, dropped


#: 输入观察（统一实施包 U09，ADR 0070）：脚本经 Python 的 `open` 读到的项目内文件最多记这么多条；
#: 超过就 `truncated=True`——回执有界，不是全系统审计。
INPUT_OBSERVER_MAX_FILES = 256
#: 每个观察到的文件最多为它算 hash 的字节数；再大只记大小，`sha256` 为 None（大文件的 hash 归数据绑定
#: 那一侧按需算，不在脚本跑完那一刻同步付出）。
INPUT_OBSERVER_HASH_LIMIT = 64 * 1024 * 1024
#: 观察不到的输入通道（D13）：这些走不到 Python 的 `open`，回执只能如实标 `partial`。
INPUT_OBSERVER_UNOBSERVED = ("native_io", "network", "subprocess", "os_open")
#: 观察到的文件里剔掉的源码后缀（它们归 `source_revision` / `local_modules`）。
CODE_SUFFIXES = frozenset({"py", "pyc", "pyi", "pyw"})
OBSERVATION_PARTIAL = "partial"


def _within(real: str, root: str, *, pathmod=os.path) -> bool:
    """`real` 在 `root` 之内（两者都已 realpath）：等于它，或以 `root + sep` 开头——与 `projectenv.contained_path`
    同一形状（先 realpath 再按前缀判、`+ sep` 防 `/a/proj-evil`），只是这里两边都已经是 realpath。

    不用 `os.path.commonpath`：Windows 上两条路径不在同一个盘（临时目录在 C:、checkout 在 D:）它抛 ValueError，
    调用方一 `except … continue` 就把一个本来该记的模块吞掉了（#498 第四轮：`local_modules` 在 Windows 上恒空）。
    比较前 `normcase`：Windows 大小写不敏感，`realpath` 对存在的路径已给出规范大小写，对短名（`RUNNER~1`）也已展开。
    """
    real_n, root_n = pathmod.normcase(real), pathmod.normcase(root.rstrip(pathmod.sep) or root)
    return real_n == root_n or real_n.startswith(root_n + pathmod.sep)


class InputObserver:
    """记下脚本执行期间经 `builtins.open` / `io.open` / `Path.open` **以只读模式成功打开**的、落在项目根之内的
    文件（ExecutionReceipt 的「已观察的数据身份」，ADR 0070）。

    观察到的就是观察到的：h5py / netCDF / 自家 C 扩展直接调 `H5Fopen` / `fopen`，`np.memmap` 走 `os.open`，
    `urllib` 走 socket——这些一条都进不来，所以 `observation` **永远是 `partial`**，`unobserved` 列出没看的通道；
    回执据此不冒充「全部输入已核」。记的是**真正打开的那条路径**（相对路径只读回退换到脚本目录之后的那份），
    去重、有界（`INPUT_OBSERVER_MAX_FILES`）。只记不改：所有调用原样交给真正的 `open`，观察器自己出错也不
    影响脚本（记账失败就少一条，不抛）。

    与 `install_relative_read_fallback()` 的叠法：观察器**先装**（贴着真正的 open），回退**后装**（在外层）——
    回退换出来的路径经内层的观察器记下，正是脚本实际读到的那份。
    """

    def __init__(self, project_root: str) -> None:
        self.project_root = os.path.abspath(project_root)
        self._seen: dict[str, str] = {}  # realpath → 项目相对 POSIX 路径
        self.truncated = False
        self._uninstall = None

    # ---- 记账 ----
    def _note(self, file) -> None:
        try:
            if not isinstance(file, (str, os.PathLike)):
                return
            name = os.fspath(file)
            if not isinstance(name, str) or not name:
                return
            real = os.path.realpath(name)
            if real in self._seen:
                return
            root = os.path.realpath(self.project_root)
            if not _within(real, root):
                return
            if not os.path.isfile(real):
                return
            if len(self._seen) >= INPUT_OBSERVER_MAX_FILES:
                self.truncated = True
                return
            self._seen[real] = pathlib.PurePath(os.path.relpath(real, root)).as_posix()
        except Exception:  # noqa: BLE001 —— 观察器绝不影响脚本
            return

    def install(self):
        """装上三处 open 的观察包装；返回卸载函数。"""
        real_open = builtins.open
        real_io_open = io.open
        real_path_open = pathlib.Path.open
        observer = self

        def _wrap(original):
            def observed_open(file, mode="r", *args, **kwargs):
                fh = original(file, mode, *args, **kwargs)
                if _readonly_mode(mode):
                    observer._note(file)
                return fh

            return observed_open

        def observed_path_open(self_path, mode="r", *args, **kwargs):
            fh = real_path_open(self_path, mode, *args, **kwargs)
            if _readonly_mode(mode):
                observer._note(self_path)
            return fh

        builtins.open = _wrap(real_open)
        io.open = _wrap(real_io_open)
        pathlib.Path.open = observed_path_open

        def uninstall() -> None:
            builtins.open = real_open
            io.open = real_io_open
            pathlib.Path.open = real_path_open

        self._uninstall = uninstall
        return uninstall

    def uninstall(self) -> None:
        if self._uninstall is not None:
            self._uninstall()
            self._uninstall = None

    # ---- 报告 ----
    def report(self, *, local_modules: list[dict] | None = None) -> dict:
        """回执的 `inputs` 段：观察到的文件（相对路径 + 大小 + sha256）、本地模块、观察的完备性。"""
        files = []
        for real, rel in sorted(self._seen.items(), key=lambda kv: kv[1]):
            # 源码文件不是数据：脚本本身是 `source_revision`，import 到的模块在 `local_modules`——
            # 同一份事实不记两遍
            if rel.rsplit(".", 1)[-1].lower() in CODE_SUFFIXES:
                continue
            entry: dict = {"path": rel, "size": None, "sha256": None}
            try:
                size = os.path.getsize(real)
                entry["size"] = size
                if size <= INPUT_OBSERVER_HASH_LIMIT:
                    entry["sha256"] = hash_file(real)[0]
            except OSError:
                pass
            files.append(entry)
        return {
            "observation": OBSERVATION_PARTIAL,
            "channels": ["python_open"],
            "unobserved": list(INPUT_OBSERVER_UNOBSERVED),
            "truncated": bool(self.truncated),
            "files": files,
            "local_modules": list(local_modules or []),
        }


def _readonly_mode(mode) -> bool:
    if not isinstance(mode, str):
        return False
    return "r" in mode and not any(c in mode for c in "+wxa")


def observed_local_modules(
    project_root: str, modules: dict, *, exclude_dir: str = ""
) -> list[dict]:
    """脚本跑完之后 `sys.modules` 里 `__file__` 落在项目根之内的模块（回执的「已观察的本地模块」）。
    `exclude_dir` 是引擎自己平铺 import 的目录（worker 把 engine 目录放进了 sys.path，那些不是用户的）。
    按名字排好序、有界（与文件同一个上限）。"""
    root = os.path.realpath(os.path.abspath(project_root))
    excl = os.path.realpath(exclude_dir) if exclude_dir else ""
    out: list[dict] = []
    for name in sorted(modules):
        mod = modules.get(name)
        file = getattr(mod, "__file__", None)
        if not isinstance(file, str) or not file:
            continue
        try:
            real = os.path.realpath(file)
        except (ValueError, OSError):
            continue
        if not _within(real, root):
            continue
        # 引擎自己平铺 import 的那些不是用户的；不在同一个盘也只是 False、不抛
        if excl and _within(real, excl):
            continue
        entry: dict = {
            "name": name,
            "path": pathlib.PurePath(os.path.relpath(real, root)).as_posix(),
        }
        try:
            entry["sha256"] = hash_file(real)[0]
        except OSError:
            entry["sha256"] = None
        out.append(entry)
        if len(out) >= INPUT_OBSERVER_MAX_FILES:
            break
    return out


def install_relative_read_fallback(
    script_dir: str, project_root: str, sandbox_dir: str | None = None
):
    """装上「相对路径只读回退」。返回一个卸载函数（测试与嵌入场景用）。

    语义见模块头。四条硬约束在这里逐条落地，任何一条不成立就原样交给
    真正的 `open` —— 包括让它抛它本来会抛的那个 `FileNotFoundError`。

    `sandbox_dir` 默认取安装那一刻的 cwd（两个调用方都已经把 cwd 设成沙盒）。
    做成参数是为了能单测，也为了脚本自己 `os.chdir()` 之后判据不跟着漂——
    沙盒是我们建的那个目录，不是「此刻碰巧在哪」。
    """
    real_open = builtins.open
    real_io_open = io.open
    real_path_open = pathlib.Path.open
    script_dir = os.path.abspath(script_dir)
    project_root = os.path.abspath(project_root)
    sandbox_dir = os.path.abspath(sandbox_dir if sandbox_dir is not None else os.getcwd())

    def _within_sandbox(name: str) -> str | None:
        """绝对路径 → 它相对沙盒的那一段；不在沙盒里就 None。"""
        try:
            real = os.path.realpath(name)
            box = os.path.realpath(sandbox_dir)
        except OSError:
            return None
        try:
            # 跨盘符在 Windows 上抛 ValueError —— 那本来就是「不在沙盒里」。
            if os.path.commonpath([real, box]) != box:
                return None
        except ValueError:
            return None
        rel = os.path.relpath(real, box)
        return None if rel.startswith("..") or rel == "." else rel

    def _fallback_path(file) -> str | None:
        if not isinstance(file, (str, os.PathLike)):
            return None  # 已经是 fd / 文件对象
        name = os.fspath(file)
        if not isinstance(name, str) or not name:
            return None
        if os.path.isabs(name):
            # **绝对路径只在一种情况下算数**：它指向沙盒内部。裸相对路径就是
            # 拿 cwd 拼出来的，而 cwd 就是沙盒——所以「先 realpath 再 open」的
            # 库（Pillow 10.4.0 的 `Image.open` 正是如此，12.x 已改回 fspath）
            # 递过来的那条路径，语义上与相对路径是同一件事。
            #
            # 沙盒**之外**的绝对路径一个都不碰：那是用户明确指名的位置，
            # 「就近找一个能用的」在那里是越权，不是便利。
            rel = _within_sandbox(name)
            if rel is None:
                return None
        else:
            rel = name
        # 存在性**按真正的 open 会用的那条路径判**：相对的按 cwd 解（脚本
        # 可能 `os.chdir()` 进了子目录），绝对的就用它自己。拿沙盒根去拼的话，
        # chdir 之后脚本自己写出来的那份会被无视、读到项目里的原件。
        if os.path.exists(name):
            return None  # 已经有了——脚本自己写出来的那份优先
        cand = os.path.abspath(os.path.join(script_dir, rel))
        try:
            real = os.path.realpath(cand)
            root = os.path.realpath(project_root)
        except OSError:
            return None
        # 越界的读不「就近找一个能用的」：符号链接解开之后仍要落在项目根里。
        # `commonpath` 在 Windows 上跨盘符会抛 ValueError——那本来就是越界，
        # 当成拒绝即可（放行的话这条边界在 Windows 上等于不存在）。
        try:
            if os.path.commonpath([real, root]) != root:
                return None
        except ValueError:
            return None
        if not os.path.isfile(cand):
            return None
        return cand

    def _readonly(mode) -> bool:
        if not isinstance(mode, str):
            return False
        return "r" in mode and not any(c in mode for c in "+wxa")

    def _wrap(original):
        def guarded_open(file, mode="r", *args, **kwargs):
            if _readonly(mode):
                alt = _fallback_path(file)
                if alt is not None:
                    return original(alt, mode, *args, **kwargs)
            return original(file, mode, *args, **kwargs)

        return guarded_open

    def guarded_path_open(self, mode="r", *args, **kwargs):
        """`Path.open` 自己也要包一层——**3.10 上它不走 `io.open`**（见模块头）。

        打在类上而不是追着 `_accessor` 打：`read_text` / `read_bytes` 都是
        `self.open(...)` 的实例方法查找，这一层对每个版本都成立。
        """
        if _readonly(mode):
            alt = _fallback_path(self)
            if alt is not None:
                return real_path_open(pathlib.Path(alt), mode, *args, **kwargs)
        return real_path_open(self, mode, *args, **kwargs)

    builtins.open = _wrap(real_open)
    # `io.open` 是**另一个绑定**（见模块头）：3.11+ 的 pathlib 走的是它。
    io.open = _wrap(real_io_open)
    # 3.10 的 pathlib 两个都不走，只好直接包它自己。
    pathlib.Path.open = guarded_path_open

    def uninstall() -> None:
        builtins.open = real_open
        io.open = real_io_open
        pathlib.Path.open = real_path_open

    return uninstall


# ---------------------------------------------------------------- 未使用的缺失 import

#: 只有这些顶级模块会被判「未使用」（评审 #555 两条 P1）：绑定没被读**证明不了** import 没用——
#: 别名同样可以只为副作用而写：`import cmocean as cm` 注册色图、`import scienceplots as _sp` 注册样式、
#: `import requests as _r` 改 warnings 过滤器并装 logging handler。占位不执行这些，之后的行为就悄悄
#: 变了（或报一句误导的错），而不是「请装它」。
#: 判据是一份**进程级副作用快照**（唯一出处 `tests/support/import_side_effects.py`）：全新解释器
#: `-I` 里 import 前后比 matplotlib 是否进 `sys.modules`、`os.environ`、warnings 过滤器、logging、
#: `sys.path` / `meta_path` / `path_hooks`、信号处理器、各 excepthook / displayhook、atexit、builtins、
#: codec 注册与 locale……**任何一项变了就不进**。实测（Python 3.13 / matplotlib 3.11.2，2026-09-24）
#: 16 个候选只剩下面 6 个：requests / astropy / sklearn 装 logger handler，numba / joblib / h5py /
#: xarray / netCDF4 / openpyxl / numexpr 改 warnings 过滤器、atexit、环境变量或 meta_path；cmocean /
#: scienceplots / colorcet / cmasher / seaborn / lmfit 还会装 matplotlib。sympy 唯一的变化是给它
#: **自己的** `SymPyDeprecationWarning` 加的过滤器（包不在，这个类就不存在）——快照里唯一的豁免。
#: 表外的名字一律照旧准备——宁可多问一次，不猜；扩名单要用同一份快照实测。
#: `tests/test_unused_missing_import.py` 在 worker 解释器里对装了的那些现量一遍。
SIDE_EFFECT_FREE_IMPORTS = frozenset(
    {"sympy", "tqdm", "statsmodels", "networkx", "tabulate", "yaml"}
)

#: 出现任何一个就判不清「名字有没有被读」：`globals()["smp"]` / `vars()` / `eval("smp")` /
#: `exec(...)` / `__import__` / `compile` / `m.__dict__` 都能不经 Name 节点读到绑定。
_OPAQUE_NAMES = frozenset(
    {"globals", "vars", "locals", "eval", "exec", "compile", "__import__", "__dict__"}
)


def unused_imports(tree) -> frozenset[str]:
    """脚本里**被 import 了、但绑定的名字从未被读取**的顶级模块名——判不清的一律不算。

    「未使用的缺失 import 不挡图」（ADR 0061 §二 2026-09-24 修订）的唯一判据：父进程
    （`importscan` → 联合计划不把它算进 needed）与 worker（`install_unused_import_placeholders`
    的名单）各调一次。只认 AST 能证明的形状，任一条不成立就不收：

    * 这个模块在脚本里出现的**每一处**都是 `import X as Y`，X 不带点（`import X.sub` 要真装载
      子模块；`from X import …` 本身就是在用它）。**裸 `import X` 一律不收**：没读过的裸 import
      常常是为了副作用（`import scienceplots` 之后 `plt.style.use("science")`、`import cmocean` 注册
      色图）——占位会把「请装 scienceplots」换成一句看不懂的「样式不存在」。起了别名 = 写的人
      打算用那个名字，一次没用才是遗留；
    * X 在 `SIDE_EFFECT_FREE_IMPORTS` 里——别名同样可以只为副作用而起（`import cmocean as cm`），
      所以「绑定没读」之外还要「import 它本身什么都不改」，这一条只能靠实测过的名单；
    * 这些 import 都不在 `try` / `with` 里（`try: import X; HAVE_X = True` 的分支走向
      取决于它 import 得到与否——占位会把「没装」变成「装了」）；
    * 绑定的名字（Y 或 X）在别处**一次都不出现**：Name（读 / 写 / 删）、形参、
      global / nonlocal、函数 / 类名、except 名、match 捕获、别的 import 的绑定、属性名、
      关键字参数名，一律算出现（宁可多判「用到了」）；
    * X 与绑定名都不作为字符串常量出现（`sys.modules["X"]` / `importlib.import_module("X")` /
      `getattr(mod, "Y")` / `__all__`）；
    * 脚本里没有 `_OPAQUE_NAMES` 里的任何一个（出现就整份放弃：判不清）。

    判据只看脚本自己这一份文件：本地模块里的 import 不在这里判（它们照旧按 needed 走）。
    """
    import ast  # noqa: PLC0415 — 只有这里用，worker 与 Flask 侧都是标准库

    guarded: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Try, ast.With, ast.AsyncWith)) or type(node).__name__ == "TryStar":
            for inner in ast.walk(node):
                if isinstance(inner, ast.Import):
                    guarded.add(id(inner))

    candidates: dict[str, set[str]] = {}  # 顶级模块名 → 绑定名
    rejected: set[str] = set()
    own_aliases: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".", 1)[0]
                if alias.asname is None or "." in alias.name or id(node) in guarded:
                    rejected.add(top)
                    continue
                candidates.setdefault(top, set()).add(alias.asname or alias.name)
                own_aliases.add(id(alias))
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            rejected.add(node.module.split(".", 1)[0])

    seen: set[str] = set()
    strings: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            seen.add(node.id)
        elif isinstance(node, ast.arg):
            seen.add(node.arg)
        elif isinstance(node, ast.Attribute):
            seen.add(node.attr)
        elif isinstance(node, ast.keyword) and node.arg:
            seen.add(node.arg)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            seen.update(node.names)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            seen.add(node.name)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            seen.add(node.name)
        elif isinstance(node, ast.alias) and id(node) not in own_aliases:
            seen.add(node.asname or node.name.split(".", 1)[0])
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            strings.add(node.value)
        else:
            for attr in ("name", "rest"):  # match 的捕获（MatchAs / MatchStar / MatchMapping）
                value = (
                    getattr(node, attr, None) if type(node).__name__.startswith("Match") else None
                )
                if isinstance(value, str):
                    seen.add(value)
    if seen & _OPAQUE_NAMES:
        return frozenset()

    def _mentioned(text: str) -> bool:
        return any(s == text or s.startswith(text + ".") for s in strings)

    out = set()
    for top, bound in candidates.items():
        if top not in SIDE_EFFECT_FREE_IMPORTS or top in rejected or _mentioned(top):
            continue
        if any(name in seen or _mentioned(name) for name in bound):
            continue
        out.add(top)
    return frozenset(out)


def install_unused_import_placeholders(script: str, names) -> None:
    """让脚本里**被证明未使用**（`unused_imports`）又确实装不上的那几条 `import X` 成功。

    只在四条同时成立时换成占位模块，其余一律原样交给真正的 `__import__`：

    * 发起 import 的是脚本自己（调用方 globals 的 `__file__` 就是这个脚本）——库里的
      `try: import X except ImportError` 永远看不到占位；
    * `level == 0`、没有 fromlist、名字在名单里；
    * 真的 import 抛了 `ModuleNotFoundError` 且缺的就是 X 本身（这一条只是短路：X 装了、它的依赖坏了时
      异常名是那个依赖，不必再问 `find_spec`——真正挡住这种情形的是下一条，X 找得到）；
    * **而且 import 系统确实找不到它**：`importlib.util.find_spec(X) is None`（评审 #555 P2）。`exc.name == X`
      只说明异常这么写着——一个找得到的同名模块（项目里的 `sympy.py`）初始化到一半自己抛
      `ModuleNotFoundError(name="sympy")`，它已经执行过的副作用不会因为占位而撤销，失败必须照常抛出。
      顶层名的 `find_spec` 只问 finder、不执行模块代码；它自己抛任何异常都按「判不清」处理，照常抛出。

    占位**不进 `sys.modules`**（别处再 import X 仍然是真实的失败）；读它的任何非 dunder
    属性抛 `ModuleNotFoundError("No module named 'X'")`——判据若错了，失败形状与原来逐字
    相同，运行后的缺包修复照旧接手。装了就不卸：脚本定义的函数在渲染期仍可能执行 import。
    """
    import importlib.util  # noqa: PLC0415
    import sys  # noqa: PLC0415
    import types  # noqa: PLC0415

    names = frozenset(names or ())
    if not names:
        return
    real_import = builtins.__import__
    target = os.path.normcase(os.path.realpath(script))
    verdict: dict[str, bool] = {}

    def _from_script(globals_) -> bool:
        file = globals_.get("__file__") if isinstance(globals_, dict) else None
        if not isinstance(file, str):
            return False
        if file not in verdict:
            verdict[file] = os.path.normcase(os.path.realpath(file)) == target
        return verdict[file]

    class _Unused(types.ModuleType):
        def __getattr__(self, attr):
            if attr.startswith("__") and attr.endswith("__"):
                raise AttributeError(attr)
            raise ModuleNotFoundError(f"No module named '{self.__name__}'", name=self.__name__)

    def _not_findable(name: str) -> bool:
        """区分「import 系统没找到」与「找到了、loader 执行时自己抛了同名的错」：只有前者给占位。"""
        try:
            return importlib.util.find_spec(name) is None
        except Exception:  # noqa: BLE001 — 判不清就不给占位
            return False

    def _import(name, globals=None, locals=None, fromlist=(), level=0):
        if level or fromlist or name not in names or not _from_script(globals):
            return real_import(name, globals, locals, fromlist, level)
        try:
            return real_import(name, globals, locals, fromlist, level)
        except ModuleNotFoundError as exc:
            # `exc.name != name` 只是省一次 find_spec 的短路；判据是「import 系统找不到 X」
            if exc.name != name or not _not_findable(name):
                raise
            print(
                f"[deps] {name} 没有安装；脚本 import 了它但没有用到，已用占位代替"
                f"（真用到时会报 No module named '{name}'）",
                file=sys.stderr,
            )
            return _Unused(name)

    builtins.__import__ = _import
