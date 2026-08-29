# 全阶段共享规则

以下规则适用于 23 个 Claude Code Session。每个 Prompt 已重复最关键边界；此文件用于人工审查、恢复和统一决策。

## 1. 任务性质

目标仓库为 `Tavotto/Tavotto`。这是一次真实产品实现，不是概念方案。每个 Session 都必须：

- 阅读当前仓库而不是假设三天前的结构仍然成立；
- 修改生产代码和测试；
- 运行真实验证；
- 更新跨 Session 交接；
- 如实报告未完成和失败项。

路径、组件名或框架若已变化，按当前架构适配；不得因 Prompt 中的旧路径不存在就停下来，也不得把仓库恢复到 Prompt 编写时的旧版本。

## 2. 优先级

发生冲突时严格按以下顺序：

1. 用户数据和编辑结果不丢失；
2. 预览、恢复和导出结果正确；
3. 现有项目与文件格式向后兼容；
4. 不自动执行用户脚本或产生科研代码副作用；
5. 单图快速编辑流程足够直接；
6. 画布、多选和高级出版能力；
7. 视觉精简与动效。

不得为了界面更漂亮牺牲保存、迁移、撤销、导出或错误可见性。

## 3. 核心产品合同

### 文档合同

明确区分：

- **用户文档数据：** 对象位置、尺寸、裁剪、覆盖、文字、样式、画布、用户选择的规范快照；
- **项目派生数据：** 脚本路径、registry 映射、源文件状态、素材 fingerprint；
- **渲染缓存：** worker、预览图片、临时矢量或位图；
- **UI 会话状态：** 当前面板、侧栏折叠、弹窗、hover；
- **恢复数据：** autosave、crash recovery、历史检查点。

派生数据刷新不得无故把文档标成 dirty，也不得进入普通撤销历史；用户修改必须进入事务、dirty 和保存链路。

### 两种工作流合同

- **快速编辑：** 按原图/原 Figure 规格工作，不要求用户先配置画布；
- **画布排版：** 多图布局、毫米尺寸、栏宽和组合导出；
- 两者必须共享对象、属性、撤销、样式、检查和渲染底层；不得复制两套编辑器。

### 样式与规范合同

- Style 决定“图长什么样”；
- Spec 决定“图需要满足什么要求”；
- Export 决定“文件如何生成”；
- Validation 只读取统一 Spec，不在多个页面硬编码阈值；
- 默认最小字号只保留 8 pt。

## 4. 绝不静默执行用户脚本

打开项目、自动保存、watcher、readiness、问题检查和自动刷新可以进行静态扫描、读取 AST、合并 registry、计算 fingerprint 和验证文档。

它们不得：

- 调用用户 `main()`、`render()` 或 `__main__`；
- 自动 probe；
- 自动启动 Codex、Claude 或 shell；
- 自动安装依赖；
- 执行可能有副作用的科研工程代码。

任何运行、probe、重建、包安装都必须由用户明确触发，并显示目标环境与结果。

## 5. 文件与迁移安全

- 不使用 pickle 等不安全格式加载不可信文档；
- 所有写入优先采用临时文件、fsync（适用时）和原子替换；
- 文档必须有 schema version 和显式 migrator；
- 路径优先使用项目相对路径；
- 外部修改冲突不能静默覆盖；
- autosave 不得替代最后一个可靠手动检查点；
- 删除、reset 或迁移前保留可恢复副本；
- 项目移动到另一台电脑后应尽可能恢复。

## 6. 复用现有架构

优先复用当前仓库已有的：

- Flask/当前后端框架；
- Python 标准库；
- worker pool 与项目隔离；
- SSE 或现有事件通道；
- Zustand/当前前端状态层；
- documentStore 的事务、撤销和自动保存；
- store/actions；
- QuickEdit、ContextBar、ArrangeSection；
- i18next、Radix UI、design token 和 motion 常量；
- Tavotto run 已有的环境、依赖安装与诊断能力。

不要：

