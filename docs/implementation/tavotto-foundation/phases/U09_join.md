# U09 · 从正确执行到正确产物的一条证据链

**前置 milestone：** U03.existing_open、U08.facade_parity。先读总提示词及当前有效handoff；遵循03门禁政策。


**两个独立出口：** `existing_env_join` 使用U03已有环境+U08新输出，不依赖私有Python；`managed_env_join` 另需U04与U05完成，并验证产品自行准备环境的整链。先取得前者即可推进U10替换；本轮最终综合资格U11必须两者齐备。不要把后者未完成变成PDF退役的循环等待。

## 实际实现

完善U01最小ExecutionReceipt：来自真正worker/native的Python/prefix/关键包/实际cwd与binding revision，控制面绑定generation/source revision/源图hash；不能复制前置probe充当本次事实。已观察的本地模块/数据身份保留，原生I/O/网络无法完整观察时标partial/unknown。

RenderPlan关联receipt和SourceArtifact；Manifest含来源、实际文件检查、规范结论、artifact/render/semantic/run身份。内部完整路径留本机，公开XMP/报告/遥测不携带秘密或科研正文。单纯hash不证明跨平台复现。

Trace默认有界并能定位当前错误阶段；不建全系统追踪平台。节点只保留真实知道的科学gid/来源关系，opaque页面不编造内部语义。

UI正常打开到输出、MCP准备/需要输入/导出统一结果；环境、数据绑定变化触发正确旧计划失效，不能丢掉用户的live编辑。静态已有成果与重新计算区分；native不靠自动杀进程来模拟重放。

## 核心联合实例

项目Python与应用不同，真实h5py，外部HDF5[2,4,8]、沙盒同名干扰[200,400,800]，中文空格路径。原上下文明确/已授权时自动；歧义时一次选择。独立真值绘图[7,13,25]，不能[601,1201,2401]。

通过真实UI或认证HTTP/MCP首开，做可见patch，新应用worker重放，再新RenderCore导出PDF/PNG/TIFF适用组合；实际科学值、源revision和finalhash对应。两科学环境都可向同一个应用渲染器交图，科学环境不需要新增PDF依赖。

## 收口

FO32先在当前可用目标通过，然后平台资格在U10/U11完成；缺最终签名不能阻止修复联调代码，仍不可宣称发行ready。旧后端早期FO成功记录与此时新终点分开。

至少测试错receipt、过期generation、错误同名数据、数据预检后变化、raw秘密泄漏、报告假绿色。可复现性核心是明确环境下的语义/视觉；严格byte模式和全数据证明非本轮硬目标。
