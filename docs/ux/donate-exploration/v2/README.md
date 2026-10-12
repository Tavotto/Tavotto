# 捐赠页 V2 · The Living Figure（2026-10-12）

V1 原型（`../prototype.html`）的深化与工程化。官网仓库 `Tavotto_website` 本会话无权访问，所以 V2 仍是本目录下
可直接打开的独立实现：没有构建步骤，`index.html` 双击即可（中文：`index.html?lang=zh`）。

| 文件 | 作用 |
| --- | --- |
| `index.html` | 八幕页面，中英文同步 |
| `styles.css` | 全部样式；token 与 `web/src/index.css` 同值 |
| `app.js` | 语言、首屏绘制、三步演示、显现、Ko-fi 状态、致谢渲染。无框架、无支付逻辑 |
| `support.config.js` | **唯一的配置处**：Ko-fi URL、维护者署名与确认、资金用途确认、名单联系方式、最近工作、外链 |
| `acknowledgements.js` | 公开致谢名单（默认为空） |
| `acknowledgements.sample.js` | 版式测试数据，**不得部署**；只在预览中以 `?ack=few` / `?ack=many` 加载，并显示「测试数据」横幅 |
| `technical-notes.html` | 原第四幕的八项技术细节，按 0.18 版重新核实并注明出处 |
| `check-config.mjs` | 上线闸门：`node check-config.mjs`，任何一项未配置或未确认都以非零退出 |
| `screenshots/` | Chromium 实际渲染截图 |

## 1. 审计（V1 → V2）

**完整保留**：首屏构图（标题住在数据空白处、剂量–反应曲线、误差棒、EC₅₀、被选中的图例是首屏唯一的蓝色）；灰桌面 + 白纸面板；
衬线图字与刻度朝内；幕头墨线与起点数据点；第二幕「夜晚时间轴」；第三幕同一张两面板图与「脚本未改动 / 覆盖项」两张卡；
第四幕结语；结尾止于「最近一次测量」、坐标轴延伸出屏幕；中英文切换、reduced-motion、容器查询响应式。

**压缩**：第二幕两段文字 → 一句引子 + 一句；九格 → 五格，并明确标注「叙事重构示例，并非任何用户的真实数据」。
第三幕五段 76vh 长滚动 → 一屏内的三步演示（约 1/3 高度）。第四幕八项 → 三项使命，八项移到 `technical-notes.html`。

**改写**：开源承诺改为「开源版本将继续免费提供」，与未来可能的 Pro 版本不冲突；删掉本地金额、币种、频率与演示付款状态。

**新增**：第五幕维护者短笺 + 最近的工作；第六幕资金用途 + Ko-fi 入口 + 信任说明；第七幕致谢。

**阻碍 Ko-fi 接入的问题（V1）**：本地金额 / 币种 / 频率无法可靠传给 Ko-fi；「继续付款」只有演示反馈。V2 全部移除，交给 Ko-fi 页面。

**仍需维护者确认 / 提供**（`check-config.mjs` 当前列出的六项）：

1. `kofiUrl`：真实的 Ko-fi 主页地址（不猜测账号）。
2. `maintainer.name`：署名。
3. `maintainer.noteConfirmed`：短笺为草稿，需本人确认或改写。
4. `fundingConfirmed`：「你的支持，帮助我们完成什么」三项用途需确认。
5. `acknowledgementsContact`：修改名字、匿名或退出名单的联系方式（mailto: 或 https:）。
6. `preview: false`：关闭预览标记。

## 2. 新的叙事结构

