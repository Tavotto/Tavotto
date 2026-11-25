/**
 * 建完图内副本后「选中新副本」（ADR 0207 §四）：副本是 override 里的一条 `copy_of`，要等下一份**权威（exact）manifest**
 * 里真的出现这个 gid 才选得中——所以 action 只登记一条「待选中」，这里订阅渲染态、对**当前状态**每次重验。
 *
 * 取消待选中（不再等、也不去选）的情形，每一条都是「用户已经不在那个现场」：
 *   - 换了文档 / 面板不在了 / 退出了这张图的图内编辑；
 *   - 副本的 `copy_of` 已不在 overrides 里（撤销，或被删掉）；
 *   - 用户在这期间自己改了选区（点了别的元素）；
 *   - 渲染失败（这一键的状态是 error），或这份 exact manifest 里没有它（引擎没建出来：与任何修改失败同一出口，选区不动）。
 * 模块级单例：会话态，不进文档。
 */
import { COPY_PROP } from '@/lib/figureElementCopy'
import { exactPanelManifest, renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useDocumentStore } from '@/store/documentStore'
import { useMountedPngStore } from '@/store/mountedPngStore'
import { useUiStore } from '@/store/uiStore'
import type { PanelObject } from '@/types/document'

interface PendingSelect {
  documentId: string
  panelId: string
  gids: string[]
  /** 登记那一刻的图内选区：之后变了 = 用户自己动过选区 */
  selected: string[]
}

let pending: PendingSelect | null = null
let unsubscribe: (() => void) | null = null

const same = (a: readonly string[], b: readonly string[]) => a.length === b.length && a.every((g, i) => g === b[i])

function cancel(): void {
  pending = null
  unsubscribe?.()
  unsubscribe = null
}

/** 对当前状态重验一次：条件满足就选中并收工，现场变了就取消，否则继续等 */
function evaluate(): void {
  const p = pending
  if (!p) return
  const docState = useDocumentStore.getState()
  const ui = useUiStore.getState()
  const panel = docState.doc.objects.find((o): o is PanelObject => o.id === p.panelId && o.type === 'panel')
  if (
    docState.documentId !== p.documentId ||
    !panel ||
    ui.elementPanelId !== p.panelId ||
    !same(ui.selectedGids, p.selected) ||
    !p.gids.every((g) => panel.overrides.some((o) => o.gid === g && o.prop === COPY_PROP))
  ) {
    cancel()
    return
  }
  const render = useRenderStore.getState()
  if (render.byKey[renderKeyOf(panel)]?.status === 'error') {
    cancel()
    return
  }
  const exact = exactPanelManifest(render, panel)
  if (!exact) return // 还在画：下一次状态变化再验
  if (p.gids.every((g) => exact.elements.some((e) => e.gid === g))) {
    cancel()
    ui.setSelectedGids(p.gids)
  } else cancel() // 这一版画好了、而副本不在里面：没建出来，选区不动
}

/** 登记「等副本出现后选中它们」。再登记一次 = 换成新的（连按 ⌘D 时只跟最后一批） */
export function selectCopiesWhenDrawn(panelId: string, gids: readonly string[]): void {
  cancel()
  pending = {
    documentId: useDocumentStore.getState().documentId,
    panelId,
    gids: [...gids],
    selected: [...useUiStore.getState().selectedGids],
  }
  const stop = [
    useRenderStore.subscribe(evaluate),
    useMountedPngStore.subscribe(evaluate), // exact 还依赖已挂载的位图清单
    useDocumentStore.subscribe(evaluate), // 撤销 / 换文档
    useUiStore.subscribe(evaluate), // 选区被用户动过 / 退出图内编辑
  ]
  unsubscribe = () => stop.forEach((f) => f())
  evaluate()
}

/** 测试与换项目用：丢掉待选中 */
export const cancelCopySelect = cancel

/** 有没有在等（测试用） */
export const copySelectPending = (): boolean => pending !== null
