# 后端 manifest 性能诊断（Issue #1006）

这条变更**只增加计时和样本**，不更换绘图顺序、不缓存 Matplotlib Artist，也不更改导出的
SVG、manifest 结构或几何。没有证据时不能直接优化引擎。

## 阶段含义

- `manifest_prepare_ms`：前置 `build_manifest` 的总耗时。
- `manifest_layout_ms`：前置阶段里的 Agg 布局 draw（包括有 frame 时的追加布局）。
- `manifest_measure_ms`：前置阶段逐元素测量、清单组装。
- `manifest_final_geometry_ms`：最终 SVG savefig 后的几何复测以及作用域退出。
- `manifest_ms`：保留原有总计时，等于 prepare + final。

布局与测量的两个内部指标属于 prepare 的一部分，可能有少量作用域进入/退出成本，
因此不要求逐毫秒严格等号。阶段采集仅在传入 `timings` 时开启。

## Agent 命令

```bash
python scripts/bench_render.py --plane python --repeat 9 \
  --figures examples/figures --json /tmp/bench-render.json
python scripts/perf_manifest_report.py /tmp/bench-render.json \
  --json /tmp/manifest-summary.json
```

请仅将 `manifest-summary.json` 提供给 Agent：原始 `bench-render.json` 可能含项目路径、
运行环境与实际文件名。匿名报告只含数字及场景顺序。无基线时
`comparison_verdict=not_checked_without_pinned_same_machine_baseline`，绝不写“性能通过”。
少于 9 次热态采样、阶段丢失或计时加总不一致都会显式失败。

后续 A/B 必须使用相同计算机、Python/Matplotlib、字体环境、样本图和热态设置，
记录 P50/P95、波动与几何不变量；不同机器的 GitHub Runner 只用来验证功能，
不能替代同机性能收益证明。


## 阶段守恒验证

真实 v1 Worker 的响应字段集合增加了上述四个阶段键，`tests/test_worker_roundtrip.py` 会逐键核对并验证两级时间总量。前置 `prepare` 在布局/测量之外仅允许少量 context enter/exit 开销，校验 `abs(prepare-layout-measure) <= max(2ms, prepare×5%)`，正反两侧均判断。不能只检查局部之和未超时而漏掉未归因的大量 CPU 时间；超出误差的样本一律标记为不完整。
