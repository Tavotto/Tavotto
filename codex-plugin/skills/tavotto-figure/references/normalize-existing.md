# 已有图的规范化：保留原图，只改点名的东西

（从 SKILL.md 迁来，用户要「改成 8 cm 宽 / 换字体 / 字号不小于 8 pt」且不想重画时读。）

用户拿来一张**内容正确、排版已经不错**的图，只要「改成 8 cm 宽」「换 Times New
Roman」「文字不小于 8 pt」「出 300 dpi 的 PNG」这类规范化修改时，按下面走，
**不要**自己拼 `tavotto_apply_overrides`、不要重写脚本、不要重画：

1. **先识别输入**：`tavotto_open_figure` 开它（脚本必须在产物旁边，否则这张图只是位图 / 轮廓文字，改不了字号字体——如实告诉用户，不要缩放位图冒充「已改字号」）。打开回来的状态就是原图基准 B0。
2. **发起保留式规范化**：`tavotto_normalize_figure { session_id, width_mm?, height_mm?,
   font_family?, min_font_pt?, font_size_pt?, formats?, dpi? }`。只传用户点名的目标：
   只说了宽度就只传 `width_mm`（高度按原长宽比自动推）；「不小于 8 pt」传
   `min_font_pt`，**不是** `font_size_pt`（后者会把标题、标签、刻度全部压成同一个字号，
   只有用户明确要求统一字号才用）。
3. **它自己会**只改点名的属性、在真实渲染上检测、做有预算的局部适配、核验最终文件，四件事同时成立才提交，否则回到 B0、不出文件（细则见 `references/compatibility.md` 的「保留式规范化」）。
4. **按结果说话**，工具调用成功 / 文件生成了都不是「任务完成」——看 `exit`：
   * `done`：说清**明确要求的修改**、**必要的局部调整**（多少 mm）、**保留的原有问题**
     （`verdict.issues.*.unchanged`，不顺手修）、规范与用户要求冲突之处（`profile_conflicts`）。
   * `constraint_conflict` / `budget_exceeded`：装不下。念出具体冲突，**列出需要放宽的最小约束**
     让用户选，**不要自己放宽**。
   * `font_unavailable`：这台机器没装那个字体，Tavotto 没有替代。告诉用户装字体或换字体；
     **不要**自己换一个相近字体再宣称达标。
   * `requires_authorization` / `protected_changed`：需要用户明确提出新要求。
   * `acceptance_failed`：文件没过验收、没有交付；把 `failed_files` 的原因如实报告。
   各档的完整话术读 `references/compatibility.md` 的「已有图的规范化：完整话术」。
5. **提交之后会话受修改约定保护**：再调 `tavotto_apply_overrides` 改约定之外的东西会被
   拒（`requires_authorization`）。只有用户**明确**提出了新要求，才带
   `user_authorized: true` 重发（约定解除、验收报告作废，导出时会如实标出）。

**禁止的绕路**（后端只能守住它管辖的那条路，其余靠这里）：不改用户的 .py 源码、
不另写一份绘图脚本替代原图、不整份替换 SVG、不用 Pillow / OpenCV / 截图缩放重建
图形、不套全局主题或 rcParams 风格、不为「消除拥挤」删曲线 / 删图例项 / 删刻度 /
截断文字、不改配色 / 数据 / 坐标范围 / 子图结构。这些都不是「规范化」，是重做。
`tavotto_export` 直接导出仍然可以，但它回执里的 `normalized.verified` 会说明这次
导的是不是通过验收的那一版——不是就别把它当规范化结果交出去。
