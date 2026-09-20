# 职责与跨主线契约

## 1. 数据方向

```text
正常打开入口（桌面 / HTTP / MCP / 原生 CLI）
    │
    ├── 已有静态图 → 直接可用的排版/标注/导出路径
    │
    └── ProjectPreparation
          环境证据 + 完整依赖意图 + 执行上下文 + 授权
                ↓
          现有 ExecutionSpec / native invocation
                ↓
          科学 worker / 用户原生进程
          Figure 捕获、图内编辑与重放
                ↓
          SourceArtifact + ExecutionReceipt
                ↓
          SourceResolver（只冻结本次源图产物）
                ↓
          原 ExportJob 内的 RenderPlan → Render IR
                ↓
          Typography / PDF合成 / PDFium栅格运行时
                ↓
          封口的最终文件 → ArtifactInspector
                ↓
          ArtifactManifest / 有范围的Proof → 原有发布事务
```

源 PNG byte copy、位图原像素网格、科学 worker SVG/EPS 直出是正式分支，不强行经历 PDF round-trip。

## 2. 最小共享合同（在 U01 定义草案，用真实切片修订）

| 合同 | 唯一责任 | 最小字段/约束 |
|---|---|---|
| PreparationPlan | 准备编排 | 项目/入口/revision、原选择与候选理由、Python要求、DependencyIntent引用、LaunchContext、grant、预算；不是实际执行证明 |
| LaunchContext / ExecutionSpec | 现有 execspec/workdir | interpreter、target、原 argv、cwd 来源、授权根/绑定版本、写入模式；旧 project 值仍是 script.parent |
| DependencyIntent | 现有 depresolve/deprepair 的无损扩展 | name/version/extras/marker/selected group/source/constraints；unknown不是空依赖 |
| SourceArtifact | 捕获/源解析 | source ID、实际产物 bytes hash、类型/尺寸、实例/override身份；文件归属和生命周期明确 |
| ExecutionReceipt | worker自报+控制面关联 | 实际 Python/prefix/关键包版本、实际 context、source revision、generation、产物；输入观察 completeness可partial |
| RenderPlan / IR | 导出编译/渲染 | 规范化原 ExportRequest、资源引用、顺序/单位/变换/clip、已排字形；无文件扫描/包安装/任意脚本 |
| ArtifactManifest | 检查与导出结果 | plan/observed/policy 分开；artifact hash、执行回执引用、检查范围、退化与unknown；发布后不改已核验字节 |

一个权威合同可放在既有模块，名称不是强制的新类名。不要复制旧 request 默认值、run 命令解析或错误枚举。UI 与 MCP 消费同一准备结果；允许为旧客户端做明确投影。

## 3. 三种身份用途不能混同

本机环境路径/prefix/配置状态可进入**私有失效键**；不必为了追求跨机器稳定而丢掉区分两个 venv 的关键信息。公开语义身份只包含可公开的规范化意图及获准来源身份。最终文件 hash 是另一个字段，不能把它再写入自身形成自引用。

旧热 Figure 代表当时的数据结果。数据后续改变时，普通导出可以按明确旧快照继续，不自动清空编辑重新计算；请求重新计算/写回时按各自冲突政策复核。SourceResolver 不承担完整实验目录备份，也不复制脚本破坏 __file__。

## 4. 原生库和运行环境

科学 worker 与应用 PDF runtime 不混包。PDFium 生命周期集中，进程内部串行；首轮采用有界单个应用 render child + 排队即可，实测需要再扩多个进程。进程隔离、内存预算和异常恢复依然要做，但不重建 workerd 或通用分布式调度层。

fontTools/HarfBuzz/成熟PDF写入适配的选型由 U02 实证。字体策略与实际写入要支持首轮批准字符/字体集合，未实现字体类型明确边界；不能先宣称“支持所有字体”，再让测试负责证明不可能的范围。

## 5. 数据定位的硬边界

恢复正确 cwd 能修复相对路径语义，但不能普遍修复脚本硬编码的失效绝对路径。只有真实接入的参数、已支持的数据绑定机制或已验证工作区映射能改变输入位置；“记住一个目录”本身不是重映射实现。核心测试先使用可表达的真实上下文，硬编码原生绝对路径迁移放X01并如实提示。

新代码不得增加任意读盘/执行调试 API 来方便测试。测试若缺观察口，优先使用有限产品协议、已知合成数值、文件/进程外部证据，单独论证最小诊断接口。
