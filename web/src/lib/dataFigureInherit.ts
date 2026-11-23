/**
 * 用「从数据到图」画出来的新图落在哪张画布，哪张画布就按画那张图时的规范检查（用户 2026-11-23：「有期刊就直接按那本期刊的图宽和字号画」）。
 * 画出来之后图在**新画布**里打开（`openFastEdit` → `createCanvasAndActivate` → `addCanvas`，它只抄页面、不抄 `doc.profile`），
 * 所以要把草稿的绑定带过去，否则编辑器会拿默认规范去查一张按期刊画的图。
 *
 * 为什么是「盯着新画布出现」而不是在某个调用点接线：从「打开并运行」到画布出现中间隔着运行、准备卡、素材清单的 await，
 * 落地时刻不归我们（`enterEdit` 还可能排队到恢复完成之后）；`fresh` 也不是可靠的锚（合画没有它、换个画法会清它）。
 * 写法用 `documentStore.silent`（与 `rebindJournalId` 同一种）：**不进撤销栈、不清重做**，所以不会动 `addedForEditDepth`，
 * 胶囊上的「撤销」照旧只撤加图那一步；撤销之后空画布仍留着绑定，与「新建画布本来就不可撤销」一致，没有半截状态。
 * 只写**没有绑定**的画布，不覆盖任何已有的 `profile`；同一个入口只认第一个落点，认完 / 过期 / 换了文档就收手。
 */
import { useDocumentStore } from '@/store/documentStore'
import type { DocumentProfile } from '@/types/document'

/** 画好之后新画布最迟多久内出现才认（运行 + 进入编辑通常几秒；留够环境准备的余量，过了就不再认，免得之后无关的「新建画布」被套上期刊） */
export const INHERIT_WINDOW_MS = 120_000

/**
 * 退路里的页宽记录换成「这张新画布此刻的页宽」：新画布的页面是从原画布抄来的，绑定里克隆过来的 `journalPrev.pageW / setW`
 * 说的是原画布，原样带过来会让新画布上的「不按期刊」把页宽打回原画布的数。`pageW = setW` = 页宽仍是它时原地不动、改过就不碰。
 */
function forCanvas(binding: DocumentProfile, pageW: number): DocumentProfile {
  const out = structuredClone(binding)
  if (out.journalPrev) out.journalPrev = { ...out.journalPrev, pageW, setW: pageW }
  return out
}

function write(binding: DocumentProfile): void {
  const pageW = useDocumentStore.getState().doc.page.w
  useDocumentStore.getState().silent((d) => {
    if (!d.profile) d.profile = forCanvas(binding, pageW)
  })
}

/**
 * 画好、开始进入编辑的那一刻调用。返回收手函数（换项目 / 下一次画图时调）。落点出现时才写（运行没成、卡在准备卡上就什么都不留）：
 *  - 此刻激活的画布是空的：`openFastEdit` 会直接用它（不新建）——这张画布上出现第一个对象时，没有绑定就写上；已有绑定不动。
 *  - 否则：下一张**新出现**并被激活的画布，没有绑定就写上。
 * `still()`：调用方的项目代际（换过项目就不写）。
 */
export function armBindingInherit(binding: DocumentProfile, still: () => boolean, now: () => number = Date.now): () => void {
  const s0 = useDocumentStore.getState()
  const documentId = s0.documentId
  const canvas0 = s0.activeCanvasId
  const emptyHere = s0.doc.objects.length === 0
  const known = new Set(s0.canvases.map((c) => c.id))
  const until = now() + INHERIT_WINDOW_MS
  let off: (() => void) | null = null
  const stop = () => {
    off?.()
    off = null
  }
  off = useDocumentStore.subscribe((s) => {
    // 落点：空画布上长出第一个对象，或第一张新出现并被激活的画布（认完就收手，写之前先收：写本身会再触发订阅）
    const landed = emptyHere ? s.activeCanvasId === canvas0 && s.doc.objects.length > 0 : !known.has(s.activeCanvasId)
    if (!landed) return
    stop()
    if (now() > until || !still() || s.documentId !== documentId || s.doc.profile) return
    write(binding)
  })
  return stop
}
