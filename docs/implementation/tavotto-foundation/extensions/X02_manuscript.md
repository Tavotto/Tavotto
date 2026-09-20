# X02 · 原E01：批量论文图编译

保留原RC-111/112的全部用户目标：共享PublicationSpec、每图独立source/override/授权、既有normalize有限修改、原ExportJob逐项发布、预算、取消、partial、恢复与增量重试。不把批量变成新的第三scope或平行renderer。

每图可有不同科学环境/执行回执，不塞入共享可变环境。预检不执行未授权脚本，不将opaque PDF误当可改字体科学图。源变化按内容身份只重做相关项，已产文件先验证hash与检查版本，不能凭存在复用。

实现最小真实API/CLI及符合原UI规范入口，随后12项混合用例验证：重复来源不同override、格式差异、名称冲突、缺字体、低PPI、normalize预算、处理中取消、报告失败。成功项真实交付；失败不改原件，合理CJK/math回退与unknown不误判整批一致。

此阶段不制作支付、云同步或账号；即使未来用于Pro，授权实现另立工程。未实现保持later，不作为PyMuPDF退役前置。
