# 写回事务（prepare → verify → commit）

> 原文出自 `src/tavotto/AGENTS.md`「诊断与排障」（2026-09-17 指导文档治理时按主题拆出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src/tavotto/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

- 「写回原始文件」撞上 Windows 独占锁（PDF 被阅读器打开）回 409
  `code=file_locked`，带上是哪个文件、回滚结果如何，并清掉 `.updating`
  半成品——不接住的话用户拿到 500 + traceback，图库里还多个垃圾文件。
- **写回是个事务（`_write_source_files`，数据损坏级）**：不变式是
  **热态所见 == 写进文件的 == 重开后重放出来的**，三段
  prepare → verify → commit，任一环不过一律 409 且**原文件零改动**。

  * **prepare**：请求可带 `expected_mtime`（前端 assetStore 里该素材的
    mtime，只比**点名的那一份**——同 stem 的另一载体客户端没有 mtime，硬比
    必然误报），对不上回 409 `source_changed`；worker 在 spawn 时记下脚本的
    `script_sha1`（`pool.script_sha1()`，两条控制面同源），写回前重算，
    对不上回 409 `script_changed`——watcher 轮询 2 秒，那个窗口里热会话跑的
    还是旧代码，而写回是覆盖原件的动作。
  * **verify**：staging 的 PDF/PNG **由 `pool.one_shot()` 起的一次性 worker
    全量重放产出，不用热会话**。热会话是增量的，「现在的样子」未必等于
    「从零按这组 patches 重放一次的样子」（FigS3 事故就是这个差）。一次性
    worker 不进 `_workers`，目录独立（`cache/engine/_replay-…`，登记进
    `_oneshot_bases` 免得被 prune 删掉），workerd 那边靠独立 out_dir +
    `TAVOTTO_REPLAY_NONCE` salt env 绕开 spec 哈希复用，用完 `discard()`——
    它返回时**进程已被 wait 回收、句柄已关、exact base 已删、`_oneshot_bases`
    已注销**（注销排在删除之后：重试期间目录仍算 active）；删不掉也注销并留日志，
    好让 `prune_engine_cache()` 还有机会回收。写回的泄漏用例只对**本次
    `one_shot()` 建出来的 base** 负责，不许再用 `ENGINE_CACHE.glob("_replay-*")`
    断言全局为空——ENGINE_CACHE 全进程共用，那样会把别的测试文件的残留算到
    自己头上（run 32937999297 就是这么误报的）。
    同一次写回只 build 一次（override + PDF + PNG 共用）。
    热会话最后应用的正是这组 patches 时（`worker.last_patch_hash`），把两份
    manifest 逐元素比 bbox/anchor（容差 0.5% figure 分数）与 size_mm（0.01mm），
    有分歧回 409 `replay_divergence` + 分歧清单。几何过了还要过**像素门**
    （ADR 0049，issue #81）：两侧各出一张 `render_png` 探针图逐像素比——
    颜色 / 线型 / 字体 / 透明度这类几何不变的纯属性分歧只有像素量得到
    （PR #49 的 facecolor 恢复顺序 bug 报了 0 处分歧）。比较器是
    `pdfbackend.compare_png`（判据结构与 `scripts/ci/pixelcompare.py` 同构但
    **逐 RGBA 通道比**——等亮度换色与纯 alpha 差异灰度量不到；灰度等值图上
    两份一致，对拍用例钉住，Flask 边界内不许 import 科学栈），阈值
    `app.REPLAY_PIXEL_TOL`；分歧作为 `field: "pixels"` 进同一份清单。
    **热态不是这组 patches 就不比**（历史恢复、跨面板同步都是），响应据实回
    `replay: "fresh_only"`——假报一次，用户学到的就是「这个提示可以无视」；
    比过且过了才在 `verification` 里报 `pixels: "ok"`。manifest 经 JSON 落盘，
    numpy 标量可能被 `default=` 写成字符串，比之前一律 `float()` 化。
    worker 的 warnings（元素不存在 / 属性不支持 / 应用失败 / 还原失败）
    **一条即阻断**，回 409 `code=write_back_warnings` + warnings 列表。
    staging 阶段**任何异常都要 unlink 掉所有 `.updating` 临时文件**
    （以前只有 file_locked 那条路径清理，PDF 成功 PNG 失败就留垃圾）。
    三个失败出口（verify / 落盘准备 / 替换撞锁）的清理都走 `_discard_updating`，
    **尽力而为**：清不掉（Windows 短暂锁住）只记日志，第二个 OSError 不许盖掉
    触发清理的原错（Codex #595 P2：verify 段以前没兜，409 又变回 500）。
    一次性 worker 在 verify 段崩溃 / 超时 / 缺依赖（`WorkerError`）同样是
    **409**，响应是 worker 错误体原样（`code` / `traceback` / `module`，前端
    按 code 出文案）外加 `stage: "verify"`——commit 段不调 worker，走到这里
    原件必然零改动（QA 2026-09-24 SCI-04-B1；以前回 500）。
  * **commit**：备份 → 落盘 → 逐个 `tmp.replace(target)`。备份与替换**两轮不许
    交错**（QA 2026-09-24 SCI-05-B1：「备份一个、换一个」时第二个目标备份撞上
    磁盘满，PDF 已换、PNG 未换、`.updating` 残留、500）。备份目录**每次写回独占**
    （`_new_backup_dir`：`<月日_时分秒>`，同秒已占用接 `-2`、`-3`，
    `mkdir(exist_ok=False)` 即占有）——按秒共用时，同一 stem 一秒内写回两次，
    第二次会覆盖掉第一次写回前的原件备份、失败清理还会删掉它（Codex #595 P2）。
    **落盘**（ADR 0023 §3.1，issue #252）分两半、处置相反：任何 replace **之前**
    先拷齐备份，并 fsync 备份、staging、备份目录与新建目录的父目录——任一步失败
    （建目录 / 磁盘满 / 权限 / fsync）一个原件都还没动：删掉本次的备份（含半截的）
    与本次目录、清 tmp，回 409 `write_back_persist_failed`（params `reason`）；
    整个替换循环**之后**再尽力 fsync 各目标的父目录，失败只记 ERROR、不回滚、
    不改响应。后者绝不许并进替换循环的 `except OSError`（也就是不许直接换成
    `atomicio.publish_file`）：那会回滚别的目标、独独留下刚换好的这一个，还对
    用户报「已回滚」。
    第 2+ 个撞锁时**把已经换掉的从本次备份恢复回去**（PDF 新 / PNG 旧比整件事失败糟糕得多），
    响应带 `rolled_back` / `rollback_failed`，`updated` 的语义是「仍处于已被
    换掉状态的文件」（回滚成功即为空）。落盘后用 `probe_asset` 比页面尺寸与
    重放 manifest 的 size_mm（容差 0.5mm），不符只记 ERROR + 响应
    `post_check: "size_mismatch"`——**此时不再自动回滚**，文件已换、备份仍在，
    如实报告就是最有用的。
  * 成功响应（update_source 与 history/restore 同构）：`updated` /
    `backup_dir` / `warnings: []` / `patch_hash`（patchspec 权威）/
    `source_sha1` / `manifest_hash`（canonical JSON 的 sha256）/
    `verification`。baked 版本条目同样带 `patch_hash`（旧条目无该键，读取兼容）。
  * 代价要认：每次写回都多跑一遍脚本（heavy 的分钟级）。这是正确性优先的
    自觉取舍，**不许为省时间跳过 verify**。
  * `/api/export`（画布合成）同样收集 warnings，但**只透出不阻断**——成图
    已经出来了，前端在结果区列出。
  * 「写回可携带画布标注」的前端换算规则见 `web/AGENTS.md`；后端入口是
    `pdfbackend.annotate_asset`（导出合成同一组 `_draw_*` 矢量），只有 PNG 的
    素材回 `annotations_need_pdf`。
  * 看护：`tests/test_write_back.py`（假 worker，全部分支）+
    `tests/test_worker_roundtrip.py` 末节（真 matplotlib + Flask 全链路，
    含 workerd 路径的一次性会话不泄漏）+ `web` 的 `WriteBackDialog.test.tsx`。

## 速查表原要点（2026-09-25 迁入，#608）

`src/tavotto/AGENTS.md` 那一行的「必守要点」从这天起只留索引（Codex 自动拼接的 32 KiB 上限，#608）。
下面是当时写在那一格、而本文上面没有逐字出现的要点，原文照搬、一字未改；
它们与上文同等有效，改规则时一并改这里。

- `expected_mtime` / `script_sha1` 两道 prepare 校验
- verify 用一次性 worker 全量重放 + 几何 + 像素（`pdfbackend.compare_png` 逐 RGBA 通道）
- 热态不是这组 patches 就报 `fresh_only` 不假比
- commit 第 2+ 个撞锁回滚
- 泄漏断言只对本次 `one_shot()` 的 base 负责
- verify 段 `WorkerError` 回 409（worker code 原样 + `stage: "verify"`）
- commit 先备份全部并 fsync 备份与 staging 再替换（失败 409 `write_back_persist_failed`，删本次备份），备份目录每次写回独占（同秒接 `-2`），替换后目录 fsync 只记日志不回滚
- 三个失败出口清 `.updating` 都尽力而为不盖原错
