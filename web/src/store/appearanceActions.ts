import { msg, type UiMessage } from '@/i18n'
import { applyColorSample, applyDefaultAppearance, clearFill, swapFillStroke, type ColorSample } from '@/lib/appearance'
import type { KeyId } from '@/lib/keymap'
import { isFillStrokeMark, isStrokedMark, markFillable } from '@/lib/markStyle'
import { useDocumentStore } from './documentStore'
import { finishActiveGesture } from './gestureCoordinator'
import { useSelectionStore } from './selectionStore'
import { useUiStore } from './uiStore'

/**
 * 外观命令表（ADR 0185 第二批）：D 默认填色描边、⇧X 互换填色和描边、/ 无填充、I 吸管。命令面板、快捷键读同一份
 * （与 `pathActions.PATH_COMMANDS` 同一种结构）。每个动作先收掉进行中的手势、对整个选区**一次 commit**；选区里不适用的对象不动。
 */

const hist = (key: string): UiMessage => msg(`history.${key}`, undefined, 'workspace')

const selectedMarks = () => {
  const ids = new Set(useSelectionStore.getState().ids)
  return useDocumentStore.getState().doc.objects.filter((o) => ids.has(o.id) && isStrokedMark(o) && !o.locked)
}
const selectedFillable = () => selectedMarks().filter((o) => isFillStrokeMark(o) && markFillable(o))

function applyToSelection(label: UiMessage, fn: (o: Parameters<typeof applyDefaultAppearance>[0]) => unknown): void {
  const ids = new Set(selectedMarks().map((o) => o.id))
  if (!ids.size) return
  finishActiveGesture()
  useDocumentStore.getState().commit(label, (d) => {
    for (const o of d.objects) if (ids.has(o.id) && isStrokedMark(o)) fn(o)
  })
}

export const resetAppearance = () => applyToSelection(hist('defaultAppearance'), applyDefaultAppearance)
export const swapAppearance = () => applyToSelection(hist('swapFillStroke'), swapFillStroke)
export const clearSelectedFill = () => applyToSelection(hist('clearFill'), clearFill)

/** 吸管落下：把吸到的颜色给选区里的标注（一次 commit）。选区里没有能上色的对象 / 什么都没吸到 = 说一句、不进历史 */
export function applyEyedropper(sample: ColorSample): boolean {
  const ui = useUiStore.getState()
  if (!sample.fill && !sample.stroke) {
    ui.setStatus(msg('status.eyedropperNothing', undefined, 'workspace'), 'info')
    return false
  }
  const targets = selectedMarks()
  if (!targets.length) {
    ui.setStatus(msg('status.eyedropperNoTarget', undefined, 'workspace'), 'info')
    return false
  }
  const ids = new Set(targets.map((o) => o.id))
  finishActiveGesture()
  useDocumentStore.getState().commit(hist('eyedropper'), (d) => {
    for (const o of d.objects) if (ids.has(o.id)) applyColorSample(o, sample)
  })
  return true
}

export interface AppearanceCommand {
  id: 'default-appearance' | 'swap-fill-stroke' | 'clear-fill' | 'eyedropper-tool'
  key: KeyId
  available: () => boolean
  run: () => void
}

export const APPEARANCE_COMMANDS: readonly AppearanceCommand[] = [
  { id: 'default-appearance', key: 'defaultAppearance', available: () => selectedMarks().length > 0, run: resetAppearance },
  { id: 'swap-fill-stroke', key: 'swapFillStroke', available: () => selectedFillable().length > 0, run: swapAppearance },
  {
    id: 'clear-fill',
    key: 'clearFill',
    available: () => selectedFillable().some((o) => isFillStrokeMark(o) && o.fill != null),
    run: clearSelectedFill,
  },
  {
    id: 'eyedropper-tool',
    key: 'toolEyedropper',
    available: () => true,
    run: () => {
      finishActiveGesture()
      useUiStore.getState().setTool('eyedropper')
    },
  },
]
