/**
 * 对业务 store 的**只读订阅**（ADR 0016 §3）。
 *
 * 选择变化不需要在 uiStore / selectionStore 里插任何一行诊断代码——它们只是
 * 状态容器，谁改的、为什么改，订阅方自己看得出来。凡是能用订阅拿到的，
 * 就不往业务代码里塞调用点：少一处调用点，就少一处「新增了一个入口、
 * 忘了记诊断」。
 *
 * 反过来，commit / undo / 渲染 / preview 那几类**必须**在 store 内部记：
 * 它们要的是「这次操作的前后状态」和「补丁结构」，订阅只看得到结果。
 */
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { panelHash } from './hash'
import { recordIfChanged } from './store'
import type { SelectionKind } from './types'
import { safeGid } from './sanitize'

function kindOf(gidCount: number, objectCount: number): SelectionKind {
  if (gidCount && objectCount) return 'mixed'
  if (gidCount) return 'element'
  if (objectCount) return 'object'
  return 'none'
}

function noteSelection(): void {
  const ui = useUiStore.getState()
  const objectIds = useSelectionStore.getState().ids
  recordIfChanged('selection', {
    type: 'selection.changed',
    panel: ui.elementPanelId ? panelHash(ui.elementPanelId) : null,
    selection_kind: kindOf(ui.selectedGids.length, objectIds.length),
    selected_count: ui.selectedGids.length,
    // 技术 gid 原样留（axes_0.title 对排障有用），形状不对的换成 hash。
    // **画布对象 id 只有计数**：那些 id 本身没有诊断价值，进来只是多一个标识
    selected_gids: ui.selectedGids
      .slice(0, 24)
      .map(safeGid)
      .filter((g): g is string => g != null),
    object_count: objectIds.length,
  })
}

/**
 * 装上订阅。返回卸载函数（测试与热更新用）。**幂等**：重复调用只装一次，
 * 否则一次选择会记两条。
 */
let installed: (() => void) | null = null

export function installDiagnosticsWiring(): () => void {
  if (installed) return installed
  const unsubUi = useUiStore.subscribe((s, prev) => {
    if (s.selectedGids === prev.selectedGids && s.elementPanelId === prev.elementPanelId) return
    noteSelection()
  })
  const unsubSel = useSelectionStore.subscribe((s, prev) => {
    if (s.ids === prev.ids) return
    noteSelection()
  })
  installed = () => {
    unsubUi()
    unsubSel()
    installed = null
  }
  return installed
}
