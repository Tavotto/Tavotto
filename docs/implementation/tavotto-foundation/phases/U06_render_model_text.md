# U06 · IR、源图计划和可检索文字先形成真实闭环

**前置 milestone：** U01.contracts、U02.render_spike。先读总提示词及当前有效handoff；遵循03门禁政策。


## 实际实现

沿用facade作为入口；实现Page/Group/Path/Image/ImportedPage/ShapedText和资源引用，明确pt、坐标原点、变换/clip顺序、paint order、alpha与数值校验。Arrow/Shape可以编译Path，不为每个图标建立插件。

RenderPlan复用ExportRequest与规范化授权，在原ExportJob内解析冻结源产物；不能把原始脚本和整个实验数据复制到staging替代执行上下文。保持source/instance/revision区别；无override静态源不重跑脚本。

按U02合法默认字体落实registry/coverage/fallback/metrics/shaping，批准字体文件身份、face/style与字形缓存。保留primary/CJK/fallback/missing的可解释语义；本轮集合外明示限制，不暗中回退系统脸。字体政策需要ADR及来源allowlist，不删旧provenance测试了事。

将实际glyph、cluster、advance/offset和逻辑Unicode写成可检索PDF文字；正确资源编码、宽度、subset/GID/ToUnicode。采用已验证适配器，不额外造完整PDF parser。缺字/非法字体不伪装成成功嵌入。

前端画布文字得到同源测量/预览且有revision保护。可以先对本轮固定字体支持可见一致策略，不重写整个编辑器；实际输入/选择/可访问逻辑文本仍可用。科学图内部字体继续由原worker决定。

## 验收

简单页+图形+中英Greek上下标的真实PDF、独立文字提取、字体结构、像素与几何；丢FontFile/ToUnicode、错GID、缺字/不同字体同名等负例。授权默认字体离线可用。

旧字体名/像素实现断言按D07批准迁移，区分合理新字体差异和真正越界/内容丢失；不能无授权修改用户框尺寸补救。新核心无旧backend import；旧默认通道和旧测试暂时仍允许存在。

## 出口

一条录制/编译/写入/预览真实切片成立，纯模型可无native import；完整所有canvas放置与raster/原图入口由U07/U08补，不在此时切默认。
