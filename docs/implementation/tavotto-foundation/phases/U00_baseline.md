# U00 · 统一基线，不先动默认实现

**前置 milestone：** 无。先读总提示词及当前有效handoff；遵循03门禁政策。


**输入：** 总提示词、范围/CI政策、registry原条目、当前仓库规则。详细旧审计与最新采样SHA不同，先以当前checkout为准。

## 实际实现

确认HEAD/status，列出自旧审计以来与pdfbackend、execspec/pool/projectenv、deprepair/workdir、UI/协议、CI/packaging相关变化。未读diff不得写“无变化”。构建一次真实调用图和能力清单，区分现有公开功能、已知缺陷、仅注释承诺与本次新增目标。

按当前命令运行最小已有测试和产品smoke，记录本来就红的检查。不因基线红而改aggregate_gate或全局豁免；将独立基线修复作为最小先行切片。

用少量合成项目冻结：同目录CSV；scripts/data分离；已有不同Python环境；非内置依赖；同名不同数据；一页文字/图形/透明PDF；原PNG metadata。首开失败真实记录，但不马上作为required挂全部PR。

从__all__及真实调用方生成facade迁移列表，标记原图byte-copy、位图native-grid、worker SVG/EPS、annotation写回、缓存、取消partial。旧测试分用户合同和实现特定断言，逐条指定迁移判据。

确认字体最小批准范围、应用Python支持范围、scientific runtime范围、可用runner和真安装目标。记录基础构建时间、测试时间、峰值资源与下载成本，只测不拍硬阈值。

## 出口

有实际基线命令/日志/最小fixture、清单和范围草案；未来能力保持not_run。原生产依赖、默认后端、根LICENSE和用户文件不变。registry映射与当前代码差异已标注，不要求全部新验收绿。

## 随切片测试

清单去重、facade漏项反例、fixture输入真值、原生参考不污染被测环境。已有产品smoke真实跑一条，不用新建test-only准备器。
