# 事件、DAG 与三个稳定 Gate

## 1. 保留“三层深度”，但拆清合并前后

| 事件/状态 | 深度 | 必须保留的语义 |
|---|---|---|
| 每个PR的新提交（草稿也含） | T1 快速筛查 | lint/真实typecheck/便宜单测、核心合同与短真实smoke；已有安全/CLA不降级 |
| 非草稿PR的新提交、ready转换、相关显式验证请求 | T2 代表性验证 | 受影响领域的集成、路径/env代表例、少量真实UI；不重复T1构建 |
| merge_group / full-ci PR | T3a 完整合并资格 | 当前完整合并前覆盖＋已激活关键矩阵；最终组合精确SHA，不用单PR绿替代 |
| main | 轻量落地审计＋既有独立lab政策 | 不因本计划重复造相同包；也不把main审计替代合并前测试 |
| nightly/lab | T3b 深度观察 | 更广组合、慢测、故障、soak、供应源与观察项，仍保留现有资格责任 |
| release | 精确发行资格 | 目标渠道、最终字节/签名/安装升级/许可，独立于普通PR是否绿 |

2–5/5–12分钟是反馈SLO候选，CI00测后再设；不是执行超过就杀死合法工作。每层有资源超时上限，其值来自实测和恢复设计。完整资格的P95同样影响开发，不说“30分钟无所谓”而忽略队列吞吐。

## 2. 先进行低风险DAG修改

当前形式：
```
frontend ───────────┐
backend-fast(全量) ─┴─► package / windows-exe-smoke ─► integration gate
```

目标形式：
```
checkout/小型必需前提
  ├─► quality/unit shards ──────────────────────┐
  ├─► web-build ─► web/plugin artifact消费者 ────┤
  ├─► native/package build ─► 安装/E2E shards ───┤
  └─► 其他当前必需平台/协议测试 ──────────────────┘
                            稳定Gate按实际必需集合AND聚合
```

某任务只需要知道另一个测试“过没过”，而不消费它的字节，不必靠needs串行等待；最后的资格Gate再汇合。是否保留短质量预筛来节约已明显坏掉的候选构建，是明确的成本/延迟决策，不让长全量pytest做所有构建的前置。

数据依赖不可删除：artifact没有出来，consumer不能假定它存在。生产者只承担必要构建/资源校验，不能先跑全套测试使新DAG只是改名。

## 3. T1/T2接入不制造新跳过陷阱

第一步可以保持现有Gate输入，以独立步骤/摘要提前报告T1，先通过分片缩短原资格。不要一次改触发、任务身份、判定器与所有测试集合。

若随后正式把长测试重新编排为T1/T2/T3a：
- 以版本化的coverage ledger列出每个旧必需集合的新位置；其并集在合并前不缩小。
- 保留三个stable context，不新发一个同名成功check掩盖失败。
- T2在普通ready PR有明确责任。推荐一个始终实际执行的PR验证聚合/执行节点，草稿执行自身最低集合，非草稿执行预先定义的代表集合；“按事件不纳入某可选集合”与“必需job skipped”分开。不要把条件跳过的T2 job直接塞进要求所有job success的旧Gate。
- `CI integration gate` 普通PR仍仅对整体完整重资格使用既有deferred；这不豁免已承诺PR验证的T2。
- merge_group/full-ci执行当前T3a，其报告独立，不套PR draft/label字段。
- 如果以上改动需要过多特判，本轮保留所有PR相同的小代表集；先取得DAG/分片收益，Ready分层可后置，不能为优雅分层制造自研调度平台。

已存在的 cheap Rust/cross-language、macOS cfg、CLA/CodeQL不可只因“不改Rust”就先删除；确需变更域选择，未知路径广覆盖、删除/重命名/共享配置/依赖锁都须有真值测试。第一轮不做精细到函数级的影响分析。

## 4. 取消与重复触发

当前ci.yml已有按event隔离、仅PR cancel-in-progress。保持该合同。[G06]

必须测试：PR A新push只替换A旧运行；PR B不受影响；main、merge_group、release不被PR覆盖；reusable workflow调用链不互相误取消；label触发不能拿旧SHA结果替代当前结果。

`ready_for_review` 只是转换事件，不是之后每次push自动触发的替代品。非草稿的`synchronize/reopened`也必须得到对应资格。`labeled/unlabeled`当前还影响full-ci；先清点所有consumer后才减少无关label重跑。策略变化到同一SHA也要正确失效。

GitHub默认concurrency队列可能用新pending替换旧pending，即使cancel-in-progress为false。保留每一个合并/发行候选时，应使用候选/运行身份隔离组，或明确使用当前官方支持的多pending策略并测试。当前`queue:max`允许最多100个pending且不能与cancel-in-progress:true组合；这不是本轮必须引入的功能，先确认实际API/校验工具支持。[W01]

不要用一个全仓`concurrency:heavy`给三台runner重新套单槽。GitHub concurrency控制同组运行，不是CPU份额、全局公平调度或自动fallback机制。

## 5. 聚合与信任迁移

逐个检查稳定Gate的needs、required闭集、事件truth-table、report集合。实际CI还从可信默认分支取判定器；变更其接口必须先提供向后兼容版本，再切consumer，不能候选PR带一个“永远通过”判定器让自己过。

先冻结版本化的minimum coverage；候选分支不许单方面删required集合降低基线。目录/域策略与自身workflow修改应触发最保守的对应测试。权限控制由受保护基线/管理员政策兜底，不仅由PR可编辑的JSON兜底。

必需实例集合与有效报告一一对应；遗漏/重复/未知、错误SHA、跨attempt错误复用、test-only假成功均拒绝。状态必须区分产品失败与基础设施错误；两者都不能完成必需资格。
