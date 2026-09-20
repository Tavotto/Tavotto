# 来源、事实与新决定

## 附件来源

原RenderCore完整提示词/验收JSON，兼容性完整提示词/验收JSON，FirstOpenBench提示词/32场景JSON，以及原始概念方案和前次评估。全部原始字节存于archive，内容hash见sources_manifest.json。

原包中的绝大部分技术判断来自固定旧SHA：`6a1a9dea5d27b1724c4aab11e38d9fb808d2a89e`。它们是此前定向源码审计，不是实际性能或跨平台测试证据。原始概念方案关于“几天实现”的估计没有被本计划继承为工程承诺。

## 本次连接器核验

截至本次采样：main指向`8b95256c0d08a14bfcfc4c81358894ef01168933`，已经不同于原详细审计基线。本次只直接复核：

- main ref；
- `scripts/ci/aggregate_gate.py`，1–162：必需job精确闭集、skipped等失败、普通PR整体deferred例外；
- `docs/support-matrix.json`：Python3.10–3.14、Windows x64/macOS arm64桌面与Linux pip等边界；
- `.github/workflows/ci.yml`，1–84：PR/merge_group/main分层、取消规则和遥测关闭。

请求过旧SHA到新SHA的compare，但大响应没有完整逐项审阅；不据此声明全部diff已审计。U00仍必须核验实施时HEAD和真实变更。

固定源码定位模板：
```text
https://github.com/Tavotto/Tavotto/blob/8b95256c0d08a14bfcfc4c81358894ef01168933/<path>
```

## 重新检查的官方资料

[W1] GitHub required status checks：最新提交、skip/neutral语义、merge_group触发和always聚合。
```text
https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks
```

[W2] pytest skip/xfail：不同结果必须区分；本计划不建议用批量xfail掩盖未取得的新能力资格。
```text
https://docs.pytest.org/en/stable/how-to/skipping.html
```

[W3] CPython venv：默认隔离、基于base解释器、不应当作可移动/可复制环境；因此固定最终目录创建后切active引用。
```text
https://docs.python.org/3/library/venv.html
```

[W4] pypdfium2 API文档：PDFium线程安全边界；本计划保持集中串行调用，逐步扩应用子进程。
```text
https://pypdfium2.readthedocs.io/en/stable/python_api.html
```

## 本次新提出的内容

统一阶段U00–U11和X01–X03，D01–D16裁决、按能力准入的enrollment流程、代表性PR场景与观察政策都是本次工程建议。它们不是当前Tavotto已实现代码，也没有从文档“推导”出测试已通过。

本包内实际运行的检查只验证文档链接、JSON来源、220项映射、依赖DAG和计划校验器负例；不涵盖Tavotto源码、Python/包环境、PDF渲染或安装物。