- 为一个 UI 页面复制底层写入逻辑；
- 在 React 组件里直接散落磁盘格式转换；
- 为 desktop/browser/embedded 各做一套核心实现；
- 无必要引入大型 watcher、tour、state 或 UI 库；
- 把临时兼容层当作最终架构。

## 7. UI 与文案原则

- 默认界面只显示完成任务所需信息；
- 解释放入问号、tooltip、详情或高级选项；
- tooltip 不能成为唯一可访问说明；
- 不向普通用户暴露 AST、registry、stem、manifest、PyMuPDF、内部对象 ID、安装路径等实现术语；
- 状态文案必须说明“发生了什么”和“用户可以做什么”；
- 不使用无意义卡片、重复摘要和重复字段；
- 固定外壳应保持稳定，但必须响应较小窗口和系统缩放。

## 8. 导出与渲染一致性

- 原图导出不得偷偷套用画布缩放；
- 画布导出必须忠实于画布；
- PDF、PNG 和预览应尽量来自同一语义渲染源；
- 不允许预览正常但导出缺字、方框或错位；
- 输出失败不得留下半文件；
- 覆盖已有文件必须明确；
- 格式不支持某能力时必须清楚降级，不得伪称矢量。

## 9. Git 安全

每个 Session 开始：

```bash
git status --short
git branch --show-current
git log -5 --oneline
```

要求：

- 不覆盖无关未提交改动；
- 不使用 `git reset --hard`；
- 不改写历史或 force push；
- 默认不自动 commit；
- 不删除失败测试来制造绿色；
- 不留调试日志、临时文件或占位实现。

## 10. 依赖与供应链

- 不擅自升级依赖；
- 新依赖必须说明必要性、体积、许可证和打包影响；
- 包管理功能只能操作 Tavotto 管理的环境，不修改系统 Python；
- package spec 必须结构化校验，禁止 shell 拼接；
- 不捆绑或分发无授权字体、图标或第三方二进制。

## 11. 测试真实性

不得：

- 伪造测试结果；
- 只跑新增测试却声称全量通过；
- 使用错误命令制造假绿；
- 用长固定 sleep 写 flaky watcher/autosave 测试；
- 只截 UI 图而不验证状态和磁盘结果。

前端完整类型和生产构建以仓库真实命令为准；若仍使用 pnpm，通常至少运行：

```bash
cd web
pnpm test
pnpm build
pnpm i18n:check
```

后端通常至少运行相关 pytest，并在阶段门禁运行全量测试。

## 12. i18n 与无障碍

所有用户可见文案同时提供自然中文和自然英文。不要把翻译后的字符串存进长期文档或 history；存 message key、枚举和结构化参数。

新增 UI 必须：

- 有可读 aria-label；
- 可键盘操作；
- 保持 focus-visible；
- 不新增 nested interactive；
- 支持 `prefers-reduced-motion`；
- 中英文、125%/150% 缩放下不溢出；
- 颜色不是唯一状态表达。

## 13. 隐私与遥测

核心功能与遥测完全解耦。只有用户已同意匿名遥测时，才可发送固定枚举的粗粒度事件。

不得发送：

- 文件名、路径、脚本名、stem；
- 图中文字、科研数据、用户输入；
- 包管理日志全文；
- Agent 登录、模型或账号信息。

## 14. 性能

- watcher 空闲时不能持续高 CPU；
- autosave、validation 和素材刷新必须防抖并可取消旧请求；
- 不在每次 pointer move 深拷贝整个文档；
- 大文档操作应避免阻塞主线程；
- 新缓存必须有失效、生命周期和内存上限说明。

## 15. 跨 Session 交接

每阶段结束更新：

```text
docs/implementation/product-ux-reliability/STATUS.md
docs/implementation/product-ux-reliability/SESSION_HANDOFF.md
docs/implementation/product-ux-reliability/TEST_MATRIX.md
docs/implementation/product-ux-reliability/DECISIONS.md
```

涉及合同变化时同步更新 `ARCHITECTURE.md` 和 `UX_CONTRACTS.md`。

交接至少包含：目标、实际完成、关键 API/类型/格式、迁移、修改文件、测试命令与真实结果、尚存限制、下一阶段不变式、工作树状态。
