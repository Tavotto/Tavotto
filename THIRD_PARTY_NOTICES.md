# 第三方声明

Tavotto 自身的许可见 `LICENSE`。下面是随源码内联的第三方素材（不含通过包管理器安装、带自己许可证文件的依赖）。

## 厂商图标（lobe-icons，MIT）

`web/src/components/settings/vendorIcons.ts` 里设置页 / 改图助手面板用来指认模型服务商的矢量路径，取自
[lobehub/lobe-icons](https://github.com/lobehub/lobe-icons)（`packages/static-svg/icons/*.svg` 的单色版，
只取 path 数据）。涉及：anthropic、bailian、deepseek、kimi、minimax、moonshot、openai、opencode、vercel、zai、zhipu。

「连接 AI」界面用的**彩色**标志在 `web/src/components/settings/brandLogoData.ts`，取自同一仓库同一提交（ab8cbcc）的
`packages/static-svg/icons/<name>-color.svg`（path 与渐变），涉及：bailian、claude、deepseek、gemini、kimi、
minimax、qwen、zhipu；许可证文本同下。「接入 AI 编辑器」那一组另取（同为 lobe-icons，npm 包 @lobehub/icons-static-svg 1.101.0）：`trae-color.svg`（trae，彩色，在 brandLogoData.ts）、
单色的 `cursor.svg`、`workbuddy.svg`、`antigravity.svg`（在 brandLogoData.ts，画成 currentColor；zcode 复用 vendorIcons.ts 里的 `zai.svg`）。VS Code 的标志取自 Simple Icons 11.0.0 的
`visualstudiocode.svg`（CC0 1.0，无需署名；在 brandLogoData.ts，颜色取其官方色 #007ACC）。
这些标志是各厂商的商标，仅用于在界面上指认对应服务，不表示与其有任何关联或背书。

```
MIT License

Copyright (c) 2023 LobeHub

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

各标志本身是其所有者的商标，这里仅用于在界面上指认对应的服务商。

## 品牌标志（Simple Icons，CC0）

`web/src/components/settings/AgentIcon.tsx` 里的 OpenAI 标志取自
[Simple Icons](https://github.com/simple-icons/simple-icons)（CC0 1.0，公共领域贡献）。

## 思考小球几何（thinking-orbs，MIT）

`web/src/components/ui/thinkingOrb/engine/` 的点阵几何（各形态的帧计算，不含上游的 canvas 画笔与 React 组件）取自
[Jakubantalik/thinking-orbs](https://github.com/Jakubantalik/thinking-orbs) 提交 de85557（0.3.2）的 `src/engine/`；
`thinkingOrb/presets.ts` 里 64 / 20 两档的调参数值同出于该仓库 `src/presets.ts`。Tavotto 的画法与尺寸派生见 ADR 0165。

```
MIT License

Copyright (c) 2026 Jakub Antalik

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## 提示动画的舞台（stageshow，MIT）

`web/src/vendor/stageshow/` 的舞台、脚本、光标与键帽（14 个文件；除 `keys.tsx` 的键帽表改用 Tavotto 的平台键名外原样）取自
[benjitaylor/skills](https://github.com/benjitaylor/skills) 提交 299140f 的 `stageshow/kit/`（1.1.1）。
只取舞台这一层：不含上游的窗口 / 手机 / 消息等部件，也不含它附带的第三方品牌图标与应用图标。
Tavotto 用它画情景提示动画卡里的小场景，见 ADR 0177。

```
MIT License

Copyright (c) 2026 Benji Taylor

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
