# X03 · 原E02：新增画布SVG

保留RC-113/114：只对可兑现的IR/来源开放，原worker SVG直出早已由U08保留。ImportedPage不意味着任意PDF能转换成语义SVG。

实现受支持Path/Group/Clip/Image/ShapedText的writer，明确定义物理尺寸、viewBox、变换/clip/alpha、资源ID冲突及文本政策。可合法提供同源font且渲染受验时用文本；用glyph outline时报告转轮廓，不伪称检索/字体嵌入。opaque PDF无真实转换时明确拒绝或用户授权局部栅格降级并报告。

XML/外链/DTD/entities/脚本/事件/CSS url/远程font有实际安全测试；独立浏览器渲染成品与canonical PDF同几何对拍，不要求跨carrier抗锯齿bit exact。UI格式开关只能来自已经验证的能力，不为齐全而开放所有场景。

不包含画布EPS、PDF/X、通用PDF逆向编辑和完整PDF→SVG parser。这一扩展独立资格，不阻塞本轮核心。
