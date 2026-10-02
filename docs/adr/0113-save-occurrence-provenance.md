# ADR 0113：逐次保存的来源事实，先于产物图幅切换

日期：2026-10-02 · 状态：Accepted（第一阶段；不启用新图幅、不迁移旧排版）
相关：[0098](0098-savefig-frame-is-the-figure.md)、[0003](0003-worker-protocol-v1.md)。

## 问题与范围

现有 `savefig_calls` 属于**第一个认领 stem 的 Figure**，只记保存参数、不记目的路径。
因此它不能回答「这个 PNG / PDF 是哪次保存」：另一张 Figure 可以覆盖同一路径，
同一张 Figure 也可以修改后再次保存。把扩展名补进请求不能恢复这些事实。

本阶段只补不会改变渲染的观察记录，供后续选取保存边界使用。`savefig_calls`、
旧的 stem 去重、图幅、descriptor fingerprint、编辑与写回的语义全部保留。
它**不是历史 Figure 快照，也不是混合格式错图的修复完成**。

## 一份有界的记录

三条入口共用 `figcapture.SavefigObservations`，在旧 stem 去重之前观察每次路径保存。
v1 build / playground load 响应增加 `savefig_observations`：

- `version: 1`：本观察结构的版本，不是文档或 worker 协议升版
- `observed_count`：这次执行遇到的路径保存次数，超出预算仍计数
- `complete`：是否保留了每次观察；丢过一条就是 false，后续不能推断「最后一次保存」
- `records`：`occurrence`、`figure_ordinal`、`stem`、`destination`、`options`、
  `figure_size_inches`、`figure_dpi`、`resolved_dpi`、`backend`、`result`

`occurrence` 从 1 起，`figure_ordinal` 按首次遇见对象的次序分配；两者**只在本次执行内**
有效，不参与跨重放身份。Figure 只留弱引用，不延长用户对象的生命。参数取现有
`savefig_call` 的值，图幅 / dpi 在调用时复制；后来的 Figure / 参数修改以及响应方修改
返回对象都不能回写先前记录。`dpi="figure"` 的实效值按 matplotlib 的 `_original_dpi`
取，不把临时绘制 dpi 当成原始 dpi。自定义 backend 只标 `custom`，不声称默认 renderer。

每次执行最多 128 条，每条 JSON 最多 8192 UTF-8 字节（记录总量至多 1 MiB）。超限、
数值不合法、观察失败都把 `complete` 置 false；不打印异常、路径或用户对象，不调用
任意 repr，不额外 draw。stream / 已打开的文件句柄不算路径产物，不伪造目的地。

## 路径与保存结果

路径按**调用当刻的 cwd**解析（含脚本里的 chdir），保留目录层级，不以 basename / stem
匹配文件。显式 format 不改文件名。无后缀字符串的缺省格式由 canvas 决定，未观察到就
报 unresolved，不能用 rc 的格式提示猜后缀；bytes 文件名原样保留，未显式指定的格式为
未知（matplotlib 不对 bytes 推断 / 补后缀）。不额外调用可重载的 canvas / PathLike 方法。

- `destination.scope="project"`：落在已知项目根内，`path` 为项目相对路径
- `"execution"`：只证得落在执行根内，`path` 为执行根相对路径；沙盒相对路径不冒充项目路径
- `"unresolved"`：根外或自定义 PathLike 无法无副作用地解析，`path=null`

没有机器绝对路径进入新记录；含绝对路径的别名 stem 同样不发。路径解析只是观察，
不放松沙盒、写入守卫或认证，不把相对路径映射到「看起来同名」的原件。

`result` 分 `intercepted`（safe / playground 吞掉写盘）、`saved`（native 原调用正常返回）、
`failed`（native 原异常原样抛回）与进行中的 `pending`。`complete=true` 仅说明记录完整，
不证明这些保存都成功；更不证明 safe 被拦下的保存本来一定会成功。
native 原保存只调用一次，返回值 / 异常不变；引擎自己的预览与导出不进入这份记录。
尺寸/DPI 只读受信任标准 Figure/Bbox/数组的原始数字存储，不调用用户 getter。自定义 Figure、
被替换/遮蔽的元数据 getter 或未知数值仍记 occurrence/身份/result，元数据为 null 且 complete=false。

## 后续启用的门槛

1. 验证选中的真实产物路径与保存记录；不完整或无法确认的来源必须说明，不能猜
2. 证明保存**之前**的独立 baseline 与之前保存造成的布局变化。普通 Figure 的复制实验
   不是通用快照证明：pyplot manager、闭包、全局变量、自定义 artist / layout 都需边界
3. 明确旧面板的坐标基准并保护已保存排版。`pos_frac` / `loc_frac` / `endpoints_frac` /
   `axes.position` / `size_mm` 依赖旧图幅，不能只换图幅后重放；参考 ADR 0098 的换基模型

每个面板 / 变体的 overrides 保持独立。共享执行基础设施与编辑语义，不等于共享可变编辑。
原图导出、配对预览、重放、写回仍须同一来源上下文；prepare → verify → commit 不减步骤。
本阶段不更改这些消费方，不承诺跨环境的物理像素完全相同，也不更改 native 屏障语义。

## 看护

`tests/test_savefig_observations.py`：不同 Figure 同 stem、同 Figure 多次修改、路径 / cwd /
格式、DPI、未知与预算、worker / playground 对拍、原件不动、paper_style 别名。
`tests/bridge/test_bridge_savefig_observations.py`：真 native 与普通执行的文件字节、保存异常、
stream 透传相同，成功 / 失败记录如实区分。旧捕获与协议用例继续看护未改变的语义。
