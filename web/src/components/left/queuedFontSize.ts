import { commonTextFields } from '@/components/inspector/textStyleModel'
import { propLabel } from '@/components/inspector/roles/registry'
import { msg } from '@/i18n'
import { elementsWith } from '@/lib/stylePanelModel'
import { pagePtLens } from '@/lib/stylePresets'
import type { PanelObject } from '@/types/document'
import { setOverride, setOverrides } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { hasActiveGesture, subscribeGestureCoordinator } from '@/store/gestureCoordinator'
import { useInteractionStore } from '@/store/interactionStore'
import { exactPanelManifest, renderKeyOf, useRenderStore } from '@/store/renderStore'
import { figureScope } from '@/store/styleBinding'
import { useUiStore } from '@/store/uiStore'

interface FontQueue {
  panelId: string
  scope: string
  key: string
  values: Map<string, number>
  resolve: (() => void)[]
  stop: (() => void)[]
}

const queues = new Map<string, FontQueue>()

function finish(q: FontQueue, cancelled: boolean) {
  if (queues.get(q.panelId) !== q) return
  queues.delete(q.panelId)
  q.stop.forEach((stop) => stop())
  if (cancelled) useUiStore.getState().setStatus(msg('stylePanel.pendingSizeCancelled', undefined, 'workspace'), 'error')
  q.resolve.forEach((resolve) => resolve())
}

function processQueue(q: FontQueue) {
  if (queues.get(q.panelId) !== q) return
  const ds = useDocumentStore.getState()
  const panel = ds.doc.objects.find((o) => o.id === q.panelId)
  if (panel?.type !== 'panel' || figureScope(ds, panel) !== q.scope || renderKeyOf(panel) !== q.key) {
    finish(q, true)
    return
  }
  const rs = useRenderStore.getState()
  if (rs.byKey[q.key]?.status === 'error') {
    finish(q, true)
    return
  }
  const manifest = exactPanelManifest(rs, panel)
  if (!manifest) return
  // 不把后台到达的字号并进用户此刻正在进行的另一条手势 / 撤销事务。
  if (ds.txn || hasActiveGesture() || useInteractionStore.getState().kind !== 'none') return
  const patches: { gid: string; prop: string; value: number }[] = []
  for (const [role, pageValue] of q.values) {
    const elements = elementsWith(manifest, role, 'fontsize')
    const field = commonTextFields(elements, ['fontsize']).get('fontsize')
    const value = pagePtLens(panel).toScript('fontsize', pageValue)
    if (!field || typeof value !== 'number' || !Number.isFinite(value) ||
        (field.min != null && value < field.min) || (field.max != null && value > field.max)) {
      finish(q, true)
      return
    }
    patches.push(...elements.map((el) => ({ gid: el.gid, prop: 'fontsize', value })))
  }
  // 先撤监听，避免本次 commit 被当成外部版本变化而取消。
  queues.delete(q.panelId)
  q.stop.forEach((stop) => stop())
  const label = msg('element.batchEdit', { label: propLabel('fontsize', '') }, 'inspector')
  if (patches.length === 1) setOverride(panel.id, patches[0].gid, 'fontsize', patches[0].value, true)
  else if (patches.length) setOverrides(panel.id, label, patches, true)
  q.resolve.forEach((resolve) => resolve())
}

/** 只保留每一角色的最后字号；等当前版本精确清单到达后重新确定目标。 */
export function queueFontSize(panel: PanelObject, role: string, value: number): Promise<void> {
  const ds = useDocumentStore.getState()
  const scope = figureScope(ds, panel)
  const key = renderKeyOf(panel)
  let q = queues.get(panel.id)
  if (q && (q.scope !== scope || q.key !== key)) {
    finish(q, true)
    q = undefined
  }
  if (!q) {
    q = { panelId: panel.id, scope, key, values: new Map(), resolve: [], stop: [] }
    queues.set(panel.id, q)
    const mine = q
    const check = () => queueMicrotask(() => processQueue(mine))
    const checkDocument = () => {
      const state = useDocumentStore.getState()
      const current = state.doc.objects.find((o) => o.id === mine.panelId)
      if (current?.type !== 'panel' || figureScope(state, current) !== mine.scope || renderKeyOf(current) !== mine.key) {
        finish(mine, true)
      } else check()
    }
    q.stop.push(useDocumentStore.subscribe(checkDocument), useRenderStore.subscribe(check),
      useInteractionStore.subscribe(check), subscribeGestureCoordinator(check))
  }
  q.values.set(role, value)
  const mine = q
  const done = new Promise<void>((resolve) => mine.resolve.push(resolve))
  queueMicrotask(() => processQueue(mine))
  return done
}
