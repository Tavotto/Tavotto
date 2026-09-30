# 说明文字精简清单（feat/copy-trim）

删 29 键、缩 102 键，共 131 键；另有 5 处组件里去掉了空节点/提示属性（见下）；保留（审过、不动）另列；让路另列。

## 按区域统计

| 区域 | 删 | 缩 |
|---|---|---|
| 属性栏 | 7 | 41 |
| 左栏/画布/状态条 | 5 | 30 |
| 弹窗 | 13 | 23 |
| 改图助手 | 1 | 6 |
| 问题面板 | 2 | 1 |
| 项目/工作区 | 1 | 1 |

## 删 / 缩清单（键、原文、新文、理由）

| 区域 | 键 | 处理 | 原文 | 新文 | 理由 |
|---|---|---|---|---|---|
| 属性栏 | `inspector.arrange.copyStyleTip` | 缩 | 复制最后选中对象的样式：图取裁剪、旋转、不透明度；文字取字号、字重、颜色、对齐。 | 复制最后选中对象的样式 | 删内容清单 |
| 属性栏 | `inspector.arrange.createLayoutTip` | 缩 | 把选区变成{{kind}}布局：间距固定，替换素材后自动重排。可撤销。 | 把选区变成{{kind}}布局，替换素材后自动重排 | 缩短 |
| 属性栏 | `inspector.arrange.dissolveTitle` | 缩 | 移除布局约束与成组，对象位置不变。 | 解除布局与成组，位置不变 | 缩短 |
| 属性栏 | `inspector.arrange.groupTip` | 删 | 点任意一个，即可选中并移动整组。 |  | 复述按钮名「成组」，提示改用按钮名 |
| 属性栏 | `inspector.arrange.refTip.primary` | 缩 | 以最后选中的对象为基准，基准对象保持不动。 | 以最后选中的对象为基准 | 缩短 |
| 属性栏 | `inspector.arrange.spacingHTitle` | 缩 | 水平间距：按 X 依次贴齐，第一个对象不动。 | 水平间距，第一个对象不动 | 缩短 |
| 属性栏 | `inspector.arrange.spacingVTitle` | 缩 | 垂直间距：按 Y 依次贴齐，第一个对象不动。 | 垂直间距，第一个对象不动 | 缩短 |
| 属性栏 | `inspector.autoHideTip` | 删 | 清空选择后自动收起。点击改为常驻。 |  | 同上 |
| 属性栏 | `inspector.control.legendCustomHint` | 缩 | 图例位于自定义位置。选择任一预设（含「最佳位置」）即可复位。 | 图例在自定义位置；选任一预设即可复位。 | 缩短 |
| 属性栏 | `inspector.element.alignHint` | 缩 | 子图调整的是它在图内的占比，文字与图例调整的是锚点；两者都不改变这张图在画布上的尺寸。 | 只调整图内布局，不改变这张图在画布上的尺寸。 | 缩短 |
| 属性栏 | `inspector.element.alignHintAnnotations` | 保留（缩短） | 画布标注跟着同一条基线排，位置改在画布对象上。 | 选中的画布标注会移到同一条基线，位置改在画布上。 | 多选混入画布标注时对齐会写标注的画布级 x/y，提示必须说清会移动标注（Codex #765）；按 hasAnnotations 二选一，有测试钉住 |
| 属性栏 | `inspector.element.alignHintGroup` | 删 | 拖画布上的组包围框手柄也能成组缩放。 |  | 复述控件：拖组包围框手柄 |
| 属性栏 | `inspector.element.alignNoop` | 缩 | 这些元素已经对齐，无需改动。 | 这些元素已对齐。 | 缩短 |
| 属性栏 | `inspector.element.batchHint_other` | 删 | 改动将写入选中的 {{count}} 个元素 |  | 复述选中计数（标题已写「已选 N 个」） |
| 属性栏 | `inspector.element.colorScaleTip` | 缩 | 色图与色阶范围共用同一份状态，修改任一处另一处同步更新。 | 色图与色阶范围同步 | 缩短 |
| 属性栏 | `inspector.element.scaleTitle` | 删 | 绕中心缩放，组内相对布局不变 |  | 输入框旁已有单位「%」与「应用」按钮，复述控件 |
| 属性栏 | `inspector.element.textBatchHintMixed_other` | 缩 | 元素类型不同，只列出共同支持的文字样式。改动会写到全部 {{count}} 个元素。 | 元素类型不同，只列出共同支持的文字样式。 | 缩短 |
| 属性栏 | `inspector.hideElementTip` | 缩 | 隐藏元素，可随时恢复 | 隐藏元素 | 控件名旁复述「可恢复」 |
| 属性栏 | `inspector.legend.customHint` | 缩 | 示意线不再由图中对象派生；恢复链接会回到由它派生的样子。 | 恢复链接会回到跟随图中对象的样子。 | 缩短 |
| 属性栏 | `inspector.legend.customHintStyled` | 缩 | 示意线用的是你在这里改的样式；恢复链接会一并撤销这些修改。 | 恢复链接会撤销这里改的样式。 | 缩短 |
| 属性栏 | `inspector.legend.followHint` | 缩 | 图例示意由图中对象派生；断开关联后才能单独修改颜色、线型和标记。 | 断开关联后才能单独改颜色、线型和标记。 | 缩短 |
| 属性栏 | `inspector.overlayTip` | 缩 | 窗口太窄，右栏暂时显示为覆盖层。加宽窗口即可固定。 | 窗口太窄，右栏暂时覆盖在画布上 | 缩短 |
| 属性栏 | `inspector.panel.aspectLocked` | 缩 | 宽高比已锁定，调整尺寸时等比缩放 | 宽高比已锁定 | 复述开关状态 |
| 属性栏 | `inspector.panel.aspectUnlocked` | 缩 | 宽高比已解锁，W 与 H 互不影响 | 宽高比已解锁 | 复述开关状态 |
| 属性栏 | `inspector.panel.cropCancelTip` | 缩 | 还原进入裁剪时的取景（Esc） | 取消裁剪（Esc） | 缩短 |
| 属性栏 | `inspector.panel.cropDoneTip` | 缩 | 保留当前取景（Enter） | 完成裁剪（Enter） | 缩短 |
| 属性栏 | `inspector.panel.frameLegacyBody` | 缩 | 这张图在排版上仍按 figsize 显示；脚本保存时把它裁成了 {{w}} × {{h}} mm，边上的标签才完整。改用那个图幅后，图在排版上的位置不变，只是外框变化。 | 这张图在排版上仍按 figsize 显示；脚本保存时裁成了 {{w}} × {{h}} mm。改用后位置不变，只是外框变化。 | 缩短 |
| 属性栏 | `inspector.panel.nativeTip` | 缩 | 素材自身尺寸 {{w}} × {{h}} cm；缩放 % 一律相对它计算 | 素材自身尺寸 {{w}} × {{h}} cm | 缩短 |
| 属性栏 | `inspector.panel.nativeTipMulti` | 缩 | 素材自身的尺寸；缩放 % 一律相对它计算 | 素材自身尺寸 | 缩短 |
| 属性栏 | `inspector.panel.replaceDescription` | 缩 | 位置、尺寸、裁剪、旋转与层级都会保留。图内修改无法跨脚本迁移，清空前会先征求你的同意。 | 图内修改无法带到新素材，清空前会先征求你的同意。 | 留会清空的后果 |
| 属性栏 | `inspector.panel.replaceTip` | 删 | 保留位置、尺寸、裁剪与层级，只换图源 |  | 弹窗说明里已写，按钮旁再写一遍是复述 |
| 属性栏 | `inspector.panel.scaleTitle` | 缩 | 原始大小的绝对百分比，100% 即原始。裁剪不改变基准 | 相对原始大小；裁剪不改变基准 | 缩短 |
| 属性栏 | `inspector.pinnedTip` | 删 | 常驻：清空选择也保持展开。点击改为自动收起 |  | 图钉按钮的提示复述 aria 名，改为只显示按钮名 |
| 属性栏 | `inspector.sync.notOnCanvas` | 缩 | 目标图不在画布上，只能写回原文件，与已有基线合并并自动备份。想先看效果，把它拖进画布再同步。 | 目标图不在画布上，只能写回原文件（合并基线并自动备份）。想先看效果，先拖进画布。 | 覆盖文件类：留后果，缩短 |
| 属性栏 | `inspector.sync.nothingToSync` | 缩 | 没有可同步的项，取消即可。 | 没有可同步的项。 | 复述取消按钮 |
| 属性栏 | `inspector.sync.tip` | 缩 | 把图内修改映射到同脚本的其它图。组图↔子图双向都行，方向随发起方。 | 把图内修改同步到同脚本的其它图 | 缩短 |
| 属性栏 | `inspector.text.interpretationAutoTip` | 缩 | 只对无法绘制的字符合成上下标（默认），其余原样保留。 | 只对无法绘制的字符合成上下标（默认） | 缩短 |
| 属性栏 | `inspector.text.interpretationScientificTip` | 缩 | 所有能识别的 Unicode 上下标都合成：字体统一，但从导出的 PDF 复制会得到 105 而不是 10⁵。 | 合成所有能识别的 Unicode 上下标；从导出的 PDF 复制会得到 105 而不是 10⁵。 | 缩短 |
| 属性栏 | `inspector.text.matchTitle` | 缩 | 标注字号是页面绝对值。「{{panel}}」缩放到 {{scale}}% 后，图内 {{base}} pt 正文约显示为 {{eff}} pt；想让标注与正文一样大，用这个值。 | 标注字号是页面绝对值。「{{panel}}」缩放到 {{scale}}% 后，图内 {{base}} pt 正文约显示为 {{eff}} pt；用这个值可与正文一样大。 | 缩短 |
| 属性栏 | `inspector.textActions.subTitle` | 缩 | 下标：选中一段再点，写成 matplotlib 公式（H$_{2}$O） | 下标：选中一段再点（H$_{2}$O） | 去掉实现细节 |
| 属性栏 | `inspector.textActions.supTitle` | 缩 | 上标：选中一段再点，写成 matplotlib 公式（cm$^{-1}$） | 上标：选中一段再点（cm$^{-1}$） | 去掉实现细节 |
| 属性栏 | `inspector.textControls.fontMissingHint` | 缩 | 这台电脑没装这个字体，图上用的是别的字体。换一个可用的字体，或装上它。 | 这台电脑没装这个字体，图上用的是别的字体。 | 去掉操作指引（下拉框本身就是换字体的控件） |
| 属性栏 | `inspector.versionHistory.originWarn` | 缩 | 「脚本原始」按脚本<b>当前</b>输出重新渲染，不还原原文件的字节。原图若经手工处理或脚本已改动，结果会不同，原文件只能从备份目录取回。 | 「脚本原始」按脚本<b>当前</b>输出重新渲染，不还原原文件的字节；原文件只能从备份目录取回。 | 覆盖文件类：留后果，缩短 |
| 属性栏 | `inspector.versionHistory.warnForward` | 缩 | 历史只前进不回卷：这次恢复会追加为一条新记录，随时可以再反悔。 | 这次恢复会追加为新记录，随时可以再恢复回去。 | 缩短 |
| 属性栏 | `inspector.writeBack.annotationsTitle` | 缩 | 叠在图上的画布箭头、文字与形状将按当前位置以矢量写入原 PDF，并同步重新生成 PNG。写回后从画布移除，可撤销。 | 叠在图上的画布箭头、文字与形状会以矢量写入原 PDF 并重新生成 PNG，写回后从画布移除（可撤销）。 | 覆盖文件类：留后果，缩短 |
| 属性栏 | `inspector.writeBack.backupDone` | 缩 | 原文件已备份到 {{dir}}，可展开看恢复办法 | 原文件已备份到 {{dir}} | 折叠行提示「可展开」复述控件 |
| 属性栏 | `inspector.writeBack.backupSummary` | 缩 | 覆盖前自动备份到 {{dir}}，可展开看恢复办法 | 覆盖前自动备份到 {{dir}} | 折叠行提示「可展开」复述控件 |
| 属性栏 | `inspector.writeBack.divergenceBody` | 缩 | ：当前编辑状态与「重开项目后重放一遍」的结果不一致，原文件未做任何改动。这属于引擎级问题，请把下面的信息报告给开发者。 | ：当前编辑状态与「重开项目后重放一遍」的结果不一致，原文件未改动。请把下面的信息报告给开发者。 | 缩短 |
| 左栏/画布/状态条 | `workspace.assets.emptyHint` | 缩 | 把 matplotlib 输出的 PDF/PNG 放进项目目录即可出现在这里。 | 把 PDF/PNG 放进项目目录即可出现在这里。 | 缩短 |
| 左栏/画布/状态条 | `workspace.assets.runtimeNoFile` | 缩 | 此图由脚本运行生成，尚无对应的原始图文件。仍可编辑、组图和导出；导出时会创建新文件。 | 此图由脚本运行生成，尚无原始图文件；导出时会创建新文件。 | 缩短，留会创建文件的后果 |
| 左栏/画布/状态条 | `workspace.assets.scriptBadgeTitle` | 缩 | 可编辑：由 matplotlib 脚本生成，能改图里的内容 | 可编辑 | 徽标的提示只需一个词 |
| 左栏/画布/状态条 | `workspace.confirm.replaceAssetBody_other` | 缩 | 这张图有 {{count}} 项图内修改，绑定在原脚本的元素上，换素材后会清空（可撤销）。位置、尺寸、裁剪与层级保留。 | 这张图有 {{count}} 项图内修改，换素材后会清空（可撤销）。位置、尺寸、裁剪与层级保留。 | 破坏性确认：保留后果 |
| 左栏/画布/状态条 | `workspace.confirm.resetOverridesBody_other` | 缩 | 这张图会回到源脚本当前生成的状态。{{count}} 项图内修改将清除（可撤销），源脚本与原始文件不变，同一文件的其他图不受影响。 | 这张图会回到源脚本当前生成的状态，{{count}} 项图内修改将清除（可撤销）。源脚本与原始文件不变。 | 破坏性确认：保留后果，去掉旁枝 |
| 左栏/画布/状态条 | `workspace.contextBar.countTitle` | 缩 | {{hint}} · 对齐参照：{{ref}} | 对齐参照：{{ref}} | 随 primaryHint 删除 |
| 左栏/画布/状态条 | `workspace.contextBar.moreArrangeTip` | 删 | 在属性页打开完整的排列工具：间距、布局组、复制样式 |  | 复述按钮名「更多排列」并列出内容清单 |
| 左栏/画布/状态条 | `workspace.contextBar.primaryHint` | 删 | 最后选中的对象是主选，轮廓更粗；「主选」参照以它为基准。 |  | 浮动栏计数的提示里复述「主选」样式说明 |
| 左栏/画布/状态条 | `workspace.crash.body` | 缩 | 排版已自动保存在本机，刷新后从最后一次快照继续。若刷新后仍然出错，可选择「打开空白排版」；原来的排版不会删除，可从「本机最近的排版」取回。 | 排版已自动保存在本机，刷新后从最后一次快照继续。仍然出错可选「打开空白排版」，原排版不会删除。 | 缩短 |
| 左栏/画布/状态条 | `workspace.drawer.pinHint` | 删 | 钉住：选中对象时不自动收起 |  | 图钉按钮的提示复述 aria 名 |
| 左栏/画布/状态条 | `workspace.drawer.unpinHint` | 删 | 取消钉住 |  | 同上 |
| 左栏/画布/状态条 | `workspace.elementTree.noPanelHint` | 删 | 双击画布上带可编辑标记的图，或从「素材」中打开 |  | 空态标题与按钮已说明，复述 |
| 左栏/画布/状态条 | `workspace.fastEdit.addedForEditLive` | 缩 | 已为编辑加入本排版，移除即撤销这一步。 | 已为编辑加入本排版，移除即撤销。 | 缩短 |
| 左栏/画布/状态条 | `workspace.layerTree.editableBadge` | 缩 | 可编辑：由脚本生成，双击进入图内编辑 | 可编辑 | 徽标的提示只需一个词 |
| 左栏/画布/状态条 | `workspace.panelBadge.approxPreviewHint` | 缩 | 画布暂显示磁盘上的原图，未反映图内修改。渲染完成后自动更新。 | 画布暂显示磁盘上的原图，未反映图内修改。 | 删「自动更新」的复述 |
| 左栏/画布/状态条 | `workspace.panelBadge.frameLegacyBlockedHint` | 缩 | 脚本保存的原图裁得不一样，但这张图的裁剪范围完全在原图的图幅之外。要改用原图的图幅，先在右侧属性里调整或重置裁剪。 | 脚本保存的原图裁得不一样，但这张图的裁剪范围完全在原图之外。先在右侧属性里调整或重置裁剪。 | 缩短 |
| 左栏/画布/状态条 | `workspace.panelBadge.frameLegacyHint` | 缩 | 脚本保存的原图裁得不一样。选中这张图，在右侧属性里可以改用原图的图幅。 | 脚本保存的原图裁得不一样。在右侧属性里可以改用原图的图幅。 | 缩短操作指引 |
| 左栏/画布/状态条 | `workspace.readiness.reason.registered_source` | 缩 | 由 {{script}} 生成，可以直接改图里的内容。 | 由 {{script}} 生成。 | 复述控件（双击可编辑） |
| 左栏/画布/状态条 | `workspace.scripts.dropped_other` | 缩 | 还有 {{count}} 张未捕获。显式 savefig 不受限。 | 还有 {{count}} 张未捕获。 | 删术语「显式 savefig」 |
| 左栏/画布/状态条 | `workspace.scripts.recoveryBody` | 缩 | 项目可能依赖原来的 Python 环境、工作目录或运行参数。先选好渲染环境；以后会支持「按项目原方式运行」。 | 项目可能依赖原来的 Python 环境、工作目录或运行参数。先选好渲染环境。 | 删设计承诺「以后会支持」 |
| 左栏/画布/状态条 | `workspace.stage.emptyHint` | 缩 | 从素材库选一张图放到画布上，或直接拖进来。 | 从素材库选一张图放到画布上。 | 复述「拖进来」 |
| 左栏/画布/状态条 | `workspace.stage.emptyHintNoAssets` | 缩 | 把 matplotlib 的 PDF/PNG 输出或绘图脚本放进项目目录，就会出现在素材库。 | 把 PDF/PNG 或绘图脚本放进项目目录，就会出现在素材库。 | 缩短 |
| 左栏/画布/状态条 | `workspace.status.dragNotMovable.pixelCoords` | 缩 | 「{{label}}」按像素、字号单位或别的对象定位，拖到的位置在换分辨率导出或那个对象变动后会漂，暂不支持拖动 | 「{{label}}」按像素或别的对象定位，拖动后位置会漂，暂不支持拖动 | 缩短，仍说明为什么 |
| 左栏/画布/状态条 | `workspace.status.dragNotMovable.series` | 缩 | 「{{label}}」的位置由数据决定，不能拖；要挪整个子图，请拖子图空白处或在元素树里选中子图 | 「{{label}}」的位置由数据决定，不能拖；要挪整个子图，请拖子图空白处 | 缩短，仍说明为什么 |
| 左栏/画布/状态条 | `workspace.status.layoutGroupCreated_other` | 缩 | 已创建{{kind}}（{{count}} 个成员）；改间距/列数会自动重排 | 已创建{{kind}}（{{count}} 个成员） | 复述控件行为 |
| 左栏/画布/状态条 | `workspace.status.objectsCopied` | 缩 | 已复制 {{count}} 个对象，可粘贴到其他排版 | 已复制 {{count}} 个对象 | 复述粘贴能力 |
| 左栏/画布/状态条 | `workspace.status.packaged_other` | 缩 | 已生成项目包 {{name}}（{{count}} 个素材），换电脑可从顶栏的排版菜单 →「导入项目包…」打开 | 已生成项目包 {{name}}（{{count}} 个素材） | 复述菜单路径 |
| 左栏/画布/状态条 | `workspace.status.sourceLinked_other` | 缩 | 已找到源脚本，双击图就能改图里的内容（{{count}} 张） | 已找到源脚本（{{count}} 张） | 复述双击操作 |
| 左栏/画布/状态条 | `workspace.status.styleCopiedArrow` | 缩 | 已复制箭头样式（线宽 / 颜色 / 端型 / 线型） | 已复制箭头样式 | 状态条内的括号清单 |
| 左栏/画布/状态条 | `workspace.status.styleCopiedPanel` | 缩 | 已复制图的样式（裁剪 / 旋转 / 不透明度） | 已复制图的样式 | 状态条内的括号清单 |
| 左栏/画布/状态条 | `workspace.status.styleCopiedShape` | 缩 | 已复制形状样式（描边 / 填充 / 圆角 / 线型） | 已复制形状样式 | 状态条内的括号清单 |
| 左栏/画布/状态条 | `workspace.status.styleCopiedText` | 缩 | 已复制文字样式（字号 / 粗斜下划线 / 颜色 / 行距 / 背景描边） | 已复制文字样式 | 状态条内的括号清单 |
| 左栏/画布/状态条 | `workspace.status.subLabelsAdded_other` | 缩 | 已添加 {{count}} 个序号标签，按从上到下、从左到右排序 | 已添加 {{count}} 个序号标签 | 排序规则已在按钮提示里 |
| 左栏/画布/状态条 | `workspace.stylePanel.mixedHint` | 缩 | 这张图里这几处的值不一样；输入一个值会把它们统一 | 这几处的值不一样；输入一个值会统一它们 | 缩短 |
| 左栏/画布/状态条 | `workspace.stylePanel.unboundHint` | 缩 | 选一套样式，这张画布上的图就按它显示；之后改它，图跟着变。 | 选一套样式，这张画布上的图就按它显示。 | 缩短 |
| 弹窗 | `dialogs.engineEnv.description` | 删 | {{product}} 用这个 Python 环境运行你的脚本。 |  | 复述弹窗标题「渲染环境」 |
| 弹窗 | `dialogs.export.blockedTitle` | 缩 | 有阻断性问题或无法自动核验的项。勾选上方确认后再导出。 | 有阻断问题或无法核验的项。勾选确认后才能导出。 | 缩短 |
| 弹窗 | `dialogs.export.epsUnavailable.canvas_scope` | 缩 | EPS 只支持按「原图尺寸」导出单张图。画布由 PDF 引擎合成，不支持 PostScript。 | EPS 只支持按「原图尺寸」导出单张图。 | 删实现说明「PDF 引擎合成」 |
| 弹窗 | `dialogs.export.jobLost` | 缩 | 找不到这次导出的作业，服务可能重启过。文件可能已写出，请检查导出目录或重新导出。 | 找不到这次导出的作业。文件可能已写出，请检查导出目录或重新导出。 | 缩短 |
| 弹窗 | `dialogs.export.locatedHint` | 缩 | 已定位到问题。再点「导出」回到导出面板，设置还在。 | 再点「导出」回到导出面板，设置还在。 | 缩短 |
| 弹窗 | `dialogs.export.reportTitle` | 缩 | 随导出生成一份 JSON 报告：所用规范、字体、字号、线宽、图幅、检查结果与导出设置，供投稿留档。 | 随导出生成一份 JSON 报告，供投稿留档。 | 内容清单删除 |
| 弹窗 | `dialogs.export.sizeDiskDiffers` | 缩 | 磁盘上的原文件为 {{dw}} × {{dh}} mm（脚本保存时已裁至内容范围）。将按图幅 {{w}} × {{h}} mm 导出。 | 磁盘上的原文件为 {{dw}} × {{dh}} mm（脚本保存时已裁边），将按图幅 {{w}} × {{h}} mm 导出。 | 缩短 |
| 弹窗 | `dialogs.export.strictTitle` | 缩 | 按所选规范重新打开写好的文件核对：完整性、尺寸、字体嵌入、文字层、图像分辨率。有一项不合格或无法核验，那个文件就不会保存。EPS 不在核验范围内。 | 按所选规范重新打开写好的文件核对；不合格或无法核验的文件不会保存。EPS 不核验。 | 内容清单删除，留「不会保存」的后果 |
| 弹窗 | `dialogs.layout.saveToProjectHint` | 缩 | 这份排版还只在本机。存进项目之后，再保存会直接更新项目里的这个文件。 | 这份排版还只在本机。存进项目后，再保存会更新项目里的这个文件。 | 缩短 |
| 弹窗 | `dialogs.nativeRun.argCount_other` | 缩 | {{count}} 个（参数值不会传入此窗口） | {{count}} 个（不显示参数值） | 缩短 |
| 弹窗 | `dialogs.presets.items.axes.hint` | 删 | x / y 方向角标 |  | 复述预设名与预览图 |
| 弹窗 | `dialogs.presets.items.braceGroup.hint` | 删 | 大括号 + 说明文字 |  | 复述预设名与预览图 |
| 弹窗 | `dialogs.presets.items.callout.hint` | 删 | 引线 + 文字 |  | 复述预设名与预览图 |
| 弹窗 | `dialogs.presets.items.crystal.hint` | 删 | 箭头 + [001] 标注 |  | 复述预设名与预览图 |
| 弹窗 | `dialogs.presets.items.dimension.hint` | 删 | 双向箭头 + 两端界线 |  | 复述预设名与预览图 |
| 弹窗 | `dialogs.presets.items.errorbar.hint` | 删 | 工字线 + ± 值 |  | 复述预设名与预览图 |
| 弹窗 | `dialogs.presets.items.magnifier.hint` | 删 | 虚线框 + 引出线 + 放大框 |  | 复述预设名与预览图 |
| 弹窗 | `dialogs.presets.items.reversible.hint` | 删 | 两条反向箭头（⇌） |  | 复述预设名与预览图 |
| 弹窗 | `dialogs.presets.items.scalebar.hint` | 删 | 粗线 + 长度标注 |  | 复述预设名与预览图 |
| 弹窗 | `dialogs.readiness.allScriptsHint` | 缩 | 这里列出项目里的所有 .py，包括无法从代码判断输出的脚本。试运行一次后，按实际画出的图建立关联。 | 这里列出全部 .py。试运行一次，按实际画出的图建立关联。 | 缩短 |
| 弹窗 | `dialogs.readiness.orphanConflicts` | 缩 | 多个脚本指向同一图名：{{stems}}。项目里尚无对应的图文件，可在下方「全部脚本」中指定归属。 | 多个脚本指向同一图名：{{stems}}。可在下方「全部脚本」中指定归属。 | 缩短 |
| 弹窗 | `dialogs.scriptInput.eofTip` | 缩 | 告诉脚本输入已经结束（相当于终端里的 Ctrl-D） | 告诉脚本输入已结束（相当于 Ctrl-D） | 缩短 |
| 弹窗 | `dialogs.scriptInput.manageIntro` | 缩 | 这个脚本运行时读到的输入。改了答案会立刻重新运行脚本。 | 改了答案会立刻重新运行脚本。 | 留会重跑脚本的后果 |
| 弹窗 | `dialogs.scriptInput.manageWhere` | 缩 | 答案保存在项目文件夹的 {{path}} 里，会随项目文件夹一起复制、同步或分享；项目包不包含它。 | 答案存在项目文件夹的 {{path}} 里，随文件夹一起复制或分享；项目包不含它。 | 缩短 |
| 弹窗 | `dialogs.scriptInput.noOutput` | 删 | 脚本还没有输出任何内容。 |  | 描述不存在的状态：没有输出就不显示「输出」小节 |
| 弹窗 | `dialogs.scriptInput.rememberNote` | 缩 | 回答会按项目记住，下次运行自动使用；随时可以在脚本行上查看和修改。 | 答案会按项目记住，下次运行自动使用。 | 删「可以查看修改」的复述 |
| 弹窗 | `dialogs.style.descriptionEmpty` | 删 | 把字号、线宽、刻度、配色存成命名样式，批量应用。只写图内修改，不改源文件。 |  | 弹窗副标题复述功能，内容与 emptyBody 重复 |
| 弹窗 | `dialogs.style.emptyBody` | 保留（缩短） | 还没有保存的样式。选中一张已渲染、可编辑的图，提取字号、线宽、刻度、配色作为起点。 | 还没有保存的样式。选中一张已渲染、可编辑的图后可从它提取。 | 提取只接受可编辑且已渲染出 manifest 的图，限定词要留，禁用状态才说得出缺什么（Codex #765） |
| 弹窗 | `dialogs.style.emptyDraft` | 缩 | 空样式。点「从当前图提取」读取字号、线宽、刻度、配色，删掉不想统一的项，再保存。 | 空样式。点「从当前图提取」，再删掉不想统一的项。 | 缩短 |
| 弹窗 | `dialogs.style.extractNeedPanel` | 保留（缩短） | 选中一张已渲染、可编辑的图后可提取 | 先选中一张已渲染、可编辑的图 | 恢复「已渲染 / 可编辑」限定，禁用原因说得出缺什么（Codex #765） |
| 弹窗 | `dialogs.style.needPanel` | 保留（缩短） | 选中一张已渲染、可编辑的图后可提取 | 先选中一张已渲染、可编辑的图 | 恢复「已渲染 / 可编辑」限定，禁用原因说得出缺什么（Codex #765） |
| 弹窗 | `dialogs.style.paletteTitle` | 缩 | 系列配色，按曲线、散点、柱形的出现顺序循环。 | 系列配色，按出现顺序循环。 | 缩短 |
| 弹窗 | `dialogs.telemetry.intro` | 缩 | 可发送匿名用量统计，帮我们了解哪些功能有人用。默认关闭；标识是本机随机 UUID，与账号无关。 | 可发送匿名用量统计，了解哪些功能有人用。默认关闭，与账号无关。 | 联网类：留关键事实，缩短 |
| 弹窗 | `dialogs.telemetry.later` | 缩 | 随时可在「设置 → 隐私、诊断与 About」里改。 | 随时可在设置里改。 | 缩短 |
| 弹窗 | `dialogs.updateNotice.intro` | 删 | 更新后即可使用本版的新功能与修复。 |  | 复述标题「新版本已发布」；无发行说明时不再摆空话 |
| 弹窗 | `dialogs.updateNotice.upgraded` | 缩 | 已升级到 {{version}}。重启 Tavotto 后生效。 | 已升级到 {{version}}，重启后生效。 | 缩短 |
| 改图助手 | `ai.history.revertTip` | 缩 | 回滚此次修改，恢复脚本快照 | 回滚此次修改 | 复述 |
| 改图助手 | `ai.history.snapshotAvailable` | 缩 | 快照可用 · id {{id}} | 快照可用 | 内部 id 不给用户看 |
| 改图助手 | `ai.history.snapshotCleared` | 缩 | 快照已清理 · id {{id}} | 快照已清理 | 内部 id 不给用户看 |
| 改图助手 | `ai.panel.agentNote` | 缩 | 直接修改脚本文件；每次运行前自动快照，可随时回滚。 | 直接修改脚本；每次运行前自动快照，可回滚。 | 改脚本类：留后果，缩短 |
| 改图助手 | `ai.panel.emptyHint` | 缩 | 助手直接修改图的脚本，改动可查看差异，也可回滚。 | 助手会直接修改脚本，改动可回滚。 | 缩短 |
| 改图助手 | `ai.panel.noPanelHint` | 删 | 带可编辑标记的图来自脚本，助手可直接修改。 |  | 死键（无引用），且复述标题 |
| 改图助手 | `ai.session.doneChanged` | 缩 | 改动已写入脚本，可查看差异或回滚 | 改动已写入脚本 | 复述旁边的「查看差异 / 回滚」按钮 |
| 问题面板 | `errors.problems.cardManualTip` | 删 | 这里的问题没有安全的自动修法，请逐条处理 |  | 复述旁边的「手动」二字 |
| 问题面板 | `errors.problems.drillDoneHint` | 删 | 其余问题在上一层 |  | 标题「这里的问题都处理完了」已说完，删去第二句 |
| 问题面板 | `errors.problems.failedHint` | 缩 | 这次没能查完。排版可能还在载入，稍后再试。 | 这次没能查完，稍后再试。 | 缩短 |
| 项目/工作区 | `project.figurePicker.description_other` | 删 | 脚本产出 {{count}} 张图，选择要打开的一张 |  | 复述弹窗标题「选择一张图」 |
| 项目/工作区 | `project.switcher.newTabTip` | 缩 | 在新标签页打开，可切到另一个项目 | 在新标签页打开 | 缩短 |

