# U07 · 完整合成与PNG/TIFF同源

**前置 milestone：** U06.ir_text。先读总提示词及当前有效handoff；遵循03门禁政策。


## 实际实现

实现ImportedPage Form导入、页盒非零原点、Rotate/UserUnit、Tavotto crop/rotation/flip顺序。普通路径、全部现有shape/arrow/brace与legacy字段按原用户合同迁移。graphics-state及资源命名/继承正确，两个实例不串。

镜像及受支持整体透明保留真实矢量与文字；内部重叠源必须验证透明组语义，不把每笔alpha当整体。复杂mask/blend明确scope/合法降级政策，不全页位图后仍报告vector。

应用拥有的PDFium child统一处理probe/preview/raster/inspect的native调用。首轮一个进程串行+有界队列、pixel/memory上限、timeout/cancel/restart/close；必要时才加受测的多进程并行。不是重写现有Rust supervisor，也不将PDF库装进科学环境。

Canonical PDF完成后生成RasterBuffer，定义stride、RGB/BGRA、bit-depth、colorspace、straight/premultiplied alpha及buffer所有权。PNG/TIFF同参数使用同一状态，保留透明和密度。能复用现有tiffwrite则保留，Pillow确有用途再按正式依赖收集codec。

预览cache含实际源、后端build/字体政策、像素/颜色参数；保持同键去重/临时发布/Windows句柄处理。异常不返回空白图或旧图作成功。

## 验收

解析式非对称几何/页盒、同名资源、透明组、opacity=0、同灰度异色、alpha边缘、padding stride；PNG/TIFF规范解码像素一致。更换renderer的视觉差分与真实geometry分开校准。

并发preview/probe/export无进程内native并发；child崩溃/超时后下一请求可恢复；资源回到文档化缓存预算内，不硬要求所有OS缓存瞬间为0。冻结最小child在目标候选真启动，最终签名留U11。

## 出口

主要合成和raster路径在候选后端上可重复通过；旧门面剩余及事务consumer由U08收口。
