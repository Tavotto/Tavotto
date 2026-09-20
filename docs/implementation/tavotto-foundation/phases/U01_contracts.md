# U01 · 用最少合同连通两条主线，测试从这里开始

**前置 milestone：** U00.baseline。先读总提示词及当前有效handoff；遵循03门禁政策。


## 实际实现

在现有模块旁定义或扩展PreparationPlan/Result、ExecutionReceipt最小载荷、SourceArtifact、RenderPlan引用，分清计划/观测、私有/公开身份。先用现有一个真实worker产生最小receipt和source，不做只有Protocol的假进展。

FirstOpenBench/RenderBench共享结果装配与fixture资源，但保留不同case和结论。先实现实例清单、参数/结果schema、身份校验、错误分类及最小真实既有产品路径；不要构建通用CI平台。

新gate enrollment在当前仓库找最小落点，初期作为已有job的步骤。读取03的planned/observing/enforced政策：未来case在台账，不因缺实现全部进入pytest默认发现或required矩阵。观察失败使用清楚命名的独立任务，不让普通PR从此常红。

校验stage scope、case scope、预期实例集合与结果分离；生成JSON/JUnit/摘要接入现有上传方式。纯契约校验始终真跑，不得用“空集合所以通过”冒充产品资格。

为新增异步准备接口复用现有会话认证，第一版能报告现有runtime正在运行、错误、取消和静态源可用；复杂依赖下一阶段补。新权限模型不重复造trust系统。

## 出口与测试

一个真实旧后端首开/导出fixture穿过公共入口；纯模型不依赖科学栈/native对象；错项目/错generation/空报告/重复ID/错SHA负例失败。其他32场景尚未实现可以planned，必须在报告中与“已通过”分开。

本阶段只把本切片的真测试纳入现有快线；不新造需要完整安装VM的required job。
