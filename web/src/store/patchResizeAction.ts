import { msg } from '@/i18n'
import type { ManifestElement } from '@/lib/api'
import type { Rect4 } from '@/lib/axesLayout'
import { patchBoundsOf, patchResizePatches } from '@/lib/patchResize'
import type { PanelObject } from '@/types/document'
import { setOverrides } from './actions'
import { useDocumentStore } from './documentStore'
import { finishActiveGesture } from './gestureCoordinator'
import { displayedExactManifest } from './mountedSvgStore'

/** 手柄与数值输入共用的校验：起手时的几何仍有效才提交。 */
export function resizePatch(
  basis: PanelObject,
  element: ManifestElement,
  next: Rect4,
): boolean {
  finishActiveGesture()
  const panel = useDocumentStore.getState().doc.objects.find((o) => o.id === basis.id)
  if (panel?.type !== 'panel' || panel.overrides !== basis.overrides) return false
  // 事件可能早于 React 重绘到达，提交时也要核对真正挂上画面的版本。
  const manifest = displayedExactManifest(panel)
  const current = manifest?.elements.find((e) => e.gid === element.gid)
  if (!current || current !== element) return false
  const start = patchBoundsOf(current)
  if (!start) return false
  const patches = patchResizePatches(current, start, next)
  if (!patches.length) return false
  setOverrides(panel.id, msg('history.resizePatch', undefined, 'workspace'), patches, true)
  return true
}
