/**
 * 准备会话的「进入编辑」——**唯一**把一次尝试捕获到的那一张图放进画布并打开图内编辑的地方（ADR 0116 / 0124 / 0125）。
 *
 * 两个调用方：卡片上用户点的「进入编辑」，和「打开并运行」认领之后会话 `completed` 时的自动进入
 * （`projectPreparationStore`）。它们共用这一份实现，所以面板身份、去重与「进入编辑才开标签页」的契约只有一处：
 * 面板身份是资产 id（不含执行语义），safe 静默回退到 native 之后仍是同一张图、同一块面板，不会多开标签页。
 * 桌面拥有的 native 会话不再有第二个展示面（`nativeSessionStore` 对它们不 build / 不放置）。
 */
import type { CapturedFigureDescriptor } from '@/lib/api'
import { captureProjectEpoch } from '@/lib/projectEpoch'
import { findFigurePanel } from '@/store/documentStore'
import { takeDeferredEntryOutcome } from '@/store/documentRestore'
import type { EditRender } from '@/store/projectPreparationStore'
import { renderKeyOf } from '@/store/renderStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import { useUiStore } from '@/store/uiStore'
import { addRuntimePanelToCanvas, openFastEdit } from '@/store/workspace'
import { useWorkspaceStore } from '@/store/workspaceStore'
import type { PanelObject } from '@/types/document'

/**
 * 入口动作为这张图创建 / 复用的面板与渲染键（动作做完、面板已在文档里的那一刻记下）。
 * 文档里找不到面板就没有可观察的渲染——不猜。
 */
function editRenderOf(assetId: string): EditRender | undefined {
  const found = findFigurePanel(assetId)
  return found ? { panelId: found.panel.id, renderKey: renderKeyOf(found.panel) } : undefined
}

/**
 * 调用方的会话持有者（`projectPreparationStore`）的两只手：项目代际（挂起期间换了项目就丢弃）与「记入编辑记录」。
 * 本模块不 import 持有者——持有者的自动进入要动态 import 本模块，反过来再 import 就是一个环（`importArchitecture` 门禁）。
 */
export interface EnterEditHost {
  /** 持有者此刻的项目代际 */
  epoch: () => number
  /** 记入编辑记录：资产 id + 它在画布上那个面板的渲染键 */
  note: (assetId: string, render?: EditRender) => void
  /**
   * 自动进入专用：素材清单的 await 之后、真正动工作区之前再核一次「用户还在看这张卡」。
   * 不满足就放弃（回 `'stale'`，什么都没做）；用户手点「进入编辑」不传它，中途切走仍按原行为。
   */
  stillWanted?: () => boolean
}

/**
 * 结果对话框刚加进画布的那个面板：**直接**记它的 id 与渲染键，不按 assetId 去文档里找——同一素材文档里可能已经有
 * 别的实例，按 assetId 找会选中旧实例、它已有的就绪渲染会让「首次编辑渲染」立刻成立。
 * 取文档里的现值（加图之后取景等动作可能改过对象），找不到才用返回的那个对象。
 */
export function noteAddedPanelFor(host: EnterEditHost, assetId: string, added: PanelObject): void {
  const live = findFigurePanel(assetId, added.id)?.panel ?? added
  host.note(assetId, { panelId: live.id, renderKey: renderKeyOf(live) })
}

/**
 * 进入编辑 = 稳定动作 `openFastEdit`（加进文档并说出口 → 进入图内编辑 → 引擎按热会话渲染，脚本不再跑）。
 * 素材清单还没取到这张新图时先用这次捕获的描述符把它加进画布，再进入编辑。只处理**一张**图（多张要用户挑，调用方不走这里）。
 *
 * 回 `'entered'`（已进入且卡片收起）/ `'deferred'`（上次的排版还在恢复：入口已排队，落地后才收卡片）/
 * `'blocked'`（上次的排版还要用户先处理，什么都没放进去，卡片留着）/ `'stale'`（挂起期间换了项目，什么都没做）/
 * `'not_entered'`（图找不到可用来源 `missing`、或进不了图内编辑 `layout_only`：没有真的进入编辑，卡片留着，手动「进入编辑」仍在）。
 * **只有真正进入编辑（`openFastEdit` 回 `editing`）才收卡片、记编辑。**
 */
