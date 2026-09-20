# 统一路线图与可并行边界

阶段数减少的是管理边界，不是声称工程工作量缩水。12个核心阶段可拆多个小PR；详细技术要求沿registry溯源，没有删掉原有功能再称完成。

```text
U00 基线 → U01 合同/测试骨架
                  ├─ U02.render_spike → U06 IR/文字 → U07 合成/栅格 → U08 全门面/检查
                  ├─ U02.runtime_spike ──────────────────┐
                  └─ U03 已有环境/数据 → U04 联合依赖 → U05 私有Python
                                                       │
             U03 + U08 → U09.existing_env_join → U10 切换/退役
             U04 + U05 + U08 → U09.managed_env_join ───┐
                                      U10 + 两个联调出口
                                             ↓
                                       U11 综合发行资格
```

U02两个milestone独立，某一方向困难不锁死另一个。U03不等U02。U04有合格已有base即可真安装；U05复用它，不重建安装服务。U09以前已经有多个真实垂直切片，不等这里才接UI/测试。U09也有两个独立出口：已有环境链通过即可推进PDF切换，私有环境链留到综合发行一并收口。

| 阶段 | 可验收成果 | 合并与激活边界 |
|---|---|---|
| [U00](phases/U00_baseline.md) | 基线、差异与核心范围 | 真实记录旧问题，不要求未来能力绿 |
| [U01](phases/U01_contracts.md) | 共同合同与增量测试骨架 | 只门禁本切片schema/harness/已有路径 |
| [U02](phases/U02_spikes.md) | 两条独立技术验证 | 小技术证明；不是全部最终签名资格 |
| [U03](phases/U03_first_open.md) | 已有环境与数据上下文的首开闭环 | 已有环境/目录可独立启用，仍用旧输出 |
| [U04](phases/U04_dependencies.md) | 标准联合依赖与版本化受管环境 | 已有base下联合依赖真实通过即可 |
| [U05](phases/U05_private_python.md) | 私有完整Python与无系统Python | 启用无系统Python前需目标真实资格 |
| [U06](phases/U06_render_model_text.md) | Render IR与真实字体文字切片 | 新核心独立；旧默认/旧测试继续运行 |
| [U07](phases/U07_render_output.md) | 矢量合成与受控栅格运行时 | 候选合成/栅格可测，未全入口不切默认 |
| [U08](phases/U08_facade_validation.md) | 全入口迁移与有限产物验证 | 全入口候选parity，普通unknown不滥阻断 |
| [U09](phases/U09_join.md) | 实际执行回执与两主线联调 | 两主线实际整链，最小证据足够且可信 |
| [U10](phases/U10_cutover.md) | 候选启用与旧依赖退役 | 默认/依赖退役与扫描同时受验 |
| [U11](phases/U11_qualification.md) | 精确发行资格与价值收口 | 最终安装字节和平台资格，不自动发布 |

## 资源和冲突域

单人配合多个编码Agent时，优先限制同时在做的重型切片为两条主线各一项；这只是协作建议，不是产品CI硬阈值。`app.py`、`execspec.py`、前端api类型、packaging、CI gate、依赖锁属于共享冲突域；指定一个集成人，其他切片通过稳定小接口协作，避免并行改同一堆文件。

前端/sidecar按相同SHA+recipe+目标构建一次供shard复用；不能使用“最新成功包”。两条线各自产出最小端到端切片和handoff，减少最后集中集成。

## 后续

X01可以按需求在U03/U04之后独立做某个provider，但不成为U06或U10强制前置。X02/X03等核心资格成立后再推进。原方案的后续功能保留，不让第一版变成环境管理器、字体编辑器、PDF通用引擎和CI平台四个项目一起从零重写。
