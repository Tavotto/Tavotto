# RefreshRequest raw row 类型修复：独立只读复核

结论：单行修复正确，未发现行为回归或新增 fail-open。它移除 raw SQL row 与公开返回类型对同一字段的矛盾交集，保留现有数字→布尔/省略映射，未使用 any、类型抑制或改变查询/预算。实际完整 tsc 未在本审查环境运行，仍由后续编译检查确认。

## 基线与精确范围

工作树 `/workspace/Tavotto-cla-confirmation-slot`，HEAD `dc2431f725798bd78c40a3b07e0a8054690fe82c`，审查时仅 `services/cla-cloudflare/src/native/store.ts` 一行未提交 diff：

- 第 152 行：`first<RefreshRequest & {capacity_blocked:number}>()` 改为 `first<Omit<RefreshRequest,'capacity_blocked'> & {capacity_blocked:number}>()`。
- 旧 store SHA256：`f56a0d9effb65a44ebb118ee33fd1611340361ea4bce9f5c3a31f77690dfad83`。
- 新 store SHA256：`35c694576c1a8e4b6630ed6337f26a5a9e39fe55ddaa38dc46e31a50b672d140`。
- 独立脚本在开始和结束校验新源文件字节一致。

## 正确性与边界

1. store.ts 第 28 行定义的公开 `RefreshRequest.capacity_blocked?:boolean` 表示已转换的调用方数据；第 146 行 SQL 则通过 `CASE WHEN (...) THEN 0 ELSE 1 END` 产生 required 数值列。旧交集强迫同一属性同时为 boolean 和 number，导致不可能的交集；这与父线程提供的 TS2700 定位一致。本轮未重新执行旧 TypeScript 编译，不将父线程编译日志计为本 agent 实测。
2. `Omit` 只移除公开模型中的冲突属性，保留 target_key/source_attempt/revision/status/attempts/due/reason 的全部约束，再以 required number 描述 raw DB row。没有将整个 row 放宽为 any。
3. 第 153 行保持原映射：缺行返回 null；数值 0 在返回对象中省略 capacity_blocked；数值 1 返回 capacity_blocked:true。输入 row 解构后不被修改，其他字段保留。background.ts 第 41 行仍以布尔字段决定 capacity backpressure。
4. SQL `CASE` 在本地 SQLite 上对 TRUE/FALSE/NULL 分别产生数字 0/1/1；即使 WHEN 值为 NULL 仍走 blocked 分支。实际运行时没有依靠 TypeScript 泛型来校验 SQL 数据。
5. SQL、绑定参数、结果转换、调用次数、运行时代码均未变化。这行修复不影响 slot pointer、撤销/签署恢复、publication 或已有预算/lease guard；没有新增可见功能。

## 实际独立验证

命令：`node /tmp/cla-refresh-row-type-review.mjs`，Node v24.19.0，实际退出码 **0**，**6 组通过 / 0 失败**。

- 精确比较 HEAD 与工作区：唯一差异恰是上述泛型替换。
- 用 Node 内置 `stripTypeScriptTypes(...,{mode:'transform'})` 对完整旧/新 store 转换；输出 JavaScript **逐字节一致**，共同 SHA256 `ddcaa66c6744ccc9db4f0f66f9960d4165156c4555adf924f0a1fbe455dd695a`。这是 Node 转换器的实测等价，不冒充 TypeScript 编译通过。
- 直接调用实际 `LiveStore.prototype.refreshRequest`，注入固定只读 D1 statement stub，验证 raw 0、raw 1、null 三条边界、参数顺序、单次 statement/first、输入不变及所有返回字段。
- 本地内存 SQLite 执行同型 CASE，验证实际数字类型和 TRUE/FALSE/NULL 值。

证据：

- `/tmp/cla-refresh-row-type-review.mjs`：独立脚本。
- `/tmp/cla-refresh-row-type-review.json`：指纹、逐组结果。
- `/tmp/cla-refresh-row-type-review.log`：原始输出。

## 未完成验证与范围限制

当前 worktree 没有 `services/cla-cloudflare/node_modules`，PATH 也没有 tsc。只读查找未找到可用本地 compiler（一次 /tmp 搜索遇到无关 codex daemon 目录的 permission denied；没有申请权限或访问其内容）。没有下载依赖或声称完整 typecheck/build/全回归通过。后续以父线程传播后的各新 head 实际 typecheck/build/check 结果为准；没有请求外部 review。

没有修改 workspace，没有调用 HTTP、worker.fetch、生产、签署、发布或下载。全部新证据写在 `/tmp`。