## 组件侧同步改动

- `PresetsDialog` 卡片去掉 `title={presetHint(id)}`，`lib/presets.ts` 删 `presetHint`
- `EngineEnvironmentDialog` / `StyleDialog` / `FigurePickerDialog` 去掉 `description` 副标题（及 `PRODUCT_NAME` 的无用 import）
- `UpdateNoticeDialog`：无发行说明时不再摆一句 intro；标题已含版本号，正文没有任何内容（无说明、无链接、无失败信息）时 body 为 null
- `ui/Dialog`：children 为 null / false 时不渲染正文容器（否则空正文仍留 24px 内边距）
- `ScriptInputDialog`：没有输出时整节（含小标题「脚本到目前为止的输出」）不显示，不再写「还没有输出」
- `ElementInspector`：对齐说明只留一句；批量修改的「改动将写入选中的 N 个元素」删去，仅「类型不同」时才出一句
- `ArrangeSection` / `MultiSelectionBar` / `Inspector` / `LeftPanel` / `ElementTree` / `ProblemPanel` / `ProblemCards` / `PanelSection` 去掉复述控件名的 `tip` / `title` / `hint` 属性（`IconButton` 缺省气泡就是按钮名）

## 保留（审过，理由）

- 覆盖文件 / 联网 / 改脚本 / 不可撤销类：写回、版本恢复、导出核验、脚本输入记住、遥测同意、原环境运行权限、确认弹窗——只缩短，后果句保留。
- `workspace.readiness.reason.no_source_candidate` / `registered_script_missing`：测试钉住「仅版面要说清还能做什么」，保留。
- `dialogs.nativeRun.permissionNotice`：测试钉住权限说明（ADR 0021 / compatibility 文档引用），保留。
- `dialogs.export.scopeUnavailable.no_figure`：画布范围下唯一的下一步指引，保留。
- `dialogs.export.pdfHint/pngHint/tiffHint`（矢量 / 位图）：初学者选格式的依据，保留。
- `inspector.canvas.presets.*.hint`：气泡里带期刊单栏宽等信息，保留。
- `project.workspace.hint`（项目 ≠ 排版的名词说明）：09-26 用户拍板的四个名词说明，保留。
- `workspace.hints.*`：一次性提示的 id 是持久化格式（onboarding-and-activity），归教程那一批，未碰。
- `workspace.status.dragNotMovable.*`：ADR 0100「拖不动要说出为什么」，只缩长句。

## 让路（在飞 PR，待合入后再删）

- **待办：让路** — `errors.json` 的 `engine.*`（依赖修复 / 运行目录 / 改指表 / 脚本备份等整片）：#716 #730 #760 在改，本轮不动，等它们合入后另开一轮。
- `workspace.status.grouped_other`：#691 新增 `groupBlocked` 紧邻它，merge-tree 报 resources.d.ts 冲突，撤回（原文「点任一成员会整组选中」待 #691 合入后再缩）。
- `dialogs.versions.*`（含 `compareDescription` / `compareTip` / `keepTitle` / `restored`）：#679 改了整片。
- `dialogs.settings.*`、`project.home.*`、`dialogs.onboarding.*` / `workspace.hints.*`：其它子代理在改，未碰。
- `dialogs.updateNotice.sourceBody`：#679/#727 邻行。
