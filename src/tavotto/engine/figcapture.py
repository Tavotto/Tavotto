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

共享捕获语义的唯一出处：

* `savefig_stem()` —— `savefig(路径)` 里那个 stem 怎么取；
* `savefig_call()` —— 一次 savefig 调用决定产物长相的那几个参数（`bbox_inches` /
  `pad_inches` / `dpi` / `transparent` / 底色 / 格式）按 matplotlib 自己的解析顺序
  记成 JSON（`savefig_calls` 进描述符，见函数 docstring）；
* `collect_pyplot_figures()` —— 脚本跑完之后还活着的 pyplot Figure 怎么补进
  捕获表（去重、命名、保序）；`adopt_displaced_writers()` —— 写了文件却因 stem 被别的图持有而没认领的图
  （在它之前调用）；
* `install_colorbar_capture()` —— 原样调用 matplotlib，记下手动 `cax=` 的显式 `ax` 宿主；
* `install_relative_read_fallback()` —— 相对路径**只读**回退（见下）；
* `remap_target()` / `install_input_remap()` / `missing_input_of()` —— 数据找不到时用户指认的
  只读改指表与「缺的是哪个」的判据（ADR 0106；父进程推规则也用 `remap_target`）；
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
路径回退一个都不碰：那是用户指名的位置，「就近找一个能用的」在那里是越权。
（数据被挪走之后，用户在界面上**亲手指认**新位置、按项目记住的只读改指表是另一回事——
那不是回退在猜，见下文「数据改指」段与 ADR 0106。）

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

**第四个入口是 numpy 自己的 `DataSource.open`**（2026-09-24）。`np.loadtxt` /
`np.genfromtxt` 不直接调 open：`DataSource.open` 先用 `os.path.exists` 在 cwd 与它的
`destpath` 下找文件，找不到当场 FileNotFoundError（"d.txt not found."），根本走不到
上面三个。而 `databinding.evidence` 对「数据就在脚本旁边」判 `default_ok`、不问用户
——它的前提正是「沙盒 + 只读回退读得到」，对科学脚本里最常见的这两个读取器前提不成立，
脚本旁边的 `data.txt` 一行 `np.loadtxt("data.txt")` 就读不到。包在它上面**不是**把回退
扩到 `exists`：判据与三个 open 入口逐条相同（同一个 `_fallback_path`），外加一条——
只在 DataSource 的 `destpath` 就是此刻的 cwd 时改指（相对路径那时才与真正的 open 解到
同一处；自建 `DataSource(destpath=…)` 的不碰）。只包已经 import 过的 numpy：本模块
纯标准库，不替脚本 import 它（worker 在装回退之前已经经 matplotlib 载入了 numpy）。

这四个之外不再扩大：pandas 的 `get_handle`、`numpy.load`、`PIL.Image.open`、
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
import contextlib
import dataclasses
import glob
import hashlib
import inspect
import io
import itertools
import json
import math
import ntpath
import os
import pathlib
import pickle
import posixpath
import re
import shutil
import stat
import sys
import threading
import time
import types
import unicodedata
import weakref

if __package__:
    from . import axestraversal, patchspec
else:
    import axestraversal
    import patchspec