| 幕 | 标题 | 关键设计 |
| --- | --- | --- |
| I 信念 | Discovery is hard enough. Expressing it shouldn’t be. | 原首屏；绘制节奏收紧（全程约 3.2 s，标题立即可读）；选择框以 0.985→1 的极轻落位出现 |
| II 问题 | The last millimetre shouldn’t take another night. | 五次运行的时间轴；叙事重构标注 |
| III 原则 | Keep the source. Shape the figure. | 左侧三步（移动图例 / 调整字号 / 对齐面板），右侧同一张图。桌面进入视口自动播放一次（每步 2.2 s，下方细线显示进度），任何点击立即接管；手机不自动播放，由读者点选；reduced-motion 直接显示最终状态。每一步只有选中框是蓝色，数据层从不变化 |
| IV 看不见的工作 | Good tools take work you may never see. | 三条「使命」以实验记录的形式排列：编号 + 持续进行 · 标题 + 一段话 · 一个细线小图（脚本→图 / 覆盖项与重放 / 可选中的文字与 PDF） |
| V 这项工作背后的人 | A note from the maintainer. | 论文署名式：衬线正文、一条墨线、署名与身份；草稿标记 |
| | What we’ve been working on | 0.18 / 0.17 / 0.16 三条，摘自仓库 `docs/release-notes/`，各自链到发布说明 |
| VI 邀请 | Help us keep building. | 开源承诺；三项资金用途；Ko-fi 卡片（按钮 + 说明 + 未配置提示 + 六条信任说明） |
| VII 致谢 | Acknowledgements | 衬线大标题如论文附录；名字等大等重、按月分组、多列；匿名一行；同意说明；「也感谢通过代码、测试、反馈与分享帮助的每一个人」与 GitHub / Issues / 分享 |
| VIII 延续 | The next discovery is still unwritten. | 原结尾；只剩一个文字链接「Support Tavotto ↑」 |

## 3. Ko-fi 当前状态

- 入口方式：官网自定义按钮 → Ko-fi 官方页面（新标签页）。没有嵌入 Ko-fi 组件，没有任何本地金额或付款状态。
- **未配置**：`kofiUrl` 为空。按钮渲染为不可用（无 `href`、`aria-disabled="true"`），下方明确写出
  「预览：尚未配置 Ko-fi 页面，因此这个按钮暂时不会跳转。本预览无法进行任何付款。」
- 只有匹配 `https://ko-fi.com/<账号>` 的地址才会让按钮生效；`check-config.mjs` 用同一条规则校验。

## 4. 致谢名单的维护方式

1. 只有本人**明确同意**公开的人才能加入；Ko-fi 上的公开留言不等于同意。
2. 在 `acknowledgements.js` 里追加 `{ name: '展示名', month: 'YYYY-MM' }`。只允许这两个字段；不要写邮箱、金额、付款 ID、真实身份或同意记录。
3. 运行 `node check-config.mjs`：多余字段、像邮箱或 ID 的名字、格式不对的月份、重复的名字都会被拦下。
4. 页面上：按月份分组（新的在前），组内按 locale 感知的字母顺序排列（拉丁字母按字母、中文按拼音），去重后显示一次；
   顺序与金额无关。没有月份的名字归入「更早」。
5. 有人选择匿名时，把 `TAVOTTO_ACKNOWLEDGE_ANONYMOUS` 设为 `true`，页面只显示「以及选择匿名的每一位」，从不显示人数。
6. 修改、匿名、退出：通过 `acknowledgementsContact` 联系后，直接编辑或删除对应条目。
7. 空名单时，页面只显示标题、感谢文字与「也感谢通过其他方式贡献的人」，不显示任何「0 人」之类的统计。

## 5. 测试结果（Chromium / Playwright，本机）

- 宽度 360 / 390 / 768 / 1440 / 1920 × 英文、中文 + 大量名单、英文 + 少量名单：15 种组合均 `scrollWidth == clientWidth`，无横向滚动，**0 条控制台错误**。
- 三步演示：进入视口前为 0；进入后按 1 → 2 → 3 推进；点击第 1 步立即接管；手机上不自动播放，点第 2 步生效。
- reduced-motion：演示直接为第 3 步，首屏曲线 `stroke-dashoffset` 为 0，所有显现元素 opacity 为 1。
- Ko-fi：未配置时无 `href`、`aria-disabled=true`、提示可见。
- `node check-config.mjs`：如预期以 1 退出，列出上面六项。
- 自审中修正：reduced-motion 下首屏图例被 CSS `transform:none` 覆盖了 SVG 位移而跳到左上角；未配置的联系链接看起来可点；页脚分隔线贯穿整屏。
- 截图：`hero-1440 / hero-1920 / hero-360`、`page-1440-en / page-1440-zh / page-768-en / page-390-en / page-390-zh`、
  `demo-step-0…3`、`ack-empty / ack-few / ack-many`、`technical-notes`。

## 6. 仍未解决

- 上面六项配置与确认。
- 维护者短笺是草稿，不陈述任何个人经历；需要本人确认或改写。
- 中文字体依赖 Google Fonts；正式上线应自托管并只打包本页用到的字。
- 暗色主题、Safari / VoiceOver 与 NVDA 通读、迁入官网仓库的组件化，留到上线阶段。
- 「最近的工作」目前手工维护在配置里；可以改为构建时从 `docs/release-notes/` 生成。
