# 命中与选择几何

> 原文出自 `web/AGENTS.md`「命中与选择几何」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`web/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

- 图内元素的命中 / 框选 / 描边全在 `web/src/lib/pathGeom.ts`（距离一律换到 mm
  再比，与图内箭头同一口径；填充按 nonzero 缠绕数算内部——判据的完整理由见
  `docs/rules/backend/pdf-backend-boundary.md`，别在别处另写一份 even-odd 的；
  空心只在描边附近命中；框选是「圈墨迹」不是「戳进去」）；`OverlaySvg` 画
  `<path>` 并套上引擎给的 clip 框。**散点与只有 marker 的 Line2D 也走这一套**
  （2026-09-06）：引擎给每颗 marker 一条闭合子路径（`multi_path`），前端一个字
  没改——几百颗点仍收在**一个** `<path>` 节点里（d 串多几段，DOM 不多一个
  节点，别把它拆成每颗一个元素）；标记数超过 `pathgeom.MAX_MARKERS` 时引擎
  不给 geometry，前端自然退回 bbox 矩形。既有连线又有 marker 的曲线仍只描
  折线（理由在 `src/tavotto/AGENTS.md` 散点几何那段）。**柱形系列也一样**
  （2026-09-13）：引擎给每根柱一条闭合子路径，柱间空白不再命中这组、框选按柱相交、
  选中描的是每根柱。**色条元素的几何代理到它的轴**（`resizable` + `geom_gid = axes_i`，
  与位图 → 宿主子图同一套 `geomTarget`；2026-09-24 起铺满子图的面状色图集合——pcolormesh / contourf 等——也走这一套，引擎侧判据 `manifest._is_area_field`）：点色条就有八个手柄、拖动写的是色条轴的
  `position`，前端一个字没改——看护 `elementPathSelection.test.tsx` 的柱形与色条两组。
- **文字 / 图例 / 子图 / 组选择继续用矩形**——它们本来就是矩形语义，别为了统一
  硬转路径。画布**原生**形状同理：`lib/shapeGeometry.ts` 的 `shapeOutline` 是
  ShapeView 显示、透明命中层、覆盖层选中描示**三处唯一的一份轮廓**
  （椭圆/三角/菱形/多边形/大括号；矩形不在此列，直线走端点那套）。
  看护 `pathGeom.test.ts` / `elementPathSelection.test.tsx` / `shapeOutline.test.tsx`。
- **重叠候选之间的轮换**（2026-09-03，issue #216）：`pickElement` 只回答得了
  「点这儿选谁」，重叠到**评分逐位相同**时给不出第二个答案——twinx 的孪生轴
  与宿主 bbox 一模一样、role 同为 `axes`，先登记的宿主恒胜，twin 容器直选
  点不中，而两个 bbox 之间没有任何空间信号可用。出路是让用户说「换下一个」：
  * **一份有序候选表** `pickElementStack`，`pickElement` 取的就是它的 `[0]`。
    排序 = 评分升序 + **评分相同按 manifest 登记序**（旧实现「严格小于才换
    优胜者」的逐位等价），所以**不轮换时选谁一个字节没变**；评分相同的候选
    因此在表里相邻，宿主的下一个永远是它的孪生轴。别改成靠 `sort` 的稳定性
    兜着——那是隐含依赖，而这里正是重叠次序唯一的出处。
  * **⌥ 点击**在候选间轮换（`cycleOverlapAt`），排在**边框命中区之前**：孪生轴
    与宿主的边框逐位重合，正是最需要轮换的那一点。⌥ 只换选中，不写文档、不
    进历史、不起拖动；⇧ 归加选，两个修饰键各管一件事。
  * **换到了谁必须说出口**：两者的选择框逐像素重合，只换 `selectedGids` 的话
    画布上一个像素都不变，轮换在用户眼里就是「随机换了个选中项」。toast 走
    `status.elementCycled`，措辞用元素树 / 属性页那份 `engineLabel`
    （「子图 2（右轴）」，引擎侧出处 `engine/manifest.py::_twin_axes_labels`），
    **不另造第二套**；`StatusToasts` 自带 `aria-live`。
  * **⌥ 双击不给破例**：两个 pointerdown 已经各轮换一次，`onDoubleClick` 再弹
    快速改字的话，用户要的是「换一个」、拿到的是一次没要的编辑，而且弹层认的是
    `pickElement`（重叠时恒为宿主），与刚换到的不是同一个元素。
  * **键盘等价路径**（issue #37「画布操作要有对象树 / inspector 等价路径」）：
    ⌘K 的 `cycle-overlap` 命令跑同一个动作，没有指针就拿当前选中元素 bbox 的
    中心当那个点（`cycleOverlapSelection`）；几何权威没就位时什么都不动
    （ADR 0017），由调用方说「正在同步」。元素树本来就分得清孪生轴，那是第二
    条键盘入口。**这条路有两个坑，两个都要堵**：① bbox 中心**不一定落在那个
    元素身上**（U 形曲线的中心在杯口里），所以 `cycleElementAt` 收一个 `anchor`
    排在表首（bbox 恒含自己的中心）；② 探针若每次现取就会跟着选中项漂走，
    第三下落进另一组候选，轮换变成出得去回不来的单程票 —— 所以一轮连续轮换里
    探针与 anchor **只取一次**（`cycleProbe`，钥匙是「上次是我选中的 gid」，
    用户点了别的自然失效）。只堵①不堵②的话环会从 3 缩成 2，照样回不去。
  * 看护：`canvas/twinAxesPick.test.tsx`（两个方向各一组：不按 ⌥ 时命中逐条
    不变 / 按 ⌥ 时换得到 twin、说得出是谁、绕得回来）+
    `e2e/twin-axes-pick.spec.ts`（真浏览器 + 真 matplotlib：引擎真的把孪生轴
    发成一个独立 axes 吗、⌥ 真的带得到命中层吗、播报真的看得见吗——jsdom 里
    命中层的 `getBoundingClientRect` 是桩出来的，这几件事量不到）。**它同时进
    webkit 那一腿**：⌥ 唯一没被量到的维度就是「换个引擎还带不带得到 `altKey`」。
- **图内箭头交互**与画布箭头同语义（2026-08-17，elementArrowEditing.test 看护）：
  命中/框选按**线本身**不按 bbox 空白矩形、选中/hover 沿线描示无矩形外框、
  拖端点 shift 锁 15°、整体拖 shift 锁水平/垂直/45°（分数坐标锁角必须换算到
  内容像素系）；图内文字/子图拖动同样有 shift 锁向，画布对象拖动可吸附图内
  元素中心线（elementSnapCandidates）。纯箭头注释（`annotate("", …)`）同样出端点、
  同一套交互（引擎侧见 `docs/rules/backend/axes-and-artist-families.md`）。
- **拖形状带着装在里面的内容走（2026-09-24，用户的流程图：拖框时框里的字与连着框的
  箭头留在原地）**。判据只有 `lib/elementGeom.patchContents` 一处，纯几何、只读权威
  manifest（`exactPanelManifest`）与文档：
  * 文字与形状（`role` 为 `text` / `patch`、可拖）的包围盒（权威 manifest 的；权威在时它与
    文档 overrides 逐字一致，不存在「override 已写、几何未回」要补位移的状态）完全落在容器框里、
    且**面积比容器小**——整体平移；嵌套的框因此带着自己的字走，
    拖外层虚线框 = 搬整个模块。「比容器小」挡的是一样大的两个框（阴影、叠放）互相带着走；
  * 箭头（有 `arrow_endpoints`）按**端点**判：落在框里的那一端跟着走、两端都在则整根
    平移——连着两个框的箭头拖其中一个时被拉长，而不是被扯走；
  * 两条都带 `PATCH_CARRY_TOL_PT`（3pt）容差：annotate 的端点是未扣 shrinkA / shrinkB
    （默认 2pt）的锚点，脚本常把它写在框的名义边上、画出来的圆角框又多一圈 pad；
  * 锁定（`lockedGids`）与隐藏的不动；多选整组拖动时选区里的形状同样带着内容走，已在
    选区里的按选区位移、不重复算。
  每件内容写**它自己的**那条 override（`pos_frac` / `endpoints_frac`，值 = 当前值 + 位移），
  与子图拖动的 `axesCompanions` 同一个办法：文档里仍是普通 override，重放、写回、撤销都
  不需要新机制；连同形状自己进**同一次** `setOverrides`（一条撤销、一次渲染）。预览：
  整体平移的内容平移 SVG 组，只有一端跟随的箭头形状变了、画覆盖层虚线
  （`svgPreviewStore.previewLine`：**挂在预览平面上**，与 SVG 预览同一个账本、同一套收尾——
  松手后留着，权威渲染换上来 / 取消 / 被顶掉时才消失；挂在交互状态上的话 `end()` 一收它就没了，
  慢图上旧箭头会先露出来，#553 评审）。覆盖层上它由 `OverlaySvg` 的 `PreviewLines` 画，**在几何权威
  闸门之外**：松手提交后 overrides 已变、新渲染没回来时 `useExactPanelManifest` 是 null，
  `ElementBoxes` 整个不画，虚线若在里面照样会先消失；换算只用面板与视口，不读 manifest。
  **出口是按住 ⌘ / Ctrl = 只拖它自己**——与拖动时临时关吸附同一个修饰键、同一种语义
  （关掉那个聪明的默认行为），⇧ 锁向、⌥ 轮换各有所属；拖动途中随时按下 / 松开都算，
  松手以最后一帧为准。它**推翻**了 #472 时「拖框不带字——要一起走用多选」的约定：
  那条约定下流程图每挪一个框要先圈上框、字、两头的箭头，而箭头还只能整根走。
  看护 `canvas/patchCarryDrag.test.tsx`。
