# CI00 · 把真实瓶颈和现有保护先量清楚

前置：无。先读总提示词、审计、当前AGENTS，检查HEAD/status。只读或增加不改变测试结论的计时，不切runner、不改变测试覆盖、不删除原CI。

## 实际实施

1. 读取所有相关workflow、reusable workflow、pytest/vitest/Playwright配置、package scripts、构建脚本、Gate脚本和测试。追踪真实命令，不按job名字判断快慢。读取runner/lab/发布规则和支持矩阵。
2. 获取当前可访问Actions run/job/step元数据与必要日志，正确分页。优先最近一批PR与合并组，覆盖成功、失败、取消与多次attempt；时间窗口和样本量写进报告。API字段不可访问就如实记录，不以created到updated随意冒充queue或运行时间。
3. 产出DAG：每条needs标`artifact/data`、`短预筛`或`verdict-only`。分别记录前提等待、runner分配、工具准备、测试、构建、上传。基于实际关键路径排序最值得改的三项。
4. 运行相关suite的计时收集，包含setup/call/teardown。先在当前串行方式取基线，不改变random seed/依赖以让它更快。明确原默认pytest是否排除slow以及插件/字体前提，列出跳过在哪里真正补验。
5. 写覆盖账：旧测试集和版本/平台/入口、PR与合并前位置、所需产物、对应Gate、拟新位置。记录必须串行的全局state、端口、目录、共享缓存、进程reaper。
6. 管理员只读清点资源：16/32是host还是guest，其他VM、CPU可分配额、真实RAM、SSD空间/IO、网络和runner注册位置。无访问权限就生成最小表，状态not_run；不自动创建VM。
7. 输出基线JSON与Markdown。性能采集器若新增，应从现有scripts/ci风格复用；其解析错误不得产生成功空报告。单元测试用固定API样例证明分页/attempt/时间处理，真实token只走环境且不落盘。

## 验收

至少有一个可核验真实run时间分解；能定位样本中的backend全量和Windows长E2E；不将dependency wait误称runner排队；全部旧必需领域有映射；机器未知信息明示。没有改变现有Gate真假。

交付CI_BASELINE.md/json、coverage ledger初版、真实DAG和CI01–04优先次序。时间不足以统计p95就不输出p95；本阶段无需先跑所有OS全suite才能写出事实清单。
