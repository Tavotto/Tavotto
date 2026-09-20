# U08 · 所有用户路径接上，而不是只完成save_pdf

**前置 milestone：** U07.compose_raster。先读总提示词及当前有效handoff；遵循03门禁政策。


## 实际实现

按U00 facade清单逐项迁移：探测、预览、text width/coverage、compare_png、original PDF/PNG/TIFF、compose、annotation写回。原PNG直接复制保字节，位图转码保native grid/明确密度，PDF默认第一页，原图不吃canvas变换。原worker SVG/EPS直出不强制PDF转换。

SourceResolver/RenderPlan交给现有ExportJob；多实例中间文件私有，多格式同一科学状态；临时结果验证、取消提交点、命名预留、覆盖、report失败/partial、写回expected identity和原件恢复都沿用既有权威。

重新打开封口staging产物验证核心尺寸/完整性/字体或图像等可观察事实；用户选择严格规范时必需项失败/未知按规范阻断。普通导出对可选复杂PDF检查unknown给说明，不因此拒绝合法成果。故意坏文件/错误实际尺寸/伪造客户端proof不能通过。

字体实际使用/声明分开、carrier与vector/mixed/raster/unknown分开；受限CTM/Form遍历有预算，未支持Type3/复杂clip明确unknown。优先验证本轮发射的受控结构，再扩大任意外部PDF覆盖；不要求所有PDF都可证明无裁切。

HTTP同步/异步/SSE、MCP、native runtime asset、前端导出回执与旧vector投影一起审计。保持字段兼容和错误码双语；未核验不显示绿色。四类Web构建的资源路径保持，独立Playground不冒充有native后端。

## 测试

真实原图/canvas/写回/MCP/格式partial/报告失败/取消/项目切换/并发；独立读取器检查产物而非只信返回JSON。验证不是在发布后才发现必需失败。

旧MuPDF几何测试迁移判据，不要求仿制整个get_drawings API；每个删除/替换的实现断言在ledger有替代证据。旧用户契约测试不凭主观“现在没必要”删除。

## 出口

facade全入口候选覆盖，常见实际输出具有可信有限检查。默认仍可保持旧实现，候选选用新实现必须明确无静默回退；U09再与完整准备链联调。
