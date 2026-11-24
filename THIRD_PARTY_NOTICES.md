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

## 路径查找器内核（VectorCraft geom + pathops，MIT OR Apache-2.0）

`vecops/vendor/vectorcraft-geom/` 与 `vecops/vendor/vectorcraft-pathops/` 是
[storytold/vectorcraft](https://github.com/storytold/vectorcraft) 提交 47500c4 的 `crates/geom` 与 `crates/pathops`
（版本 0.24.1）随源码内联的副本：`src/` 逐字未改，只改了各自的 `Cargo.toml`（workspace 继承字段写成明值、去掉
dev-dependencies），上游的 `tests/` 没有搬。它们编进 `web/src/lib/vecops/vecops.wasm`，给画布的路径查找器、
偏移路径、轮廓化描边用（ADR 0185 §五 M2）。许可证原文在各自目录的 `LICENSE-MIT` / `LICENSE-APACHE`，上游的
NOTICE 在 `vecops/vendor/NOTICE-vectorcraft`（其中提到的商标、字体与图标都没有随这两个 crate 搬进来）。
它们经 crates.io 引入的依赖（kurbo、linesweeper、polycool、arrayvec、smallvec、euclid、num-traits、
rustc-hash、serde、thiserror 等，版本钉在 `vecops/Cargo.lock`）各带自己的 MIT / Apache-2.0 许可。

```
Copyright (c) 2026 ArtCraft Team and the VectorCraft contributors.
Licensed under the MIT License or the Apache License, Version 2.0, at your option.
```

## 推荐配色目录（ADR 0207）

`src/tavotto/profiles/palettes.json`（许可宽松）与 `src/tavotto/profiles/palettes_copyleft.json`（GPL）里的色值，
由 `scripts/dev/gen_palettes.py` 从下列来源转录。前端经别名整份 import，引擎经 `engine/palettes.py` 读同一份文件。
方案名写「风格」，不表示与对应期刊、机构有任何关联或背书。

### Okabe–Ito（事实性色值）

Okabe M, Ito K (2008). *Color Universal Design (CUD): how to make figures and presentations that are friendly to
colorblind people*，<https://jfly.uni-koeln.de/color/>。八个色值是公开的推荐值，被广泛引用复用，不附许可证。

### Paul Tol 配色（tol-colors 2.2.0，BSD-3-Clause）

方案出自 Paul Tol 的 SRON 技术说明 *Colour Schemes*（SRON/EPS/TN/09-002），<https://personal.sron.nl/~pault/>；
色值取自 Python 包 tol-colors 2.2.0。

```
BSD 3-Clause License

Copyright (c) 2019, Paul Tol, Clément Haëck

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its
   contributors may be used to endorse or promote products derived from
   this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

### ColorBrewer（Apache-2.0）

Set1、Set2、Dark2、Paired 四套分类色板，<https://colorbrewer2.org/>，色值与 matplotlib 内置的同名色图一致。
按 Apache License 2.0 的要求附致谢句（不以其名义为本产品背书）：

This product includes color specifications and designs developed by Cynthia Brewer (http://colorbrewer.org/).

许可证全文：<https://www.apache.org/licenses/LICENSE-2.0>。

### Crameri 科学色图（MIT）

Scientific colour maps，<https://www.fabiocrameri.ch/colourmaps/>，数据版本 8（随 Python 包 cmcrameri 1.10 发布）。
目录里只收分类 S 系列中对白底对比度不低于 3:1 的颜色。作者请求使用者引用：Crameri F (2018). *Scientific colour maps*,
Zenodo, doi:10.5281/zenodo.1243862；Crameri F, Shephard GE, Heron PJ (2020). *The misuse of colour in science
communication*. Nature Communications 11, 5444。

```
MIT License

Copyright (c) 2020 Fabio Crameri

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

### ggsci 期刊风格色板（GPL-3.0-or-later，只在 `palettes_copyleft.json`）

NPG、AAAS、NEJM、Lancet、JAMA、JCO 六套风格色板出自 R 包 ggsci（Nan Xiao），<https://nanx.me/ggsci/>，
许可为 GPL-3.0-or-later，与 Tavotto 的 AGPL-3.0-only 兼容。这一份**单独成文件**：闭源的高级版组件（ADR 0198）
不得读取或分发它；构建时设 `TAVOTTO_NO_COPYLEFT_PALETTES=1` 即整份换成空表（ADR 0207 §三）。
GPL 全文：<https://www.gnu.org/licenses/gpl-3.0.txt>。