export type EnterEditOutcome = 'entered' | 'deferred' | 'blocked' | 'stale' | 'not_entered'

export async function enterEdit(host: EnterEditHost, d: CapturedFigureDescriptor): Promise<EnterEditOutcome> {
  // 挂起期间可能切了项目：续延带着 A 的描述符，绝不能把 A 的面板加进 B 的版面
  const guard = captureProjectEpoch(host.epoch)
  await useRuntimeAssetStore.getState().loadAssets()
  if (!guard.still()) return 'stale'
  if (host.stillWanted && !host.stillWanted()) return 'stale'
  // 清单里没有、文档里也还没有这张图才加：清单请求在途时连点两下，两个续延都看到「清单里没有」，
  // 只有文档才是已经加过一次的权威（补审 #914 r4236701619）。这两步之间没有 await，第二个续延必然看到第一个加的面板
  // 只认带描述符的条目：开始于跑完之前的清单请求被 `loadAssets()` 合并时，缓存里这张图的条目可能还是 `descriptor: null`，
  // 这时 `openFastEdit` 找不到可用来源——用报告里这次捕获的描述符 `d` 补上。重跑一张已缓存的图时，合并进来的旧清单里
  // 条目可能是**上一次**的非空描述符（尺寸 / 来源已变）：只有与这次的 `d` 一致才算已知，否则同样以 `d` 为准（Codex #1044 r4285970263）
  const fresh = JSON.stringify(d)
  const known = (useRuntimeAssetStore.getState().assets ?? []).some(
    (a) => a.id === d.asset_id && !!a.descriptor && JSON.stringify(a.descriptor) === fresh,
  )
  if (!known && !findFigurePanel(d.asset_id)) {
    // 与下面的 `openFastEdit` 同一个守卫：恢复期间两者都是排队的副作用（插入面板 + 取景 / 进入编辑 / 收卡片），切走后一个都不许执行
    if (host.stillWanted) addRuntimePanelToCanvas(d, undefined, undefined, { guard: host.stillWanted })
    else addRuntimePanelToCanvas(d)
  }
  const outcome = host.stillWanted ? openFastEdit(d.asset_id, { guard: host.stillWanted }) : openFastEdit(d.asset_id)
  // 记 `openFastEdit` 实际打开的那个面板（它选中的实例），不是文档里按素材 id 找到的第一个；
  // 进入编辑之后卡片自动收起，不留角标（工作区自己的「渲染完成」说结果）
  const landed = () => {
    const opened = useWorkspaceStore.getState().activePanelId
    const found = opened ? findFigurePanel(d.asset_id, opened) : null
    if (found) noteAddedPanelFor(host, d.asset_id, found.panel)
    else host.note(d.asset_id, editRenderOf(d.asset_id))
    useUiStore.getState().setGuideCard('closed')
  }
  if (outcome === 'blocked') {
    // 上次的排版还要用户先处理：这次什么都没放进去（门已经说了「上次的排版还没打开」）——卡片留着，处理完再点
    return 'blocked'
  }
  if (outcome === 'deferred') {
    // 上次的排版还在恢复：入口已排队，**真的落进文档之后**才收卡片；被取代 / 超时（没落）就留着
    // 以这个入口**自己的结局**为准（`ran`）：恢复被同项目里显式打开的别的排版取代时，入口被丢掉；
    // 那份排版里恰好有同一张图也不能当成这次进入编辑成了
    void takeDeferredEntryOutcome()?.then((result) => {
      // 自动进入：排队的入口已经跑了（撤不回），但恢复期间用户可能已离开这张卡——不替他收卡片 / 记编辑
      if (result === 'ran' && guard.still() && findFigurePanel(d.asset_id) && (!host.stillWanted || host.stillWanted())) landed()
    })
    return 'deferred'
  }
  if (outcome !== 'editing') {
    // `missing`（没有可用来源，工作区没动，已经说了一句）/ `layout_only`（图进来了但进不了图内编辑）：不算进入编辑，不收卡、不记编辑
    return 'not_entered'
  }
  landed()
  return 'entered'
}
