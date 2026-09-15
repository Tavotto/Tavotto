# CI01 · 先解开长串行边，保持合并前资格

依赖CI00。读取02_EVENTS_DAG_GATES。以最小PR先改一个确定无数据依赖的等待边，不同时重构所有workflow。

## 实际实施

1. 确认package/windows-exe-smoke/macos-app-smoke/posix-e2e各自真实输入。存在`needs:backend-fast`但不消费其输出时，移除判定依赖或只保留短质量预筛。不要删除frontend artifact生产者的必要边。
2. 最终Gate仍等待所有必需测试、构建和平台消费者。下游先启动不代表可以先发行；任何祖先判定失败均让对应资格失败。为一条测试失败但构建成功的反例写聚合用例。
3. 核验当前默认分支可信聚合器与候选consumer接口；先兼容后切换。同步needs/required/workflow契约测试、CLA/CodeQL边界，不改ruleset来放行当前PR。
4. 把原三层建议落成事件表。Ready追加只是选项；先保留可解释的同一PR代表集也可。若做草稿/非草稿区分，覆盖opened/synchronize/reopened/ready_for_review/相关label和full-ci；merge_group不读取不存在的PR字段。
5. 保持PR新提交取消旧PR；为不同PR、合并组、main、release/reusable调用分别测key。评估非PR同组pending覆盖风险，选择能保留必要候选的key/queue策略。不把公共heavy池重新串成一个concurrency组。
6. 先保持完整pytest的合并前并集。后续重新分层需CI00覆盖账审批并有测试。不得把platforms或打包完整性移到main以后来凑2分钟。
7. 验证一个正常候选和一个故意失败候选的DAG时间及Gate。无部署权限时做workflow/actionlint与判定器测试，实机路径仍not_run；不能仅靠YAML解析宣布调度已验证。

## 验收

新DAG确有独立路径重叠或证明其可调度关系；`failure/skipped/cancelled/missing`不会被最终Gate吞掉；每类旧保护仍位于合并前；PR取消不伤其他链。人工批准的覆盖迁移和未执行事实都有记录。

回退是恢复上一份needs/事件策略，不删除失败job、不更换requiredcontext为新的恒绿名称。
