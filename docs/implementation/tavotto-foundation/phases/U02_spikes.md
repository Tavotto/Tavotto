# U02 · 两个独立技术证明，不用最终发行门挡模型开发

**前置 milestone：** U01.contracts。先读总提示词及当前有效handoff；遵循03门禁政策。


两个milestone分别验收。`render_spike`通过即可推进U06；`runtime_spike`通过即可推进U05，彼此不要求对方所有平台完成。

## render_spike

用候选固定版本PDFium/pikepdf和成熟字体处理组件，制作小型真实PDF：导入非对称源页、变换/clip、内部重叠的整体opacity，以及中英/Greek/上下标可检索文字。用独立读取器确认几何/字体映射与实际pixels。决定成熟写入器适配还是受限自有emitter，不做通用parser。

确定合法默认字体来源与许可，验证字体真实文件、shaped glyph到PDF编码/子集/ToUnicode，不以outline替代。先支持本轮字体/字符集合；复杂字体仅边界测试。记录会改变的旧字体名称/布局基线。

做最小应用render child，统一串行native调用、错误/close/超限路径。用本机与至少一个可用真实目标构建最小候选freeze，验证资源和child启动；另一目标未测明确保留，不影响纯模型/另一主线开发，但阻止在该目标默认启用。

## runtime_spike

验证一个固定provisioner（uv或等效可兑现组件）、私有完整Python来源、最小venv与wheel安装。只选择一条首轮生产路径，不同时自制多个resolver。验证应用私有目录、不改系统Python/注册/用户配置、无下载授权不联网、坏hash不执行。

尽早做无host帮助的最小目标试验，验证embeddable与完整Python差别。最终完整产品安装器和签名不在此gate；未具备干净目标的部分是未来启用阻塞，不伪造本阶段跨平台成功。

## 交付

两份短ADR或一个含独立结论的ADR：版本/平台/字体来源、实测输入/产物/退出码、失败路线、选择原因、仍缺的目标。不得使用“方案应该可行”替代真实小产物。不存在默认切换。
