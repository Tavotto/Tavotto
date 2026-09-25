/**
 * 「这个面板的 overrides 是否恰好等于文件上已经烙好的基线」——判据只有这一份。
 *
 * 「文件上已经烙好」是**两个**条件：overrides 与基线逐字相等，**且**磁盘文件自写回
 * 之后没被外部改写（`baked_current`，后端按写回时记录的文件身份判）。只查前者的话，
 * 用户在 Tavotto 外重跑自己的构建脚本把产物刷回脚本原值后，预览会一直挂磁盘原图
 * （脚本原值）而编辑态显示 script+overrides——永久分叉且互不报错。
 *
 * 三档不能压扁：`baked_current === false` 是「基线已失效」；`undefined` 是老后端
 * 没发这个字段，维持旧行为（当作有效）；`true` 是有效。
 *
 * 这是个纯函数：调用方自己把面板的 overrides 与素材的基线事实递进来。消费点
 * （`renderTargets` 跳过渲染、`PanelView` 显示走 /api/render、写回候选熄灭、导出
 * 判「有没有图内修改」）全部经由它，别在消费点各补一刀。`store/actions.isJustBakedBaseline`
 * 是它读素材表的那层薄包装。
 */

export interface BakedBaselineFacts {
  baked_overrides?: readonly { gid: string; prop: string; value: unknown }[] | null
  baked_current?: boolean
}

export function isJustBakedBaselineOf(
  overrides: readonly unknown[],
  facts: BakedBaselineFacts | undefined,
): boolean {
  const baked = facts?.baked_overrides
  if (!baked?.length) return false
  if (facts?.baked_current === false) return false
  return JSON.stringify(overrides) === JSON.stringify(baked)
}

/**
 * 「这个面板的 overrides 就是从素材基线原样抄来的」：只比内容，**不看** `baked_current`。
 *
 * 与 `isJustBakedBaselineOf` 回答的是两个问题：那个问「磁盘文件是否已经带着这些 override」
 * （决定渲染 / 写回 / 导出，文件被外部改过就不成立）；这个问「这些 override 是不是用户的手改」
 * ——文件被外部改过不会把 `addPanel` / 替换素材机械抄进来的基线变成用户的手改。唯一消费点是
 * 画布跟随样式的「新图」判据（ADR 0081）：拿前者判的话，基线失效的素材加进绑定画布会被当成
 * 手改过、永远不对齐（Codex #547 P2）。
 */
export function isCopiedBakedBaseline(overrides: readonly unknown[], facts: BakedBaselineFacts | undefined): boolean {
  const baked = facts?.baked_overrides
  if (!baked?.length) return false
  return JSON.stringify(overrides) === JSON.stringify(baked)
}