__all__ = [
    "savefig_stem",
    "savefig_call",
    "record_savefig_call",
    "savefig_target_key",
    "savefig_verify_render",
    "savefig_verifier",
    "note_savefig_writer",
    "MAX_SAVEFIG_WRITERS",
    "savefig_calls_of",
    "SavefigObservations",
    "savefig_metadata_reader",
    "frame_call",
    "frame_extra_artists",
    "FRAME_ATTR",
    "MAX_SAVEFIG_CALLS",
    "MAX_SAVEFIG_CALLS_HARD",
    "collect_pyplot_figures",
    "fallback_stems",
    "install_relative_read_fallback",
    "install_colorbar_capture",
    "unused_imports",
    "reaches_main",
    "install_unused_import_placeholders",
    "SIDE_EFFECT_FREE_IMPORTS",
    "InputObserver",
    "observed_local_modules",
    "INPUT_OBSERVER_MAX_FILES",
    "OBSERVATION_PARTIAL",
    "MAX_PYPLOT_FALLBACK",
    "ShownFigures",
    "PdfPagesLedger",
    "MAX_PDF_PAGES_LEDGER",
    "adopt_pdf_pages",
    "SOURCE_SAVEFIG",
    "SOURCE_PYPLOT",
    "PROFILE_SAFE",
    "PROFILE_NATIVE",
    "ARTIFACT_EXTS",
    "DESCRIPTOR_VERSION",
    "PREVIEW_LEDGER",
    "publish_preview",
    "usable_preview_generation",
    "open_preview_pair",
    "PreviewLedgerUnreadable",
    "published_preview_stems",
    "CapturedFigureDescriptor",
    "build_descriptor",
    "descriptor_from_payload",
    "runtime_asset_id",
    "source_fingerprint",
    "size_mm_of",
    "find_artifacts",
    "find_original_artifact",
    "project_relative_stem",
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

#: 一个 stem 最多记几次 savefig 调用。同一张图存 pdf + png（+ svg）是常态，循环里
#: 反复存同一个名字也见过；再多的调用对「产物长什么样」不再有新信息，记下来只是
#: 让 build 响应变胖。超出的丢掉、不报错（前 8 次已足够说明脚本的意图）——**例外**：
#: 超出之后第一次出现的格式仍记下（图幅按「与原件同格式的第一次」挑，`frame_call`；丢了它，
#: 循环里先存了 8 张 png 的脚本，末尾那份 tight 的 pdf 原件就按 png 那次的框出图）。
#: 这类例外本身也有上限：总数最多 `MAX_SAVEFIG_CALLS_HARD`。
MAX_SAVEFIG_CALLS = 8
MAX_SAVEFIG_CALLS_HARD = 2 * MAX_SAVEFIG_CALLS


def install_colorbar_capture(mfigure) -> None:
    """Record explicit `cax=…, ax=…` owners without giving matplotlib layout metadata.

    Figure and SubFigure inherit this method. Forward the call unchanged; only
    standard Axes / lists / tuples / ndarray declarations are observed. In
    particular, do not consume iterators which matplotlib ignores with cax.
    """
    from functools import wraps

    original = mfigure.FigureBase.colorbar
    if getattr(original, "_tavotto_colorbar_capture", False):
        return
    axes_type, array_type = mfigure.Axes, mfigure.np.ndarray

    @wraps(original)
    def colorbar(self, *args, **kwargs):
        cb = original(self, *args, **kwargs)
        cax = kwargs.get("cax", args[1] if len(args) > 1 else None)
        ax = kwargs.get("ax", args[2] if len(args) > 2 else None)
        if cax is not None and ax is not None:
            parents = list(ax.flat) if type(ax) is array_type else ax
            if type(parents) not in (list, tuple):
                parents = [parents]
            if parents and all(isinstance(p, axes_type) for p in parents):
                cb._tavotto_colorbar_parents = tuple(parents)  # noqa: SLF001
        return cb

    colorbar._tavotto_colorbar_capture = True  # noqa: SLF001
    mfigure.FigureBase.colorbar = colorbar


#: 每次执行的观察预算，独立于旧的逐 stem 调用表。超限必须说历史不完整，
#: 不能把「最后一条留下的记录」当成「磁盘上最后一次保存」。不保留 Figure 强引用。
MAX_SAVEFIG_OBSERVATIONS = 128
MAX_SAVEFIG_OBSERVATION_BYTES = 8192

#: 根 Figure 上挂图幅（frame，ADR 0098）四边外伸的属性名。唯一的写入方是
#: `pathgeom.set_frame`（它那边的 `FRAME_ATTR` 与这里是同一个字面量，用例钉住）。
FRAME_ATTR = "_mm_frame"

#: 已知的图产物扩展名——「什么算一份原始产物」的唯一出处（顺序即优先级）。
#: `discover.OUT_EXTS` 与 `handoff.OUT_EXTS` 是它的镜像别名：静态扫描认产物、
#: 交接找产物、描述符判「有没有原件」必须是同一张表，否则三处各认一套，
#: 表现是「discover 说这是图、写回说没有原件」。
ARTIFACT_EXTS = (".pdf", ".png", ".svg", ".jpg", ".jpeg", ".eps", ".tif", ".tiff")

#: 捕获描述符的 schema 版本。**捕获语义改变时才升**（stem 取法、去重、
#: fingerprint 构成……）；它参与 fingerprint，所以升版 = 所有旧 fingerprint
#: 自然失配 = 「按旧语义捕的图可能已过时」这句 stale hint 如实成立。
#: v2（2026-10-24）：写进 `PdfPages` 的图也被认领，stem 取 PDF 文件名（见 `PdfPagesLedger`）。
DESCRIPTOR_VERSION = 2

#: 预览发布账本（P01b，EX12）：会话产物目录里的这份文件逐 stem 记着
#: 「**本次运行**已经完整发布过的预览」。`<stem>.svg` 文件存在**不是**完成事实——
#: 半写的文件、上一次运行残留的文件都满足「存在」。CLI 的「捕获了几张图」读它，
#: 不再数 `.svg` 后缀。写入方是 `figsession.render`（发布成功之后才记），读取方是
#: `runcli`；两边都 import 这一个模块，不各写一份格式。
#:
#: **文件名不能撞用户 stem**：stem 来自 `savefig("published.png")` 这类脚本自己起的名字
#: （可以以点开头），引擎写进同一目录的名字只有 `<stem>.svg` / `<stem>.json` /
#: `.<stem>.svg.part` / `.<stem>.json.part` / `.<stem>.<ext>.prev.part`。账本取一个
#: **不带任何扩展名**的点名——它不以 `.svg` / `.json` / `.part` 结尾，上面任何一个
#: 形态都不可能等于它（`published.json` 就曾被 stem=`published` 的 manifest 撞上）。
#: 它也不是图：任何按 `.svg` / `.json` 后缀枚举目录的人都看不见它。
PREVIEW_LEDGER = ".preview-ledger"


class PreviewLedgerUnreadable(OSError):
    """账本**存在但读不了 / 格式坏**——与「账本不存在」（首次发布）是两件事（#969）。

    发布路径遇到它必须失败（上一版预览原封不动），不能当成空账本重建：重建会把已发布的
    其它 stem 的完成记录静默覆盖掉。
    """


def _load_preview_ledger(out_dir) -> dict | None:
    """读账本。**不存在** → None（首次发布）；读不出来 / 格式坏 → `PreviewLedgerUnreadable`。"""
    try:
        with open(os.path.join(os.fspath(out_dir), PREVIEW_LEDGER), encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise PreviewLedgerUnreadable(f"preview ledger cannot be read: {exc}") from exc
    stems = data.get("stems") if isinstance(data, dict) else None
    seq = data.get("seq") if isinstance(data, dict) else None
    if not isinstance(stems, dict) or type(seq) is not int:
        raise PreviewLedgerUnreadable("preview ledger is malformed")
    return {"seq": seq, "stems": stems}


def _read_preview_ledger(out_dir) -> dict:
    """读方用：读不到一律当空（**读方对「无法确认」的回答就是「无预览」**，不会放出东西）。

    发布路径不许用它——见 `PreviewLedgerUnreadable`。
    """
    try:
        return _load_preview_ledger(out_dir) or {"seq": 0, "stems": {}}
    except PreviewLedgerUnreadable:
        return {"seq": 0, "stems": {}}


#: 代目录：每次发布一个**不可变**的 `.gen-<序号>/`，里头放这一版的 `<stem>.svg` 与 `<stem>.json`。
#: 账本里 stem 的记录就是**指针**——切换它（一次 `os.replace`）是发布的唯一提交点，所以读方
#: 经 `usable_preview_generation` 一次解出的这一对 SVG / manifest 永远出自同一次发布（#969 原子指针）。
#: 目录名以点开头且无扩展名，既不会被按 `.svg` / `.json` 后缀枚举的人看见，也撞不上用户 stem 的任何产物名。
GENERATION_DIR_PREFIX = ".gen-"
#: 被新版取代的代目录要**同时**满足「落后当前序号至少这么多」和「已经过了这么多秒」才清：刚解出旧路径、
#: 正要打开的读方不会撞上刚删的文件。只按序号不够——拖动编辑时发布很密，读方被调度走几十毫秒就落后好几个序号；
#: 只按时间则密集发布会堆出一串大 SVG。两者取交集：窗口是秒级，磁盘上限是 `GRACE` 个代目录起步 + 两秒内的发布量。
GENERATION_GRACE = 4
GENERATION_GRACE_SECONDS = 2.0


def preview_generation_dir(out_dir, seq: int) -> str:
    return os.path.join(os.fspath(out_dir), f"{GENERATION_DIR_PREFIX}{int(seq)}")


def _replace_with_retry(src: str, dst: str, attempts: int = 12) -> None:
    """`os.replace`，Windows 上目标正被读方短时打开时会被拒（`PermissionError`，共享冲突）：有界退避重试，
    总时长不超过约 1 秒，仍失败就把最后的错误抛出去（发布失败，不无限等）。读方（`_load_preview_ledger`）
    读完立刻关，所以冲突是瞬时的。"""
    delay = 0.005
    for attempt in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if os.name != "nt" or attempt == attempts - 1:
                raise
            time.sleep(delay)
            delay = min(delay * 1.6, 0.15)


def _write_preview_ledger(out_dir: str, ledger: dict) -> None:
    path = os.path.join(out_dir, PREVIEW_LEDGER)
    tmp = path + ".part"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(ledger, fh)
        _replace_with_retry(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def publish_preview(out_dir, stem: str, svg_part, man_part, svg_bytes: int, capabilities=()) -> int:
    """把已写完的临时 SVG / manifest 发布成 `stem` 的新一代，返回发布序号（单调递增）。

    **同代发布**：两个文件先落进这一版独有的新目录（不覆盖任何读方看得见的文件），最后一次
    账本替换把指针切过去——要么读方看到整套新版，要么整套旧版。任何一步失败：新目录撤掉，
    账本不动，上一版预览与它的完成记录原封不动。账本存在但读不出来时同样失败，**不**当空账本重建
    （#969：重建会丢掉已发布的其它图）；账本不存在才是首次发布。
    """
    out_dir = os.fspath(out_dir)
    committed = False
    gen = None
    try:
        with _ledger_lock(out_dir):  # 与摘指针 / 审计的读-改-写串行：谁也不会把对方刚写的记录盖掉
            ledger = _load_preview_ledger(out_dir) or {"seq": 0, "stems": {}}
            seq = ledger["seq"] + 1
            gen = preview_generation_dir(out_dir, seq)
            shutil.rmtree(gen, ignore_errors=True)  # 账本丢过时留下的同序号孤儿
            os.makedirs(gen)
            os.replace(os.fspath(svg_part), os.path.join(gen, f"{stem}.svg"))
            os.replace(os.fspath(man_part), os.path.join(gen, f"{stem}.json"))
            stems = dict(ledger["stems"])
            # 完成描述符：指针所在的这条记录就是这一代的完成事实，能力只按实际完成的工作声明；
            # `at` 是发布时刻（作废审计在序号下限未知时靠它判新旧）
            stems[stem] = {
                "seq": seq,
                "svg_bytes": int(svg_bytes),
                "capabilities": list(capabilities),
                "at": time.time(),
            }
            _write_preview_ledger(out_dir, {"seq": seq, "stems": stems})
            committed = True
    finally:
        if not committed and gen is not None:
            shutil.rmtree(gen, ignore_errors=True)
    # 旧的失效标记必须撤掉：撤不掉时读方仍判这个 stem 无预览，此时报发布成功就是谎话（画布 404、CLI 少计一张）。
    # 新一代已提交但对读方不可见，如实抛出让调用方走失败 / 重试（重试会再发布并再撤标记）（Codex #978）
    try:
        clear_preview_invalid(out_dir, stem)
    except OSError as exc:
        raise OSError(
            f"preview of {stem!r} was published but the stale invalid marker could not be removed: {exc}"
        ) from exc
    _prune_generations(out_dir, {"seq": seq, "stems": stems})
    return seq


def _prune_generations(out_dir: str, ledger: dict) -> None:
    referenced = {
        int(rec["seq"])
        for rec in ledger["stems"].values()
        if isinstance(rec, dict) and type(rec.get("seq")) is int
    }
    try:
        names = os.listdir(out_dir)
    except OSError:
        return
    for name in names:
        if not name.startswith(GENERATION_DIR_PREFIX):
            continue
        try:
            n = int(name[len(GENERATION_DIR_PREFIX) :])
        except ValueError:
            continue
        if n in referenced or n > ledger["seq"] - GENERATION_GRACE:
            continue
        path = os.path.join(out_dir, name)
        try:
            if time.time() - os.stat(path).st_mtime < GENERATION_GRACE_SECONDS:
                continue
        except OSError:
            continue
        shutil.rmtree(path, ignore_errors=True)


def _invalid_marker(out_dir, stem: str) -> str:
    # 无扩展名（不以 .svg / .json / .part 结尾），与账本同理不会撞用户 stem 的任何产物名
    return os.path.join(os.fspath(out_dir), f".{stem}.preview-invalid")


def preview_invalid(out_dir, stem: str) -> bool:
    """`stem` 的预览是否被标成失效（发布中途失败且还原也失败，磁盘上可能新旧错配）。"""
    return os.path.exists(_invalid_marker(out_dir, stem))


#: worker 直出位图（`figsession.do_export` → `Figure.savefig`）的像素预算——**唯一出处**。
#:
#: 判据的主语是「即将分配的那块 Agg 栅格」的像素数（宽×高，bbox_inches 生效后的真实尺寸），
#: 不是 figsize×dpi 的名义值。Agg 整幅 RGBA（4 B/px）一次分配，PNG / TIFF / JPEG 的编码器再各拷一份，
#: 峰值约 3 倍：1 亿像素 ≈ 400 MB 栅格、峰值约 1.2 GB——worker 在这个量级仍能正常收尾，再往上
#: 被系统 OOM 杀掉的风险就由「说不清的 session_dead」变成真实概率。
#: 这与 RenderCore 最终导出的预算（`rendercore/renderchild.py` 的 `DEFAULT_MAX_PIXELS` /
#: `DEFAULT_MAX_TOTAL_PIXELS`）是**两条路径、两种内存模型**：那边 PDFium 条带栅格、整页不落内存，
#: 所以上限更高；这边没有条带，互不替代。超预算一律报错，**不降 dpi、不缩尺寸、不写文件**。
EXPORT_RASTER_TOO_LARGE_CODE = "export_raster_too_large"
MAX_EXPORT_RASTER_PIXELS = 100_000_000
#: 走 Agg 整幅栅格的格式（`savefig` 的 `format`，小写）；pdf / svg / eps / ps / pgf 是矢量，不在此列。
RASTER_EXPORT_FORMATS = frozenset(
    {"png", "tif", "tiff", "jpg", "jpeg", "webp", "bmp", "gif", "raw", "rgba"}
)


class ExportRasterTooLarge(ValueError):
    """worker 直出的位图超过像素预算。`params` 是线格式里平铺进错误信封的字段（宽 / 高 / 上限）。"""

    code = EXPORT_RASTER_TOO_LARGE_CODE

    def __init__(self, width: int, height: int, max_pixels: int):
        super().__init__(
            f"位图导出 {width}×{height} 像素（{width * height} 个），超过上限 {max_pixels} 个像素；"
            "请降低导出分辨率或缩小图幅"
        )
        self.params = {"width": width, "height": height, "max_pixels": max_pixels}


def check_export_raster(fmt: str, width_px: int, height_px: int) -> None:
    """位图格式且宽×高超预算 → `ExportRasterTooLarge`；其余原样放行。必须在分配栅格**之前**调用。

    预算在调用时读模块常量（测试可调小；产品没有对外的调参入口）。
    """
    if str(fmt).lower() not in RASTER_EXPORT_FORMATS:
        return
    if int(width_px) * int(height_px) > MAX_EXPORT_RASTER_PIXELS:
        raise ExportRasterTooLarge(int(width_px), int(height_px), MAX_EXPORT_RASTER_PIXELS)


#: 失效标记都写不下去时的最后一道（`PREVIEW_POISONED_CODE`，规则 worker-protocol-and-lifecycle §预览失效四级）：
#: 写预览的是 worker / native 子进程，读的是 app 进程——磁盘标记写不下去，worker 进程内的任何
#: 内存标记读方都看不见。所以这一级由 **读方进程**自己记：app 侧收到该错误码（会话同时被判死）
#: 就把整个 `out_dir` 记进这张表，`usable_preview_path` 对它回「无预览」，直到下一代会话 build 成功。
PREVIEW_POISONED_CODE = "preview_poisoned"
#: 目录键 → (作废那一刻账本的发布序号, 作废代号)。解除时要求磁盘上每个还有预览文件的 stem 都在该序号之后重新
#: 完整发布过；代号每次作废单调递增（与账本无关），审计结束时按它 compare-and-delete（Codex #958）。
#: 第三项是作废发生的墙钟时刻：作废那一刻账本读不出来（序号下限未知，记 None）时，审计改按登记项的发布时刻
#: （`at`）判「作废之后才发布」——不能拿一个无穷大的下限，那会把账本恢复后新 worker 正常发布的记录全判成旧的摘掉。
_poisoned_dirs: dict[str, tuple[int | None, int, float]] = {}
_poison_generations = itertools.count(1)
#: 作废 / 解除的状态转换互斥：读方审计与新的 `preview_poisoned` 可能并发（Codex #958）
_poison_lock = threading.Lock()


def _dir_key(out_dir) -> str:
    return os.path.normcase(os.path.realpath(os.fspath(out_dir)))


def poison_preview_dir(out_dir) -> None:
    """读方进程：`out_dir` 下所有预览产物都不可信（见 `PREVIEW_POISONED_CODE`）。记下此刻的账本序号。"""
    key = _dir_key(out_dir)
    at = time.time()
    try:
        ledger = _load_preview_ledger(out_dir)
        seq = ledger["seq"] if ledger else 0
    except PreviewLedgerUnreadable:
        seq = None  # 下限未知：审计时按发布时刻判（见 `_poisoned_dirs`）；当 0 会让旧一代在账本恢复后被放行
    with _poison_lock:
        prev = _poisoned_dirs.get(key)
        if prev is None:
            floor, when = seq, at
        else:
            floor = None if seq is None or prev[0] is None else max(seq, prev[0])
            when = max(at, prev[2])
        _poisoned_dirs[key] = (floor, next(_poison_generations), when)


def clear_preview_poison(out_dir) -> bool:
    """新一代会话 build 成功后**尝试**解除，返回是否已解除。

    `out_dir` 跨会话代复用、build 不会清掉旧文件：新一代可能一张图都没出、或出的是另一组 stem，被作废时
    藏起来的错配文件仍在磁盘上（Codex #958）。所以只有当磁盘上**每一个**还有 `<stem>.svg` / `<stem>.json`
    的 stem 都在作废之后重新完整发布过（账本序号大于作废时的序号、字节数对得上），才解除；否则继续「无预览」。
    """
    key = _dir_key(out_dir)
    with _poison_lock:
        if key not in _poisoned_dirs:
            return True
        floor, generation, poisoned_at = _poisoned_dirs[key]
    # 读方能经指针看到的预览只有账本里登记的 stem；指针之外的文件（代目录里被取代的旧版、`.part` 临时件）
    # 本来就读不到。作废期间没被重新完整发布过的登记项要摘掉指针——新一代不再出这张图时它永远不会被覆盖，
    # 留着会让整个目录永远「无预览」（Codex #958）。摘不掉就继续作废，宁可无预览也不放出错配。
    try:
        ledger = (_load_preview_ledger(out_dir) or {"stems": {}})["stems"]
    except PreviewLedgerUnreadable:
        # 账本读不出来 = 审计不了：继续作废。当空账本处理会解除作废，账本恢复可读后被标为不可信的旧一代又被放出来（Codex #978）
        return False
    fresh = set(published_preview_stems(out_dir))
    stale = False
    for stem, rec in list(ledger.items()):
        if stem in fresh and isinstance(rec, dict):
            after = (
                int(rec.get("seq") or 0) > floor
                if floor is not None
                else float(rec.get("at") or 0.0) > poisoned_at
            )
            if after:
                continue
        # compare-and-delete：只摘审计时看到的那一条；审计期间 worker 为同一 stem 发布了新记录就别动它
        seen = rec.get("seq") if isinstance(rec, dict) else None
        if not _drop_ledger_stem(out_dir, stem, expected_seq=seen):
            stale = True
    if stale:
        return False
    with _poison_lock:
        # compare-and-delete：审计期间若又来了一次作废（代号变了——哪怕账本序号没动），这次审计没覆盖它，不得一并抹掉
        if (_poisoned_dirs.get(key) or (None, None))[1] != generation:
            return False
        del _poisoned_dirs[key]
    return True


def preview_dir_poisoned(out_dir) -> bool:
    """`out_dir` 是否仍在作废中。作废期间每次问都**重试审计**：build 成功那一刻旧产物可能正被锁着删不掉，
    之后锁放开、worker 却不会再 build——一次性的解除会让目录在这个 worker 的整个生命周期都「无预览」（Codex #958）。"""
    if _dir_key(out_dir) not in _poisoned_dirs:
        return False
    return not clear_preview_poison(out_dir)


def usable_preview_generation(out_dir, stem: str) -> tuple[int, str, str] | None:
    """`stem` 当前发布的这一代：`(序号, svg 路径, manifest 路径)`；没有可用的预览返回 None。

    要**一对**产物的读方用它：SVG 与 manifest 由账本里的同一个指针一次解出，出自同一次发布，
    中间不会插进另一次发布（#969 原子指针）。失效 / 作废 / 没发布过 / 账本读不出来 → None。
    """
    out_dir = os.fspath(out_dir)
    if preview_invalid(out_dir, stem) or preview_dir_poisoned(out_dir):
        return None
    rec = _read_preview_ledger(out_dir)["stems"].get(stem)
    if not isinstance(rec, dict) or type(rec.get("seq")) is not int:
        return None
    gen = preview_generation_dir(out_dir, rec["seq"])
    return rec["seq"], os.path.join(gen, f"{stem}.svg"), os.path.join(gen, f"{stem}.json")


def open_preview_pair(out_dir, stem: str, attempts: int = 3):
    """解出当前一代并**立刻**以二进制打开 SVG 与 manifest，返回 `(序号, svg 文件对象, manifest 文件对象)`；无预览返回 None。

    要读一对产物又会读得久的消费者用它：打开的句柄把这一代的内容钉住（POSIX 上删目录不影响已打开的句柄；
    Windows 上删不掉的目录留到下次清理），读多久都是同一代。只持有**路径**的读方没有这个保证——
    清理是有界的（序号 + 秒数双宽限），拖过宽限再去开就会 FileNotFoundError；这里在
    解出 → 打开之间撞上清理时重新解出（新指针指向的一代必在），所以拿到的永远是完整的一对（Codex #978）。
    """
    for _ in range(max(1, attempts)):
        gen = usable_preview_generation(out_dir, stem)
        if gen is None:
            return None
        seq, svg, man = gen
        opened = []
        try:
            for path in (svg, man):
                opened.append(open(path, "rb"))
        except FileNotFoundError:
            for fh in opened:
                fh.close()
            continue
        return seq, opened[0], opened[1]
    return None


def usable_preview_path(out_dir, stem: str, suffix: str) -> str:
    """读 `<stem><suffix>`（`.svg` / `.json`）的**统一入口**：没有可用预览的 stem 返回一个必不存在的路径。

    所有按路径读预览产物的消费者（`/api/engine/svg`、manifest 读取、runtimeasset 物化……）
    经它拿路径，于是「新像素配旧几何」的错配代只会被读成「无预览」。路径指向账本里那一代的
    不可变文件；要 SVG 与 manifest 成对一致，用 `usable_preview_generation` 一次解出两个。
    """
    out_dir = os.fspath(out_dir)
    gen = usable_preview_generation(out_dir, stem)
    if gen is None:
        return os.path.join(out_dir, f".{stem}{suffix}.invalid-absent")
    return (
        gen[1]
        if suffix == ".svg"
        else gen[2]
        if suffix == ".json"
        else os.path.join(os.path.dirname(gen[1]), f"{stem}{suffix}")
    )


def mark_preview_invalid(out_dir, stem: str) -> bool:
    """尽力把 `stem` 从账本里摘掉并落失效标记；返回**标记是否写成**（账本那步失败不抛）。

    标记写不下去（缓存卷满 / 权限变了）不是可以吞掉的小事：它可能是读方唯一的防线，
    调用方必须据返回值升级成 `PreviewPoisoned`（见 `PREVIEW_POISONED_CODE`）。
    """
    out_dir = os.fspath(out_dir)
    marked = True
    try:
        with open(_invalid_marker(out_dir, stem), "w", encoding="utf-8") as fh:
            fh.write("invalid\n")
    except OSError:
        marked = False
    # 摘指针是尽力而为（标记才是读方的防线），但读-改-写仍走账本锁与 compare 语义：不盖掉别人刚发布的记录
    _drop_ledger_stem(out_dir, stem)
    return marked


def _drop_ledger_stem(out_dir, stem: str, expected_seq=None, *, check: bool = False) -> bool:
    """把 `stem` 的指针从账本摘掉（读方从此看不到它）；返回是否成功。

    `check=True`（或给了 `expected_seq`）时是 compare-and-delete：账本里该 stem 当前记录的 `seq` 不等于
    `expected_seq` 就不删（别人已经为它发布了新一代），返回 True。读-改-写在账本锁里完成，与发布串行。
    """
    out_dir = os.fspath(out_dir)
    try:
        with _ledger_lock(out_dir):
            ledger = _load_preview_ledger(out_dir)
            if ledger is None or stem not in ledger["stems"]:
                return True
            if check or expected_seq is not None:
                rec = ledger["stems"][stem]
                if (rec.get("seq") if isinstance(rec, dict) else None) != expected_seq:
                    return True
            del ledger["stems"][stem]
            _write_preview_ledger(out_dir, ledger)
    except OSError:
        return False
    return True


#: 账本读-改-写的互斥（跨进程：发布方在 worker，审计 / 摘指针在 app）用**操作系统的 advisory lock**，
#: 锁常驻在产物目录里的 `.preview-ledger.lock`（文件本身从不删除，里头没有内容）：持锁进程退出 / 被杀，
#: 内核自动释放，所以没有 pid、陈旧阈值、遗留回收这类靠文件存在加启发式判活的东西（它们连着出过三轮竞态）。
#: 同一进程的多个线程先过这把进程内的锁再去拿文件锁——`flock` 对同一进程内不同 fd 的语义因平台而异，不依赖它。
_LEDGER_THREAD_LOCK = threading.Lock()


def _try_lock_file(fd: int) -> bool:
    if os.name == "nt":
        import msvcrt  # noqa: PLC0415

        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)  # 锁第 0 字节（可越过 EOF）
        except OSError:
            return False
        return True
    import fcntl  # noqa: PLC0415

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock_file(fd: int) -> None:
    try:
        if os.name == "nt":
            import msvcrt  # noqa: PLC0415

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl  # noqa: PLC0415

            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass  # 关 fd 时内核也会放


@contextlib.contextmanager
def _ledger_lock(out_dir, timeout: float = 5.0):
    """账本读-改-写互斥；等锁用非阻塞加轮询，最多 `timeout` 秒，超时抛 `OSError`（发布 / 摘指针按失败处理，不无锁硬写）。"""
    path = os.path.join(os.fspath(out_dir), PREVIEW_LEDGER + ".lock")
    deadline = time.monotonic() + timeout
    if not _LEDGER_THREAD_LOCK.acquire(timeout=timeout):
        raise OSError(f"preview ledger is locked: {path}")
    fd = None
    try:
        fd = os.open(path, os.O_CREAT | os.O_RDWR)
        while not _try_lock_file(fd):
            if time.monotonic() >= deadline:
                raise OSError(f"preview ledger is locked: {path}")
            time.sleep(0.01)
        try:
            yield
        finally:
            _unlock_file(fd)
    finally:
        if fd is not None:
            os.close(fd)
        _LEDGER_THREAD_LOCK.release()


def clear_preview_invalid(out_dir, stem: str) -> None:
    try:
        os.remove(_invalid_marker(out_dir, stem))
    except FileNotFoundError:
        pass


def published_preview_stems(out_dir) -> list:
    """本次运行**完整发布过**预览的 stem。

    账本说发布了、指针指向的那一代 `<stem>.svg` 也还在且字节数对得上才算——被截断或被
    换掉的文件不计。没有账本（引擎一张都没发布成功）就是空表，不退回去数文件。
    """
    out_dir = os.fspath(out_dir)
    done = []
    for stem, rec in _read_preview_ledger(out_dir)["stems"].items():
        if preview_invalid(out_dir, stem):
            continue
        if not isinstance(rec, dict) or type(rec.get("seq")) is not int:
            continue
        try:
            size = os.stat(
                os.path.join(preview_generation_dir(out_dir, rec["seq"]), f"{stem}.svg")
            ).st_size
        except OSError:
            continue
        if rec.get("svg_bytes") == size:
            done.append(stem)
    return done


def size_mm_of(fig) -> tuple[float, float]:
    """Figure 的物理尺寸（mm，两位小数）。

    与 manifest 的 `size_mm` 同一个公式（inches × 25.4，round 2）。描述符在
    build 阶段就要报尺寸，而 browser 侧那时还没建 manifest——两边都从 Figure
    直接算，公式只有这一份，worker/browser 的描述符才比得齐。

    脚本按 `bbox_inches` 存盘的图报的是**图幅（frame，ADR 0098）**的尺寸：figsize 加上
    挂在 Figure 上的四边外伸（`FRAME_ATTR`，由 `pathgeom.set_frame` 写）。
    """
    w_in, h_in = (float(v) for v in fig.get_size_inches())
    attrs = getattr(fig, "__dict__", {})
    out = attrs.get(FRAME_ATTR)
    if out is not None and not attrs.get("_mm_frame_suppressed"):
        left, bottom, right, top = out
        w_in, h_in = w_in + right - left, h_in + top - bottom
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


#: 资产 id 里运行配置段的分隔符（T03）：`runtime:<script>#<stem>~<rc_…>`。只有非空 argv 的运行才有这一段，
#: 空 argv 的 id 与 T03 之前**逐字节相同**（保存的文档、override、画布引用全部照旧）。
RUN_CONFIG_SEP = "~"


def runtime_asset_id(script: str, stem: str, run_config: str = "") -> str:
    """捕获 Figure 的稳定身份：`runtime:<script 相对路径>#<stem>`（ADR 0013 §2）。

    只由 (脚本相对路径, stem) 决定——项目那一维由「id 存在哪个项目的文档里」
    承担。**刻意不含** PID、临时目录、绝对路径、会话 id、时间戳、entry：

    * 前五者会让重跑 / 重开 / 换机器后 override 挂错身份；
    * entry 不进 id 是 ADR 0013 决策 2：注册表里一个脚本只有一个 entry，
      (script, stem) 已唯一；把 entry 编进去，用户改脚本换入口重新探测后，
      同一张图会变成一个新身份，历史 override 全部变成孤儿。

    id 是**不透明标识**：消费方不得从中反解 script/stem（脚本名里可以有
    `#`），要用就取描述符里那两个独立字段——这就是 stem 冲突的显式处理。

    **运行配置维度（T03）**：同一脚本用不同 argv 跑出同名图是两张不同的图。`run_config` 是本机运行配置的
    不透明引用（`engine/runconfig.py`），非空时拼在 id 末尾；它**不是 argv 的摘要**——参数值不进身份。
    空串 = 没给参数，id 与旧版一致。
    """
    script = normalize_relative_script(script)
    if not isinstance(stem, str) or not stem:
        raise ValueError("stem 必须是非空字符串")
    if not isinstance(run_config, str):
        raise ValueError("run_config 必须是字符串")
    base = f"runtime:{script}#{stem}"
    return f"{base}{RUN_CONFIG_SEP}{run_config}" if run_config else base


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


def project_relative_stem(stem: object) -> bool:
    """注册表里的 stem 能不能当「项目内相对名」拼进路径：只认字符串，拒绝绝对（POSIX / Windows 两种写法都拒，
    与当前操作系统无关）、盘符、UNC / 以分隔符开头、含 NUL、任一分量是 `..`。子目录 `sub/plot` 合法。
    这一关在 `os.path.join` **之前**——拼出来再判太晚：Windows 上 `lstat` 一个 UNC 路径就已经同步连出去了。
    判据唯一出处；`find_original_artifact` 与导入即扫描、素材库脚本清单都走它（#820 r4232425162）。"""
    if not isinstance(stem, str) or not stem or "\0" in stem:
        return False
    if posixpath.isabs(stem) or ntpath.isabs(stem) or stem[0] in "/\\":
        return False
    if ntpath.splitdrive(stem)[0]:
        return False
    return ".." not in stem.replace("\\", "/").split("/")


#: 默认的磁盘探测谓词（import 时的那个函数对象）：worker 里用户脚本沙箱可能替换 `os.path.isfile`，
#: 判「是不是真磁盘」不能拿模块属性比（那会让沙箱里恒走合成路径探测，丢掉真实大小写）
_DISK_ISFILE = os.path.isfile
#: 同理的真 `os.scandir`：worker 里 `install_probe_observer` 会把 `os.scandir` / `os.listdir` 换成记账版（脚本读了哪些路径的诊断）。
#: Tavotto 自己的列目录（`find_artifacts`）不是用户脚本的探测，不能被记进缺失输入诊断 / rasterhint——直接用 import 时的真函数
_DISK_SCANDIR = os.scandir


def find_artifacts(project_root: str, stem: str, *, isfile=_DISK_ISFILE, formats=None) -> list[str]:
    """项目根下 stem 的**所有**产物（相对路径，POSIX，按 `ARTIFACT_EXTS` 顺序）；没有回 `[]`。

    `formats`（这张图自己 savefig 过的格式）给了就只认这些扩展名，且**每个存在的都收**：同一张图存了
    `panel.pdf` 与 `panel.svg` 时两个文件都是它的（#1251：打开哪个都该是这张图）。不给（不知道它存过什么）
    = 旧判据，只回第一个存在的——不知道格式时不能把同 stem 下别的图的文件算进来。
    """
    if not project_relative_stem(stem):
        return []  # 先于任何拼接：UNC / 绝对 / 盘符 / `..` 的 stem 一个字节都不探
    allowed = None if formats is None else {_norm_format(f) for f in formats if f}
    out: list[str] = []
    if isfile is _DISK_ISFILE:
        # 真磁盘：列目录取**真实文件名**（含扩展名大小写，`panel.PDF`）——合成 `stem + ".pdf"` 去探测，在 Windows / macOS
        # 上记下的是小写的合成路径，在区分大小写的文件系统上什么都找不到；而素材盘点的 id 是真实文件名，
        # 注册表 `artifacts` 的键必须与它逐字相同（#1251）
        by_ext = _real_files_with_stem(project_root, stem)
        for ext in ARTIFACT_EXTS:
            if allowed is not None and _norm_format(ext[1:]) not in allowed:
                continue
            for name in by_ext.get(ext, ()):
                out.append(name)
                if allowed is None:
                    return out
        return out
    for ext in ARTIFACT_EXTS:  # 注入的 isfile（测试 / handoff 的 dry 场景）：合成路径探测，保持原样
        if allowed is not None and _norm_format(ext[1:]) not in allowed:
            continue
        if isfile(os.path.join(project_root, stem + ext)):
            out.append(stem + ext)
            if allowed is None:
                break
    return out


def _real_files_with_stem(project_root: str, stem: str) -> dict[str, list[str]]:
    """`{小写扩展名: [项目相对真实路径（POSIX）]}`：`stem` 所在目录里，文件名主干 == stem 的普通文件。
    扩展名大小写不敏感（`.PDF` 归 `.pdf`），主干按原样比；读不了目录 = 空。"""
    rel_dir, _, base = stem.replace("\\", "/").rpartition("/")
    try:
        entries = list(
            _DISK_SCANDIR(os.path.join(project_root, rel_dir) if rel_dir else project_root)
        )
    except OSError:
        return {}
    out: dict[str, list[str]] = {}
    for entry in sorted(entries, key=lambda e: e.name):
        root_name, ext = os.path.splitext(entry.name)
        if root_name != base or not ext:
            continue
        try:
            if not entry.is_file():
                continue
        except OSError:
            continue
        out.setdefault(ext.lower(), []).append(f"{rel_dir}/{entry.name}" if rel_dir else entry.name)
    return out


def find_original_artifact(
    project_root: str, stem: str, *, isfile=_DISK_ISFILE, formats=None
) -> str | None:
    """项目根下 stem 的原始产物（相对路径，POSIX）；没有回 None。

    判据与 handoff 交接找产物是同一份（它现在就调这里）：只看项目根一层、
    按 `ARTIFACT_EXTS` 的顺序取第一个存在的。`isfile` 可注入是给测试与
    handoff 的 dry 场景用的。

    `formats`（这张图自己 savefig 过的格式，取自它的调用表）给了就只认这些扩展名：同一 stem 下别的图写的
    文件不是它的原件——`panel.png` 甲 / `panel.pdf` 乙时，甲的原件若按扩展名顺序取成 `panel.pdf`，写回就会
    拿甲去覆盖乙的产物。不给（不知道它存过什么）= 旧判据。「全部产物」是 `find_artifacts`，这里取它的首个。
    """
    found = find_artifacts(project_root, stem, isfile=isfile, formats=formats)
    return found[0] if found else None


def saved_formats(calls) -> list[str] | None:
    """调用表（`savefig_calls[stem]`）里出现过的格式；调用表是 None（没观察到）或空回 None = 不知道。"""
    if not calls:
        return None
    out = [c.get("format") for c in calls if isinstance(c, dict) and c.get("format")]
    return out or None


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
    #: 认领这个 stem 的 savefig 调用（`savefig_call()` 的形态，按调用顺序，最多
    #: `MAX_SAVEFIG_CALLS` 次，另加超出后新出现的格式各一次）。三档，「不知道」是独立一档：
    #: `None` = 没观察到（`paper_style.save` 捷径整个被替换、看不见参数；旧 payload
    #: 没有这个键也是这一档）；`()` = 确实没有 savefig（pyplot 捕获）；非空 = 记下的调用。
    savefig_calls: tuple | None = None
    #: 产出这张图的运行配置引用（T03；本机不透明 id，不是 argv）。空 = 没给参数——此时 payload 里**没有**
    #: 这个键，旧描述符与对拍用例一个字节不变。
    run_config: str = ""
    #: 这张图写过的**所有**磁盘文件（项目相对路径，POSIX；#1251）：按它自己存过的格式绑定，含 `original_artifact`。
    #: `original_artifact` 语义不变（首选原件、写回目标）；这里是「打开其中任何一个文件都是这张图」的证据，
    #: 由 `probe.descriptor_artifacts` 登记进注册表。空 = 没有（pyplot 捕获 / playground / 不知道）——此时 payload 里
    #: **没有**这个键，旧描述符与对拍用例一个字节不变。
    artifacts: tuple[str, ...] = ()

    def to_payload(self) -> dict:
        out = dataclasses.asdict(self)
        if not self.run_config:
            out.pop("run_config")
        if self.artifacts:
            out["artifacts"] = list(self.artifacts)
        else:
            out.pop("artifacts")
        out["size_mm"] = [float(v) for v in self.size_mm]
        out["savefig_calls"] = (
            None if self.savefig_calls is None else [dict(c) for c in self.savefig_calls]
        )
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
    savefig_calls=None,
    run_config: str = "",
    artifacts=(),
) -> CapturedFigureDescriptor:
    """描述符工厂——**writeback 能力只能派生，不能指定**。

    * `capture_source == "pyplot"` 的图从没存过盘：`original_artifact` 必须是
      None（给了就抛，不是悄悄清掉——上游把来源标错时要当场炸出来，而不是
      让一个「看起来能写回」的描述符流出去）；
    * `can_writeback_artifact` = savefig 来源 **且** 原件真实在磁盘上。
      磁盘上碰巧躺着同名文件而来源是 pyplot 时它必须是 False——那份文件
      不是这张图写的，往上写回就是覆盖一个不相干的文件；
    * `can_writeback_source`（改写用户脚本）v1 一律 False；
    * `savefig_calls`：None（没观察到）/ 调用列表；pyplot 捕获从没 savefig 过，
      给了非空列表就抛（同一条「来源标错当场炸」）。
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
    if artifacts is None:
        artifacts = ()
    if not isinstance(artifacts, (list, tuple)) or not all(
        isinstance(a, str) and a for a in artifacts
    ):
        raise ValueError(f"artifacts 必须是非空字符串列表: {artifacts!r}")
    artifacts = tuple(dict.fromkeys(a.replace("\\", "/") for a in artifacts))
    if capture_source == SOURCE_PYPLOT and artifacts:
        raise ValueError(f"pyplot 捕获的 Figure 没有产物文件，artifacts 必须为空: {artifacts!r}")
    if savefig_calls is not None:
        if not isinstance(savefig_calls, (list, tuple)) or not all(
            isinstance(c, dict) for c in savefig_calls
        ):
            raise ValueError(f"savefig_calls 必须是对象列表或 None: {savefig_calls!r}")
        if capture_source == SOURCE_PYPLOT and savefig_calls:
            raise ValueError("pyplot 捕获的 Figure 没有 savefig 调用，savefig_calls 必须为空")
        savefig_calls = tuple(dict(c) for c in savefig_calls)
    return CapturedFigureDescriptor(
        asset_id=runtime_asset_id(script, stem, run_config),
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
        savefig_calls=savefig_calls,
        run_config=run_config,
        artifacts=artifacts,
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
        savefig_calls=data.get("savefig_calls"),
        run_config=data.get("run_config") or "",
        artifacts=data.get("artifacts") or (),
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
SELECTED_ARTIFACT_POLICY = "selected-artifact-v1"
SELECTED_FIGSIZE_POLICY = "selected-figsize-v1"
#: 来源驱动采用（ADR 0139）：编辑档安全条件 + 准入结论（exact / needs_confirmation），与上面两条永不共享 worker / 缓存 / witness。
SCRIPT_BACKED_POLICY = "script-backed-figure-v1"
SELECTED_ARTIFACT_POLICIES = (
    SELECTED_ARTIFACT_POLICY,
    SELECTED_FIGSIZE_POLICY,
    SCRIPT_BACKED_POLICY,
)
#: 要求「整图 figsize 图幅」的两条策略：编辑请求必须带 `figure.frame == "figsize"`，且只收 PNG。
FIGSIZE_FRAME_POLICIES = (SELECTED_FIGSIZE_POLICY, SCRIPT_BACKED_POLICY)
ARTIFACT_VALIDATION_MAX_BYTES = 32 * 1024 * 1024
ARTIFACT_VALIDATION_MAX_PIXELS = 16_000_000


class ArtifactContextError(ValueError):
    code = "artifact_source_unavailable"

    def __init__(self, reason: str, message: str, *, retryable: bool | None = None, owner=None):
        super().__init__(message)
        self.reason = reason
        self.retryable = reason == "source_changed" if retryable is None else retryable
        self.params = {"reason": reason}
        if owner is not None:
            self.params.update(
                {key: owner[key] for key in ("file_id", "bytes_sha256", "size_bytes")}
            )


def artifact_request(policy, expected_source=None, patches=()):
    """Validate the opt-in and durable byte basis; execution tokens are never accepted."""
    if policy is None and expected_source is None:
        return None
    if policy not in SELECTED_ARTIFACT_POLICIES:
        raise ArtifactContextError("unsupported_render_state", "Unsupported source policy.")
    if policy in FIGSIZE_FRAME_POLICIES and patches:
        frame = next(
            (
                p["value"]
                for p in patchspec.canonicalize(patches)
                if (p["gid"], p["prop"]) == ("figure", "frame")
            ),
            None,
        )
        if frame != "figsize":
            raise ArtifactContextError(
                "unsupported_render_state", "Selected figsize edits require the figsize frame."
            )
    if expected_source is None:
        if patches:
            raise ArtifactContextError(
                "source_changed",
                "Edited sources require their saved byte identity.",
                retryable=False,
            )
        return None
    if (
        not isinstance(expected_source, dict)
        or set(expected_source) != {"bytes_sha256", "size_bytes"}
        or not isinstance(expected_source["bytes_sha256"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", expected_source["bytes_sha256"])
        or type(expected_source["size_bytes"]) is not int
        or expected_source["size_bytes"] <= 0
    ):
        raise ArtifactContextError("source_changed", "Invalid source basis.", retryable=False)
    return dict(expected_source)


def selected_artifact_context(value):
    """Validate the controller-owned static source identity; None preserves legacy rendering."""
    if value is None:
        return None
    if not isinstance(value, dict) or value.get("render_policy") not in SELECTED_ARTIFACT_POLICIES:
        raise ValueError("unsupported artifact render policy")
    source_id = normalize_relative_script(value.get("source_id"))
    if ".." in pathlib.PurePosixPath(source_id).parts:
        raise ValueError("artifact source must remain inside its project")
    artifact = SourceArtifact(
        source_id=pathlib.PurePosixPath(source_id).as_posix(),
        origin=ORIGIN_STATIC,
        kind=value.get("kind"),
        bytes_sha256=value.get("bytes_sha256"),
        size_bytes=value.get("size_bytes"),
    )
    if artifact.kind not in ("pdf", "png"):
        raise ValueError("selected artifact rendering supports PDF and PNG")
    policy = value["render_policy"]
    if policy in FIGSIZE_FRAME_POLICIES and artifact.kind != "png":
        raise ArtifactContextError(
            "unsupported_render_state", "Selected figsize requires a PNG source."
        )
    return {**artifact.to_payload(), "render_policy": policy}


def selected_artifact_key(value) -> str:
    """A rendering context includes the actual bytes, unlike SourceArtifact's semantic ID."""
    context = selected_artifact_context(value)
    if context is None:
        return ""
    return hashlib.sha256(
        json.dumps(context, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class FileHashBudgetExceeded(ValueError):
    """The byte budget was exceeded while reading, including a growing source."""


def hash_file(path, *, max_bytes: int | None = None) -> tuple[str, int]:
    """SHA256 + byte count; an optional budget stops after at most one excess chunk."""
    h = hashlib.sha256()
    size = 0
    with open(os.fspath(path), "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            size += len(chunk)
            if max_bytes is not None and size > max_bytes:
                raise FileHashBudgetExceeded("File exceeds the hashing byte budget.")
            h.update(chunk)
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


def savefig_targets_path(fname) -> bool:
    """`savefig(fname)` 写的是不是一个**路径**（而不是 BytesIO / 脚本自己打开的文件对象）。

    safe worker 与浏览器的拦截只吞写到路径的那些（那才是「一份图产物」，沙盒纪律管的是它）；
    写进文件对象的原样透传——`savefig(buf, format="png"); Image.open(buf)` 是转 TIFF 的
    常见写法，吞掉它 buf 就是空的，脚本在 PIL 那一行崩（2026-09-29 用户实报
    `cannot identify image file <_io.BytesIO ...>`）。脚本自己 `open(path, "wb")` 出来的
    句柄也一样：文件在 open 那一刻已经建好（截断），吞掉 savefig 只会留下一个 0 字节的文件。

    `bytes` 也是路径（`savefig(b"out.pdf")` 照样落盘，#739 Codex P2）：漏掉它会把一次真实写盘放出沙盒。
    取名 / 推格式一律经 `os.fsdecode`，str / bytes / PathLike 同一条路。
    """
    return isinstance(fname, (str, bytes, os.PathLike))


def savefig_stem(fname) -> str:
    """`savefig(fname)` 的第一个参数 → stem；不是路径（缓冲区）时返回空串。

    worker 与 browser 以前各写各的（一个用 `Path(...).stem`，一个用
    `os.path.splitext(os.path.basename(...))`）。两者在 `a.tar.gz` 这类
    多后缀上一致，在 `Path("out")/"f.pdf"` 上也一致——但「一致」这件事没有
    任何东西看着它。
    """
    if not savefig_targets_path(fname):
        return ""  # BytesIO / 文件对象：不是一份产物
    return os.path.splitext(os.path.basename(os.fsdecode(fname)))[0]


#: 「先写临时文件再改名」的临时后缀（#1124）
TEMP_SAVE_SUFFIXES = (".tmp", ".temp", ".part")


def temp_save_target(fname) -> tuple[str, str] | None:
    """`savefig("final.png.tmp")` 这种「先写临时文件、再改名成产物」的名字 → `(最终文件名, 最终 stem)`，
    即 `("final.png", "final")`；不是这种形状回 None。

    **只是形状判断，不是事实**：脚本可能根本没改名、而磁盘上躺着一份旧的 `final.png`。拿它改 stem 之前，
    调用方必须确认这次执行真的把这个临时文件改名成了那个最终文件、内容就是这次写的（#1124，
    Codex #1126 P1）——否则会把这张图关联到一份它没产出的原件上，编辑写回就覆盖了别人的文件。
    """
    if not savefig_targets_path(fname):
        return None
    root, ext = os.path.splitext(os.path.basename(os.fsdecode(fname)))
    if ext.lower() not in TEMP_SAVE_SUFFIXES:
        return None
    inner, inner_ext = os.path.splitext(root)
    if not inner or inner_ext.lower() not in ARTIFACT_EXTS:
        return None
    return root, inner


def savefig_effective_names(fname, kwargs: dict, canvas=None) -> list:
    """`savefig(fname, **kwargs)` 真正会写（被拦截时就是没写）的文件名，供 `BlockedFileOps` 记账（P01a）。

    `print_figure` 的规则：显式 `format=` 时文件名**原样**（不补后缀）；否则 `str` / `PathLike`
    取文件名后缀作格式，**没有后缀**就补 `canvas.get_default_filetype()`（`stage` → `stage.png`，`stage.` → `stage.png`）。
    缺省格式由该 Figure 当前的 canvas 决定（自定义 canvas 可以重载，不一定等于 `rcParams["savefig.format"]`），
    所以无后缀且无显式 `format=` 时只记**这一个**确定的名字：`fname.rstrip('.') + '.' + 该 canvas 的缺省格式`，
    与 matplotlib 自己的解析一致；拿不到 canvas / 调用抛错 / 返回的不是非空字符串，就一个候选都不记
    （证明不了就不归因，不推测 png，也不单信 rcParams）。显式 `format=` / 有后缀 / `bytes` 只记字面名。
    """
    if not savefig_targets_path(fname):
        return []
    if kwargs.get("format") is not None or isinstance(fname, bytes):
        return [fname]
    name = os.fsdecode(fname)
    if os.path.splitext(name)[1][1:]:
        return [fname]
    # 没有后缀也没有显式 format：matplotlib 写的是补了后缀的名字，字面名不会被写出——不记，
    # 否则同名的无关旧文件被读时会被误判成回读被拦下的 savefig（#952 Codex P2）
    try:
        fmt = canvas.get_default_filetype()
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(fmt, str) or not fmt:
        return []
    return [name.rstrip(".") + "." + fmt]


def savefig_call(fname, kwargs: dict) -> dict:
    """一次 `fig.savefig(fname, **kwargs)` 里决定产物长相的参数 → JSON 对象。

    拦截只取 stem 的年代（2026-09 之前）这些参数一个都没记，`bbox_inches="tight"`
    的脚本于是在 Tavotto 里按 figsize 出图、紧贴图幅的轴标题被切掉半截（审计
    T14 / T33）。这里只**记**，不改任何渲染——怎么用它是 tight 图幅那份 ADR 的事。

    取值是**这一次调用的实效值**，按 matplotlib 自己的解析顺序：显式参数优先，
    缺省（或显式 None）取调用那一刻的 `rcParams["savefig.*"]`——`rc_context` 里存的
    图、`savefig.bbox: tight` 写在 matplotlibrc 里的图，参数表上一个字都没有，
    产物照样是 tight 的。两个「推迟到图上」的取值原样保留、不在这里代求：
    `dpi == "figure"`（图的 dpi 之后还能变）与底色 `"auto"`（取图自己的底色）。

    * `format`：显式 `format=` > 文件名后缀 > `rcParams["savefig.format"]`，小写；
    * `bbox_inches`：`"tight"` / `None`（按 figsize）/ 显式 Bbox 的 `[x0, y0, x1, y1]`
      （英寸，figure 左下为原点）；认不出的形态记成 `str`，不猜；
    * `pad_inches`：数值或 `"layout"`（matplotlib 3.8+）；
    * `dpi`：数值或 `"figure"`；`transparent`：布尔；
    * `facecolor` / `edgecolor`：`#rrggbbaa`、`"auto"` 或 `"none"`；认不出的记 `str`。
      `transparent` 为真而这个键没给时是 `"none"`（matplotlib 自己的 `setdefault`）；
    * `bbox_extra_artists`：给了几个（对象本身进不了 JSON）；没给是 None。

    纯标准库：matplotlib 此刻必然已载入（调用方手里就有一张 Figure），按
    `sys.modules` 取，不替调用方 import。
    """
    mpl = sys.modules.get("matplotlib")
    rc = getattr(mpl, "rcParams", None) or {}
    colors = sys.modules.get("matplotlib.colors")

    def _pick(key: str, rc_key: str):
        value = kwargs.get(key)
        return rc.get(rc_key) if value is None else value

    def _num(value):
        if value is None or isinstance(value, str):
            return value
        try:
            return float(value)
        except (TypeError, ValueError):
            return str(value)

    def _color(value):
        if value is None or (isinstance(value, str) and value in ("auto", "none")):
            return value
        try:
            return colors.to_hex(value, keep_alpha=True)
        except Exception:  # noqa: BLE001 - 认不出的颜色原样记成字符串，记录不许挡住捕获
            return str(value)

    fmt = kwargs.get("format")
    if fmt is None and savefig_targets_path(fname):
        fmt = os.path.splitext(os.fsdecode(fname))[1][1:] or None
    if fmt is None:
        fmt = rc.get("savefig.format")

    bbox = _pick("bbox_inches", "savefig.bbox")
    if bbox is not None and not isinstance(bbox, str):
        try:
            bbox = [round(float(v), 6) for v in bbox.extents]
        except (AttributeError, TypeError, ValueError):
            bbox = str(bbox)
    extra = kwargs.get("bbox_extra_artists")
    try:
        extra_count = None if extra is None else len(extra)
    except TypeError:
        extra_count = None
    transparent = bool(_pick("transparent", "savefig.transparent"))

    def _background(key: str):
        # `Figure.savefig` 在 transparent 时 `kwargs.setdefault(key, "none")`（3.8 与 3.11 同一句）：
        # 只有**没给这个键**时才换成 "none"；显式给了（哪怕是 None）照旧按给的值 / rcParams 走
        if transparent and key not in kwargs:
            return "none"
        return _color(_pick(key, f"savefig.{key}"))

    return {
        "format": str(fmt).lower() if fmt else None,
        "bbox_inches": bbox,
        "pad_inches": _num(_pick("pad_inches", "savefig.pad_inches")),
        "dpi": _num(_pick("dpi", "savefig.dpi")),
        "transparent": transparent,
        "facecolor": _background("facecolor"),
        "edgecolor": _background("edgecolor"),
        "bbox_extra_artists": extra_count,
    }


def record_savefig_call(calls: dict, capture: dict, stem: str, fig, call: dict | None) -> bool:
    """把一次 savefig 调用记到 `calls[stem]` 名下——三条入口（safe worker、native bridge、
    浏览器 playground）的同一条记账规则。

    * 只记**认领了这个 stem 的那张图**的调用：同名 stem 已被另一张图认领时，这次
      调用的产物在磁盘上会覆盖前者，但捕获表里那张不是它，参数也就不是它的；
    * `call is None` = 这次存盘发生了、参数没观察到（`paper_style.save` 捷径）：整份
      变成「不知道」并保持下去——只记到一部分的列表会被当成全貌；
    * 最多 `MAX_SAVEFIG_CALLS` 次，多的丢掉；但一种还没记过的格式的第一次照记（总数
      不超过 `MAX_SAVEFIG_CALLS_HARD`）——只追加不替换，调用方按下标对齐的
      `bbox_extra_artists` 才不会错位。

    返回这次调用是否被记下（调用方据此把同一次调用里进不了 JSON 的
    `bbox_extra_artists` 对象按同一个下标另存）。
    """
    if not stem or capture.get(stem) is not fig:
        return False
    if call is None:
        calls[stem] = None
        return False
    seq = calls.setdefault(stem, [])
    if seq is None:
        return False
    if len(seq) >= MAX_SAVEFIG_CALLS:
        fmt = _norm_format(call.get("format"))
        if len(seq) >= MAX_SAVEFIG_CALLS_HARD or any(
            _norm_format(c.get("format")) == fmt for c in seq
        ):
            return False
    seq.append(call)
    return True


_CASE_FOLD_CACHE: dict = {}


#: 模块加载时的 `os.listdir`：大小写探测是 Tavotto 自己的簿记，不能经保真度账本的 `watched_listdir`
#: （否则自己的探测会把图标成「脚本列过受影响目录」= suspect）
_UNWATCHED_LISTDIR = os.listdir


def _dir_folds_case(directory: str) -> bool:
    """这个目录所在的文件系统是否不区分大小写：目录存在时探测——翻转路径里最后一个有大小写的字母所在的那一级名字，
    父目录的 `os.listdir` 里**字面存在**这个变体名 → 它是独立条目（符号链接别名或另一个目录）→ 区分大小写；
    不存在，而翻转后的路径存在且 `samefile` → 不区分。探测不了（目录不存在 / 路径里没有大小写字母 / 出错）按平台默认
    （darwin / win32 不区分，其余区分；Pyodide 虚拟 FS 区分）。结果按目录缓存。"""
    if directory in _CASE_FOLD_CACHE:
        return _CASE_FOLD_CACHE[directory]
    result = sys.platform in ("darwin", "win32")
    try:
        if os.path.isdir(directory):
            parts = os.path.normpath(directory).split(os.sep)
            for k in range(len(parts) - 1, -1, -1):
                name = parts[k]
                if name.lower() == name.upper():
                    continue
                flipped_name = name.swapcase()
                parent = os.sep.join(parts[:k]) or os.sep
                if flipped_name in _UNWATCHED_LISTDIR(parent):
                    result = False
                else:
                    flipped = os.sep.join(parts[:k] + [flipped_name] + parts[k + 1 :]) or os.sep
                    result = os.path.exists(flipped) and os.path.samefile(flipped, directory)
                break
    except OSError:
        pass
    _CASE_FOLD_CACHE[directory] = result
    return result


_UNICODE_FOLD_CACHE: dict = {}

#: 探测 NFC / NFD 时最多扫目录里多少个条目找证据（只看非 ASCII 的名字）
_UNICODE_PROBE_SCAN = 200


def _unicode_evidence(directory: str, name: str, listing=None) -> bool | None:
    """用目录里一个**真实存在**的名字当证据：它的另一种归一形式（NFC <-> NFD）是不是**同一个名字**。
    True = 是；False = 不是（另一种写法不存在，或是独立的目录项）；None = 没有证据（名字是纯 ASCII、两种形式相同、
    或这个名字本身在盘上不存在）。

    `samefile` 只能证明同一个 inode——硬链接 / 符号链接别名也满足，区分归一化的文件系统上 NFC / NFD 两个
    名字互为硬链接时同样为真，那不是「文件系统把两种写法当同一个名字」。所以要同时满足：另一种写法能 `lstat` 到
    同一个目录项、**且目录列表里只出现一种写法**（归一化的文件系统一个文件只有一个目录项；
    两种写法都作为独立目录项出现 = 别名，不归一）；比较用 `lstat`（目录项本身，不跟随符号链接）。不在用户目录里写探针文件。"""
    nfc, nfd = (unicodedata.normalize(f, name) for f in ("NFC", "NFD"))
    if nfc == nfd:
        return None
    here = os.path.join(directory, name)
    if not os.path.lexists(here):
        return None
    other_name = nfd if name != nfd else nfc
    other = os.path.join(directory, other_name)
    if not os.path.lexists(other):
        return False
    listing = set(_UNWATCHED_LISTDIR(directory)) if listing is None else listing
    if (nfc in listing) and (nfd in listing):
        return False  # 两种写法都是独立目录项：别名，不是归一化
    # 比**目录项本身**（lstat，不跟随链接）：归一卷上一个名字可分解的单个符号链接，两种写法 lstat 到同一项，
    # 是有效证据；两个独立的链接 / 硬链接是两个目录项，已被上面「列出两种写法」排除，lstat 也不同
    return os.path.samestat(os.lstat(here), os.lstat(other))


def _dir_folds_unicode(directory: str, base: str | None = None) -> bool:
    """这个目录所在的文件系统是否把 Unicode 的 NFC / NFD 写法当同一个文件名——**只在能证明时才算是**。

    APFS 比较时不区分 NFC / NFD（HFS+ 直接存成 NFD），NTFS、ext4、exFAT、多数网络卷按码点比较。误并 =
    把两个不同的文件当成一个（编辑到另一张图），比漏并更坏，所以不按平台猜：先拿 `base`（这次要写的名字，
    它已经在盘上时）当证据，再扫目录里的非 ASCII 条目；探测不了（目录不存在 / 没有这样的条目 / 出错）按
    「不归一」。native 保存成功后另有 samefile 对账兜底（`_canonical_target`）。结论按目录缓存（只缓存有证据的）。"""
    if directory in _UNICODE_FOLD_CACHE:
        return _UNICODE_FOLD_CACHE[directory]
    try:
        if not os.path.isdir(directory):
            return False
        verdict = _unicode_evidence(directory, base) if base else None
        if verdict is None:
            seen = 0
            listing = set(_UNWATCHED_LISTDIR(directory))
            for entry in listing:
                if entry.isascii():
                    continue
                verdict = _unicode_evidence(directory, entry, listing)
                seen += 1
                if verdict is not None or seen >= _UNICODE_PROBE_SCAN:
                    break
    except OSError:
        return False
    if verdict is None:
        return False
    _UNICODE_FOLD_CACHE[directory] = verdict
    return verdict


def savefig_target_key(fname, kwargs: dict, canvas=None) -> str | None:
    """`savefig(fname)` 真正写的那个**磁盘文件**的可比较身份；写不出路径（缓冲区 / 认不出文件名）时 None。

    「同一路径多个写入者」（`note_savefig_writer`）的键。文件名经 `savefig_effective_names`（显式 `format=`
    不改文件名、无后缀补 canvas 缺省格式——与 matplotlib 一致），所以 `savefig("a.pdf")` 与
    `savefig("a.pdf", format="png")` 是同一个文件，`a.pdf` 与 `a.svg` 不是。

    * 三条入口同一个**路径**键（不用 inode：脚本删掉再重建同一路径时 inode 会变，而磁盘上那个路径留下的仍是
      最后写入者——Codex #1141 r4305717514）：路径取 `realpath`（符号链接解到目标），文件名是否折叠大小写按**该目录所在文件系统**
      判（`_dir_folds_case`），不是一刀切的 `normcase`——macOS 默认卷上 `Plot.pdf` / `plot.pdf` 是同一个文件，
      区分大小写的卷（Linux、Windows 的区分目录）上是两个。
    * 目录里能**证明** NFC / NFD 是同一个名字（`_dir_folds_unicode`：拿真实存在的名字试另一种写法）时先归一成 NFC 再比；证明不了不归一。
    * 硬链接别名（两个路径同一个 inode）键里认不出，由 native 保存成功后的 `_canonical_target` 用 samefile 对账。
    """
    names = savefig_effective_names(fname, kwargs, canvas)
    if len(names) != 1:
        return None
    try:
        path = os.path.abspath(os.fsdecode(names[0]))
        directory, base = os.path.split(os.path.realpath(path))
        if _dir_folds_unicode(directory, base):
            directory, base = (unicodedata.normalize("NFC", part) for part in (directory, base))
        if _dir_folds_case(directory):
            # 只折叠大小写（`lower`），不做 `casefold` 的全 Unicode 折叠：NTFS / APFS 的不区分大小写比较不把
            # `ß` 当 `ss`（Codex #1141 r4305745044）；NTFS 的大写表等更细的排序规则不模拟（见细则「已知未覆盖」，#1150）
            base = base.lower()
        return os.path.join(directory, base)
    except Exception:  # noqa: BLE001 —— 簿记尽力而为：认不出路径就不参与顶替，绝不改变用户 savefig 的结果
        return None


def savefig_verify_render(real_savefig, fig, fname, args, kwargs: dict) -> bool:
    """用**未打补丁**的 `Figure.savefig` 把这次保存同一组参数不落盘地真渲染进 `io.BytesIO()`：成功 = 原生下这次保存
    不会抛。safe worker / 浏览器不真写盘，拿不到成败，又没法穷举所有会让 savefig 抛的参数（格式 × backend ×
    dpi × …），所以只在**这次写入会改变捕获**（`note_savefig_writer` 要顶替）的那一刻交给 matplotlib 自己判。
    格式取显式 `format=`，否则取 `savefig_effective_names` 解出的后缀；解不出格式按成功算。Figure 的状态
    （dpi / 尺寸 / 布局）matplotlib 的 savefig 自己会还原。抛任何异常 = 失败。"""
    try:
        kw = dict(kwargs)
        if kw.get("format") is None:
            names = savefig_effective_names(fname, kwargs, getattr(fig, "canvas", None))
            ext = os.path.splitext(os.fsdecode(names[0]))[1][1:].lower() if names else ""
            if not ext:
                return True
            kw["format"] = ext
        # tight 量包围盒失败在原生下同样会抛、文件不会写出：与 native 同源判失败（Codex #1141 r4306180020）。
        # 这里只决定**顶替**，持有者自己的保存不经验证，ADR 0098 的 frame_unavailable 报告不受影响
        # 验证渲染是我们的动作、不是脚本改图（它发生在**另一张图**保存时）：用户可见的 `fig.stale` 与「存后被改过」的
        # 私有标记都原样还回去。先还 stale（置 True 会经 `_StaleRecorder` 弄脏标记），再还标记
        stale = getattr(fig, "stale", None)
        dirty = getattr(fig, DIRTY_SINCE_SAVE, None)
        try:
            real_savefig(fig, io.BytesIO(), *args, **kw)
        finally:
            try:
                if stale is not None:
                    fig.stale = stale
                if dirty is not None:
                    setattr(fig, DIRTY_SINCE_SAVE, dirty)
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001 —— 原生下这次保存会抛
        return False
    return True


def _replayable_on_blank(args, kwargs: dict) -> bool:
    """这组保存参数能不能在空白 Figure 上**原样**复现。引用原 Figure 内对象的参数（`bbox_extra_artists`，以及任何值里
    带 Artist 的参数）在空白 Figure 上没法复现：不能原样复现就不能拿空白 Figure 的成功当「失败只来自内容」的证据。
    不含 Artist 的值（None、空容器、数、非法值）原样传过去：合法的照常复现，非法的在空白 Figure 上同样失败 -> 判 False。"""
    from matplotlib.artist import Artist

    def refers_to_figure(value, depth=0) -> bool:
        if isinstance(value, Artist):
            return True
        if depth < 2 and isinstance(value, (list, tuple, set, frozenset)):
            return any(refers_to_figure(v, depth + 1) for v in value)
        if depth < 2 and isinstance(value, dict):
            return any(refers_to_figure(v, depth + 1) for v in value.values())
        return False

    return not any(refers_to_figure(v) for v in (*args, *kwargs.values()))


def _content_only_failure(real_savefig, fname, args, kwargs: dict) -> bool:
    """同一组保存参数在**空白 Figure** 上能原样渲染：此时 Figure 那一次渲染不出，原因只在它的内容。
    参数不能原样复现（`_replayable_on_blank`）一律 False；不碰用户的 Figure、不进 pyplot 的图表
    （`Figure()` 直接建，不经 `plt.figure`）。任何异常按「不是」算（保持原有的失败判定）。"""
    try:
        from matplotlib.figure import Figure

        if not _replayable_on_blank(args, kwargs):
            return False
        return savefig_verify_render(real_savefig, Figure(), fname, args, dict(kwargs))
    except Exception:  # noqa: BLE001
        return False


#: 「这张图被观察到存过盘」的标记；与 `DIRTY_SINCE_SAVE` 一起判「存完之后脚本又改过它」（ADR 0139 §八 的告知）
_SAVED_MARK = "_mm_saved_once"
#: 最近一次保存之后，这张 Figure 是否被改过（私有记录，由 `_StaleRecorder` 写、`mark_saved` 清）
DIRTY_SINCE_SAVE = "_mm_dirty_since_save"
#: 记录包装之下的原 `stale_callback`（可能是 None，也可能是 pyplot 的 `_auto_draw_if_interactive`）
_ORIG_STALE_CALLBACK = "_mm_orig_stale_callback"
#: 已连上 `draw_event` 清标记回调的记号（只连一次）
_DRAW_HOOK = "_mm_draw_hook_cid"
#: 判过一次的结论（True = 存盘后被脚本改过）；只判一次——之后 Tavotto 自己的编辑也会让标记变 True
MODIFIED_AFTER_SAVE = "_mm_modified_after_save"


class _StaleRecorder:
    """`Figure.stale_callback` 的透明包装：被以真值调用时在 Figure 上记 `DIRTY_SINCE_SAVE`，然后**原样**转调原回调。
    不碰 `fig.stale` 的值，用户脚本观察到的 stale 与原生 matplotlib 完全一致。"""

    def __init__(self, orig):
        self.orig = orig

    def __call__(self, artist, val):
        if val:
            try:
                setattr(artist, DIRTY_SINCE_SAVE, True)
            except Exception:  # noqa: BLE001 —— 记账尽力而为
                pass
        if self.orig is not None:
            return self.orig(artist, val)
        return None


def _hook_draw_event(fig) -> None:
    """连一个 `draw_event` 回调（只连一次）：Figure 每画完一次，把 `DIRTY_SINCE_SAVE` 清成 False。
    draw 过程中 matplotlib 自己会多次置 stale（`Axes.apply_aspect` 等），那些噪声先弄脏标记、画完被这里清掉；
    `draw_event` 在 `Figure.draw` 画完之后才发，回调注册表在 figure 上，`print_figure` 换临时 canvas 时照样触发
    （matplotlib 3.11 实测）。闭包只持 weakref，不拖住 Figure。"""
    if getattr(fig, _DRAW_HOOK, None) is not None:
        return
    import weakref

    ref = weakref.ref(fig)

    def _on_draw(_event):
        f = ref()
        if f is not None:
            try:
                setattr(f, DIRTY_SINCE_SAVE, False)
            except Exception:  # noqa: BLE001
                pass

    try:
        setattr(fig, _DRAW_HOOK, fig.canvas.mpl_connect("draw_event", _on_draw))
    except Exception:  # noqa: BLE001 —— 连不上就退化成不清（只会多报，不抛）
        pass


def _unhook(fig) -> None:
    """卸掉 `mark_saved` 装的两个钩子：`stale_callback` 若仍是我们的 `_StaleRecorder` 就还原成它记住的原回调
    （用户换成自己的就不动），`draw_event` 回调断开并清掉记号。不抛。"""
    try:
        cb = getattr(fig, "stale_callback", None)
        if isinstance(cb, _StaleRecorder):
            fig.stale_callback = cb.orig
    except Exception:  # noqa: BLE001
        pass
    try:
        cid = getattr(fig, _DRAW_HOOK, None)
        if cid is not None:
            fig.canvas.mpl_disconnect(cid)
            setattr(fig, _DRAW_HOOK, None)
    except Exception:  # noqa: BLE001
        pass


def mark_saved(fig) -> None:
    """一次 savefig 记完账之后调用：把 `DIRTY_SINCE_SAVE` 清成 False，打上「存过盘」的标记，并保证 Figure 的
    `stale_callback` 外面套着一层只记录的 `_StaleRecorder`（只套一次；重复保存不叠层）。

    判据来自 matplotlib 自己的 stale 传播：`Artist.stale` setter 在值为 True 时调 `stale_callback(self, val)`，子 artist
    经 `_stale_figure_callback` / `_stale_axes_callback` 把 `figure.stale = True` 一路传到 Figure，所以 Figure 的
    回调在每次画面相关改动时都会被调。实测（matplotlib 3.11）：savefig 之后 `ax.set_title` / `line.set_color` /
    `fig.set_size_inches` / `fig.text` / `ax.cla` 都触发（True）；只改 rcParams、`plt.close` 不触发。
    `bbox_inches="tight"` 保存过程中的临时改动发生在本函数**之前**，不会误报。回调调用不看 stale 原值，
    而 `print_figure` 收尾本来就把 stale 置 True，所以不需要（也不能）去清 `fig.stale`——
    那会让存盘后读 `fig.stale` 的用户脚本在 Tavotto 下走不同分支。
    语义与旧的「清 `stale`」一致：从**最后一次保存或最后一次重绘（`draw_event`）之后**被改过才算——存后脚本自己
    `fig.canvas.draw()` 不报告；已知盲区：存后改了又重绘（`plt.pause`）一次，也不再报告。
    **两个钩子只在脚本运行期存在**：`note_modified_after_save` 判定时一并卸掉，之后 Figure 与原生无异
    （采纳检查 `figsession._require_artifact_figure` 看不到它们）；已判过的图不再装。
    用户脚本若自己替换 `fig.stale_callback`：记录随之丢失（下次保存时重新套）；尽力而为、绝不抛错。
    本函数只报事实、不额外渲染、不复制 Figure；保存瞬间的快照是另一件事（`SaveMomentLedger`，ADR 0139 §九，只在 safe worker 的 `note_savefig` 里拍）。"""
    if hasattr(fig, MODIFIED_AFTER_SAVE):
        # 已经判过（例如 native 在 show 屏障判过、之后脚本又存盘）：钩子早已卸掉，不再装
        try:
            setattr(fig, _SAVED_MARK, True)
        except Exception:  # noqa: BLE001
            pass
        return
    try:
        cb = getattr(fig, "stale_callback", None)
        if not isinstance(cb, _StaleRecorder):
            setattr(fig, _ORIG_STALE_CALLBACK, cb)
            fig.stale_callback = _StaleRecorder(cb)
        _hook_draw_event(fig)
        setattr(fig, DIRTY_SINCE_SAVE, False)
        setattr(fig, _SAVED_MARK, True)
    except Exception:  # noqa: BLE001 —— 记账尽力而为，绝不影响用户的 savefig
        pass


def note_modified_after_save(capture: dict, capture_source: dict) -> list[str]:
    """脚本跑完（或 native 第一次把图交出去）时，给存过盘、之后又被脚本改过的 savefig 图打上
    `MODIFIED_AFTER_SAVE`；返回这些 stem。每张图只判一次：判过之后 Tavotto 自己的编辑同样会把标记置真，
    再判就是把我们的改动算到脚本头上。三条入口在 `adopt_displaced_writers` 之后各调一次；manifest 读这个标记。"""
    out = []
    for stem, fig in capture.items():
        if capture_source.get(stem) != SOURCE_SAVEFIG or not getattr(fig, _SAVED_MARK, False):
            continue
        _unhook(fig)  # 钩子只活到判定这一刻（不论这次是否新判、不论结论）
        if not hasattr(fig, MODIFIED_AFTER_SAVE):
            try:
                setattr(fig, MODIFIED_AFTER_SAVE, bool(getattr(fig, DIRTY_SINCE_SAVE, False)))
            except Exception:  # noqa: BLE001
                continue
        if getattr(fig, MODIFIED_AFTER_SAVE, False):
            out.append(stem)
    return out


#: 保存瞬间快照（ADR 0139 §九）。只在 safe worker 里、只活在本进程内存里：**不落盘、不跨进程、不读项目文件里的任何 pickle**。
#: 记在 Figure 上的结论属性（manifest 读它；`manifest.SAVE_MOMENT_ATTR` 是同源对，`tests/test_save_moment.py` 钉相等）
SAVE_MOMENT = "_mm_save_moment"
#: 结论取值：`SAVE_MOMENT_RESTORED`，或下面闭集里的一个失败原因（编辑基础退回脚本跑完的最终态，照旧只告知）
SAVE_MOMENT_RESTORED = "restored"
SAVE_MOMENT_REASONS = frozenset(
    {
        "unpicklable",  # pickle.dumps 抛了（lambda 格式化器、不可序列化的 artist……）
        "too_large",  # 单张快照超过 SAVE_MOMENT_MAX_BYTES
        "budget_exceeded",  # 本次执行累计超过 SAVE_MOMENT_TOTAL_BYTES
        "bbox_extra_artists",  # 保存带 bbox_extra_artists：那批 artist 属于原件，复制品对不上
        "restore_failed",  # 还原失败（含白名单外的类：用户自定义的 artist / 格式化器 / 变换）
        "structure_mismatch",  # 还原出的 Figure 与拍快照时的结构签名对不上
        "multi_stem",  # 同一张图以多个 stem 被捕获：快照只对应它最后一次保存，说不清是哪个 stem 的
        "not_selected_save",  # 选定原件对应的那次保存不是这张图的最后一次保存（快照取的是最后一次）
        "matplotlib_patched",  # 脚本改了 matplotlib 的方法面（patch 过类 / 函数）：拍摄与还原都会走被改过的代码，一律放弃
    }
)
#: 单张快照 / 本次执行累计的字节上限。**只用字节数当预算，不用墙钟**：热会话与写回的一次性重放必须在同一处同样地
#: 放弃，否则「热态所见 == 重放所得」会被计时抖动破坏
SAVE_MOMENT_MAX_BYTES = 32 * 1024 * 1024
SAVE_MOMENT_TOTAL_BYTES = 128 * 1024 * 1024
#: 关掉快照（只告知，行为与 #1250 相同）：环境变量值为 "0"。worker 由同一个父进程拉起，热会话与一次性重放读到同一个值
SAVE_MOMENT_ENV = "TAVOTTO_SAVE_MOMENT_SNAPSHOT"
#: 还原时允许解析的全局：matplotlib / mpl_toolkits / numpy 本体（名字不含点、对象自己的 `__module__` 也在这些包里），
#: 加下面几个精确名字。用户自己的类 / 函数 / 模块一律拒绝——还原不执行用户代码、不 import 用户模块。
#: 这是纵深防御，不是边界：边界是快照字节只在产生它的进程里存在，从不落盘、从不来自项目文件（ADR 0139 §九）
_SAVE_MOMENT_PACKAGES = ("matplotlib", "mpl_toolkits", "numpy")
_SAVE_MOMENT_EXACT = frozenset(
    {
        ("builtins", "object"),
        ("builtins", "getattr"),
        ("builtins", "set"),
        ("builtins", "frozenset"),
        ("builtins", "slice"),
        ("builtins", "complex"),
        ("builtins", "bytearray"),
        ("builtins", "range"),
        ("collections", "OrderedDict"),
        ("collections", "defaultdict"),
        ("collections", "deque"),
        ("functools", "partial"),
        ("itertools", "count"),
        ("itertools", "cycle"),
        ("copyreg", "_reconstructor"),
    }
)
#: 拍快照时从复制品上抹掉的、属于 `mark_saved` 的私有记账（复制品没有这些钩子）
_SAVE_MOMENT_STRIP = (_SAVED_MARK, DIRTY_SINCE_SAVE, _ORIG_STALE_CALLBACK, _DRAW_HOOK)


def save_moment_enabled() -> bool:
    return os.environ.get(SAVE_MOMENT_ENV, "1").strip() != "0"


# ---- 静态读取原语（Codex #1314 P1-e）：拍摄 / 指纹 / 白名单把关全程**不许触发用户代码** ----
# 被检查的对象可能带自定义 `__getattr__` / `__getattribute__` / `__eq__` / `__hash__` / `__bool__`，或元类的 `__instancecheck__`。
# 所以这一段只用：`type(x)` + `is`、`id`、直接从 `type` / `function` / `code` / `staticmethod` 等**内建类型的 `__dict__` 里取出来的描述符**
# 去读对象（描述符是 C 实现，不经过对象自己的属性查找），以及精确 `str` / `dict` 上的读取。不用 `getattr` / `hasattr` / `isinstance` /
# `callable` / `==` / `hash` / `bool` / `str` / `repr` 去碰被检查的对象。
_T_DICT = type.__dict__["__dict__"]
_T_MRO = type.__dict__["__mro__"]
_T_MODULE = type.__dict__["__module__"]
_T_QUALNAME = type.__dict__["__qualname__"]
_FN_CODE = types.FunctionType.__dict__["__code__"]
_FN_MODULE = types.FunctionType.__dict__["__module__"]
_FN_QUALNAME = types.FunctionType.__dict__["__qualname__"]
_CODE_FILENAME = types.CodeType.__dict__["co_filename"]
_BF_MODULE = types.BuiltinFunctionType.__dict__["__module__"]
_BF_QUALNAME = types.BuiltinFunctionType.__dict__["__qualname__"]
_BF_SELF = types.BuiltinFunctionType.__dict__["__self__"]
_SM_FUNC = staticmethod.__dict__["__func__"]
_CM_FUNC = classmethod.__dict__["__func__"]
_PROP_PARTS = tuple(property.__dict__[n] for n in ("fget", "fset", "fdel"))
_MOD_DICT = types.ModuleType.__dict__["__dict__"]
#: 值本身不可能是钩子的简单数据：这些类型的「重新绑定」不记进指纹（原地改容器不换 id，本来也看不出）
_SIMPLE_TYPES = (
    int,
    float,
    complex,
    str,
    bytes,
    bool,
    type(None),
    tuple,
    frozenset,
    range,
    slice,
    type(Ellipsis),
    type(NotImplemented),
    list,  # 如 copyreg 在首次 pickle 时写入的 `__slotnames__` 缓存
    dict,
    set,
)


def _is_class_type(t) -> bool:
    """`t`（某个对象的类型）的 MRO 里有 `type` = 那个对象本身是个类。只用 `is`。"""
    for c in _T_MRO.__get__(t):
        if c is type:
            return True
    return False


def _is_simple(v) -> bool:
    t = type(v)
    for c in _SIMPLE_TYPES:
        if t is c:
            return True
    return False


def _static_type_names(t):
    return _T_MODULE.__get__(t), _T_QUALNAME.__get__(t)


def save_moment_module_allowed(module, name) -> bool:
    """白名单的第一关（还原侧与拍摄侧共用）：精确名单里的 `(模块, 名字)`，或模块顶层包是 matplotlib / mpl_toolkits / numpy。
    只看名字、不 import、不解析、不执行任何东西（名字必须是精确的 `str`，其它一律拒绝）。用户模块（含用户继承 matplotlib 的子类，
    它们的 `__module__` 是用户模块）在这里被拒。
    威胁模型是「诚实的用户钩子意外改了图」，不是蓄意把自己的类的 `__module__` 伪装成 numpy——后者本来就能直接改 Figure。"""
    if type(module) is not str or type(name) is not str:
        return False
    return (module, name) in _SAVE_MOMENT_EXACT or module.split(".", 1)[0] in _SAVE_MOMENT_PACKAGES


def _snapshot_type_allowed(t) -> bool:
    """`t`（一个类）能不能出现在快照里：按它的 `__module__` / `__qualname__`（静态读）查共用白名单的第一关。"""
    return save_moment_module_allowed(*_static_type_names(t))


def save_moment_global_allowed(module, name, resolve) -> bool:
    """还原侧（`find_class`）的完整判据：第一关 `save_moment_module_allowed`（拍摄侧同用这一关），再要求名字不含点或点前是类
    （`BoxStyle.Round` 这种嵌套类）、解析到的对象自己的 `__module__` 也在这些包里。`resolve(module, name)` 是 `Unpickler.find_class`。
    用户模块在第一关就被拒，不会走到 `resolve`（不 import 用户模块）。"""
    if not save_moment_module_allowed(module, name):
        return False
    if (module, name) in _SAVE_MOMENT_EXACT:
        return True
    if "." in name and not _is_class_type(type(resolve(module, name.split(".")[0]))):
        # 带点的名字只许是类的嵌套类（`BoxStyle.Round`），不许沿模块属性链走到别的模块去
        return False
    found = resolve(module, name)
    tf = type(found)
    if _is_class_type(tf):
        owner = _T_MODULE.__get__(found)
    elif tf is types.FunctionType:
        owner = _FN_MODULE.__get__(found)
    elif tf is types.BuiltinFunctionType:
        owner = _BF_MODULE.__get__(found)
    else:
        owner = _T_MODULE.__get__(tf)
    return type(owner) is str and owner.split(".", 1)[0] in _SAVE_MOMENT_PACKAGES


class _RestrictedUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        if not save_moment_global_allowed(module, name, super().find_class):
            raise pickle.UnpicklingError(f"not allowed: {module}.{name}")
        return super().find_class(module, name)


# ---- 可信方法面指纹（Codex #1314 P1-d；用户裁决：检测到 matplotlib 被 patch 就放弃快照，不逐个封堵） ----
#: 指纹范围：matplotlib / mpl_toolkits 已导入模块里每个类 `__dict__` 中的非简单数据值（函数、描述符、嵌套类、其它对象）和模块级的类 / 函数，
#: 再加还原路径实测会解析到的标准库与 numpy 入口（见 `_SURFACE_EXTRA_MODULES`）。值记 `id`，同时握住对象本身（防地址复用漏报）。
#: **全程静态读取**（见上面的原语）：被改过的对象可能带自定义 `__getattr__` 等，比对本身不能把它们的代码跑起来。
_SURFACE_PACKAGES = ("matplotlib", "mpl_toolkits")
_SURFACE_EXTRA_MODULES = (
    "copyreg",
    "enum",
    "collections",
    "functools",
    "numpy.ma.core",
    "numpy._core.multiarray",
    "numpy._core.numeric",
    "numpy.core.multiarray",
    "numpy.core.numeric",
)
_surface_lock = threading.Lock()
_surface_base: dict[str, tuple[dict, list]] | None = None
_surface_roots: tuple[str, ...] = ()
#: 仅 matplotlib / mpl_toolkits 自己的包目录：变化条目复核（`_changed_entries_are_library`）只认这里的函数
_surface_mpl_roots: tuple[str, ...] = ()
_realpath_cache: dict[str, tuple[str, str]] = {}
_STDLIB_NAMES = frozenset(getattr(sys, "stdlib_module_names", ()))
_LIB_TOPS = (
    frozenset(_SURFACE_PACKAGES) | {"numpy"} | frozenset(getattr(sys, "stdlib_module_names", ()))
)


def _module_dict(mod):
    """精确 `ModuleType` 的命名空间（静态读）；模块子类 / 别的对象一律 None（不碰）。"""
    if type(mod) is not types.ModuleType:
        return None
    d = _MOD_DICT.__get__(mod)
    return d if type(d) is dict else None


def _defined_in(cls, name: str) -> bool:
    module = _T_MODULE.__get__(cls)
    return type(module) is str and module == name


def _record_class_value(v) -> bool:
    """类字典里的这个值要不要进指纹：函数 / 内建 / 描述符 / 嵌套类一律记；其余对象只记「类型不是库的」（用户对象）。
    库类型的实例（如 `Path._unit_circle` 这种首次使用才写入的缓存）会合法地被重新绑定，不记。
    它们若被当成序列化钩子，`_class_hooks_trusted` 另有一层拦。"""
    t = type(v)
    if (
        t is types.FunctionType
        or t is types.BuiltinFunctionType
        or t is staticmethod
        or t is classmethod
        or t is property
        or _is_class_type(t)
    ):
        return True
    if _is_simple(v):
        return False
    if not _owner_module_ok(t):
        return True
    # 库类型的实例里，**可调用的 / 描述符**（`functools.partial(用户函数)`、绑定方法、`cached_property` 之类）也要记：
    # 它们一被取用 / 调用就可能跑到用户代码。不可调用的普通数据（首次使用才写入的缓存）仍不记
    for c in _T_MRO.__get__(t):
        cd = _T_DICT.__get__(c)
        if (
            _dict_get(cd, "__call__", _MISSING) is not _MISSING
            or _dict_get(cd, "__get__", _MISSING) is not _MISSING
        ):
            return True
    return False


_MISSING = object()


def _dict_get(d, name: str, default=None):
    """精确 dict / mappingproxy 里取 `name`：逐项只比精确 `str` 键。不用 `in` / `[]` / `.get`——非 `str` 键可能带自定义
    `__hash__` / `__eq__`，哈希碰撞时会被调用。"""
    for k, v in d.items():
        if type(k) is str and k == name:
            return v
    return default


def _loaded_module_dict(name: str):
    """`sys.modules` 里 `name` 的命名空间（静态读）；`sys.modules` 被换成别的容器类型就当不可信（None）。"""
    mods = sys.modules
    if type(mods) is not dict:
        return None
    return _module_dict(mods.get(name))


def _surface_module_map(name: str, mod) -> tuple[dict, dict]:
    """(条目 -> 对象 id, 条目 -> 对象本身)。对象本身既握住（防地址复用），也供「变了的条目来源是不是库」复核用。"""
    out: dict = {}
    objs: dict = {("!mod", None): mod}
    d = _module_dict(mod)
    if d is None:
        out[("!nonstd", None)] = id(type(mod))
        return out, objs

    def put(key, obj) -> None:
        out[key] = id(obj)
        objs[key] = obj

    for attr, val in list(d.items()):
        if type(attr) is not str:
            put(("!key", id(attr)), val)
            continue
        t = type(val)
        if _is_class_type(t):
            if not _defined_in(val, name):
                # 别的模块定义的类由它自己的模块登记；库里的类在模块间被惰性重新绑定是正常的（如 pyplot 的 `_backend_mod`），
                # 只有「用户的类被塞进库模块」才记
                if not _owner_module_ok(val):
                    put((attr, "!foreign"), val)
                continue
            qualname = _T_QUALNAME.__get__(val)
            if type(qualname) is str and "<locals>" in qualname:
                continue  # 函数里现造的类（pyplot 每次切后端重新建的 `_backend_mod`）：库自己的惰性状态
            put((attr, None), val)
            for k, v in list(_T_DICT.__get__(val).items()):
                if type(k) is not str:
                    put(("!key", id(k)), v)
                elif _record_class_value(v):
                    put((attr, k), v)
        elif t is types.FunctionType or t is types.BuiltinFunctionType:
            put((attr, None), val)
    return out, objs


def _surface_modules() -> dict:
    out = {}
    mods = sys.modules
    if type(mods) is not dict:
        raise TypeError("sys.modules is not a plain dict")  # 外层兜底成「被改过」
    for k, m in list(mods.items()):
        if type(k) is not str or m is None:
            continue
        if k.split(".", 1)[0] in _SURFACE_PACKAGES or k in _SURFACE_EXTRA_MODULES:
            out[k] = m
    return out


def _owner_module_ok(cls) -> bool:
    """类（或某个对象的类型）定义在库里：matplotlib / mpl_toolkits / numpy / 标准库，或装在 site-packages 里的依赖
    （cycler / pyparsing / PIL / dateutil / kiwisolver……，按该模块的文件位置判）。用户脚本 / 用户项目里的模块不算。"""
    module = _T_MODULE.__get__(cls)
    if type(module) is not str:
        return False
    top = module.split(".", 1)[0]
    if top in _LIB_TOPS:
        return True
    d = _loaded_module_dict(top)
    if d is None:
        return False
    loc = _dict_get(d, "__file__")
    if type(loc) is str:
        return _path_under_roots(loc, _surface_roots)
    # 包：`__path__` 只认精确的 list / tuple（里面全是精确 str）。命名空间包的 `_NamespacePath` 一迭代就会重算——走
    # `sys.meta_path` / `path_hooks`，那是用户可注入的代码——所以其它类型一律当「非库来源」
    paths = _dict_get(d, "__path__")
    if type(paths) is list or type(paths) is tuple:
        return bool(paths) and all(
            type(x) is str and _path_under_roots(x, _surface_roots) for x in paths
        )
    return False


def _path_under_roots(filename: str, roots) -> bool:
    """`filename` 在 `roots` 之下（先按原样规整比，再按 realpath 比；Windows 的大小写 / 短路径 / 符号链接差异都不该造成误判）。
    `<…>` 形式的合成文件名（`<string>`、`<frozen …>` 等）一律不算：`exec` 生成的用户函数就是 `<string>`；
    冻结的标准库函数由 `_function_origin_ok` 按 `__module__` 另判。"""
    if filename.startswith("<"):
        return False
    cached = _realpath_cache.get(filename)
    if cached is None:
        raw = os.path.normcase(os.path.normpath(filename))
        try:
            real = os.path.normcase(os.path.realpath(filename))
        except Exception:  # noqa: BLE001
            real = raw
        cached = _realpath_cache[filename] = (raw, real)
    for candidate in cached:
        for root in roots:
            if candidate == root or candidate.startswith(root + os.sep):
                return True
    return False


def _function_origin_ok(fn, roots) -> bool:
    filename = _CODE_FILENAME.__get__(_FN_CODE.__get__(fn))
    module = _FN_MODULE.__get__(fn)
    if type(filename) is not str:
        return False
    if filename.startswith("<"):
        # 合成文件名：`<string>` 等一律不算库来源；`<frozen X>` 只在 X 精确等于该函数的 `__module__` 且它是标准库模块时才认
        if filename.startswith("<frozen ") and filename.endswith(">"):
            return (
                type(module) is str
                and filename[len("<frozen ") : -1] == module
                and module.split(".", 1)[0] in _STDLIB_NAMES
            )
        return False
    if _path_under_roots(filename, roots):
        return True
    # 路径对不上时（打包 runtime 的目录布局各异）再看函数声明的所属模块：库的函数 `__module__` 是库的模块，
    # 用户脚本里写的 patch 函数是用户的模块。威胁模型同上（防诚实的用户钩子，不防蓄意伪装 `__module__`）
    return type(module) is str and module.split(".", 1)[0] in _LIB_TOPS


def _origin_value_ok(v, roots, depth: int = 0) -> bool:
    """类字典里的一个值，来源是库吗？函数看定义文件，描述符拆开看里面的函数，其它对象看它的类型定义在哪。"""
    if depth > 4:
        return False
    if _is_simple(v):
        return True
    t = type(v)
    if t is types.FunctionType:
        return _function_origin_ok(v, roots)
    if t is types.BuiltinFunctionType:
        return True
    if t is staticmethod:
        return _origin_value_ok(_SM_FUNC.__get__(v), roots, depth + 1)
    if t is classmethod:
        return _origin_value_ok(_CM_FUNC.__get__(v), roots, depth + 1)
    if t is property:
        for part in _PROP_PARTS:
            if not _origin_value_ok(part.__get__(v), roots, depth + 1):
                return False
        return True
    return _owner_module_ok(v if _is_class_type(t) else t)


def _module_origin_ok(mod, name: str, roots) -> bool:
    """首次才见到的模块（脚本运行期间才导入，如 `mpl_toolkits.mplot3d`）：没有「启动时」的基线可比，
    所以从严——它的 Python 函数必须定义在 matplotlib / mpl_toolkits 自己的目录（或标准库 / numpy）里，其它对象的类型也必须是库的。"""
    d = _module_dict(mod)
    if d is None:
        return False
    for attr, val in list(d.items()):
        if type(attr) is not str:
            return False
        t = type(val)
        if _is_class_type(t):
            if not _owner_module_ok(val):
                return False
            if _defined_in(val, name):
                for k, v in list(_T_DICT.__get__(val).items()):
                    if type(k) is not str or not _origin_value_ok(v, roots):
                        return False
        elif t is types.FunctionType and not _function_origin_ok(val, roots):
            return False
    return True


def freeze_trusted_surface() -> None:
    """进程内只拍一次（同 worker 重试不重拍——否则脚本上一轮的 patch 会被当成基线）。safe worker 在跑用户脚本之前调。"""
    global _surface_base, _surface_roots, _surface_mpl_roots
    with _surface_lock:
        if _surface_base is not None:
            return
        roots: list[str] = []

        def add(path) -> None:
            if type(path) is str and path:
                for form in (os.path.normpath(path), os.path.realpath(path)):
                    form = os.path.normcase(form)
                    if form not in roots:
                        roots.append(form)

        for pkg in _SURFACE_PACKAGES:
            d = _loaded_module_dict(pkg)
            paths = _dict_get(d, "__path__") if d is not None else None
            # 只读精确的 list / tuple；命名空间包（mpl_toolkits）的 `_NamespacePath` 不迭代（迭代会重算、走 meta_path / path_hooks），
            # 改取它已算好的内部 `_path` 列表，再不行就用 matplotlib 目录的兄弟目录
            if type(paths) is not list and type(paths) is not tuple and paths is not None:
                try:
                    inner = object.__getattribute__(paths, "_path")
                except Exception:  # noqa: BLE001
                    inner = None
                paths = inner if type(inner) is list or type(inner) is tuple else None
            for p in paths or ():
                add(p)
        if roots and not any(os.path.basename(r) == "mpl_toolkits" for r in roots):
            add(os.path.join(os.path.dirname(roots[0]), "mpl_toolkits"))
        _surface_mpl_roots = tuple(roots)
        # 「库来源」还包括：matplotlib 所在的 site-packages（它的依赖）、解释器安装前缀（标准库、嵌入式发行版的 python3xx.zip、
        # 各平台 site-packages）、Tavotto 引擎目录（worker 对 `plt.show` 等的自家 patch）。被用户脚本里的函数顶替才是 patch
        import site  # noqa: PLC0415
        import sysconfig  # noqa: PLC0415

        if roots:
            add(os.path.dirname(roots[0]))
        for path in (
            sysconfig.get_paths().get("stdlib"),
            sysconfig.get_paths().get("platstdlib"),
            sys.prefix,
            sys.base_prefix,
            sys.exec_prefix,
            os.path.dirname(os.path.abspath(__file__)),
        ):
            add(path)
        try:
            for path in site.getsitepackages():
                add(path)
        except Exception:  # noqa: BLE001
            pass
        _surface_roots = tuple(roots)
        _surface_base = {k: _surface_module_map(k, m) for k, m in _surface_modules().items()}


def _reset_trusted_surface_for_tests() -> None:
    global _surface_base
    with _surface_lock:
        _surface_base = None


def trusted_surface_diff() -> list:
    """诊断用：与基线不一致的条目（不补登记新模块）。"""
    base = _surface_base or {}
    out = []
    for name, mod in _surface_modules().items():
        known = base.get(name)
        if known is None:
            continue
        cur = _surface_module_map(name, mod)[0]
        out += [(name, k) for k in set(cur) | set(known[0]) if cur.get(k) != known[0].get(k)]
    return out


_surface_reported: set = set()


def _report_patched(why: str) -> None:
    """只记一次、只写 stderr（worker.log）：下次再有环境差异造成的误判，能直接看到是哪个模块的哪一项。"""
    if why in _surface_reported:
        return
    _surface_reported.add(why)
    try:
        print(f"[snapshot] matplotlib_patched: {why[:300]}", file=sys.stderr)
    except Exception:  # noqa: BLE001
        pass


def _is_mpl_own_function(obj) -> bool:
    """变化条目复核唯一接受的新对象：matplotlib / mpl_toolkits **自己**的函数——精确类型 `FunctionType`（文件在 matplotlib /
    mpl_toolkits 的包目录下，`__module__` 顶层也是它们），或 C 层 builtin（`__module__` 属于它们）。
    partial / 绑定方法 / 描述符包装 / 库类型的实例 / 别的第三方包（scienceplots、seaborn……）/ 标准库的函数一律不是。"""
    t = type(obj)
    if t is types.FunctionType:
        module = _FN_MODULE.__get__(obj)
        filename = _CODE_FILENAME.__get__(_FN_CODE.__get__(obj))
        return (
            type(module) is str
            and module.split(".", 1)[0] in _SURFACE_PACKAGES
            and type(filename) is str
            and not filename.startswith("<")
            and _path_under_roots(filename, _surface_mpl_roots)
        )
    if t is types.BuiltinFunctionType:
        module = _BF_MODULE.__get__(obj)
        return type(module) is str and module.split(".", 1)[0] in _SURFACE_PACKAGES
    return False


def _changed_entries_are_library(name: str, cur_ids: dict, cur_objs: dict, known_ids: dict) -> bool:
    """基线之后有变化的条目：只有新对象是 matplotlib / mpl_toolkits 自己的函数（它们自己的惰性重绑定，如 pyplot 的
    `switch_backend`）才接受并更新基线；其余一律是被改过（用户裁决：matplotlib 被任何东西改过就放弃快照）。被移除的条目不算。"""
    for key in set(cur_ids) | set(known_ids):
        if cur_ids.get(key) == known_ids.get(key) or key not in cur_ids:
            continue
        if not _is_mpl_own_function(cur_objs.get(key)):
            _report_patched(f"{name}.{key[0]}.{key[1]}")
            return False
    return True


def trusted_surface_intact() -> bool:
    """此刻 matplotlib（及还原路径上的几个入口）的方法面与基线一致吗？每次都按真实值重算，不缓存。
    基线里有的模块逐项比对；基线之后才导入的模块首次见到时按来源从严检查、通过则补登记（之后的 patch 靠 id 比对发现）。
    与基线不一致的条目再复核一次：只有 matplotlib / mpl_toolkits 自己的函数重绑定才接受并更新基线。
    全程静态读取；任何意外异常都按「被改过」处理（返回 False），绝不向调用方抛。"""
    try:
        if _surface_base is None:
            freeze_trusted_surface()
        base = _surface_base
        if base is None:
            return False
        with _surface_lock:
            for name, mod in _surface_modules().items():
                known = base.get(name)
                if known is None:
                    if name.split(".", 1)[0] in _SURFACE_PACKAGES and not _module_origin_ok(
                        mod, name, _surface_roots
                    ):
                        _report_patched(f"{name}: 首次见到的模块里有非库来源的函数 / 对象")
                        return False
                    base[name] = _surface_module_map(name, mod)
                    continue
                cur = _surface_module_map(name, mod)
                if cur[0] != known[0]:
                    if not _changed_entries_are_library(name, cur[0], cur[1], known[0]):
                        return False
                    base[name] = cur
        return True
    except Exception as exc:  # noqa: BLE001 —— 比对失败本身就是不可信
        _report_patched(f"异常 {type(exc).__name__}")
        return False


_SERIALISATION_HOOKS = ("__reduce_ex__", "__reduce__", "__getstate__", "__setstate__")
_C_HOOK_TYPES = (
    types.WrapperDescriptorType,
    types.MethodDescriptorType,
    types.BuiltinFunctionType,
    types.ClassMethodDescriptorType,
)


#: 标准库里自带 Python 级序列化钩子的模块（`enum.Enum.__reduce_ex__` 等；matplotlib 的枚举 / 集合类型会用到）
_TRUSTED_HOOK_MODULES = frozenset({"enum", "copyreg", "collections", "functools"})


def _class_hooks_trusted(t) -> bool:
    """纵深层（Codex #1314 P1-c）：`t` 沿 MRO 解析出的类级序列化钩子必须出自白名单包或是 C 实现（`object` 自带的槽）。
    脚本可以 patch `Line2D.__reduce__` / `Figure.__getstate__`——类型名仍是 matplotlib，只看模块拦不住。
    只静态读各类的 `__dict__`，不调用、不触发描述符；每次 dump 重新判，不跨快照缓存。"""
    for name in _SERIALISATION_HOOKS:
        for klass in _T_MRO.__get__(t):
            attr = _dict_get(_T_DICT.__get__(klass), name, _MISSING)
            if attr is _MISSING:
                continue
            ta = type(attr)
            if ta is staticmethod:
                attr = _SM_FUNC.__get__(attr)
            elif ta is classmethod:
                attr = _CM_FUNC.__get__(attr)
            ta = type(attr)
            if any(ta is c for c in _C_HOOK_TYPES):
                break
            if ta is types.FunctionType:
                module, qualname = _FN_MODULE.__get__(attr), _FN_QUALNAME.__get__(attr)
                if (type(module) is str and module in _TRUSTED_HOOK_MODULES) or (
                    save_moment_module_allowed(module, qualname)
                ):
                    break
            return False
    return True


class _SnapshotPickler(pickle.Pickler):
    """拍摄侧的守门：**任何用户的 `__reduce__` / `__reduce_ex__` / `__getstate__` 被调用之前**先按类型把关。

    `reducer_override` 先于 `__reduce_ex__` 被调用（C 实现里 int / str / bytes / float / bool / None 与精确的 list / tuple /
    dict / set / frozenset / bytearray 走快速通道、不经过它——它们没有用户代码，容器里的元素仍逐个经过这里）。
    不在白名单里的类型（用户类、用户继承 matplotlib 的子类、函数、lambda……）立刻抛 `PicklingError`，
    所以拍快照绝不执行用户的序列化代码，也就不可能借它改活 Figure。允许的返回 `NotImplemented`，交给默认机制。
    判断本身只做静态读取（`type(obj)` + 内建类型的描述符），不对 `obj` 调 `isinstance` / `getattr`，所以带自定义属性钩子的对象也跑不起来。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        #: id(类型) -> 类级序列化钩子是否都出自白名单。只活在这一次 dump 里：脚本在两次快照之间 patch 类方法，下一次重新判
        self._hooks_ok: dict[int, bool] = {}

    def _class_hooks_ok(self, t) -> bool:
        key = id(t)
        ok = self._hooks_ok.get(key)
        if ok is None:
            ok = self._hooks_ok[key] = _class_hooks_trusted(t)
        return ok

    def reducer_override(self, obj):
        t = type(obj)
        if _is_class_type(t):
            # 类对象一律按全局名存（元类的 `__reduce__` 不会被调用，已实测），只看类自己在不在名单里
            ok = _snapshot_type_allowed(obj)
        elif t is types.BuiltinFunctionType:
            owner = _BF_SELF.__get__(obj)
            if owner is None or type(owner) is types.ModuleType:
                # 模块级内建函数按全局名存
                ok = save_moment_module_allowed(_BF_MODULE.__get__(obj), _BF_QUALNAME.__get__(obj))
            else:
                # 内建对象的绑定方法（`list.remove` 的 bound 形态）存成 `getattr(self, 名字)`，`self` 之后逐个过这里
                ok = True
        elif t is types.FunctionType:
            # 函数按全局名存（`copyreg._reconstructor`、numpy 的重建函数……）；lambda / 用户函数 / 内部函数都不在名单里
            ok = save_moment_module_allowed(_FN_MODULE.__get__(obj), _FN_QUALNAME.__get__(obj))
        elif t is types.MethodType:
            # 绑定方法存成 `getattr(self, 名字)`：`__reduce__` 是 C 实现、不跑用户代码；`self` 之后仍逐个经过这里
            ok = True
        else:
            ok = _snapshot_type_allowed(t) and self._class_hooks_ok(t)
            if ok:
                # 实例上挂着的序列化钩子（`obj.__reduce_ex__ = ...`）也是用户代码；实例字典只在库类型上静态读
                try:
                    inst = object.__getattribute__(obj, "__dict__")
                except AttributeError:
                    inst = None
                if type(inst) is dict:
                    for k in inst:
                        if type(k) is str and k in ("__reduce__", "__reduce_ex__", "__getstate__"):
                            ok = False
                            break
        if not ok:
            module, qualname = _static_type_names(t)
            raise pickle.PicklingError(f"not snapshot-safe: {module}.{qualname}")
        return NotImplemented


def _snapshot_dumps(fig) -> bytes:
    buf = io.BytesIO()
    _SnapshotPickler(buf, protocol=pickle.HIGHEST_PROTOCOL).dump(fig)
    return buf.getvalue()


def _structure_signature(fig) -> tuple:
    """还原前后都能算的廉价结构签名：图幅 + 每个 Axes 的 artist 计数。不渲染、不改 Figure。

    轴的清单走 `axestraversal.ordered_axes`（全产品唯一的遍历权威，含 `inset_axes` / `secondary_[xy]axis` / 寄生轴）。
    静态读纪律：只在**已通过类型白名单的库对象图**上调用——拍摄侧排在序列化成功之后（图里全是库类型），还原侧算在刚还原出的复制品上；
    `ordered_axes` 只按属性名 `axes` / `child_axes` / `parasites` 走对象图。"""
    axes = tuple(
        (
            type(ax).__name__,
            len(ax.lines),
            len(ax.collections),
            len(ax.patches),
            len(ax.texts),
            len(ax.images),
            len(ax.containers),
        )
        for ax in axestraversal.ordered_axes(fig)[0]
    )
    w, h = fig.get_size_inches()
    return (round(float(w), 6), round(float(h), 6), len(fig.texts), len(fig.artists), axes)


class SaveMomentLedger:
    """保存瞬间快照账（一个 `FigureSession` 一份，ADR 0139 §九）。

    每张 Figure 只留**最近一次**保存的快照（与「改动判据是最近一次保存之后」同一时刻）。快照是 `pickle.dumps(fig)`
    ——matplotlib 文档化的 Figure 持久化——的字节串，只握在本进程内存里，随会话消亡；绝不写盘、不发给别的进程。
    """

    def __init__(self):
        #: id(fig) -> {"ref", "blob", "occurrence", "signature", "reason", "stems"}
        self._rows: dict[int, dict] = {}
        self._total = 0

    def _drop(self, key: int) -> None:
        row = self._rows.pop(key, None)
        if row is not None and row["blob"] is not None:
            self._total -= len(row["blob"])

    def take(self, fig, occurrence, extra_artists=None) -> None:
        """一次 savefig 记完账之后调用：给 `fig` 拍快照（替换它上一次的）。绝不抛，也不改 Figure。"""
        for key in [k for k, r in self._rows.items() if r["ref"]() is None]:
            self._drop(key)
        key = id(fig)
        self._drop(key)
        row = {
            "ref": weakref.ref(fig),
            "blob": None,
            "occurrence": occurrence,
            "signature": None,
            "reason": None,
        }
        self._rows[key] = row
        if extra_artists:
            row["reason"] = "bbox_extra_artists"
            return
        # 结构层（Codex #1314 P1-c）：拍摄期间 Figure 若被置脏（任何途径改了图，含被 patch 过的类级钩子），
        # 这次改动必须留在 `DIRTY_SINCE_SAVE` 里如实报成 `modified_after_save`——这里绝不清标记，快照作废。
        # 兜底：下面任何一步（含指纹比对）出了意外都只是放弃快照、退回只告知，绝不让 build 失败
        try:
            if not trusted_surface_intact():
                row["reason"] = "matplotlib_patched"
                return
            # 先看类型再碰对象：Figure 若是用户子类，读它的任何属性都可能是用户代码
            if not _snapshot_type_allowed(type(fig)):
                row["reason"] = "unpicklable"
                return
            dirty_before = bool(getattr(fig, DIRTY_SINCE_SAVE, False))
            try:
                self._take_into(row, fig)
            finally:
                if not dirty_before and getattr(fig, DIRTY_SINCE_SAVE, False):
                    row["blob"], row["signature"], row["reason"] = None, None, "unpicklable"
        except Exception:  # noqa: BLE001
            row["blob"], row["signature"], row["reason"] = None, None, "unpicklable"
        if row["blob"] is None:
            return
        blob = row["blob"]
        row["blob"] = None
        if len(blob) > SAVE_MOMENT_MAX_BYTES:
            row["reason"] = "too_large"
        elif self._total + len(blob) > SAVE_MOMENT_TOTAL_BYTES:
            row["reason"] = "budget_exceeded"
        else:
            row["blob"] = blob
            self._total += len(blob)

    @staticmethod
    def _take_into(row, fig) -> None:
        """序列化 `fig` 进 `row["blob"]`；失败写 `row["reason"]`。绝不抛。"""
        canvas = manager = None
        try:
            # 先看类型再碰对象：Figure / canvas 若是用户子类，读它们的属性（`fig.axes`、`canvas.manager = None`）就可能是用户代码
            if not _snapshot_type_allowed(type(fig)):
                raise pickle.PicklingError("user Figure subclass")
            canvas = fig.canvas
            if canvas is not None and not _snapshot_type_allowed(type(canvas)):
                canvas = None
                raise pickle.PicklingError("user canvas")
            # 复制品不能带「还原时挂回 pyplot」：临时摘掉 canvas.manager（matplotlib 据此写 `_restore_to_pylab`），用完放回
            manager = getattr(canvas, "manager", None)
            canvas.manager = None
            row["blob"] = _snapshot_dumps(fig)
            # 签名最后算：序列化通过 = 图里全是库类型，读 `ax.lines` 之类不会落到用户重写的属性上
            row["signature"] = _structure_signature(fig)
        except Exception:  # noqa: BLE001 —— 快照尽力而为：失败只是退回「告知」
            row["blob"], row["reason"] = None, "unpicklable"
        finally:
            if manager is not None:
                canvas.manager = manager

    def restore(self, fig, selected_occurrence=None):
        """还原 `fig` 最近一次保存时的复制品；返回 `(restored, None)` 或 `(None, 原因)`。绝不抛。"""
        row = self._rows.get(id(fig))
        if row is None or row["ref"]() is not fig:
            return None, "unpicklable"
        if row["reason"] is not None:
            return None, row["reason"]
        if selected_occurrence is not None and selected_occurrence != row["occurrence"]:
            return None, "not_selected_save"
        # 还原会走 `__setstate__` 等类级钩子：脚本在拍摄之后才 patch 的也要拦住（拍摄前的那次比对管不到）
        try:
            if not trusted_surface_intact():
                return None, "matplotlib_patched"
        except Exception:  # noqa: BLE001 —— 比对本身出意外 = 不可信
            return None, "matplotlib_patched"
        try:
            restored = _RestrictedUnpickler(io.BytesIO(row["blob"])).load()
            if _structure_signature(restored) != row["signature"]:
                return None, "structure_mismatch"
        except Exception:  # noqa: BLE001
            return None, "restore_failed"
        for name in _SAVE_MOMENT_STRIP:
            restored.__dict__.pop(name, None)
        return restored, None

    def release(self) -> None:
        """判定完（采用或放弃）之后释放所有快照字节。"""
        self._rows.clear()
        self._total = 0


def savefig_verifier(real_savefig, fig, fname, args, kwargs: dict):
    """`note_savefig_writer` 的 `verify` 闭包：只持有 Figure 的弱引用与 args / kwargs 的浅拷贝（登记会活得比这次调用久，
    不能把 Figure 或调用方后来改动的 kwargs 拖住）；Figure 已死 = 失败。

    闭包本身 `verify()` 是**当场**验证（外来写入：Figure 此刻的内容就是保存时的内容，成败是真值）。
    `verify.deferred()` 给持有者的**惰性**验证用，三值：True 成功、False 失败、None 未知——保存早已发生，
    Figure 此后可能被脚本改坏，所以「Figure 此刻渲染不出、但同一组参数在空白 Figure 上能渲染」说不清当时成败，
    记 None（ADR 0113 的 `intercepted` 语义：safe 没有磁盘，拿不到真值），调用方按已保存算、不推翻选择。
    参数本身造成的失败（与内容无关）仍是 False。"""
    ref, a, kw = weakref.ref(fig), tuple(args), dict(kwargs)

    def verify() -> bool:
        target = ref()
        return target is not None and savefig_verify_render(real_savefig, target, fname, a, kw)

    def deferred():
        target = ref()
        if target is None:
            return False
        if savefig_verify_render(real_savefig, target, fname, a, kw):
            return True
        return None if _content_only_failure(real_savefig, fname, a, kw) else False

    verify.deferred = deferred
    return verify


class _FileEntry:
    """`writers[stem]["files"][目标键]`：最近写它的 Figure（弱引用，调用即取）+ 这次保存是否已确认真能成功。
    持有者自己的写入先不验证（常规路径零渲染），带上 `thunk` 惰性验证；native 与已验证过的 `verified=True`。
    未验证的登记保留它覆盖掉的那条（`prev`）作后备：验证失败 = 那次保存从未落盘，磁盘上仍是上一条的产物
    （Codex #1141 r4306209189）。后备链有界（`_MAX_PREV`），已验证的登记之后不再需要更早的后备。"""

    __slots__ = ("ref", "thunk", "verified", "prev")

    def __init__(self, fig, thunk=None, prev=None):
        self.ref = weakref.ref(fig)
        self.thunk = thunk
        self.verified = thunk is None
        self.prev = None if self.verified else _bounded_prev(prev)

    def __call__(self):
        return self.ref()


_MAX_PREV = 8


def _bounded_prev(prev):
    """截断后备链：遇到已验证的登记就到此为止；未验证的最多留 `_MAX_PREV` 层。"""
    head, node, depth = prev, prev, 0
    while node is not None:
        depth += 1
        if node.verified or depth >= _MAX_PREV:
            node.prev = None
            break
        node = node.prev
    return head


def _resolve_entry(entry):
    """把一条（可能未验证的）登记解析成磁盘上真实的那条：未验证就跑 thunk，失败退到 `prev`；全都不成立 = None。"""
    while entry is not None:
        if entry() is None:
            return None
        if entry.verified:
            return entry
        # None = 未知（保存后 Figure 被改坏，说不清当时成败）：按已保存算，不推翻一次可能成功的保存
        if getattr(entry.thunk, "deferred", entry.thunk)() is not False:
            entry.verified, entry.thunk, entry.prev = True, None, None
            return entry
        entry = entry.prev
    return None


def _canonical_target(files: dict, target: str) -> str:
    """native 保存成功之后：这个目标若与某个已登记目标是磁盘上的同一个文件（硬链接别名、符号链接之外的
    同 inode 写法），并入那个已登记的键。**不替代路径键**——删掉再重建同一路径仍是同一目标，所以只在
    两个键都还对应着存在的文件时才合并；任一 stat 出错 = 不合并。"""
    if target in files:
        return target
    for known in list(files):
        try:
            if os.path.samefile(known, target):
                return known
        except OSError:
            continue
    return target


def _held_still_on_disk(files: dict, held, target) -> bool:
    """持有者在**别的**目标文件上是否仍是最近成功写入者。未验证的登记只在它要挡住一次顶替时才验证
    （`_resolve_entry`）：失败退回它覆盖掉的那条，再看磁盘上的那条是不是持有者。"""
    for t, entry in list(files.items()):
        if t == target:
            continue
        if entry() is not held and entry.verified:
            continue
        real = _resolve_entry(entry)
        if real is None:
            del files[t]
            continue
        files[t] = real
        if real() is held:
            return True
    return False


#: 每个 stem 最多同时记几个写入者的调用表；超出时先淘汰不是持有者、也不是任何目标最近成功写入者的（或已死的）
MAX_SAVEFIG_WRITERS = 16


def note_savefig_writer(
    writers: dict,
    capture: dict,
    capture_source: dict,
    calls: dict,
    extras: dict,
    stem: str,
    fig,
    target: str | None,
    call: dict | None = None,
    extra=None,
    locked: bool = False,
    verify=None,
) -> bool:
    """一次 savefig 的写入者记账与「同一路径最后写入者胜」——三条入口（safe worker、native bridge、浏览器）的同一份
    实现，也是 `calls` / `extras`（`savefig_calls[stem]` / `savefig_extras[stem]`）的唯一写入点。调用方须在
    `capture` 已按先到先得认领之后调用。native 在 `_REAL_SAVEFIG` **成功返回之后**才调用（失败的保存不进任何表）；
    safe / 浏览器不真写盘、拿不到成败，传 `verify`（`savefig_verify_render` 的闭包）：本函数对每次**非持有者**
    的写入（`fig` 不是 `capture[stem]`，且 `target` / `call` 都非 None——不管最终顶不顶替，它都会更新 `files`）
    先调用它，返回 False（原生下这次保存会抛）就整次忽略——不顶替、不更新 `files`、不记该写入者的调用。持有者
    自己的写入照常记账、不立刻验证，但它在 `files` 里的登记带着 `verify` 作为惰性验证：只有当它要**挡住一次顶替**
    （持有者在别的目标上「仍是最近写入者」）时才对挡路的登记验证，成功 = 确认落盘，失败 = 从未落盘，删掉登记。
    所以常规路径零渲染，只有同 stem 多图时才有额外渲染。

    每个 stem 一份状态 `writers[stem]` = `{"files": {目标文件键 -> 最近写它的 Figure（弱引用）},
    "by": {id(fig) -> [弱引用, 该写入者自己的调用表（None = 不知道）, extras]}}`：

    * 调用按**写入者**分表记，规则与 `record_savefig_call` 完全一致（上限、新格式第一次照记、
      `call is None` 整份变「不知道」），与其它写入者交替多少次互不影响；
    * 持有者（`capture[stem]`）的调用表同步成 `calls[stem]` / `extras[stem]` 的副本；
    * 顶替：`fig` 不是持有者，且持有者**不是本 stem 任何目标文件的最近写入者**（含持有者从未写过）。
      护栏（任一不满足都不顶）：`target` 或 `call` 为 None（不知道写的是哪个文件）、`locked`（stem 已进入编辑或已记
      失败）、来源不是 savefig（pyplot 认领永不被顶）、持有者在别的目标文件上仍是最近写入者（它的文件还在磁盘上）；
    * 有界：最多 `MAX_SAVEFIG_WRITERS` 个写入者表，被淘汰者日后重新成为写入者就从新调用开始。

    返回是否顶替了。
    """
    if not stem:
        return False
    entry = writers.setdefault(stem, {"files": {}, "by": {}})
    files, by = entry["files"], entry["by"]
    if target is not None and verify is None:
        target = _canonical_target(files, target)  # native：保存已落盘，磁盘上的真值胜过路径字符串
    held = capture.get(stem)
    foreign = target is not None and call is not None and held is not None and held is not fig
    if foreign and verify is not None and not verify():
        return False
    takes = (
        foreign
        and not locked
        and capture_source.get(stem) == SOURCE_SAVEFIG
        and not _held_still_on_disk(files, held, target)
    )
    mine = by.get(id(fig))
    if mine is None or mine[0]() is not fig:
        mine = by[id(fig)] = [weakref.ref(fig), [], []]
    _record_writer_call(mine, call, extra)
    if target is not None and call is not None:
        # 外来写入前面已验证；持有者自己的写入带上 thunk 惰性验证（verify 为 None = native，保存已成功）
        files[target] = _FileEntry(fig, None if foreign else verify, prev=files.get(target))
    if takes:
        capture[stem] = held = fig
    if held is fig:
        calls[stem] = None if mine[1] is None else list(mine[1])
        if mine[1] is None:
            extras.pop(stem, None)
        else:
            extras[stem] = list(mine[2])
    _trim_writers(entry, held)
    _keep_displaced(entry, held)
    return takes


def _keep_displaced(entry: dict, held) -> None:
    """强引用「仍是本 stem 某个目标文件的最近写入者、却没持有 stem」的图（`entry["kept"]`）。

    写入者表只握弱引用：`a.png` 甲 / `a.pdf` 乙、乙随后 `plt.close` 且出了作用域，乙就被回收——原生两个文件
    都在，捕获里却只剩甲，乙**整张丢了**。留住它，脚本跑完由 `adopt_displaced_writers` 以自己的名字补进来。
    有界：跟着 `_trim_writers`（每 stem 至多 `MAX_SAVEFIG_WRITERS` 个写入者），不再是最近写入者的立即放掉。"""
    latest = {}
    for ref in entry["files"].values():
        fig = ref()
        if fig is not None and fig is not held and id(fig) in entry["by"]:
            latest.setdefault(id(fig), fig)
    entry["kept"] = latest


def adopt_displaced_writers(
    writers: dict,
    capture: dict,
    capture_source: dict,
    calls: dict,
    extras: dict,
    file_stems: dict | None = None,
) -> list[str]:
    """把「写了某个文件、因为 stem 已被别的图持有而没被认领」的图补进捕获表；返回新 stem（产出顺序）。

    不同的图写同一 stem 的不同文件（`panel.png` 甲 / `panel.pdf` 乙）是两张图，不是同一张图的两种格式：
    先认领者持有 `panel`（`note_savefig_writer` 的既有契约，不变），乙以 `fallback_stems` 的规则编号成
    `panel-2`，来源仍是 savefig，带**它自己**的调用表（图幅 / 原件格式按它自己的保存定，ADR 0098）。
    已被认领的（同一张图也持有别的 stem，或 pyplot 兜底已收）按 `claimed_ids` 跳过。三条入口在 pyplot 兜底
    **之前**各调一次，同一个脚本在 safe / native / 浏览器产出同样的 stem。`file_stems`（给了就填）记新 stem →
    它写的文件用的 stem（`panel-2` → `panel`），找原件用（`find_original_artifact(formats=…)`）。"""
    added: list[str] = []
    for stem, entry in list(writers.items()):
        for key, fig in list((entry.get("kept") or {}).items()):
            if id(fig) in claimed_ids(capture):
                continue
            mine = entry["by"].get(key)
            if mine is None or mine[0]() is not fig:
                continue
            name = fallback_stems(capture.keys(), stem, 1)[0]  # stem 已被持有 → `stem-2`、`stem-3`…
            capture[name] = fig
            capture_source[name] = SOURCE_SAVEFIG
            if file_stems is not None:
                file_stems[name] = stem
            if mine[1] is None:
                calls[name] = None
                extras.pop(name, None)
            else:
                calls[name] = list(mine[1])
                extras[name] = list(mine[2])
            added.append(name)
    return added


def _record_writer_call(mine: list, call: dict | None, extra) -> None:
    """把一次调用记进某个写入者自己的表（`record_savefig_call` 同一条规则，`mine[1] is None` = 不知道）。"""
    if mine[1] is None:
        return
    if call is None:
        mine[1], mine[2] = None, []
        return
    tmp = {"k": mine[1]}
    if record_savefig_call(tmp, {"k": mine[0]()}, "k", mine[0](), call):
        mine[2].append(extra)


def _trim_writers(entry: dict, held) -> None:
    """写入者表有界：先淘汰已死的、不是持有者也不是任何目标最近写入者的；还超就按先来后到淘汰非持有者
    （连同它名下的目标记录——有界的代价：它的文件之后算「没人写过」）。持有者永不淘汰。"""
    by, files = entry["by"], entry["files"]
    if len(by) <= MAX_SAVEFIG_WRITERS:
        return
    latest = {id(ref()) for ref in files.values() if ref() is not None}
    for hard in (False, True):
        for key in list(by):
            if len(by) <= MAX_SAVEFIG_WRITERS:
                return
            fig = by[key][0]()
            if fig is None or (fig is not held and (hard or id(fig) not in latest)):
                del by[key]
                for t in [t for t, ref in files.items() if ref() is fig]:
                    del files[t]


def _norm_format(fmt) -> str:
    """格式名的同一性：大小写与 jpeg/jpg、tif/tiff 两对别名不算不同的格式。"""
    fmt = str(fmt or "").lower()
    return {"jpeg": "jpg", "tif": "tiff"}.get(fmt, fmt)


def frame_call(calls, original_artifact: str | None):
    """哪一次 savefig 定义这张图的图幅（ADR 0098 §一）：与写回原件同格式的第一次调用；
    没有原件（或没有同格式的调用）取第一次。没有调用 / 没观察到回 None。"""
    if not calls:
        return None
    if original_artifact:
        ext = _norm_format(os.path.splitext(original_artifact)[1].lstrip("."))
        for call in calls:
            if _norm_format(call.get("format")) == ext:
                return call
    return calls[0]


def frame_extra_artists(calls, extras, call):
    """与 `call` 同一次调用的 `bbox_extra_artists` 对象（`extras` 与 `calls` 逐项对齐）。"""
    if not calls or call is None or not extras:
        return None
    for i, c in enumerate(calls):
        if c is call:
            return extras[i] if i < len(extras) else None
    return None


def savefig_calls_of(calls: dict, stem: str, capture_source: str):
    """描述符的 `savefig_calls`：pyplot 捕获 = `[]`（确实没存过盘）；savefig 捕获
    取记下的调用，没有记录 = None（没观察到，不是「没有」）。"""
    if capture_source == SOURCE_PYPLOT:
        return []
    seq = calls.get(stem)
    return None if seq is None else list(seq)


def savefig_metadata_reader(mfigure):
    """Capture trusted storage/types before script code; never invoke Figure getters."""
    try:
        module = object.__getattribute__(mfigure, "__dict__")
        figure_type, bbox_type = module["Figure"], module["Bbox"]
        array_type = object.__getattribute__(module["np"], "__dict__")["ndarray"]
        figure_fields, bbox_fields = ("get_size_inches", "dpi"), ("p1", "get_points")
        figure_access = figure_fields + (
            "bbox_inches",
            "_dpi",
            "_original_dpi",
            "__getattribute__",
            "__getattr__",
        )
        bbox_access = bbox_fields + ("_points", "__getattribute__", "__getattr__")
        missing = object()
        figure_methods = [
            inspect.getattr_static(figure_type, key, missing) for key in figure_access
        ]
        bbox_methods = [inspect.getattr_static(bbox_type, key, missing) for key in bbox_access]

        if any(method is missing for method in figure_methods[:2] + bbox_methods[:2]):
            return None
    except Exception:  # noqa: BLE001 - unavailable observation setup must not stop a script
        return None

    def read(fig):
        if type(fig) is not figure_type:
            return None
        attrs = object.__getattribute__(fig, "__dict__")
        if any(key in attrs for key in figure_fields) or any(
            inspect.getattr_static(figure_type, key, missing) is not original
            for key, original in zip(figure_access, figure_methods)
        ):
            return None
        bbox = attrs.get("bbox_inches")
        if type(bbox) is not bbox_type:
            return None
        box_attrs = object.__getattribute__(bbox, "__dict__")
        if any(key in box_attrs for key in bbox_fields) or any(
            inspect.getattr_static(bbox_type, key, missing) is not original
            for key, original in zip(bbox_access, bbox_methods)
        ):
            return None
        points = box_attrs.get("_points")
        if type(points) is not array_type or points.shape != (2, 2) or points.dtype.kind != "f":
            return None
        # Figure.get_size_inches returns bbox_inches.p1, not the bbox width/height.
        return points[1].tolist(), attrs.get("_dpi"), attrs.get("_original_dpi", attrs.get("_dpi"))

    return read


#: 只读原件的内容摘要上限：更大的文件不算摘要，也就核验不了，不提供只读降级。
ORIGINAL_DIGEST_LIMIT_BYTES = 64 * 1024 * 1024


def _content_digest(path, size: int):
    if size > ORIGINAL_DIGEST_LIMIT_BYTES:
        return None
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


class SavefigObservations:
    """按保存发生次序记事实，不参与 stem 捕获、图幅选择或编辑。

    occurrence / figure_ordinal 只在**这次执行**内有意义，不是跨重放身份，更不是
    Figure 快照。旧的 savefig_calls 保持原样；这里在其去重之前观察，所以另一张
    同名图与同一张图的后续保存都不会消失。没有额外 draw，也不复制 artist。
    """

    def __init__(self, *, project_root=None, execution_root=None, metadata=None):
        self._roots = [
            (scope, os.path.realpath(root))
            for scope, root in (("project", project_root), ("execution", execution_root))
            if root is not None
        ]
        self.metadata = metadata
        self._records: list[dict] = []
        self._figures: list = []
        self._observed_count = 0
        #: 所有记录（含 PdfPages 的页）共用的次序号；`_observed_count` 只数 savefig 自己的，PdfPages 的页有自己的预算
        self._occurrences = 0
        self._pdf_files: list[dict] = []
        self._complete = True
        #: 记录下标 -> 保存完成那一刻的事实（时刻、字节数、mtime）。**不进 `report()`**：
        #: 观察报告的形状是既有合同，这是 P08a 给「只读原件」做核对用的内部旁证。
        self._saved_facts: dict[int, dict] = {}

    def record(self, fig, fname, stem, call, *, explicit_format=False, backend=None):
        """返回待完成记录的下标。失败只标不完整，不改变原 savefig 的返回 / 异常。

        call 是已有 savefig_call 生成的 JSON 值；不再次解析用户参数或调用 repr。
        stream 不算磁盘产物。native 调用后再 complete，safe 拦截后标 intercepted。
        """
        if is_pdf_pages(fname):
            return self._record_pdf_page(fig, fname, call)
        if not savefig_targets_path(fname):
            return None
        self._observed_count += 1
        if self._observed_count > MAX_SAVEFIG_OBSERVATIONS:
            self._complete = False
            return None
        self._occurrences += 1
        if call is None:
            self._complete = False  # 旧入口没有解析参数；不为观察而新增任意 repr / 用户代码调用
        try:
            options = dict(call or {})  # 下方 JSON 往返复制嵌套值；目的地判据只修正这份格式事实
            size, dpi, original_dpi = self._figure_metadata(fig)
            if size is None:
                self._complete = False
            resolved_dpi = original_dpi if options.get("dpi") == "figure" else options.get("dpi")
            figure_ordinal = next(
                (i + 1 for i, ref in enumerate(self._figures) if ref() is fig), None
            )
            if figure_ordinal is None:
                figure_ordinal = len(self._figures) + 1
                self._figures.append(weakref.ref(fig))
            destination = self._destination(fname, options, explicit_format)
            record = {
                "occurrence": self._occurrences,
                "figure_ordinal": figure_ordinal,
                "stem": None
                if destination["scope"] == "unresolved"
                or os.path.isabs(stem)
                or pathlib.PureWindowsPath(stem).is_absolute()
                else stem,
                "destination": destination,
                "options": options,
                "figure_size_inches": size,
                "figure_dpi": dpi,
                "resolved_dpi": resolved_dpi,
                "backend": "default" if backend is None else "custom",
                "result": "pending",
            }
            # 只复制已知 JSON 值；没有 default=str，未知值不能执行任意 repr。
            encoded = json.dumps(record, ensure_ascii=False, allow_nan=False).encode("utf-8")
            # 完成时 pending → intercepted 最多增加 4 字节，预算连这一步一起算。
            if len(encoded) + 4 > MAX_SAVEFIG_OBSERVATION_BYTES:
                self._complete = False
                return None
            self._records.append(json.loads(encoded))
            return len(self._records) - 1
        except Exception:  # noqa: BLE001 - 观察失败不能改变用户保存的行为，也不记录用户对象 / 路径
            self._complete = False
            return None

    def _figure_metadata(self, fig):
        """`(size_inches, dpi, original_dpi)`；读不到 = `(None, None, None)`（自定义 metadata 不许影响一次原生保存）。"""
        try:
            values = self.metadata(fig) if self.metadata is not None else None
            if values is None:
                raise ValueError("unknown figure metadata")
            size = [self._number(value) for value in values[0]]
            dpi, original_dpi = (self._number(value) for value in values[1:])
            return size, dpi, original_dpi
        except Exception:  # noqa: BLE001
            return None, None, None

    def _pdf_entry(self, pdf):
        """这份 `PdfPages` 的账（找不到就新建；超过 `MAX_PDF_PAGES_FILES` 份返回 None）。"""
        for entry in self._pdf_files:
            if entry["ref"]() is pdf:
                return entry
        if len(self._pdf_files) >= MAX_PDF_PAGES_FILES:
            return None
        entry = {
            "ref": weakref.ref(pdf),
            "destination": None,
            "recorded": 0,
            "complete": True,
        }
        self._pdf_files.append(entry)
        return entry

    def _record_pdf_page(self, fig, pdf, call):
        """写进 `PdfPages` 的一页，记成**那份 PDF 路径**的一次 occurrence（带页号 = 这份 PDF 的第几次写入，从 1 起；ADR 0113 补充条款）。
        与 savefig 的记录一样在真写之前建、由入口按自己的语义补结果（safe `intercepted` / native `saved`、`failed`）。

        目的地的解析规则与 savefig 的完全相同（`_destination`，按此刻的 cwd、scope 取 project / execution /
        unresolved，根外路径不外发），且**以这份 PDF 的第一页为准**（matplotlib 在第一次 savefig 才真正打开文件）。
        缓冲区目标（没有文件名）不记。页自己的预算（`MAX_PDF_PAGES_LEDGER`）与 savefig 的 128 分开：
        一份多页 PDF 不能把别的图的观察挤成不完整（`report()["complete"]` 只管 savefig 自己的）；超出的页只让这份 PDF
        的 `pdf_file_facts()["complete"]` 变假。是**页**的次序事实，不是 Figure 快照，也不是原件：`saved_originals` 不认它。
        """
        name = pdf_pages_filename(pdf)
        if name is None:
            return None
        entry = self._pdf_entry(pdf)
        if entry is None:
            return None
        if entry["recorded"] >= MAX_PDF_PAGES_LEDGER:
            entry["complete"] = False
            return None
        try:
            options = dict(call or {})
            options["format"] = "pdf"  # PdfPages 恒写 pdf
            size, dpi, original_dpi = self._figure_metadata(fig)
            if size is None:
                entry["complete"] = False
            if entry["destination"] is None:
                entry["destination"] = self._destination(name, options, True)
            resolved_dpi = original_dpi if options.get("dpi") == "figure" else options.get("dpi")
            figure_ordinal = next(
                (i + 1 for i, ref in enumerate(self._figures) if ref() is fig), None
            )
            if figure_ordinal is None:
                figure_ordinal = len(self._figures) + 1
                self._figures.append(weakref.ref(fig))
            self._occurrences += 1
            record = {
                "occurrence": self._occurrences,
                "figure_ordinal": figure_ordinal,
                "stem": None,
                "pdf_page": entry["recorded"] + 1,
                "destination": dict(entry["destination"]),
                "options": options,
                "figure_size_inches": size,
                "figure_dpi": dpi,
                "resolved_dpi": resolved_dpi,
                "backend": "default",  # PdfPages 自己传 backend="pdf"，不是用户选了别的后端
                "result": "pending",
            }
            encoded = json.dumps(record, ensure_ascii=False, allow_nan=False).encode("utf-8")
            if len(encoded) + 4 > MAX_SAVEFIG_OBSERVATION_BYTES:
                entry["complete"] = False
                return None
            self._records.append(json.loads(encoded))
            entry["recorded"] += 1
            return len(self._records) - 1
        except Exception:  # noqa: BLE001 - 观察失败不能改变用户保存的行为，也不记录用户对象 / 路径
            entry["complete"] = False
            return None

    def pdf_file_facts(self, pdf) -> dict | None:
        """这份 `PdfPages` 的观察事实 `{scope, path, recorded, complete}`（没有记录过 = None）。给 `PdfPagesLedger.summary`。"""
        for entry in self._pdf_files:
            if entry["ref"]() is pdf and entry["destination"] is not None:
                return {
                    "scope": entry["destination"]["scope"],
                    "path": entry["destination"]["path"],
                    "recorded": entry["recorded"],
                    "complete": bool(entry["complete"]),
                }
        return None

    @staticmethod
    def _number(value):
        if type(value) not in (int, float):
            raise ValueError("unknown numeric metadata")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("non-finite save observation")
        return number

    def _destination(self, fname, call, explicit_format):
        unknown = {"scope": "unresolved", "path": None}
        # 自定义 PathLike 的 __fspath__ 可能有副作用；观察器不多调用它一次。
        if type(fname) not in (str, bytes, pathlib.PosixPath, pathlib.WindowsPath):
            if not explicit_format:
                call["format"] = None
            return unknown
        name = os.fsdecode(fname)
        if not explicit_format:
            if type(fname) is bytes:
                # print_figure 只对 str 推后缀 / 补后缀，bytes 目的地原样、格式问 canvas。
                call["format"] = None
            elif not os.path.splitext(name)[1].lstrip("."):
                # 缺省格式由 canvas.get_default_filetype 决定，不一定是 savefig.format；
                # 不额外调用用户可重载的方法，也不把 rc 的提示当成已确定的文件名。
                call["format"] = None
                return unknown
        target = os.path.realpath(name)  # 必须是调用那一刻的 cwd；不推测原脚本的运行目录
        for scope, root in self._roots:
            try:
                if os.path.commonpath((target, root)) == root:
                    return {
                        "scope": scope,
                        "path": os.path.relpath(target, root).replace(os.sep, "/"),
                    }
            except ValueError:  # Windows 不同盘符
                continue
        return unknown  # 根外路径不发给调用方，不把 basename 当成项目文件身份

    def complete(self, index, result):
        if index is not None:
            self._records[index]["result"] = result
            if result == "saved" and "pdf_page" not in self._records[index]:
                self._saved_facts[index] = self._stat_saved(self._records[index])

    def _original_path(self, record):
        """记录的目的地换回绝对路径：只认记录当时已解出的 `scope` + 相对路径，不扫目录、不按文件名找。"""
        dest = record.get("destination") or {}
        root = dict(self._roots).get(dest.get("scope"))
        rel = dest.get("path")
        if root is None or not rel:
            return None
        return os.path.join(root, *rel.split("/"))

    def _stat_saved(self, record):
        facts = {"at": time.time(), "bytes": None, "mtime_ns": None, "sha256": None}
        path = self._original_path(record)
        if path is not None:
            try:
                st = os.stat(path)
            except OSError:
                return facts
            if stat.S_ISREG(st.st_mode):
                facts["bytes"], facts["mtime_ns"] = st.st_size, st.st_mtime_ns
                facts["sha256"] = _content_digest(path, st.st_size)
        return facts

    def saved_originals(self, fig) -> list[dict]:
        """**这次运行里这个 Figure 对象**成功 save 到磁盘的原产物（P08a 只读降级的唯一依据）。

        认领靠保存当时记下的 Figure 身份（`figure_ordinal` 对应的弱引用就是这个对象）与 `saved`
        结果，不看 basename、不扫目录、不用 mtime 找「最像的」。`intact` 是保存完成时记下的字节数 /
        mtime 与现在磁盘上的对账：脚本之后又覆盖了它（或被别的进程改了），这条就不再是「本次保存的那份」。
        没有活着的 Figure 引用 / 不在项目与执行目录之内（根外路径不外发）的保存一律不返回。
        """
        out = []
        for index, record in enumerate(self._records):
            facts = self._saved_facts.get(index)
            if record.get("result") != "saved" or facts is None or "pdf_page" in record:
                continue  # PdfPages 的页不是这张图的原件
            ordinal = record.get("figure_ordinal")
            if type(ordinal) is not int or not 0 < ordinal <= len(self._figures):
                continue
            if self._figures[ordinal - 1]() is not fig:
                continue
            path = self._original_path(record)
            if path is None:
                continue
            try:
                st = os.stat(path)
                # 字节数 + mtime 挡不住 copy2 / 粗时间戳文件系统上"同长度同时间戳"的替换：再对一遍内容摘要。
                # 保存当时没能算出摘要（超过上限 / 读失败）= 无法核验 = 不提供只读降级（Codex #978）
                intact = (
                    stat.S_ISREG(st.st_mode)
                    and facts["sha256"] is not None
                    and facts["bytes"] == st.st_size
                    and facts["mtime_ns"] == st.st_mtime_ns
                    and _content_digest(path, st.st_size) == facts["sha256"]
                )
            except OSError:
                intact = False
            dest = record["destination"]
            ext = os.path.splitext(dest["path"])[1].lstrip(".").lower()
            out.append(
                {
                    "scope": dest["scope"],
                    "path": dest["path"],
                    "format": (record.get("options") or {}).get("format") or ext or None,
                    "occurrence": record["occurrence"],
                    "saved_at": facts["at"],
                    "bytes": facts["bytes"],
                    "intact": bool(intact),
                }
            )
        return out

    def observation(self, index):
        return None if index is None else json.loads(json.dumps(self._records[index]))

    def report(self):
        return {
            "version": 1,
            "complete": self._complete,
            "observed_count": self._observed_count,
            "records": json.loads(json.dumps(self._records)),
        }


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


def claimed_ids(capture) -> set[int]:
    """已被 savefig（或别的来源）认领进 `capture` 的 Figure 的 id——`collect_pyplot_figures` 去重与
    `ShownFigures` 执行上限用的是**同一个**判据。"""
    return {id(f) for f in (capture or {}).values()}


class ShownFigures:
    """`plt.show()` 那一刻还开着的 pyplot Figure 的账（有界）。

    兜底（`collect_pyplot_figures`）只看脚本**跑完**时还活着的 Figure；而 `plt.show(); plt.close()`
    （或循环里逐张 show + close）的脚本在跑完时一张都不剩——交互用户明明看到过每一张。
    show 是「我画完了，看看吧」，所以 show 当时开着的图记下来，兜底时与跑完仍活着的一起补进去。

    有界：最多强引用 `limit` 张（超出的只在 `overflow` 里计数，Figure 本身不留——
    `for i in range(10000): ...; plt.show(); plt.close()` 不能把内存烧光）；身份判重用弱引用集合，
    同一张图 show 多次只记一次。上限只限 pyplot 兜底：此刻已被 savefig 认领的图不占名额（`note(capture=)`，
    判据 `claimed_ids`），之后才认领的会在下一次 show 时腾出名额。不改 Gcf 的活动图（不碰 `plt.figure(num)`），脚本之后的 `plt.gcf()` 不变。
    """

    def __init__(self, limit: int = MAX_PYPLOT_FALLBACK):
        import weakref

        self.limit = max(0, int(limit))
        self.kept: list = []
        #: 超出上限、没留引用的张数（含此刻仍开着的）；跑完时仍开着的那些由 `collect_pyplot_figures` 自己数，
        #: 用 `overflow_closed` 从这里扣掉，免得同一张图丢两次。
        self.overflow = 0
        self._seen = weakref.WeakSet()
        self._overflowed = weakref.WeakSet()

    def note(self, plt, capture: dict | None = None) -> None:
        """记下此刻 Gcf 里所有开着的 Figure（show 调用时）。`capture` 是 savefig 已认领的图（见 `claimed_ids`）。"""
        claimed = claimed_ids(capture)
        # 已被 savefig 认领的不必再留强引用（capture 自己握着），腾出名额给兜底的图
        self.kept = [f for f in self.kept if id(f) not in claimed]
        for manager in list(plt._pylab_helpers.Gcf.get_all_fig_managers()):
            fig = manager.canvas.figure
            if fig in self._seen:
                continue
            self._seen.add(fig)
            if id(fig) in claimed:
                continue
            if len(self.kept) < self.limit:
                self.kept.append(fig)
            else:
                self.overflow += 1
                self._overflowed.add(fig)

    def overflow_closed(self, plt, capture: dict | None = None) -> int:
        """超出上限、且跑完时已经关掉、也没被 savefig 认领的张数
        （仍开着的归 `collect_pyplot_figures` 数，认领了的已经在 `capture` 里）。"""
        claimed = claimed_ids(capture)
        open_ids = {id(m.canvas.figure) for m in plt._pylab_helpers.Gcf.get_all_fig_managers()}
        accounted = sum(1 for f in list(self._overflowed) if id(f) in open_ids or id(f) in claimed)
        return max(0, self.overflow - accounted)


def collect_pyplot_figures(
    capture: dict,
    script_stem: str,
    plt,
    limit: int = MAX_PYPLOT_FALLBACK,
    shown: ShownFigures | None = None,
) -> tuple[list[str], int]:
    """把脚本里 show 过或跑完仍活着、且没被 savefig 认领的 pyplot Figure 补进 `capture`。

    就地修改 `capture`（stem → Figure，保持产出顺序），返回
    `(新增的 stem 列表, 因上限被丢掉的张数)`——调用方据此分辨「这张图有没有
    原始产物」，以及要不要跟用户说「还有 N 张没显示」。

    去重按 `id(figure)`：`fig.savefig("a.pdf")` 之后那张图还在 pyplot 的
    figure 管理器里，不去重就会同一张图挂两个 stem，用户看到两个一模一样的
    面板、改一个另一个不动。

    `plt.get_fignums()` 的顺序即产出顺序（pyplot 的 Gcf 按创建先后维护），
    stem 的序号只由「本次捕获里的第几张」决定，与 figure 号无关。

    `shown`（safe worker 传；见 `ShownFigures`）：`plt.show()` 时开着、之后被 `plt.close()` 的图排在
    跑完仍活着的图**之前**（它们先于脚本结尾出现），同一张图仍只一个 stem、仍受同一个上限。
    不传 = 只看跑完仍活着的（浏览器 playground 与 native 的既有行为）。
    """
    seen = claimed_ids(capture)
    pending = []
    for fig in shown.kept if shown is not None else ():
        if id(fig) in seen:
            continue
        seen.add(id(fig))
        pending.append(fig)
    for num in plt.get_fignums():
        fig = plt.figure(num)
        if id(fig) in seen:
            continue
        seen.add(id(fig))
        pending.append(fig)
    dropped = max(0, len(pending) - max(0, int(limit)))
    if dropped:
        pending = pending[: max(0, int(limit))]
    if shown is not None:
        dropped += shown.overflow_closed(plt, capture)
    stems = fallback_stems(capture.keys(), script_stem, len(pending))
    for stem, fig in zip(stems, pending):
        capture[stem] = fig
    return stems, dropped


#: `PdfPages` 账本的强引用上限（张）。与 pyplot 兜底的 8 张分开设：一份多页 PDF 动辄几十页，
#: 8 张会把「三十页的 all_samples.pdf」截成一个令人困惑的前八页。超出的只计数、不留 Figure。
MAX_PDF_PAGES_LEDGER = 64
#: 账本里最多同时记几份不同的 PdfPages（每份有一个强引用的 PdfPages 对象；同样有界）。
MAX_PDF_PAGES_FILES = 32


def pdf_pages_filename(pdf) -> str | None:
    """`PdfPages` 实例写的那个文件名（str，原样）；缓冲区 / 取不到 / 不是 PdfPages 返回 None。

    读 PdfPages 的私有 `_filename`，不包 `PdfPages.__init__`：包 `__init__` 要在用户 import
    `matplotlib.backends.backend_pdf` 那一刻装 import 钩子（native bridge 启动阶段绝不 import matplotlib，
    钩子要另装一套），而 `_filename` 在 matplotlib 3.8–3.11 都是 `__init__` 的第一行赋值；
    读不到就退回脚本名——`tests/test_capture_pdfpages.py` 在 CI 的每个 matplotlib 版本上钉着这个属性仍在。
    """
    mod = sys.modules.get("matplotlib.backends.backend_pdf")
    cls = getattr(mod, "PdfPages", None)
    if cls is None or not isinstance(pdf, cls):
        return None
    name = getattr(pdf, "_filename", None)
    if isinstance(name, (str, bytes, os.PathLike)):
        try:
            return os.fsdecode(name)
        except (TypeError, ValueError):
            return None
    return None


def is_pdf_pages(obj) -> bool:
    """`obj` 是不是 matplotlib 的 `PdfPages`（只看已经载入的模块，不主动 import matplotlib）。"""
    cls = getattr(sys.modules.get("matplotlib.backends.backend_pdf"), "PdfPages", None)
    return cls is not None and isinstance(obj, cls)


class PdfPagesLedger:
    """写进 `PdfPages`（`pdf.savefig(fig)` → `fig.savefig(<PdfPages>, format="pdf")`）的 Figure 的账（有界）。

    `plt.close(fig)` 之后图就不在 pyplot 里，而 `savefig` 的第一个参数不是路径，stem 取不出来：
    三种常见写法（逐页关图、结尾 `plt.close("all")`、一直开着）里前两种以前一张都捕获不到。
    保存那一刻记下来，脚本结束时由 `adopt_pdf_pages` 一起补进捕获表。

    * 按 PdfPages 实例分组，组内按写入顺序；**同一张 Figure 只认一次**（多页写同一张、写进两份 PDF 都只留第一次）。
    * 有界：强引用 Figure ≤ `limit`，PdfPages 实例 ≤ `MAX_PDF_PAGES_FILES`；超出的只在 `omitted` 里计数。
    * 来源仍是 pyplot：多页 PDF **不是**任何一张图的原件，绝不能被当成写回目标（见 `adopt_pdf_pages`）。
    """

    def __init__(self, limit: int = MAX_PDF_PAGES_LEDGER):
        self.limit = max(0, int(limit))
        #: 每份 PdfPages 一组：{"pdf", "name", "pages", "figs", "adopted", "stems", "omitted"}
        self.groups: list[dict] = []
        self._kept_ids: set[int] = set()
        #: 超出上限、没留引用的（不同的）Figure 张数
        self.omitted = 0
        #: 其中属于「第 `MAX_PDF_PAGES_FILES` 份之后的 PdfPages」的（每次写入算一次，不判重：没有组可挂）
        self.unlisted = 0

    def _group(self, pdf) -> dict | None:
        for group in self.groups:
            if group["pdf"] is pdf:
                return group
        if len(self.groups) >= MAX_PDF_PAGES_FILES:
            return None
        group = {
            "pdf": pdf,
            "name": pdf_pages_filename(pdf),
            "pages": 0,
            "figs": [],
            #: 按页序：每一页写的是哪张 Figure（`id`，已留强引用的才有；超出上限没留的页是 None）
            "page_ids": [],
            #: 按页序的 stem（`adopt_pdf_pages` 认领后才填；同一张图写多页时 stem 重复；没留的页是 None）
            "page_stems": [],
            "adopted": 0,
            "stems": [],
            "omitted": 0,
        }
        self.groups.append(group)
        return group

    def note(self, pdf, fig) -> int | None:
        """一次**成功**写进 `pdf` 的保存（失败的保存没有这一页，不记）。返回这一页的页号（从 1 起）；账满了没地方记返回 None。"""
        group = self._group(pdf)
        if group is None:
            # 超过 PdfPages 份数上限：没有组可挂，计进「不点名」的溢出（summary 里单列一条）
            self.omitted += 1
            self.unlisted += 1
            return None
        group["pages"] += 1
        page = group["pages"]
        if id(fig) in self._kept_ids:
            group["page_ids"].append(id(fig))
            return page
        if sum(len(g["figs"]) for g in self.groups) >= self.limit:
            self.omitted += 1
            group["omitted"] += 1
            group["page_ids"].append(None)
            return page
        self._kept_ids.add(id(fig))
        group["figs"].append(fig)
        group["page_ids"].append(id(fig))
        return page

    def summary(self, observations: "SavefigObservations | None" = None) -> list[dict]:
        """`[{name, pages, captured, stems, path, scope, page_stems, complete, omitted?}]`，每份写过页的 PDF 一项
        （包括它的图都已被 savefig 认领、`captured == 0` 的：写回守卫要知道**所有**脚本写过的多页 PDF；界面那句话
        只提 `captured > 0` 或有省略的）。

        `name` / `pages` / `captured` / `stems` / `omitted` 是界面那句话用的（PR1）；`path` / `scope` 是这份 PDF 的
        目的地（`observations` 按 ADR 0113 的规则解出：项目相对路径，`unresolved` 时 `path` 为 null、不外发根外路径），
        `page_stems` 按页序（同一张图写多页时 stem 重复，没收进来的页是 null），`complete` = 每一页都有 stem 且都记上了观察。
        写回守卫（`app._own_write_back_targets`）认 `project` 与 `execution` 的 `path`（按项目目录里的同名位置核对）。
        """
        out = []
        for g in self.groups:
            facts = observations.pdf_file_facts(g["pdf"]) if observations is not None else None
            item = {
                "name": os.path.basename(g["name"]) if g["name"] else None,
                "pages": g["pages"],
                "captured": len(g["stems"]),
                "stems": list(g["stems"]),
                "path": facts["path"] if facts else None,
                "scope": facts["scope"] if facts else "unresolved",
                "page_stems": list(g["page_stems"]),
                # 缓冲区目标没有文件，不要求有观察；有文件名的要每一页都记上
                "complete": not g["omitted"]
                and (
                    g["name"] is None
                    or (facts is not None and facts["complete"] and facts["recorded"] == g["pages"])
                ),
            }
            if g["omitted"]:
                item["omitted"] = g["omitted"]
            out.append(item)
        if self.unlisted:
            # 份数超限的 PDF 没有组：不点名的一条，只报省略数
            out.append(
                {
                    "name": None,
                    "pages": self.unlisted,
                    "captured": 0,
                    "stems": [],
                    "path": None,
                    "scope": "unresolved",
                    "page_stems": [],
                    "complete": False,
                    "omitted": self.unlisted,
                }
            )
        return out


def adopt_pdf_pages(
    capture: dict, capture_source: dict, ledger: PdfPagesLedger | None, script_stem: str
) -> list[str]:
    """把 `ledger` 里还没被认领的 Figure 补进 `capture`（就地），返回新增的 stem（保序）。

    先于 pyplot 兜底调用（三条入口同一个位置）：一张图既写进了 PdfPages、又还开在 pyplot 里时，
    只有一个 stem，而且是 PDF 文件名而不是脚本名。stem 取 PDF 的文件名（`all_samples.pdf` →
    `all_samples`、`all_samples-2`…按页序，重名的第二份 PDF 接着往下编）；缓冲区 / 取不到文件名
    退回 `script_stem`。来源一律 `SOURCE_PYPLOT`：**多页 PDF 不是这些图的原件**，
    `find_original_artifact` / 写回只对 savefig 来源的 stem 查，这里绝不登记 savefig 来源。
    """
    if ledger is None:
        return []
    claimed = claimed_ids(capture)
    added: list[str] = []
    for group in ledger.groups:
        fresh = []
        for fig in group["figs"][group["adopted"] :]:
            if id(fig) in claimed:
                continue
            claimed.add(id(fig))
            fresh.append(fig)
        group["adopted"] = len(group["figs"])
        if not fresh:
            _fill_page_stems(group, capture)
            continue
        base = ""
        if group["name"]:
            base = os.path.splitext(os.path.basename(group["name"]))[0]
        stems = fallback_stems(capture.keys(), base or script_stem, len(fresh))
        for stem, fig in zip(stems, fresh):
            capture[stem] = fig
            capture_source[stem] = SOURCE_PYPLOT
        group["stems"].extend(stems)
        added.extend(stems)
        _fill_page_stems(group, capture)
    return added


def _fill_page_stems(group: dict, capture: dict) -> None:
    """页序的 stem：每一页写的那张 Figure 现在在捕获表里叫什么（别处先认领的图用它自己的 stem）。"""
    by_id: dict[int, str] = {}
    for stem, fig in capture.items():
        by_id.setdefault(id(fig), stem)
    group["page_stems"] = [None if i is None else by_id.get(i) for i in group["page_ids"]]


#: 输入观察（统一实施包 U09，ADR 0070）：脚本经 Python 的 `open`（与 numpy 的 `DataSource.open`）读到的项目内文件最多记这么多条；
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


def interpreter_dirs_within(root: str) -> tuple[str, ...]:
    """**这个进程的解释器**自己的目录里、严格落在项目根 `root`（已 realpath）之内的那些（realpath）。

    主语：观察器所在的进程（safe worker / native bridge 里跑用户脚本的那个解释器）。项目自带的 `.venv`
    放在项目根里是 ADR 0057 首开第 4 条的标准形态——它的 site-packages 里的扩展模块、字体、mplstyle
    被库在 import / 初始化时读到，不是脚本的数据输入，也不是用户的本地模块（QA 2026-09-24 PATH-B2：
    一个脚本 41–67 条，把 256 条的预算挤满、真数据被挤出回执）。候选：`sys.prefix` / `base_prefix` /
    `exec_prefix` / `base_exec_prefix` 与 `sysconfig` 的 purelib / platlib。

    只收**严格在项目根之内**的：等于项目根、或把项目根包在里面的前缀（项目放在某个 conda 环境目录底下）
    若也排除，项目里的每一个数据文件都会跟着消失——那时宁可多记，不能全丢。"""
    candidates = [
        getattr(sys, name, None)
        for name in ("prefix", "base_prefix", "exec_prefix", "base_exec_prefix")
    ]
    try:
        import sysconfig

        paths = sysconfig.get_paths()
        candidates += [paths.get("purelib"), paths.get("platlib")]
    except Exception:  # noqa: BLE001 —— 拿不到就只用 sys 的四个前缀
        pass
    out: list[str] = []
    for cand in candidates:
        if not isinstance(cand, str) or not cand:
            continue
        try:
            real = os.path.realpath(cand)
        except (OSError, ValueError):
            continue
        if real in out or not _within(real, root):
            continue
        if os.path.normcase(real) == os.path.normcase(root.rstrip(os.sep) or root):
            continue
        out.append(real)
    return tuple(out)


class InputObserver:
    """记下脚本执行期间经 `builtins.open` / `io.open` / `Path.open` / numpy 的 `DataSource.open` **以只读模式成功打开**的、落在项目根之内的
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
        #: 实际装上了的观察通道（`report()` 的 `channels`）：numpy 没载入时就没有 `numpy_datasource`
        self._channels: list[str] = []
        #: numpy 通道的还原（装上了才有）：`install()` 时已载入就当场装，否则由调用方在 numpy 载入那一刻补
        #: （`observe_numpy_datasource`，native bridge 的 import 钩子）
        self._restore_numpy = None
        #: `paused()` 的嵌套深度，按线程记：只有进入 `paused()` 的那个线程自己的读（savefig 簿记算摘要、
        #: 屏障里出预览）不是脚本的输入；屏障期间用户脚本自己起的后台线程仍在跑，它的读是输入，不能一并压掉
        self._pause = threading.local()
        #: 解释器自己落在项目根里的目录（项目 `.venv`）：库文件不是数据输入，也不占预算（PATH-B2）
        try:
            self._interpreter_dirs = interpreter_dirs_within(os.path.realpath(self.project_root))
        except Exception:  # noqa: BLE001 —— 观察器绝不影响脚本
            self._interpreter_dirs = ()

    # ---- 记账 ----
    def _note(self, file) -> None:
        if getattr(self._pause, "depth", 0):
            return
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
            if any(_within(real, d) for d in self._interpreter_dirs):
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
        """装上 open 的观察包装（三处 Python open + numpy 的 `DataSource.open`）；返回卸载函数。"""
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

        self._channels = ["python_open"]
        self.observe_numpy_datasource(sys.modules.get("numpy.lib._datasource"))

        def uninstall() -> None:
            builtins.open = real_open
            io.open = real_io_open
            pathlib.Path.open = real_path_open
            if self._restore_numpy is not None:
                self._restore_numpy()
                self._restore_numpy = None

        self._uninstall = uninstall
        return uninstall

    def observe_numpy_datasource(self, module) -> None:
        """装上 numpy 的 `DataSource.open` 通道（`np.loadtxt` / `np.genfromtxt`）；`module` 是 `numpy.lib._datasource`，None / 已装 = 什么都不做。

        它的文件打开器在 numpy 载入时就绑好了**原来的** `io.open`，三处 open 包装都看不见它读了什么。记的是它**实际
        打开的那个文件**（文件对象的 `.name`，gzip / bz2 也有）——相对路径由它按自己的 destpath 解，拿请求里的字符串去猜
        会记错文件。只包已载入的 numpy，不替脚本 import：safe worker 装观察器时 numpy 早已载入；native bridge 装在用户代码
        之前，由 import 钩子在 numpy 载入那一刻调这里。"""
        if self._restore_numpy is not None:
            return
        datasource = getattr(module, "DataSource", None)
        real_ds_open = getattr(datasource, "open", None)
        if real_ds_open is None:
            return
        observer = self

        def observed_datasource_open(self_ds, path, mode="r", *args, **kwargs):
            fh = real_ds_open(self_ds, path, mode, *args, **kwargs)
            if _readonly_mode(mode):
                observer._note(getattr(fh, "name", None))
            return fh

        datasource.open = observed_datasource_open
        self._channels.append("numpy_datasource")

        def restore() -> None:
            datasource.open = real_ds_open

        self._restore_numpy = restore

    def uninstall(self) -> None:
        if self._uninstall is not None:
            self._uninstall()
            self._uninstall = None

    @contextlib.contextmanager
    def paused(self):
        """这一段里的打开不记账：引擎自己读文件（不是脚本的输入）。可嵌套；只影响记账，open 照常。

        按线程生效：只压掉进入本上下文的这个线程；用户脚本的其他线程在此期间的读照常记账。"""
        self._pause.depth = getattr(self._pause, "depth", 0) + 1
        try:
            yield
        finally:
            self._pause.depth -= 1

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
            "channels": list(self._channels) or ["python_open"],
            "unobserved": list(INPUT_OBSERVER_UNOBSERVED),
            "truncated": bool(self.truncated),
            "files": files,
            "local_modules": list(local_modules or []),
        }


def _readonly_mode(mode) -> bool:
    if not isinstance(mode, str):
        return False
    return "r" in mode and not any(c in mode for c in "+wxa")


#: 退出归因认的探路调用：**savefig 成功写出文件之后必然为真 / 不抛**的那些，且必须**解析到**标准库的这些 API
#: （看 import 别名与属性链，不是只比名字）。`isdir` / `islink` / `is_dir` / `is_symlink` / `ismount` 在真实文件语义下
#: savefig 之后同样是 False——退出不是守卫造成的，故意不在表里；用户自己的同名函数 / 类方法一律不认（证明不了就不归因）。
_PROBE_FUNCS = frozenset(
    {
        "os.path.exists", "os.path.lexists", "os.path.isfile", "os.path.getsize",
        "os.path.getmtime", "os.path.getctime", "os.path.getatime", "os.stat", "os.lstat",
    }
)  # fmt: skip
#: `pathlib.Path(...)` 对象上的探路方法
_PROBE_PATH_METHODS = frozenset({"exists", "is_file", "stat", "lstat"})
_PATH_CLASSES = frozenset(
    {"pathlib.Path", "pathlib.PurePath", "pathlib.PosixPath", "pathlib.WindowsPath"}
)


def exit_controlled_by_probe(source: str, exit_line: int | None) -> bool:
    """`exit_line` 那一行的退出，是不是由"落空的 savefig 目标探测"决定的。纯函数，永不抛。

    只在能证明时成立：该行位于某个 `if` / `while` / 三元 / `and`-`or` 链的分支之下，且**对分支而言必要**的条件
    就是对 savefig 目标的探测——
    * `if not P: <退出>`、`while not P:`、`if A and not P: …`（合取里有 `not P`：P 为真则分支不进）；
    * `if P: … else: <退出>`（else 侧：只有条件整体就是 `P` 才必要）；
    * `P or <退出>`（先于退出的任一操作数是 `P`）、`not P and <退出>`；
    * 三元同 if。
    `P` 是对 `_PROBE_CALL_NAMES` 的调用，且被探测的对象与脚本里某次 `savefig(…)` 的目标**是同一个**（同一常量路径、
    或同一个表达式 / 变量；`Path(x)` / `str(x)` / `os.fspath(x)` 视同 `x`）。
    不成立：无条件退出；`not P or 别的`（别的独立为真也进分支）；`or` 的非必要析取；探测的是别的文件；
    探测结果先存进变量再判断；源码里没有 savefig 目标；语法错。判不出一律 False——保留原分类，宁可不归因。"""
    if not isinstance(source, str) or not isinstance(exit_line, int):
        return False
    try:
        import ast  # noqa: PLC0415

        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return False

    wrappers = {"Path", "PurePath", "PosixPath", "WindowsPath", "str", "fspath"}

    # 只被赋值过一次的常量字符串变量按常量算：`o = 'o.png'; savefig(o); exists('o.png')` 是同一个目标
    assigned: dict[str, list] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    assigned.setdefault(t.id, []).append(node.value)
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign, ast.NamedExpr)):
            t = node.target
            if isinstance(t, ast.Name):
                assigned.setdefault(t.id, []).append(None)
    constants = {
        name: values[0].value
        for name, values in assigned.items()
        if len(values) == 1
        and isinstance(values[0], ast.Constant)
        and isinstance(values[0].value, str)
    }

    def target_key(node):
        while (
            isinstance(node, ast.Call) and node.args and len(node.args) == 1 and not node.keywords
        ):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
            if name not in wrappers:
                break
            node = node.args[0]
        if isinstance(node, ast.Name) and node.id in constants:
            return ("c", os.path.normpath(constants[node.id]))
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return ("c", os.path.normpath(node.value))
        return ("d", ast.dump(node))

    savefig_keys = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "savefig"
        ):
            arg = node.args[0] if node.args else None
            for kw in node.keywords:
                if kw.arg in ("fname", "path"):
                    arg = kw.value
            if arg is not None:
                savefig_keys.add(target_key(arg))
    if not savefig_keys:
        return False

    # import 别名：只认 `import os` / `import os.path as osp` / `from os import path` / `from os.path import exists`
    # 这类绝对导入；被用户自己定义 / 赋值 / 当参数的名字一律不解析（它不再指向标准库）
    imports: dict[str, str] = {}
    shadowed: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for al in node.names:
                if al.asname:
                    imports[al.asname] = al.name
                else:
                    imports[al.name.split(".")[0]] = al.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            for al in node.names:
                imports[al.asname or al.name] = f"{node.module}.{al.name}"
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            shadowed.add(node.name)
            if not isinstance(node, ast.ClassDef):
                shadowed.update(a.arg for a in ast.walk(node.args) if isinstance(a, ast.arg))
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            shadowed.add(node.id)

    def dotted(node) -> str | None:
        if isinstance(node, ast.Name):
            return None if node.id in shadowed else imports.get(node.id)
        if isinstance(node, ast.Attribute):
            base = dotted(node.value)
            return f"{base}.{node.attr}" if base else None
        return None

    def is_path_object(node) -> bool:
        """`node` 是 `pathlib.Path(...)` 的实例：直接构造，或只赋值过一次、值就是这样一次构造的变量。"""
        if isinstance(node, ast.Name) and len(assigned.get(node.id, ())) == 1:
            node = assigned[node.id][0]
        return isinstance(node, ast.Call) and dotted(node.func) in _PATH_CLASSES

    def is_probe(node) -> bool:
        """`node` 就是一次对 savefig 目标的探路调用，且 callee 解析到标准库的 os.path / os.stat / pathlib.Path。"""
        if not isinstance(node, ast.Call):
            return False
        fn = node.func
        if dotted(fn) in _PROBE_FUNCS and node.args:  # os.path.exists(o) / os.stat(o)
            return target_key(node.args[0]) in savefig_keys
        if (
            isinstance(fn, ast.Attribute)
            and fn.attr in _PROBE_PATH_METHODS
            and not node.args
            and is_path_object(fn.value)
        ):  # Path(o).exists() / out.exists()
            return target_key(fn.value) in savefig_keys
        return False

    def negated_probe(node) -> bool:
        return (
            isinstance(node, ast.UnaryOp)
            and isinstance(node.op, ast.Not)
            and is_probe(node.operand)
        )

    def needs_probe_false(test) -> bool:
        """条件为真 ⇒ 探测落空（探测落空是进入 body 的必要条件）。"""
        if negated_probe(test):
            return True
        if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
            return any(needs_probe_false(v) for v in test.values)
        return False

    def contains(node, line: int) -> bool:
        return getattr(node, "lineno", 0) <= line <= getattr(node, "end_lineno", 0)

    def walk(node, line: int) -> bool:
        for child in ast.iter_child_nodes(node):
            if not contains(child, line):
                continue
            if isinstance(node, (ast.If, ast.While, ast.IfExp)) and not contains(node.test, line):
                in_orelse = child in getattr(node, "orelse", []) or child is getattr(
                    node, "orelse", None
                )
                if in_orelse:
                    if is_probe(node.test):  # 条件整体是 P：走 else ⇒ P 为假
                        return True
                elif needs_probe_false(node.test):
                    return True
            if isinstance(node, ast.BoolOp) and child in node.values:
                idx = node.values.index(child)
                before = node.values[:idx]
                if isinstance(node.op, ast.Or) and any(is_probe(v) for v in before):
                    return True  # 走到后面的操作数 ⇒ 前面的 P 为假
                if isinstance(node.op, ast.And) and any(negated_probe(v) for v in before):
                    return True  # 走到后面 ⇒ `not P` 为真 ⇒ P 落空
            if walk(child, line):
                return True
        return False

    try:
        return walk(tree, exit_line)
    except RecursionError:
        return False


class BlockedFileOps:
    """safe worker 的守卫**确实拦下**了哪些关键文件操作，以及脚本随后有没有去看被它们影响的路径（P01a）。

    守卫（删除 / 改名 / `write_text` 对真实图库、以及被拦截不落盘的 `savefig` 路径目标）是 safe 档的
    保护，原样保留；它们靠「悄悄跳过」工作，脚本看不到异常。这本账补上缺的那半句终局事实：
    **跳过之后脚本读到了什么**。判据一条：被拦下的操作影响了路径 P，此后脚本打开（读，含 `r+`/`a+`）
    或 `os.stat`（`exists` / `isfile` / `Path.exists`）P，就是「这个脚本随后观察到的世界与直接用 Python
    运行不同」——`material()` 非空，build 以 `safe_file_operation_blocked` 结束，不记成完整成功。

    * **只算拦下之后的观察**：先于阻断的读、读不相干的路径、拦下了但从没回读（脚本只是顺手删个过期
      输出），都不算——这些脚本照旧成功。
    * **脚本自己重写（`w` / `x` 打开）会清掉那条路径的嫌疑**：此后读到的是它自己写的，与直接运行一致。
    * **拦下上百次也不丢**：事件账有上限（`MAX_EVENTS`），超出的操作并入按 (op, kind) 的溢出路径集合
      （去重、有上限，再超则粗化为父目录，最后记「丢失」＝此后任何观察都算命中）；回读第 201+ 个被拦路径
      同样判失败，拦下几百次但从不回读照旧成功。
    * **事实在带外账本里，不在异常里**：脚本 `except Exception: pass` 吞掉什么都洗不掉它。
    * **盲区写在明处（判不出就别判）**：C 扩展直接 `fopen`、子进程、`os.access`、`os.path.samefile` 等
      不在包装清单里的状态查询观察不到——这些不会产生 `material()`，也就**不宣称**它们干净；
      账本只回答「观察到了」，不回答「没观察到所以没事」。被拦截的 `savefig`
      目标的 `exists` / `getsize` 这类 stat 不进 `material()`：`savefig(p); print(os.path.exists(p))`
      是常见的校验打印，据此判失败会把原本能出图的脚本变成失败。
    * **三档（V2 PR3a）**：`material()` 非空 = 确定偏差（`deviated`，build 失败）；没有确定偏差、但记过弱信号
      （`suspects()`：savefig 目标被 stat、stat / `os.open` / 改名落空、`listdir` / `scandir`（`glob` 经它）计数受影响、
      改名的源就是被拦截的 savefig 目标）= 疑似（`suspect`，build 照常成功、回执标注）；都没有 = `none_observed`
      （只表示这些通道没看到，盲区仍在）。弱信号只存 (kind, via) 的计数。
    * **savefig 目标衍生的 `FileNotFoundError` 是守卫造成的**：直接运行时 `savefig(p)` 之后 `p` 必然存在，
      所以异常链里指向被拦截 savefig 目标（或被拦改名的路径）的 `FileNotFoundError` 一旦到达终局，
      `explains(exc)` 把它升为确定偏差——脚本自己吞掉的那些落在弱信号档。
    * 记账只存**操作类别与次数**，路径只在进程内用来比对，不出进程、不进 message / 诊断。

    `install()` 装的 open / `os.stat` / `os.path.exists` 系包装在没有任何阻断记录时是一次属性读取就返回；`stop()`
    （脚本跑完）之后整本账变成惰性——引擎自己后续写预览、读字体不会被误算。
    """

    MAX_EVENTS = 200
    #: 超过 `MAX_EVENTS` 之后的「溢出账」：每个 (op, kind) 一本，只存受影响路径的集合（去重）。
    #: 路径数超过 `MAX_SPILL_PATHS` 就粗化成它们的父目录；父目录也超过就记「丢失」，
    #: 此后任何脚本观察都算命中（宁可误报也不假成功；只发生在拦下上千个互不相干的目录之后）。
    MAX_SPILL_PATHS = 2048

    #: 操作类别（`kind`）的闭集：message 与载荷只出现这些，不出现路径。
    KIND_RENAME = "rename"
    KIND_DELETE = "delete"
    KIND_DELETE_DIR = "delete_dir"
    KIND_WRITE = "write"
    KIND_SAVEFIG = "savefig"

    #: 账本里 `op` 的闭集：守卫记账时用的名字（worker 的 `_guard_delete` / `Path.unlink` / `write_text` / `savefig`）。
    #: 回执只收这些，开放字符串（路径）一律丢弃；加守卫入口时同步（`tests/test_worker_fidelity.py` 对照 worker 源码）。
    OPS = (
        "Path.unlink",
        "Path.write_text",
        "os.remove",
        "os.unlink",
        "os.rmdir",
        "os.rename",
        "os.replace",
        "shutil.rmtree",
        "shutil.move",
        "savefig",
    )

    #: 弱信号（`suspects()`）的 `via` 闭集：只有这些字符串会出现在回执 / 诊断里。
    VIA_STAT = "stat"  # 脚本查了被拦截 savefig 目标的状态（`exists` / `getsize` / `Path.stat`…）
    VIA_PROBE_FAILED = (
        "probe_failed"  # stat / `os.open` 落在受影响路径上 FileNotFoundError（可能被脚本吞了）
    )
    VIA_LISTING = (
        "listing"  # `listdir` / `scandir`（`glob` / `os.walk` 经它）列了受影响路径所在的目录
    )
    VIA_MOVE_FAILED = "move_failed"  # 删除 / 改名 / 移动落在受影响路径上 FileNotFoundError
    VIA_RENAME_SRC = "rename_src"  # 被守卫拦下的改名，源就是被拦截的 savefig 目标
    SIGNAL_VIAS = (VIA_STAT, VIA_PROBE_FAILED, VIA_LISTING, VIA_MOVE_FAILED, VIA_RENAME_SRC)

    #: `summary()["level"]` 的闭集。
    LEVEL_DEVIATED = "deviated"
    LEVEL_SUSPECT = "suspect"
    LEVEL_NONE_OBSERVED = "none_observed"
    LEVELS = (LEVEL_DEVIATED, LEVEL_SUSPECT, LEVEL_NONE_OBSERVED)

    MAX_COUNT = 9999

    def __init__(self) -> None:
        self._events: list[dict] = []
        self._spill: dict[tuple[str, str], dict] = {}
        self._active = True
        self._busy = False
        self._uninstall = None
        #: 脚本自己 `w` / `x` 重写成功的路径：目录级污点（`rmtree` 后重建）盖不到它们。后来的阻断会撤销。
        self._rewritten: set[str] = set()
        #: 只读相对路径回退的解析函数（worker 装回退后挂上）：脚本交出的路径 → 回退实际会去读的路径。
        self.read_redirect = None
        #: 弱信号计数 {(kind, via): n}；拦下的操作计数 {(op, kind): n}（含没被回读的，只存次数）
        self._signals: dict[tuple[str, str], int] = {}
        self._counts: dict[tuple[str, str], int] = {}
        #: 正在执行的「探路」调用（`exists` / `isfile` / `Path.exists`…）深度：它们本来就靠 stat 落空作答，不算 stat 失败
        self._check_depth = 0
        #: 观察序号：每次脚本的 open / stat / 列目录 +1。`explain_exit` 要求"落空的 savefig 目标探测"是退出前最后一次观察。
        self._seq = 0

    # ---- 记账 ----
    @staticmethod
    def _norm(path) -> str | None:
        if isinstance(path, bytes):
            try:
                path = os.fsdecode(path)
            except ValueError:
                return None
        elif isinstance(path, os.PathLike):
            try:
                path = os.fspath(path)
            except TypeError:
                return None
            if isinstance(path, bytes):
                path = os.fsdecode(path)
        if not isinstance(path, str) or not path:
            return None
        try:
            return os.path.normcase(os.path.realpath(path))
        except (OSError, ValueError):
            return None

    def blocked_savefig(self, names, fmt, supported) -> None:
        """被拦截的 `savefig` 记账——**只在保存本身在真实语义下能成功时**才记。目标目录不存在、格式不支持（或确认不了）时，
        直接运行里 `savefig` 自己就会失败：之后的 `FileNotFoundError` 不是守卫造成的，不能算到它头上。
        `names` 是 `savefig_effective_names`，`fmt` 是显式 `format=`，`supported` 是 matplotlib 认的格式集合（None = 确认不了）。
        检查本身不是脚本的观察，期间置 busy。永不抛。"""
        was, self._busy = self._busy, True
        try:
            if not names or not self._active:
                return
            for name in names:
                path = os.fsdecode(name)
                parent = os.path.dirname(os.path.abspath(path))
                if not os.path.isdir(parent):
                    return
                ext = os.path.splitext(path)[1][1:]
                chosen = str(fmt or ext or "").lower()
                if supported is None or chosen not in supported:
                    return
        except Exception:  # noqa: BLE001 —— 确认不了就不记（不归因）
            return
        finally:
            self._busy = was
        self.blocked("savefig", self.KIND_SAVEFIG, *names)

    def blocked(self, op: str, kind: str, *paths, dests=()) -> None:
        """守卫拦下了 `op`（`os.replace` / `Path.unlink` / `savefig`…）：记它影响的路径。永不抛。

        `dests`：`paths` 里属于改名 / 移动**目的**的那几条（直接运行后它们存在、不是被移走的）。"""
        try:
            if not self._active:
                return
            affected = [p for p in (self._norm(x) for x in paths) if p]
            if not affected:
                return
            key = (op, kind)
            self._counts[key] = min(self._counts.get(key, 0) + 1, self.MAX_COUNT)
            if kind == self.KIND_RENAME and self._savefig_covers(affected[0]):
                # 直接运行时这次改名必然成功（源刚被 savefig 写出）；这里被守卫静默跳过，目的地永远不会出现
                self._signal(kind, self.VIA_RENAME_SRC)
            dest_set = {p for p in (self._norm(x) for x in dests) if p}
            if self._rewritten:  # 新的阻断重新盖住这些路径：之前的「已重写」不再作数
                self._rewritten = {
                    r for r in self._rewritten if not any(self._covers(p, r) for p in affected)
                }
            if len(self._events) >= self.MAX_EVENTS:
                self._spill_paths(op, kind, affected, dest_set)
                return
            self._events.append(
                {"op": op, "kind": kind, "paths": affected, "dests": dest_set, "observed": False}
            )
        except Exception:  # noqa: BLE001 —— 记账失败就少一条，不影响脚本
            return

    def _savefig_covers(self, real: str) -> bool:
        for ev in (*self._events, *self._spill.values()):
            if ev["kind"] != self.KIND_SAVEFIG:
                continue
            if isinstance(ev["paths"], list):
                if any(self._covers(p, real) for p in ev["paths"]):
                    return True
            elif self._spill_hit(ev, real):
                return True
        return False

    def _signal(self, kind: str, via: str) -> None:
        key = (kind, via)
        self._signals[key] = min(self._signals.get(key, 0) + 1, self.MAX_COUNT)

    def _spill_paths(
        self, op: str, kind: str, affected: list[str], dest_set: frozenset | set = frozenset()
    ) -> None:
        """超过 `MAX_EVENTS` 的操作不丢：影响的路径并进该 (op, kind) 的溢出集合，终局仍能判回读。"""
        ev = self._spill.get((op, kind))
        if ev is None:
            ev = self._spill[(op, kind)] = {
                "op": op,
                "kind": kind,
                "paths": set(),
                "dests": set(),
                "observed": False,
                "lost": False,
                "coarse": False,
            }
        if ev["lost"]:
            return
        ev["paths"].update(affected)
        if len(ev["dests"]) + len(dest_set) <= self.MAX_SPILL_PATHS:
            ev["dests"].update(dest_set)
        if len(ev["paths"]) > self.MAX_SPILL_PATHS:
            if ev["coarse"]:
                ev["lost"], ev["paths"] = True, set()
            else:
                ev["coarse"] = True
                ev["paths"] = {os.path.dirname(p) or p for p in ev["paths"]}
                if len(ev["paths"]) > self.MAX_SPILL_PATHS:
                    ev["lost"], ev["paths"] = True, set()

    @staticmethod
    def _spill_hit(ev: dict, real: str) -> bool:
        if ev["lost"]:
            return True
        paths = ev["paths"]
        cur = real
        while True:
            if cur in paths:
                return True
            parent = os.path.dirname(cur)
            if not parent or parent == cur:
                return False
            cur = parent

    @staticmethod
    def _covers(taint: str, path: str) -> bool:
        return path == taint or path.startswith(taint.rstrip(os.sep) + os.sep)

    def observe(self, path, mode="r") -> None:
        """脚本打开了 `path`（`mode` 是 `open` 的模式串）。永不抛。"""
        if not (self._events or self._spill) or not self._active or self._busy:
            return
        self._busy = True
        try:
            self._seq += 1
            if not isinstance(mode, str):
                return
            real = self._norm(path)
            if real is None:
                return
            if "w" in mode or "x" in mode:
                return  # 写模式：打开**成功**之后才清嫌疑（`opened`），这里什么都不动
            if "a" in mode and "+" not in mode:
                return  # 纯追加：不读
            self._mark(real)
            # 只读相对路径回退会把这次读改到项目里的另一条路径：那才是脚本实际读到的文件，也要比对
            redirect = self.read_redirect
            if redirect is not None and _readonly_mode(mode):
                alt = self._norm(redirect(path))
                if alt is not None and alt != real:
                    self._mark(alt)
        except Exception:  # noqa: BLE001
            return
        finally:
            self._busy = False

    @staticmethod
    def is_write_mode(mode) -> bool:
        return isinstance(mode, str) and ("w" in mode or "x" in mode)

    def opened(self, path, mode) -> None:
        """写模式（`w` / `x`）的底层 open **成功返回**了：脚本自己重写了这条路径，此后读到的是它写的
        （与直接运行一致），清掉它的嫌疑。open 抛异常（如被拦删除后 `x` 撞上还在的旧文件）时不调用，记录保留。永不抛。"""
        if not (self._events or self._spill) or not self._active or self._busy:
            return
        self._busy = True
        try:
            real = self._norm(path)
            if real is None:
                return
            # 写进一个被拦下的目录删除所覆盖、且脚本没（成功）重建过的目录：直接运行里父目录已被删，
            # 这次 open 会 FileNotFoundError，这里却因目录还在而成功——写入本身就是分叉，记为观察，不给豁免
            unrecreated = self._under_unrecreated_deleted_dir(real)
            if unrecreated:
                self._mark(real)
            for ev in self._events:
                ev["paths"] = [p for p in ev["paths"] if p != real]
            for ev in self._spill.values():
                if not ev["coarse"]:  # 粗化后存的是目录，清不掉其中某个文件的嫌疑
                    ev["paths"].discard(real)
            # 目录级污点（`rmtree` 之后脚本重建目录、写进新文件）盖不到脚本自己写成功的后代
            if not unrecreated and len(self._rewritten) < self.MAX_SPILL_PATHS:
                self._rewritten.add(real)
        except Exception:  # noqa: BLE001
            return
        finally:
            self._busy = False

    def _deleted_dirs_covering(self, real: str) -> list[str]:
        """被拦下的目录删除里，覆盖 `real`（自身或祖先）的那些目录。粗化 / 丢失的溢出账认不出具体目录，不在此列。"""
        found: list[str] = []
        for ev in (*self._events, *self._spill.values()):
            if ev["kind"] != self.KIND_DELETE_DIR or ev.get("lost") or ev.get("coarse"):
                continue
            found.extend(p for p in ev["paths"] if self._covers(p, real))
        return found

    def _under_unrecreated_deleted_dir(self, real: str) -> bool:
        """`real` 落在某个被拦下的目录删除之下，且那个被删的目录没被脚本成功重建过。
        溢出账粗化 / 丢失后认不出具体目录：命中就宁可当作没重建。"""
        for ev in (*self._events, *self._spill.values()):
            if ev["kind"] != self.KIND_DELETE_DIR:
                continue
            if ev.get("lost"):
                return True
            if ev.get("coarse"):
                if self._spill_hit(ev, real):
                    return True
                continue
            if any(
                d != real and self._covers(d, real) and d not in self._rewritten
                for d in ev["paths"]
            ):
                return True
        return False

    def recreating_directory(self, path, *, parents: bool = False) -> None:
        """脚本 `mkdir` 一个被拦下的 `rmtree` / `rmdir` 影响过的目录，且直接运行也会得到同样的成功结果
        （mkdir 成功，或 `makedirs(exist_ok=True)` / `Path.mkdir(exist_ok=True)` 撞上还在的目录）：
        记这条目录已被脚本重建，随后的 `isdir` 探测与写进去的新文件都不算分叉。只豁免这条目录自身的状态
        与脚本写成功的后代——里面没被重写的文件仍受污点覆盖。
        `path` 是被删目录**之内**的子目录时：`parents`（`makedirs` / `mkdir(parents=True)`）会连父目录一起重建；
        否则直接运行里父目录不在、会 FileNotFoundError，这里的成功是分叉，记为观察。永不抛。"""
        if not (self._events or self._spill) or not self._active or self._busy:
            return
        self._busy = True
        try:
            real = self._norm(path)
            if real is None:
                return
            covering = self._deleted_dirs_covering(real)
            if not covering:
                return
            if real in covering:
                self._rewritten.add(real)
            elif parents:
                self._rewritten.update(covering)
            else:
                self._mark(real)
        except Exception:  # noqa: BLE001
            return
        finally:
            self._busy = False

    def mkdir_collided(self, path) -> None:
        """脚本 `os.mkdir`（非 `exist_ok`）一个被拦下的删除所覆盖的目录，撞上了守卫留下的旧目录
        （`FileExistsError`）：直接运行里目录已被删、mkdir 会成功——脚本走进了异常分支，本身就是一次观察。
        脚本自己重建过的目录不算（两边都会失败；`_mark` 对 `_rewritten` 直接放行）。
        与 `x` 打开撞名同一判据：只有**破坏性**操作（删除 / 删除目录 / 改名移动的源）影响过的路径才算——
        写入等保留存在性的操作被拦后，`os.mkdir` 撞名在两边同样失败，不是分叉（Codex #952）。永不抛。"""
        self.exclusive_create_failed(path)

    def exclusive_create_failed(self, path) -> None:
        """`x` 打开撞上了已存在的文件（`FileExistsError`）。若这条路径被守卫拦下的**破坏性**操作
        （删除 / 删除目录 / 改名移动的源）影响过，直接运行时那次操作已经生效、`x` 会成功——脚本这里看到的
        结果（异常、随后读到的旧文件）与直接运行不同，本身就是一次观察，不必等它回读。
        写入（直跑后同样存在）、改名移动的**目的**、被拦截的 savefig 目标（直跑后也存在）不走这条捷径：
        `x` 在两边都会失败。永不抛。"""
        if not (self._events or self._spill) or not self._active or self._busy:
            return
        self._busy = True
        try:
            real = self._norm(path)
            if real is None or real in self._rewritten:
                return
            destructive = (self.KIND_DELETE, self.KIND_DELETE_DIR, self.KIND_RENAME)
            for ev in (*self._events, *self._spill.values()):
                if ev["observed"] or ev["kind"] not in destructive:
                    continue
                if isinstance(ev["paths"], list):
                    hit = any(self._covers(p, real) for p in ev["paths"])
                else:
                    hit = self._spill_hit(ev, real)
                if not hit:
                    continue
                if ev["kind"] == self.KIND_RENAME and any(
                    self._covers(d, real) for d in ev["dests"]
                ):
                    continue  # 改名 / 移动的目的：直跑后它存在，`x` 同样失败
                ev["observed"] = True
        except Exception:  # noqa: BLE001
            return
        finally:
            self._busy = False

    def move_destination(self, src, dst):
        """`shutil.move(src, dst)` 真正影响的目的路径：`dst` 是已存在的目录时是 `dst/basename(src)`，
        否则就是 `dst`——不能把整个目录记成受影响（目录里无关的文件不受这次移动影响）。永不抛。"""
        try:
            self._busy, was = True, self._busy  # 判断目录不算脚本的观察
            try:
                dst_s = os.fspath(dst)
                src_s = os.fspath(src)
                if os.path.isdir(dst_s):
                    if isinstance(src_s, bytes):
                        src_s = os.fsdecode(src_s)
                    if isinstance(dst_s, bytes):
                        dst_s = os.fsdecode(dst_s)
                    base = os.path.basename(src_s.rstrip("/" + (os.sep or "/")))
                    return os.path.join(dst_s, base) if base else dst_s
                return dst_s
            finally:
                self._busy = was
        except Exception:  # noqa: BLE001
            return dst

    def observe_stat(self, path) -> None:
        """脚本查了 `path` 的状态（`exists` / `isfile` / `stat`）。永不抛。"""
        if not (self._events or self._spill) or not self._active or self._busy:
            return
        self._busy = True
        try:
            self._seq += 1
            real = self._norm(path)
            if real is not None:
                self._mark(real, via_stat=True)
        except Exception:  # noqa: BLE001
            return
        finally:
            self._busy = False

    def _mark(self, real: str, *, via_stat: bool = False) -> None:
        if real in self._rewritten:
            return  # 读到的是脚本自己写的（阻断在其后会撤销这条）
        for ev in self._events:
            # 被拦截的 savefig 目标：`exists` / `getsize` 多是「存好了」的校验打印，不据此判分叉；
            # 读内容（open）才算——`Image.open` / `imread` / `open('rb')` 读到的才是错的数据。
            # 但它是弱信号（V2 PR3a）：回执标 `suspect`；脚本随后以非零退出时 `explain_exit` 才升级
            if via_stat and ev["kind"] == self.KIND_SAVEFIG:
                if any(self._covers(p, real) for p in ev["paths"]):
                    ev["stat_seen"] = True
                    self._signal(self.KIND_SAVEFIG, self.VIA_STAT)
                continue
            if not ev["observed"] and any(self._covers(p, real) for p in ev["paths"]):
                ev["observed"] = True
        for ev in self._spill.values():
            if via_stat and ev["kind"] == self.KIND_SAVEFIG:
                if self._spill_hit(ev, real):
                    ev["stat_seen"] = True
                    self._signal(self.KIND_SAVEFIG, self.VIA_STAT)
                continue
            if not ev["observed"] and self._spill_hit(ev, real):
                ev["observed"] = True

    # ---- 弱信号 / 异常归因（V2 PR3a） ----
    def observe_listing(self, path) -> None:
        """脚本列了目录 `path`（`listdir` / `scandir`）。目录里有受影响路径时，条目数与直接运行不同：弱信号。永不抛。"""
        if not (self._events or self._spill) or not self._active or self._busy:
            return
        self._busy = True
        try:
            self._seq += 1
            real = self._norm(path if path is not None else ".")
            if real is None:
                return
            prefix = real.rstrip(os.sep) + os.sep
            for ev in (*self._events, *self._spill.values()):
                # 受影响路径在被列目录之内、就是它、或者它在受影响目录之内（被拦的 rmtree 留下的目录仍在）
                if ev.get("lost") or any(
                    p.startswith(prefix) or self._covers(p, real) for p in ev["paths"]
                ):
                    self._signal(ev["kind"], self.VIA_LISTING)
                    return
        except Exception:  # noqa: BLE001
            return
        finally:
            self._busy = False

    @staticmethod
    def uses_dir_fd(kwargs) -> bool:
        """调用带了非空的 `*dir_fd`：异常里的相对 `filename` 相对的是那个 fd，不是 cwd，解析不了就别解析。"""
        return any(k.endswith("dir_fd") and v is not None for k, v in kwargs.items())

    @staticmethod
    def tag_unresolvable(exc: BaseException) -> None:
        """给这次 dir_fd 调用抛出的异常打标：之后 `explains` / 弱信号不拿它的 `filename` 去对 cwd 相对的账。永不抛。"""
        try:
            exc._tavotto_unresolvable = True  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass

    def _failure_events(self, exc: BaseException | None) -> list[dict]:
        """异常链里的「文件不存在」指向的路径，落在被拦截 savefig 目标或被拦改名的路径上的那些事件。"""
        fnf = _file_not_found_in(exc)
        if fnf is None or getattr(fnf, "_tavotto_unresolvable", False):
            return []
        # 只对账 `filename`（失败的那条路径）。改名类调用还带 `filename2`（目的地）：失败的若是源，才可能是守卫
        # 没把它写出来；目的地目录不存在 / 不可用时直接运行同样失败，保留原分类
        real = self._norm(getattr(fnf, "filename", None))
        if real is None or real in self._rewritten:
            return []
        dest = getattr(fnf, "filename2", None)
        if dest is not None and not self._destination_usable(dest):
            return []
        reals = [real]
        hits = []
        for ev in (*self._events, *self._spill.values()):
            if ev["kind"] not in (self.KIND_SAVEFIG, self.KIND_RENAME):
                continue
            if isinstance(ev["paths"], list):
                if any(self._covers(p, r) for p in ev["paths"] for r in reals):
                    hits.append(ev)
            elif any(self._spill_hit(ev, r) for r in reals):
                hits.append(ev)
        return hits

    def _destination_usable(self, dest) -> bool:
        """改名 / 移动的目的地的父目录此刻是个存在的目录（直接运行里这次改名才不会因目的地失败）。
        判断不是脚本的观察：临时置 busy，不记账。"""
        was, self._busy = self._busy, True
        try:
            real = self._norm(dest)
            return real is not None and os.path.isdir(os.path.dirname(real) or real)
        finally:
            self._busy = was

    def note_failed_call(self, exc: BaseException, via: str) -> None:
        """某个被包装的调用（stat / `os.open` / 守卫放行的改名删除）抛了文件不存在，且指向受影响路径：弱信号
        （脚本可能 `except OSError` 吞掉）。异常若一路到达终局，`explains` 再升级。永不抛。"""
        if not (self._events or self._spill) or not self._active or self._busy:
            return
        self._busy = True
        try:
            for ev in self._failure_events(exc)[:1]:
                self._signal(ev["kind"], via)
        except Exception:  # noqa: BLE001
            return
        finally:
            self._busy = False

    def explains(self, exc: BaseException) -> bool:
        """终局的异常是不是守卫造成的：它的 `FileNotFoundError` 指向被拦截的 savefig 目标（直接运行时它必然存在）
        或被拦改名的路径。是 → 那些事件记为已观察（确定偏差），`material()` 随之非空。永不抛。"""
        try:
            events = self._failure_events(exc)
            for ev in events:
                ev["observed"] = True
            return bool(events)
        except Exception:  # noqa: BLE001
            return False

    def note_missing_stat(self, exc: BaseException) -> None:
        """被拦截的 savefig 目标上的 stat 落空了（任何探路都算）：记下此刻的观察序号，供 `explain_exit` 判"退出由它控制"。永不抛。"""
        if not (self._events or self._spill) or not self._active or self._busy:
            return
        self._busy = True
        try:
            for ev in self._failure_events(exc):
                if ev["kind"] == self.KIND_SAVEFIG:
                    ev["miss_seq"] = self._seq
        except Exception:  # noqa: BLE001
            return
        finally:
            self._busy = False

    def note_missing_path(self, path) -> None:
        """探路调用（`exists` / `isfile` / `Path.exists`…）对被拦截的 savefig 目标**返回了假**：记下此刻的观察序号。
        POSIX 上落空本来经 `os.stat` 抛 `FileNotFoundError` 被 `note_missing_stat` 记下；Windows 3.12+ 的这些探路是 C 实现、
        不碰 `os.stat`，只能从返回值认。两条路径记同一个序号，重复无害。永不抛。"""
        if not (self._events or self._spill) or not self._active or self._busy:
            return
        self._busy = True
        try:
            real = self._norm(path)
            if real is None or real in self._rewritten:
                return
            for ev in (*self._events, *self._spill.values()):
                if ev["kind"] != self.KIND_SAVEFIG:
                    continue
                hit = (
                    any(self._covers(p, real) for p in ev["paths"])
                    if isinstance(ev["paths"], list)
                    else self._spill_hit(ev, real)
                )
                if hit:
                    ev["miss_seq"] = self._seq
        except Exception:  # noqa: BLE001
            return
        finally:
            self._busy = False

    def explain_exit(self, *, controlled: bool) -> bool:
        """脚本以非零状态退出：退出是守卫造成的吗？必须同时成立（缺一保留原分类）：
        1. 退出前**最后一次**观察就是被拦截 savefig 目标的落空探测（`miss_seq == _seq`）；
        2. `controlled`：这次退出在脚本源码里位于由探测函数控制的分支之下（`exit_controlled_by_probe`）。
           只"最后一次探测"不够——`os.path.exists(o); sys.exit(7)` 直接运行也是 7。
        参数解析库自己的退出由调用方排除。满足时把这些 savefig 事件记为已观察。永不抛。"""
        if not controlled:
            return False
        try:
            hit = False
            for ev in (*self._events, *self._spill.values()):
                if ev["kind"] == self.KIND_SAVEFIG and ev.get("miss_seq") == self._seq:
                    ev["observed"] = True
                    hit = True
            return hit
        except Exception:  # noqa: BLE001
            return False

    # ---- 终局 ----
    def material(self) -> list[dict]:
        """被拦下、且脚本随后观察过受影响路径的操作，按 (op, kind) 聚合：`[{op, kind, count}]`。"""
        counts: dict[tuple[str, str], int] = {}
        for ev in (*self._events, *self._spill.values()):
            if ev["observed"]:
                key = (ev["op"], ev["kind"])
                counts[key] = counts.get(key, 0) + 1
        return [{"op": op, "kind": kind, "count": n} for (op, kind), n in sorted(counts.items())]

    def suspects(self) -> list[dict]:
        """弱信号（疑似偏差）：`[{kind, via, count}]`，只有闭集枚举与计数，没有路径。"""
        return [
            {"kind": kind, "via": via, "count": n}
            for (kind, via), n in sorted(self._signals.items())
            if via in self.SIGNAL_VIAS
        ]

    def summary(self) -> dict:
        """回执 / 失败诊断的 `fidelity` 段：`level`（三档）、确定偏差的操作、拦下的操作类别与次数、弱信号、盲区。
        全是闭集枚举与计数——没有路径、argv、stem。"""
        material = self.material()
        signals = self.suspects()
        level = (
            self.LEVEL_DEVIATED
            if material
            else self.LEVEL_SUSPECT
            if signals
            else self.LEVEL_NONE_OBSERVED
        )
        return {
            "level": level,
            "blocked": material,
            "intercepted": [
                {"op": op, "kind": kind, "count": n}
                for (op, kind), n in sorted(self._counts.items())
            ],
            "signals": signals,
            "unobserved": list(INPUT_OBSERVER_UNOBSERVED),
        }

    def stop(self) -> None:
        """脚本跑完：之后一切观察都不算（引擎自己的读写不是脚本的）。"""
        self._active = False

    # ---- 钩子 ----
    def install(self):
        """装 open（`builtins.open` / `io.open` / `Path.open`）与 `os.stat` 的观察包装；返回卸载函数。

        worker 把它装在**最外层**（晚于输入观察器、数据改指与相对路径回退）：看到的是脚本交出的那条路径，
        与守卫记账时用的是同一个 cwd 下的同一种解析。`os.stat` 一并包——`os.path.exists` /
        `isfile` / `Path.exists` 都经它（Windows 3.12+ 的 `os.path.exists` 系是 C 实现、不经 `os.stat`，另行逐个包装，见下）。
        """
        ledger = self
        real_open, real_io_open = builtins.open, io.open
        real_path_open, real_stat = pathlib.Path.open, os.stat
        real_mkdir = os.mkdir
        real_makedirs = os.makedirs
        real_path_mkdir = pathlib.Path.mkdir
        #: 正在进行的 `makedirs` / `Path.mkdir` 的 (exist_ok, parents)：内部的 `os.mkdir` 撞上还在的目录时
        #: 由外层自己吞掉（`exist_ok`），不是脚本看到的异常
        mk_ctx: list[tuple[bool, bool]] = []

        def _open_through(original, file, mode, args, kwargs):
            # 自带 `opener`（常见用法是 `os.open(p, f, dir_fd=fd)`）：路径怎么解析由 opener 说了算，不当 cwd 相对路径记账
            if kwargs.get("opener") is None:
                ledger.observe(file, mode)
            if not ledger.is_write_mode(mode):
                return original(file, mode, *args, **kwargs)
            try:
                result = original(file, mode, *args, **kwargs)
            except FileExistsError:
                if "x" in mode:
                    ledger.exclusive_create_failed(file)
                raise
            ledger.opened(file, mode)  # 写模式成功之后才清嫌疑
            return result

        def _wrap(original):
            def watched_open(file, mode="r", *args, **kwargs):
                return _open_through(original, file, mode, args, kwargs)

            return watched_open

        def watched_path_open(self_path, mode="r", *args, **kwargs):
            return _open_through(
                lambda f, m, *a, **k: real_path_open(f, m, *a, **k), self_path, mode, args, kwargs
            )

        def watched_stat(path, *args, **kwargs):
            # 带 dir_fd：路径相对那个 fd，不能当 cwd 相对路径记账 / 归因（直接传 fd 的 `_norm` 本来就是 None）
            via_fd = ledger.uses_dir_fd(kwargs)
            if (
                not ledger._check_depth and not via_fd
            ):  # 探路包装已经观察过这一次（POSIX 上 exists 内部再走 os.stat）：只算一次
                ledger.observe_stat(path)
            try:
                return real_stat(path, *args, **kwargs)
            except FileNotFoundError as exc:
                if via_fd:
                    ledger.tag_unresolvable(exc)
                    raise
                ledger.note_missing_stat(exc)
                if (
                    not ledger._check_depth
                ):  # `exists()` 一类探路本来就靠落空作答；`getsize` / `stat()` 的落空才是信号
                    ledger.note_failed_call(exc, ledger.VIA_PROBE_FAILED)
                raise

        real_os_open = os.open
        real_listdir, real_scandir = os.listdir, os.scandir

        def watched_os_open(path, *args, **kwargs):
            try:
                return real_os_open(path, *args, **kwargs)
            except FileNotFoundError as exc:
                if ledger.uses_dir_fd(kwargs):
                    ledger.tag_unresolvable(exc)
                else:
                    ledger.note_failed_call(exc, ledger.VIA_PROBE_FAILED)
                raise

        def watched_listdir(path=None):
            ledger.observe_listing(path)
            return real_listdir() if path is None else real_listdir(path)

        def watched_scandir(path=None):
            ledger.observe_listing(path)
            return real_scandir() if path is None else real_scandir(path)

        def _counting(original):
            def counted(*args, **kwargs):
                # `Path.mkdir(exist_ok=True)` 内部自己会 `is_dir()`（3.10 / 3.11 在撞上旧目录之后、
                # 豁免记账之前）：那是 mkdir 的实现，不是脚本的观察
                if not ledger._check_depth and not mk_ctx and args:
                    ledger.observe_stat(args[0])
                ledger._check_depth += 1
                try:
                    result = original(*args, **kwargs)
                finally:
                    ledger._check_depth -= 1
                if args and not result and not mk_ctx:
                    ledger.note_missing_path(args[0])
                return result

            return counted

        # Windows 3.12+ 的 `os.path.exists` / `isfile` / `isdir` / `islink` / `lexists` 是 C 实现，
        # 不经 `os.stat`：逐个包上（POSIX 上它们本来经 `os.stat`，重复观察是幂等的）
        real_path_checks: dict[str, object] = {}

        def _wrap_check(original):
            def watched_check(path, *args, **kwargs):
                if not ledger._check_depth:
                    ledger.observe_stat(path)
                ledger._check_depth += 1
                try:
                    result = original(path, *args, **kwargs)
                finally:
                    ledger._check_depth -= 1
                if not result:
                    ledger.note_missing_path(path)
                return result

            return watched_check

        for name in ("exists", "lexists", "isfile", "isdir", "islink"):
            fn = getattr(os.path, name, None)
            if fn is not None:
                real_path_checks[name] = fn
                setattr(os.path, name, _wrap_check(fn))

        # `Path.exists` / `is_file` / `is_dir` / `is_symlink` 也是探路（3.12+ 里它们不一定经 `os.path.*`）
        real_path_checks_methods: dict[str, object] = {}
        for name in ("exists", "is_file", "is_dir", "is_symlink"):
            fn = getattr(pathlib.Path, name, None)
            if fn is not None:
                real_path_checks_methods[name] = fn
                setattr(pathlib.Path, name, _counting(fn))

        builtins.open = _wrap(real_open)
        io.open = _wrap(real_io_open)
        pathlib.Path.open = watched_path_open
        os.stat = watched_stat
        os.open = watched_os_open
        os.listdir = watched_listdir
        os.scandir = watched_scandir

        def watched_mkdir(path, *args, **kwargs):
            tolerant, parents = mk_ctx[-1] if mk_ctx else (False, False)
            try:
                result = real_mkdir(path, *args, **kwargs)
            except FileExistsError:
                if tolerant:
                    ledger.recreating_directory(path, parents=parents)  # 直跑也成功：等价结果
                else:
                    ledger.mkdir_collided(path)
                raise
            ledger.recreating_directory(path, parents=parents)  # 成功之后才给豁免
            return result

        def watched_makedirs(name, mode=0o777, exist_ok=False):
            mk_ctx.append((bool(exist_ok), True))
            try:
                return real_makedirs(name, mode, exist_ok)
            finally:
                mk_ctx.pop()

        def watched_path_mkdir(self_path, mode=0o777, parents=False, exist_ok=False):
            # 3.10 的 `Path.mkdir` 走导入时绑定的 `self._accessor.mkdir`，看不到上面的 `os.mkdir` 包装——
            # 这里自己按结果记账（3.11+ 内层 `os.mkdir` 包装也会记一次，重复记账无害）
            mk_ctx.append((bool(exist_ok), bool(parents)))
            try:
                result = real_path_mkdir(self_path, mode, parents, exist_ok)
            except FileExistsError:
                if not exist_ok:
                    ledger.mkdir_collided(self_path)
                raise
            finally:
                mk_ctx.pop()
            # 正常返回：目录此刻在（新建或 exist_ok 撞上）
            ledger.recreating_directory(self_path, parents=parents)
            return result

        os.mkdir = watched_mkdir
        os.makedirs = watched_makedirs
        pathlib.Path.mkdir = watched_path_mkdir

        def uninstall() -> None:
            os.mkdir = real_mkdir
            os.makedirs = real_makedirs
            pathlib.Path.mkdir = real_path_mkdir
            builtins.open = real_open
            io.open = real_io_open
            pathlib.Path.open = real_path_open
            os.stat = real_stat
            os.open = real_os_open
            os.listdir = real_listdir
            os.scandir = real_scandir
            for name, fn in real_path_checks.items():
                setattr(os.path, name, fn)
            for name, fn in real_path_checks_methods.items():
                setattr(pathlib.Path, name, fn)

        self._uninstall = uninstall
        return uninstall

    def uninstall(self) -> None:
        if self._uninstall is not None:
            self._uninstall()
            self._uninstall = None


def observed_local_modules(
    project_root: str, modules: dict, *, exclude_dir: str = ""
) -> list[dict]:
    """脚本跑完之后 `sys.modules` 里 `__file__` 落在项目根之内的模块（回执的「已观察的本地模块」）。
    `exclude_dir` 是引擎自己平铺 import 的目录（worker 把 engine 目录放进了 sys.path，那些不是用户的）。
    按名字排好序、有界（与文件同一个上限）。"""
    root = os.path.realpath(os.path.abspath(project_root))
    excl = os.path.realpath(exclude_dir) if exclude_dir else ""
    # 项目 `.venv` 里 import 进来的第三方包（matplotlib / numpy…）不是用户的本地模块（PATH-B2，与文件观察同一份判据）
    interp = interpreter_dirs_within(root)
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
        if any(_within(real, d) for d in interp):
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

    def _present(path) -> bool:
        """回退自己的「这条路径在不在」——走 `os.stat`，**不经** `os.path.exists`。

        `os.path.exists` 在沙盒会话里已被 `install_probe_observer` 换成记账版（观察器先装、回退后装）：
        回退每问一次「沙盒里有没有这份」就会被记成脚本在沙盒里落空了一次探路，哪怕随后正是回退把读救回来、
        脚本读到了数据——于是「在脚本目录里运行」的引导会挂到与目录无关的失败上（读到的文件内容不对 →
        ValueError，准备卡标题「运行出错了」却问选运行目录）。证据必须只来自**脚本自己**的探路。
        """
        try:
            os.stat(path)
        except (OSError, ValueError):
            return False
        return True

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
            # 沙盒**之外**的绝对路径回退一个都不碰：那是用户明确指名的位置，
            # 「就近找一个能用的」在那里是越权，不是便利（用户亲手指认的改指表
            # 在 `install_input_remap`，ADR 0106）。
            rel = _within_sandbox(name)
            if rel is None:
                return None
        else:
            rel = name
        # 存在性**按真正的 open 会用的那条路径判**：相对的按 cwd 解（脚本
        # 可能 `os.chdir()` 进了子目录），绝对的就用它自己。拿沙盒根去拼的话，
        # chdir 之后脚本自己写出来的那份会被无视、读到项目里的原件。
        if _present(name):
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

    def _is_cwd(destpath) -> bool:
        """DataSource 的 destpath 就是此刻的 cwd：相对路径在它那里与在真正的 open 里是同一处。"""
        if not isinstance(destpath, str) or not destpath:
            return False
        try:
            return os.path.realpath(destpath) == os.path.realpath(os.getcwd())
        except OSError:
            return False

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

    # 第四个：numpy 的 `DataSource.open`（`loadtxt` / `genfromtxt` 经它读，先 exists 再 open，
    # 见模块头）。只包已经载入的 numpy，不替脚本 import。
    datasource = getattr(sys.modules.get("numpy.lib._datasource"), "DataSource", None)
    real_ds_open = getattr(datasource, "open", None)

    def _datasource_candidates(ds, path) -> list:
        """numpy 自己会试的那串名字：原名，再依次加 `.gz` / `.bz2` / `.xz` …（`_possible_names`）。
        `np.loadtxt("values")` 而旁边只有 `values.gz` 时 numpy 照样读得到——回退得按同一串名字找。"""
        if not isinstance(path, (str, os.PathLike)):
            return [path]
        name = os.fspath(path)
        possible = getattr(ds, "_possible_names", None)
        try:
            names = list(possible(name)) if possible is not None else [name]
        except Exception:  # noqa: BLE001 —— numpy 内部接口变了就只认原名
            names = [name]
        return names or [name]

    def guarded_datasource_open(self, path, mode="r", *args, **kwargs):
        if _readonly(mode) and _is_cwd(getattr(self, "_destpath", None)):
            names = _datasource_candidates(self, path)
            # 这串名字里只要有一个在 cwd（沙盒）下存在，numpy 自己就会读到它——不改道
            # （`_fallback_path` 对单个名字已经是这条判据，这里把它扩到整串）
            if not any(isinstance(n, str) and _present(n) for n in names):
                for n in names:
                    alt = _fallback_path(n)
                    if alt is not None:
                        return real_ds_open(self, alt, mode, *args, **kwargs)
        return real_ds_open(self, path, mode, *args, **kwargs)

    if real_ds_open is not None:
        datasource.open = guarded_datasource_open

    def uninstall() -> None:
        builtins.open = real_open
        io.open = real_io_open
        pathlib.Path.open = real_path_open
        if real_ds_open is not None:
            datasource.open = real_ds_open

    #: 账本比对用：这次只读 open 回退会改读哪条路径（None＝不改道）。与回退本身是同一个判据。
    uninstall.resolve = _fallback_path
    return uninstall


# ---------------------------------------------------------------- 数据改指（ADR 0106）
#
# 脚本要读的数据不在它写的那个位置（脚本被单独复制出来、数据被挪走、换了电脑）时，界面请用户
# 指认一次，按项目记一张**只读**改指表。两件事都在这里：
#
# * 规则怎么匹配（`remap_target`）——父进程推规则（`inputremap.derive`）、给弹窗筛「这次指认
#   会顺带修好哪些」与 worker 真正改道用的是同一份判据；
# * worker 里的装载（`install_input_remap`）——包四个打开入口，**原路径打开失败**（真的
#   `FileNotFoundError`）之后才查表，命中且目标是文件就改读目标；没命中就把脚本写的那串记进
#   `InputMisses`、原样抛出。原路径存在时一次都不查表：数据回来了，规则自动不起作用。
#
# 与相对路径只读回退的分工：回退装在外层（先找脚本目录），改指装在内层（回退也找不到时才轮到
# 它），两者都只碰只读打开。`exists` / `glob` / `listdir` 与 C++ 读取器照样不经过这里
# （ADR 0047 / 0106 §四）。

#: 改指规则的两种形状。`prefix`：`from` 是路径前缀（按路径段比，`""` = 任意相对路径）；
#: `file`：`from` 整串对上才改（用户指认的文件改了名时，只能说这一个）。
REMAP_PREFIX = "prefix"
REMAP_FILE = "file"
REMAP_KINDS = (REMAP_PREFIX, REMAP_FILE)
#: 一张表最多几条（设置文件被手改大了也只认这么多）。
MAX_REMAP_RULES = 64
#: 一次 build 里最多记几条落空的读（去重后）。
MAX_INPUT_MISSES = 32

_DRIVE_RE = re.compile(r"^[A-Za-z]:$")


def remap_parts(text: str) -> tuple[bool, tuple[str, ...]] | None:
    """路径串 → `(是否绝对, 路径段)`；说不清的（空串、`~`、URL）回 None。

    主语是**脚本作者写的那串**，它可能写于另一个 OS：反斜杠一律当分隔符，POSIX 根
    `/`、盘符 `C:` 与 UNC 的 `\\\\server\\share` 都算绝对（后者整个是一个根段 `//server/share`）；
    盘符与 UNC 根按小写比（Windows 上 `C:` 与 `c:` 是同一个盘）。
    相对路径里的 `.` 与空段丢掉，`..` 保留（`../data/x.csv` 的 `..` 是前缀的一部分）。
    """
    if not isinstance(text, str) or not text or "\x00" in text:
        return None
    if text.startswith("~") or "://" in text:
        return None
    norm = text.replace("\\", "/")
    raw = norm.split("/")
    unc = norm.startswith("//") and len(raw) > 3 and raw[2] not in ("", ".", "..") and raw[3] != ""
    if unc:
        # UNC `\\server\share\…`：server/share 是一个独立的根（不是 `/` 下的两段），
        # 否则拼回去成了本机的 `/server/share`（Codex 评 #716 P2）；与盘符一样按小写比
        head: tuple[str, ...] = (f"//{raw[2]}/{raw[3]}".lower(),)
        rest = raw[4:]
        absolute = True
    elif norm.startswith("/"):
        head = ("/",)
        rest = raw[1:]
        absolute = True
    elif _DRIVE_RE.match(raw[0]) and len(raw) > 1:
        head = (raw[0].lower(),)
        rest = raw[1:]
        absolute = True
    else:
        head = ()
        rest = raw
        absolute = False
    parts = head + tuple(p for p in rest if p not in ("", "."))
    return absolute, parts


def _join_parts(parts: tuple[str, ...]) -> str:
    if not parts:
        return ""
    if parts[0] == "/":
        return "/" + "/".join(parts[1:])
    if _DRIVE_RE.match(parts[0]):
        return parts[0] + "/" + "/".join(parts[1:])
    return "/".join(parts)  # 相对路径；UNC 根 `//server/share` 本身带着前导 `//`，同样直接拼


def clean_remap_rules(rules) -> list[dict]:
    """设置 / argv 里的改指表 → 校验过的规则列表（坏条目丢掉，不抛）。

    每条 `{"kind", "from", "to"}`：`to` 必须是绝对路径（它是用户在本机指认的位置）；
    `from` 是脚本写法的那一侧，相对或绝对都行（`prefix` 的相对 `from` 可以是 `""`）。
    """
    out: list[dict] = []
    if not isinstance(rules, (list, tuple)):
        return out
    for r in rules:
        if not isinstance(r, dict):
            continue
        kind, src, dst = r.get("kind"), r.get("from"), r.get("to")
        if kind not in REMAP_KINDS or not isinstance(src, str) or not isinstance(dst, str):
            continue
        dst_parts = remap_parts(dst)
        if dst_parts is None or not dst_parts[0]:
            continue
        if src == "" and kind == REMAP_PREFIX:
            pass
        elif remap_parts(src) is None:
            continue
        rule = {"kind": kind, "from": src, "to": dst}
        if rule not in out:
            out.append(rule)
        if len(out) >= MAX_REMAP_RULES:
            break
    return out


def remap_target(rules, name: str, *, whole: bool = False) -> str | None:
    """脚本写的路径串 `name` → 改指后的路径（本机真实路径串）；没有规则命中回 None。

    **只做字符串换算，不看磁盘**——「目标在不在」由调用方判（worker 在改道前判是文件，
    父进程筛弹窗列表时判存在）。命中顺序：`file` 规则整串相等优先；其次 `prefix` 规则里
    `from` 路径段最长的那条（更具体的赢）。相对路径只配相对的 `from`，绝对只配绝对的。

    `whole=True`：`name` 可以**就是** `prefix` 的 `from` 本身（换算成 `to`）——改写脚本里的
    目录常量（`DATA = "/Users/a/proj"`，ADR 0110 §二）要这一档；worker 改道永远不用它（打开的
    是文件，`from` 是目录）。
    """
    parsed = remap_parts(name)
    if parsed is None:
        return None
    absolute, parts = parsed
    best: tuple[int, str | None] | None = None
    for r in clean_remap_rules(rules):
        src = remap_parts(r["from"]) if r["from"] else (False, ())
        if src is None or src[0] != absolute:
            continue
        if r["kind"] == REMAP_FILE:
            if src[1] == parts:
                return r["to"]
            continue
        n = len(src[1])
        if parts[:n] != src[1] or (n == len(parts) and not whole):
            continue  # 前缀要严格短于路径本身：`from` 本身是目录，不是这个文件
        if best is None or n > best[0]:
            best = (n, None)  # 最长前缀即使拒绝越界，也不能退到另一条规则悄悄换一份数据
            rest = parts[n:]
            # 原串的 `..` 可以被 from 消耗；剩下那段不能越过用户指认的 to。
            # 只判层数、不改写字符串（glob 的 `*/../` 不能当作普通路径折叠）。
            depth = 0
            for part in rest:
                depth += -1 if part == ".." else 1
                if depth < 0:
                    break
            else:
                to = r["to"].replace("\\", "/")
                target = to.rstrip("/") + "/" + "/".join(rest) if rest else to
                if name.endswith(("/", "\\")) and not target.endswith("/"):
                    target += "/"  # glob 的末尾分隔符表示只匹配目录，不能丢
                best = (n, target)
    return best[1] if best else None


def _is_sys_path_entry(name: str) -> bool:
    """`name` 就是 `sys.path` 的某个条目（如 Homebrew 的 `.../lib/python313.zip`）。

    `importlib.metadata` 扫已安装分发时对每个条目试 `zipfile.ZipFile(条目)`——不存在的条目 `io.open` 抛
    `FileNotFoundError`，被它自己吞掉；但打开入口的包装已经记了一笔，占掉 `MAX_INPUT_MISSES` 的名额
    （#1253）。解释器自己的搜索路径条目永远不是用户数据，不记。
    """
    if not os.path.isabs(name):
        return False
    key = os.path.normcase(os.path.normpath(name))
    return any(
        isinstance(p, str) and p and os.path.normcase(os.path.normpath(p)) == key for p in sys.path
    )


class InputMisses:
    """一次 build 里**原路径打开失败、改指也没救回来**的只读打开：脚本写的那串 + 当时的 cwd。

    只记不改；`missing_input` 的分类（`missing_input_of`）靠它说出「缺的是哪个」——
    numpy 的 `"d.txt not found."`、h5py 的 C 层消息里没有 `exc.filename`。
    """

    def __init__(self) -> None:
        self.items: list[dict] = []

    def note(self, name, cwd: str | None = None) -> None:
        """`cwd` 是 `name` 相对的那个目录；不给就是此刻的 cwd（脚本写的相对路径就是相对它）。"""
        if not isinstance(name, str) or not name:
            return
        if cwd is None:
            try:
                cwd = os.getcwd()
            except OSError:
                cwd = ""
        entry = {"requested": name, "cwd": cwd}
        if entry in self.items:
            # 同一条又落空一次：挪到最后（「最近一次落空的」是 exc 没带文件名时的答案）
            self.items.remove(entry)
        elif len(self.items) >= MAX_INPUT_MISSES:
            return
        self.items.append(entry)

    def names(self) -> list[str]:
        return [m["requested"] for m in self.items]


def path_literals(source: str | None) -> tuple[list[tuple[str, ...]], list[tuple[str, ...]]]:
    """脚本里写着的路径常量 → `(绝对的路径段, 相对的路径段)`（`remap_parts` 规范化过；空段、说不清的不要）。
    `install_input_remap` 靠它判一条落在 cwd 里的绝对路径到底是脚本写的绝对路径，还是相对路径被库规范化出来的。

    证据必须是一个**本身就是相对路径的表达式**，不是任意匹配上的字符串常量：`/` 拼接
    （`pathlib` 的 `__truediv__`）或 `os.path.join` 一类拼接里，操作数必须**全部**是已知的相对
    字符串常量，才把这条拼接的每一段计入相对证据；只要有一个操作数不是（解析不了的名字——
    `ast.Name` / 属性访问 / 调用结果，不管它是不是 `root = Path(__file__).parent` 这样先赋值成
    局部变量再拼接的；或者操作数本身就是绝对路径的常量），整条拼接里的字符串片段一个都不算数——
    也不认领绝对证据（Codex 评 #716 两轮 P1：`Path(__file__).parent / "data" / "x.csv"` 与
    `root = Path(__file__).parent; root / "data" / "x.csv"` 里的 `"data"` 都不能被当成
    「脚本写过相对路径 data」；解析不了的操作数一律当成建不了相对来源的证据，不去追踪它是不是
    绝对锚点的赋值）。没有这种操作数的拼接（`os.path.join("data", "x.csv")` 这类，结果本身就是
    相对路径）与孤立常量一样，各段仍然算数。
    """
    import ast

    abs_parts: list[tuple[str, ...]] = []
    rel_parts: list[tuple[str, ...]] = []
    if not source:
        return abs_parts, rel_parts
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return abs_parts, rel_parts

    def _record(node: ast.AST) -> None:
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and "\n" not in node.value
        ):
            parsed = remap_parts(node.value)
            if parsed is None or not parsed[1]:
                return
            (abs_parts if parsed[0] else rel_parts).append(parsed[1])

    def _is_name_or_attr(func: ast.AST, name: str) -> bool:
        return (isinstance(func, ast.Name) and func.id == name) or (
            isinstance(func, ast.Attribute) and func.attr == name
        )

    def _is_known_relative_literal(node: ast.AST) -> bool:
        """操作数是不是一个「已知的相对路径」字符串常量——拼接里只有这种操作数才不需要
        进一步追踪来源。任何解析不了的名字（`ast.Name` / 属性访问 / 调用结果，不管它是不是先
        赋值成局部变量再拼接的）或本身就是绝对路径的常量，都不算「已知」。"""
        if not (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and "\n" not in node.value
        ):
            return False
        parsed = remap_parts(node.value)
        return bool(parsed) and not parsed[0]

    def _is_join_call(node: ast.AST) -> bool:
        return isinstance(node, ast.Call) and _is_name_or_attr(node.func, "join")

    def _flatten(node: ast.AST, out: list[ast.AST]) -> None:
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            _flatten(node.left, out)
            _flatten(node.right, out)
        else:
            out.append(node)

    def _visit_operands(operands: list[ast.AST]) -> None:
        # 拼接里只要有一个操作数不是「已知的相对常量」，整条拼接的字符串片段都不独立算数
        # （也不认领绝对证据）：追不清哪一段才是「脚本写的相对路径」时，宁可不算证据，保留绝对身份。
        anchored = any(not _is_known_relative_literal(op) for op in operands)
        if anchored:
            # 整个操作数子树都不看：`root / Path("data")`、`root / ("a" + "b")`、`root / f"{x}/d"`
            # 里嵌套的字面量一样不是「脚本写过的相对路径」（Codex 评 #716 P1）
            return
        for op in operands:
            _visit(op)

    def _visit(node: ast.AST) -> None:
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            operands: list[ast.AST] = []
            _flatten(node, operands)
            _visit_operands(operands)
            return
        if _is_join_call(node):
            operands = []
            for arg in node.args:
                _flatten(arg, operands)
            _visit_operands(operands)
            return
        _record(node)
        for child in ast.iter_child_nodes(node):
            _visit(child)

    _visit(tree)
    return abs_parts, rel_parts


def install_input_remap(
    rules, misses: InputMisses, base_dir: str | None = None, *, script_source: str | None = None
):
    """装上改指（ADR 0106）与落空记账；返回卸载函数。

    `base_dir` 是装的那一刻脚本的 cwd（沙盒或脚本目录 / 项目根）：`Image.open` 这类先
    realpath 的库递进来的是 `<cwd>/x.png`——在 cwd 之内的绝对路径，**能证明**是相对路径规范化来的才按相对
    那一段查表、记账。证据是脚本源码（`script_source`，`path_literals`）：有相对常量的路径段是它的前缀、且没有
    绝对常量认领它。证不出就**保留绝对身份**——`cwd_mode=project` 时脚本显式写的 `<项目>/data/x.csv` 缺了，
    一条宽泛的相对规则不许悄悄接管它，弹窗也不许把它当相对路径推出一条 `from=""` 的全项目规则（Codex 评 #716
    P1）。没给源码（旧调用方）维持原来的判法。

    只在**原路径打开抛 `FileNotFoundError`** 之后查表：成功的打开零额外开销，原路径存在时
    永远读原路径。只读模式才改（写 / 追加 / 读写一个字节都不改道）；改指目标必须是文件，
    否则原样抛出原来那个异常。
    """
    rules = clean_remap_rules(rules)
    real_open = builtins.open
    real_io_open = io.open
    real_path_open = pathlib.Path.open
    base = os.path.abspath(base_dir if base_dir is not None else os.getcwd())
    literals = path_literals(script_source) if script_source is not None else None

    def _proven_relative(name: str, rel: str) -> bool:
        if literals is None:
            return True
        abs_lits, rel_lits = literals
        for n in {name, os.path.realpath(name)}:
            parsed = remap_parts(n)
            if parsed and any(parsed[1][: len(p)] == p for p in abs_lits):
                return False  # 脚本自己写着这条（或它的上级）绝对路径
        parsed = remap_parts(rel)
        return bool(parsed) and any(parsed[1][: len(p)] == p for p in rel_lits)

    def _script_name(name: str) -> str:
        """落在 base 里、能证明是相对路径规范化来的绝对路径 → 相对那一段；否则原样（保留绝对身份）。"""
        if not os.path.isabs(name):
            return name
        try:
            real = os.path.realpath(name)
            real_base = os.path.realpath(base)
            if os.path.commonpath([real, real_base]) != real_base:
                return name
        except (OSError, ValueError):
            return name
        rel = os.path.relpath(real, real_base)
        if rel.startswith("..") or rel == ".":
            return name
        # `os.path.relpath` 在 Windows 上回反斜杠：记账 / 弹窗展示的名字与 `remap_target` 的匹配
        # 一样按正斜杠算（`remap_parts` 本就把反斜杠当分隔符），不随运行的 OS 变出两种拼法
        # （Codex 评 #716 Windows full-ci：同一个 chdir 场景，Windows 腿记下的是 `sub\x.csv`）。
        rel = rel.replace("\\", "/")
        return rel if _proven_relative(name, rel) else name

    def _alt(file) -> tuple[str | None, str | None]:
        """回 `(改指目标, 记账用的名字)`；不是路径（fd / 文件对象）两个都是 None。"""
        if not isinstance(file, (str, os.PathLike)):
            return None, None
        name = os.fspath(file)
        if not isinstance(name, str) or not name:
            return None, None
        shown = _script_name(name)
        if not rules:
            return None, shown
        target = remap_target(rules, shown)
        if target is None and shown != name:
            target = remap_target(rules, name)
        if target is not None and os.path.isfile(target):
            return target, shown
        return None, shown

    def _note_miss(file, shown) -> None:
        # 绝对路径换成了相对 base 的那一段：它相对的是 base，不是此刻的 cwd——脚本 chdir 过之后
        # 两者不同，按 cwd 拼回去是 base/sub/sub/x.csv，分类对不上（Codex 评 #716 P2）
        raw = os.fspath(file) if isinstance(file, (str, os.PathLike)) else None
        if isinstance(raw, str) and _is_sys_path_entry(raw):
            return  # import 机制探测不存在的 sys.path 条目（importlib.metadata 的 zipfile 打开），不是脚本的输入
        misses.note(shown, cwd=base if isinstance(raw, str) and shown != raw else None)

    def _readonly(mode) -> bool:
        return isinstance(mode, str) and "r" in mode and not any(c in mode for c in "+wxa")

    def _wrap(original):
        def remapped_open(file, mode="r", *args, **kwargs):
            try:
                return original(file, mode, *args, **kwargs)
            except FileNotFoundError:
                if not _readonly(mode):
                    raise
                alt, shown = _alt(file)
                if alt is not None:
                    return original(alt, mode, *args, **kwargs)
                _note_miss(file, shown)
                raise

        return remapped_open

    def remapped_path_open(self, mode="r", *args, **kwargs):
        try:
            return real_path_open(self, mode, *args, **kwargs)
        except FileNotFoundError:
            if not _readonly(mode):
                raise
            alt, shown = _alt(self)
            if alt is not None:
                return real_path_open(pathlib.Path(alt), mode, *args, **kwargs)
            _note_miss(self, shown)
            raise

    builtins.open = _wrap(real_open)
    io.open = _wrap(real_io_open)
    pathlib.Path.open = remapped_path_open

    # numpy 的 `DataSource.open`：它先自己 exists 再 open，找不到当场 FileNotFoundError（"x not
    # found."，没有 filename），三个 open 入口够不着——同一条纪律再包一次。
    datasource = getattr(sys.modules.get("numpy.lib._datasource"), "DataSource", None)
    real_ds_open = getattr(datasource, "open", None)

    def remapped_datasource_open(self_ds, path, mode="r", *args, **kwargs):
        try:
            return real_ds_open(self_ds, path, mode, *args, **kwargs)
        except FileNotFoundError:
            if not _readonly(mode):
                raise
            alt, shown = _alt(path)
            if alt is not None:
                return real_ds_open(self_ds, alt, mode, *args, **kwargs)
            _note_miss(path, shown)
            raise

    if real_ds_open is not None:
        datasource.open = remapped_datasource_open

    def uninstall() -> None:
        builtins.open = real_open
        io.open = real_io_open
        pathlib.Path.open = real_path_open
        if real_ds_open is not None:
            datasource.open = real_ds_open

    return uninstall


#: 一次 build 里最多记几条落空的探路调用（去重后）。
MAX_PROBE_MISSES = 16
PROBE_GLOB, PROBE_DIR, PROBE_PATH = "glob", "dir", "path"


class ProbeMisses:
    """一次 build 里**沙盒 cwd 下落空的相对探路调用**：`glob` 回空、`listdir` 的目录不存在、`exists` 为假……

    只记「问了什么」（`{kind, target, recursive, hidden}`，脚本写的那串相对目标），**不改任何返回值**；
    它在脚本目录里到底找不找得到由父进程判（`workdir.probe_hint`，与 ADR 0084 首开的 `databinding._probe_hit`
    同一把尺子）。探路调用的目标是参数、不是写在调用里的常量时，首开的静态探测（`probe_literals`）认不出来，
    这是它唯一的证据来源（#1254）。
    """

    def __init__(self) -> None:
        self.items: list[dict] = []

    def note(self, kind: str, target, *, recursive: bool = False, hidden: bool = False) -> None:
        if isinstance(target, os.PathLike):
            target = os.fspath(target)
        if not isinstance(target, str) or not target or len(target) > 512:
            return
        if target.startswith("~") or os.path.isabs(target) or ntpath.isabs(target):
            return  # 绝对路径不依赖 cwd
        norm = target.replace("\\", "/")
        if all(part in ("", ".") for part in norm.split("/")):
            return  # 列 cwd 本身不指名任何东西：脚本目录里恒有东西，说明不了什么（ADR 0084）
        entry = {"kind": kind, "target": norm, "recursive": bool(recursive), "hidden": bool(hidden)}
        if entry in self.items or len(self.items) >= MAX_PROBE_MISSES:
            return
        self.items.append(entry)


def install_probe_observer(misses: ProbeMisses, base_dir: str):
    """装上沙盒里落空的探路调用记账（只记不改）；返回卸载函数。只在 cwd 是沙盒的会话里装。

    包的是 `glob.glob|iglob` / `os.listdir|scandir` / `os.path.exists|isfile|isdir` / `pathlib.Path.glob|rglob|iterdir|
    exists|is_file|is_dir`。纪律：

    * **返回值原样**：同一个对象（`glob.glob` / `listdir` 的 list、`exists` 的 bool）；返回迭代器的（`iglob` /
      `Path.glob` / `iterdir`）换成逐项转发的生成器，吐出的元素与异常一模一样，耗尽时若一项都没吐才记账；
    * 只记相对目标、且调用时的 cwd 还是装的那一刻的沙盒（脚本 `chdir` 走了就不是沙盒里的事实了）；
    * stdlib 自己的内部探路（`glob` 里逐段 `isdir`、`Path.glob` 里的 `scandir`）不记：线程内重入时直接放行；
    * `os.scandir` 只在目录不存在（`FileNotFoundError`）时记，返回的迭代器不包；
    * 名额有限（`MAX_PROBE_MISSES`），去重。
    """
    base = os.path.realpath(base_dir)
    state = threading.local()

    def _in_base() -> bool:
        try:
            return os.path.realpath(os.getcwd()) == base
        except OSError:
            return False

    def _note(kind, target, **flags) -> None:
        if getattr(state, "depth", 0) == 0 and _in_base():
            misses.note(kind, target, **flags)

    class _Guard:
        def __enter__(self):
            state.depth = getattr(state, "depth", 0) + 1

        def __exit__(self, *exc):
            state.depth -= 1

    def _busy() -> bool:
        return getattr(state, "depth", 0) > 0

    def _watched(iterator, kind, target, **flags):
        """逐项转发；一项都没吐就耗尽（或目录不存在）→ 记账。"""
        it = iter(iterator)
        empty = True
        while True:
            try:
                with _Guard():
                    item = next(it)
            except StopIteration:
                break
            except (FileNotFoundError, NotADirectoryError):
                _note(kind, target, **flags)
                raise
            empty = False
            yield item
        if empty:
            _note(kind, target, **flags)

    def _glob_target(pathname, root_dir, dir_fd):
        if dir_fd is not None or not isinstance(pathname, (str, os.PathLike)):
            return None
        pathname = os.fspath(pathname)
        if not isinstance(pathname, str):
            return None
        if root_dir is None:
            return pathname
        if not isinstance(root_dir, (str, os.PathLike)):
            return None
        root_dir = os.fspath(root_dir)
        if not isinstance(root_dir, str) or os.path.isabs(root_dir):
            return None
        return root_dir.rstrip("/\\") + "/" + pathname

    real_glob, real_iglob = glob.glob, glob.iglob
    real_listdir, real_scandir = os.listdir, os.scandir
    real_exists, real_isfile, real_isdir = os.path.exists, os.path.isfile, os.path.isdir
    real_p_glob, real_p_rglob = pathlib.Path.glob, pathlib.Path.rglob
    real_p_iterdir = pathlib.Path.iterdir
    real_p_exists, real_p_is_file, real_p_is_dir = (
        pathlib.Path.exists,
        pathlib.Path.is_file,
        pathlib.Path.is_dir,
    )

    def glob_glob(pathname, *args, **kwargs):
        if _busy():
            return real_glob(pathname, *args, **kwargs)
        with _Guard():
            result = real_glob(pathname, *args, **kwargs)
        if not result:
            target = _glob_target(pathname, kwargs.get("root_dir"), kwargs.get("dir_fd"))
            if target is not None:
                _note(
                    PROBE_GLOB,
                    target,
                    recursive=kwargs.get("recursive", False),
                    hidden=kwargs.get("include_hidden", False),
                )
        return result

    def glob_iglob(pathname, *args, **kwargs):
        if _busy():
            return real_iglob(pathname, *args, **kwargs)
        with _Guard():
            result = real_iglob(pathname, *args, **kwargs)
        target = _glob_target(pathname, kwargs.get("root_dir"), kwargs.get("dir_fd"))
        if target is None:
            return result
        return _watched(
            result,
            PROBE_GLOB,
            target,
            recursive=kwargs.get("recursive", False),
            hidden=kwargs.get("include_hidden", False),
        )

    def os_listdir(path=None):
        if _busy():
            return real_listdir() if path is None else real_listdir(path)
        try:
            with _Guard():
                result = real_listdir() if path is None else real_listdir(path)
        except (FileNotFoundError, NotADirectoryError):
            if path is not None:
                _note(PROBE_DIR, path)
            raise
        if not result and path is not None:
            _note(PROBE_DIR, path)
        return result

    def os_scandir(path=None):
        if _busy():
            return real_scandir() if path is None else real_scandir(path)
        try:
            with _Guard():
                return real_scandir() if path is None else real_scandir(path)
        except FileNotFoundError:
            if path is not None:
                _note(PROBE_DIR, path)
            raise

    def _bool_probe(real, kind):
        def probe(path):
            if _busy():
                return real(path)
            with _Guard():
                ok = real(path)
            if not ok:
                _note(kind, path)
            return ok

        return probe

    def path_glob(self, pattern, *args, **kwargs):
        if _busy():
            return real_p_glob(self, pattern, *args, **kwargs)
        with _Guard():
            result = real_p_glob(self, pattern, *args, **kwargs)
        return _watched(
            result, PROBE_GLOB, _join_target(self, pattern), recursive=True, hidden=True
        )

    def path_rglob(self, pattern, *args, **kwargs):
        if _busy():
            return real_p_rglob(self, pattern, *args, **kwargs)
        with _Guard():
            result = real_p_rglob(self, pattern, *args, **kwargs)
        return _watched(
            result,
            PROBE_GLOB,
            _join_target(self, "**/" + str(pattern)),
            recursive=True,
            hidden=True,
        )

    def path_iterdir(self):
        if _busy():
            return real_p_iterdir(self)
        with _Guard():
            result = real_p_iterdir(self)
        return _watched(result, PROBE_DIR, str(self))

    def _path_bool(real, kind):
        def probe(self, *args, **kwargs):
            if _busy():
                return real(self, *args, **kwargs)
            with _Guard():
                ok = real(self, *args, **kwargs)
            if not ok:
                _note(kind, str(self))
            return ok

        return probe

    def _join_target(receiver, pattern):
        head = str(receiver)
        return str(pattern) if head in ("", ".") else head.rstrip("/\\") + "/" + str(pattern)

    glob.glob, glob.iglob = glob_glob, glob_iglob
    os.listdir, os.scandir = os_listdir, os_scandir
    os.path.exists = _bool_probe(real_exists, PROBE_PATH)
    os.path.isfile = _bool_probe(real_isfile, PROBE_PATH)
    os.path.isdir = _bool_probe(real_isdir, PROBE_DIR)
    pathlib.Path.glob, pathlib.Path.rglob, pathlib.Path.iterdir = (
        path_glob,
        path_rglob,
        path_iterdir,
    )
    pathlib.Path.exists = _path_bool(real_p_exists, PROBE_PATH)
    pathlib.Path.is_file = _path_bool(real_p_is_file, PROBE_PATH)
    pathlib.Path.is_dir = _path_bool(real_p_is_dir, PROBE_DIR)

    def uninstall() -> None:
        glob.glob, glob.iglob = real_glob, real_iglob
        os.listdir, os.scandir = real_listdir, real_scandir
        os.path.exists, os.path.isfile, os.path.isdir = real_exists, real_isfile, real_isdir
        pathlib.Path.glob, pathlib.Path.rglob, pathlib.Path.iterdir = (
            real_p_glob,
            real_p_rglob,
            real_p_iterdir,
        )
        pathlib.Path.exists, pathlib.Path.is_file, pathlib.Path.is_dir = (
            real_p_exists,
            real_p_is_file,
            real_p_is_dir,
        )

    return uninstall


def _file_not_found_in(exc: BaseException | None) -> BaseException | None:
    """异常链（`__cause__` / `__context__`）里第一个「文件不存在」：`FileNotFoundError`，
    或 errno 是 ENOENT 的 `OSError`（h5py 之类包过一层的）。"""
    import errno as _errno  # noqa: PLC0415

    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, FileNotFoundError):
            return exc
        if isinstance(exc, OSError) and getattr(exc, "errno", None) == _errno.ENOENT:
            return exc
        exc = exc.__cause__ or exc.__context__
    return None


#: C 层读取器消息里的路径：h5py 是 `unable to open file: name = '<路径>'`；其余取第一个单引号串。
_NAME_EQ_RE = re.compile(r"\bname = '([^'\n]+)'")
_QUOTED_RE = re.compile(r"'([^'\n]+)'")


def quoted_name_in(exc: BaseException) -> str | None:
    """异常消息里引号括着的那条路径（`exc.filename` 为 None 的读取器）；取不出回 None。"""
    try:
        text = str(exc)
    except Exception:  # noqa: BLE001 —— 消息都拿不到就是说不出
        return None
    m = _NAME_EQ_RE.search(text) or _QUOTED_RE.search(text)
    return m.group(1) if m else None


def enoent_fact(exc: BaseException) -> dict | None:
    """build 失败、**不是**一次落空的只读打开（`missing_input_of` 说不出）时，异常链里的「文件不存在」
    → `{filename, named, cwd}`，没有回 None（ADR 0110 §一）。

    只报事实、不归因：C++ 读取器（h5py / netCDF4 / xarray / ovito）不经四个打开入口，worker 不知道
    脚本里哪串常量对应它；归因在父进程（`inputremap.native_miss`，拿脚本的静态证据对）。
    """
    fnf = _file_not_found_in(exc)
    if fnf is None:
        return None
    filename = getattr(fnf, "filename", None)
    if isinstance(filename, (bytes, os.PathLike)):
        try:
            filename = os.fsdecode(filename)
        except (TypeError, ValueError):
            filename = None
    if not isinstance(filename, str) or not filename:
        filename = None
    try:
        cwd = os.getcwd()
    except OSError:
        cwd = ""
    return {
        "filename": filename,
        # pyarrow 的 `FileNotFoundError("cg_data.parquet")`：整条消息就是路径，没有引号也没有 filename
        "named": None if filename else (quoted_name_in(fnf) or _message_of(fnf)),
        "cwd": cwd,
    }


def _message_of(exc: BaseException) -> str | None:
    """`FileNotFoundError("<路径>")` 这类只有一个字符串实参的异常消息；有 errno 的二元形态、多行、空的回 None。"""
    args = getattr(exc, "args", ())
    if len(args) != 1 or not isinstance(args[0], str):
        return None
    text = args[0]
    return text if text and "\n" not in text and len(text) <= 4096 else None


def _named_miss(fnf: BaseException, misses: InputMisses) -> str | None:
    """异常消息里点了名的那次落空（从最近的往前找）；没点名回 None。点名按路径段边界：
    `data.txt` 不算点了 `a.txt`，`d` 不算点了 `d.txt`。"""
    text = _message_of(fnf)
    if text is None:
        return None
    text = text.replace("\\", "/")
    for item in reversed(misses.items):
        needle = item["requested"].replace("\\", "/")
        # 路径分隔符同样不是边界：`data` 不算点了 `data/file.parquet`，`file.parquet` 也不算（Codex #1297）
        if re.search(rf"(?<![\w.\-/]){re.escape(needle)}(?![\w\-/]|\.\w)", text):
            return item["requested"]
    return None


def missing_input_of(exc: BaseException, misses: InputMisses) -> dict | None:
    """build 失败的异常 → `missing_input` 的事实（缺的是哪个），说不出回 None。

    **只在异常与记账对得上时才说**（ADR 0106 §一.2）：异常链里要有「文件不存在」，而且缺的
    那一串要么就是 `exc.filename` 且它确实作为只读打开落空过（写模式的「目录不存在」不算——
    那不是输入），要么异常没带文件名、但消息里点了名某次落空的那一串（`_named_miss`）。两样都没有就不判，
    调用方照旧报 `script_error`（C++ 读取器再走 `enoent_fact` → `native_miss`）。**不再退到「最近一次
    落空的」**——那会指认与这次异常无关的路径（#1253）。
    """
    fnf = _file_not_found_in(exc)
    if fnf is None or not misses.items:
        return None
    names = misses.names()
    filename = getattr(fnf, "filename", None)
    if not isinstance(filename, (str, os.PathLike)):
        # h5py 这类 C 层读取器不带 `filename`、只把路径写进消息：说得出就按它对账——否则一次被
        # `try` 吞掉的可选读（`open("local.cfg")`）会被当成「最近一次落空」指错文件（ADR 0110 §一）
        filename = quoted_name_in(fnf)
    requested: str | None = None
    if isinstance(filename, (str, os.PathLike)):
        filename = os.fspath(filename)
        if isinstance(filename, str):
            if filename in names:
                requested = filename
            else:
                # 记账记的是「脚本写的那串」（cwd 内的绝对路径已换回相对）：按真身再比一次
                for m in misses.items:
                    try:
                        joined = os.path.join(m["cwd"], m["requested"])
                        if os.path.realpath(joined) == os.path.realpath(filename):
                            requested = m["requested"]
                            break
                    except (OSError, ValueError):
                        continue
            if requested is None:
                return None  # 带了文件名却不是一次只读打开落空的：不判
    else:
        # 没文件名、消息里也没有引号括着的路径：只认**消息里点名了的**那次落空（numpy 的
        # `"d.txt not found."`、pyarrow 的 `FileNotFoundError("cg_data.parquet")`）。点不了名就不判——
        # 不能拿「最近一次落空的」凑数：被 try 吞掉的可选读、import 机制探测不存在的 sys.path 条目
        # （importlib.metadata 的 zipfile 打开）都会排在后面，指认一个与这次异常无关的路径（#1253）。
        requested = _named_miss(fnf, misses)
        if requested is None:
            return None
    entry = next(m for m in reversed(misses.items) if m["requested"] == requested)
    parsed = remap_parts(requested)
    return {
        "requested": requested,
        "absolute": bool(parsed and parsed[0]),
        "cwd": entry["cwd"],
        "misses": names,
    }


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


#: 本地模块里出现这些名字（Name 或属性）就可能借到脚本的全局：`sys._getframe(1).f_globals["smp"]`、
#: `inspect.currentframe().f_back.f_globals`、`inspect.stack()`。
#: `stack` 单列：`np.stack` 太常见，只认 `inspect.stack` 与 `from inspect import stack`。
_FRAME_NAMES = frozenset({"_getframe", "currentframe", "f_globals", "f_locals", "f_back"})


def reaches_main(tree, script_stem: str) -> bool:
    """一个被跟进的**本地模块**能不能够到脚本自己的命名空间（评审 #555 P2）——能就说明脚本的别名可能
    被它借走（`from __main__ import smp` 之后 `smp.Symbol(...)`），「脚本里没读」不再证明没用到。

    `importscan` 对每个跟进到的本地模块调一次；任何一个为真，脚本的全部 `unused` 一律作废（按整份脚本、
    不按名字：`from __main__ import *`、`getattr(__main__, 变量)`、经栈帧取 globals 都给不出名字，按名字
    精确保留在静态上做不完备）。认的形状：

    * `import __main__` / `from __main__ import …`；以及 import 脚本自己的模块名（`entry` 不是 `__main__`
      时脚本是按 stem 作为模块 import 的，`from plot import smp` 同样借得到）；
    * 字符串常量 `"__main__"` 或脚本的 stem（`sys.modules["__main__"]`、`import_module("__main__")`）——
      **`__name__ == "__main__"` 那种入口守卫里的不算**，否则带入口守卫的本地模块全都会被误判；
    * 经栈帧取调用方的全局（`_FRAME_NAMES`）。
    """
    import ast  # noqa: PLC0415

    targets = {"__main__", script_stem}
    guard_constants: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            sides = [node.left, *node.comparators]
            if any(isinstance(x, ast.Name) and x.id == "__name__" for x in sides):
                guard_constants.update(id(x) for x in sides if isinstance(x, ast.Constant))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name.split(".", 1)[0] in targets for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if not node.level and (node.module or "").split(".", 1)[0] in targets:
                return True
            if node.module == "inspect" and any(a.name == "stack" for a in node.names):
                return True
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in targets and id(node) not in guard_constants:
                return True
        elif isinstance(node, ast.Name) and node.id in _FRAME_NAMES | {"__main__"}:
            return True
        elif isinstance(node, ast.Attribute) and (
            node.attr in _FRAME_NAMES
            or (
                node.attr == "stack"
                and isinstance(node.value, ast.Name)
                and node.value.id == "inspect"
            )
        ):
            return True
    return False


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
