/**
 * 「按期刊调整样式」的读与写（判据在 `lib/journalStyle`，这里只取数和落地）。
 *
 * - 读：当前画布每张图**此刻这一版**的 manifest（`exactPanelManifest`，与样式绑定同一口径——旧渲染的值
 *   不拿来判），字号 / 线宽换成页面 pt（`pagePtLens`，换算只有这一份；样式里的数字就是这个口径），加上画布标注的字号。
 * - 写：画布跟随着一套样式 → 改这套样式本身（`editBoundStyleAll`：内置的先复制，一次 commit、撤销一步）；
 *   没跟随（或已脱离）→ 新建一份只含这些改动的样式（名字是期刊名），再让画布跟随它（`bindCanvasStyle`）。
 */
import { msg } from '@/i18n'
import { journalCatalogUi } from '@/lib/journalUi'
import {
  FONT_FAMILY_ROLES,
  FONT_SIZE_SLOTS,
  LINE_WIDTH_SLOTS,
  journalStyleEdits,
  journalStyleTargets,
  type JournalStyleEdit,
} from '@/lib/journalStyle'
import { isSubLabel, pagePtLens, type StyleProfileData } from '@/lib/stylePresets'
import type { FigureDocument, PanelObject, TextObject } from '@/types/document'
import { useDocumentStore } from './documentStore'
import { useProfileStore } from './profileStore'
import { exactPanelManifest, useRenderStore } from './renderStore'
import { documentGeneration } from './styleWork'
import { bindCanvasStyle, canvasStyle, editBoundStyleAll, resolvedStyle } from './styleBinding'
import { useUiStore } from './uiStore'

const SLOTS: [string, string][] = [
  ...FONT_SIZE_SLOTS,
  ...LINE_WIDTH_SLOTS,
  ...FONT_FAMILY_ROLES.map((r): [string, string] => [r, 'fontfamily']),
]

/** 图上看到的值：role → prop → 各元素的值（数字是页面 pt） */
function observe(doc: FigureDocument): Record<string, Record<string, unknown[]>> {
  const out: Record<string, Record<string, unknown[]>> = {}
  const state = useRenderStore.getState()
  for (const o of doc.objects) {
    if (o.type !== 'panel' || o.hidden) continue
    const panel = o as PanelObject
    const m = exactPanelManifest(state, panel)
    if (!m) continue
    const lens = pagePtLens(panel)
    for (const e of m.elements) {
      for (const [role, prop] of SLOTS) {
        if (e.role !== role) continue
        const f = e.editable.find((x) => x.prop === prop)
        if (!f || f.value === null || f.value === undefined) continue
        ;((out[role] ??= {})[prop] ??= []).push(prop === 'fontfamily' ? f.value : lens.toPage(prop, f.value))
      }
    }
  }
  return out
}

/** 这张画布此刻按期刊要调的改动（没按期刊 / 都合要求 = 空） */
export function journalStylePlan(
  renderable: readonly string[],
  doc: FigureDocument = useDocumentStore.getState().doc,
): { journal: string; edits: JournalStyleEdit[] } | null {
  if (!journalCatalogUi()) return null
  const targets = journalStyleTargets(doc.profile)
  if (!targets) return null
  const binding = canvasStyle(doc)
  const edits = journalStyleEdits(
    {
      style: binding ? resolvedStyle(binding) : null,
      observed: observe(doc),
      annotationSizes: doc.objects
        .filter((o): o is TextObject => o.type === 'text' && !o.hidden && !isSubLabel(o as TextObject))
        .map((t) => t.sizePt),
      renderable,
    },
    targets,
  )
  return { journal: targets.journal, edits }
}

/** 把改动写成一份样式内容（新建那条路用） */
function styleData(edits: JournalStyleEdit[]): StyleProfileData {
  const data: StyleProfileData = { element: {}, pt_basis: 'page' }
  for (const e of edits) {
    if (e.kind === 'element') data.element[e.role] = { ...(data.element[e.role] ?? {}), [e.prop]: e.value }
    else data.annotation = { ...(data.annotation ?? {}), sizePt: e.value }
  }
  return data
}

/** 用户点了「按期刊调整」：一次可撤销的修改。返回是否落地 */
export async function adjustStyleToJournal(renderable: readonly string[]): Promise<boolean> {
  const plan = journalStylePlan(renderable)
  if (!plan || !plan.edits.length) return false
  const label = (name: string) => msg('history.journalStyle', { journal: plan.journal, name }, 'workspace')
  if (canvasStyle()) {
    const ok = await editBoundStyleAll(
      plan.edits.map((e) =>
        e.kind === 'element'
          ? { kind: 'element' as const, role: e.role, prop: e.prop, value: e.value }
          : { kind: 'annotation' as const, prop: 'sizePt' as const, value: e.value },
      ),
      label,
    )
    if (ok) useUiStore.getState().setStatus(msg('stylePanel.journalAdjusted', { journal: plan.journal }, 'workspace'), 'done')
    return ok
  }
  // 新建样式要等一次存库：期间换了画布 / 文档 / 项目，绑定会落到别的画布上——先记下发起时的代次，
  // 回来对不上就不绑，说一句（与 `editBoundStyleAll` 同一判据；Codex #1851 P1）
  const origin = documentGeneration(useDocumentStore.getState())
  const rec = await useProfileStore
    .getState()
    .create('style', plan.journal, styleData(plan.edits) as unknown as Record<string, unknown>)
  if (!rec) {
    const err = useProfileStore.getState().error
    useUiStore
      .getState()
      .setStatus(
        err
          ? msg('stylePanel.saveFailed', { reason: err.message }, 'workspace')
          : msg('stylePanel.saveFailedPlain', undefined, 'workspace'),
        'error',
      )
    return false
  }
  if (documentGeneration(useDocumentStore.getState()) !== origin) {
    useUiStore.getState().setStatus(msg('stylePanel.bindCancelled', undefined, 'workspace'), 'error')
    return false
  }
  bindCanvasStyle(rec.id)
  useUiStore.getState().setStatus(msg('stylePanel.journalAdjusted', { journal: plan.journal }, 'workspace'), 'done')
  return true
}
