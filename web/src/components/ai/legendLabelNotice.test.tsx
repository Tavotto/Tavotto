/**
 * 改图任务收尾的「图例里也写着同一段文字」跟进卡（`task.suggestions`，kind = legend_label）：
 * 出现 / 面板已有这条 override 就不出现 / 点「一起改」走画布 `setOverride(panelId, gid, 'label', to, true)` 且卡片随之消失。
 * 选择器一律认 `data-*`。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { t } from '@/i18n'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { agentCaps, capsOf } from '@/components/settings/testCaps'
import { setCurrentProjectId } from '@/lib/session'
import { useAiStore, type AiSession } from '@/store/aiStore'
import { useDocumentStore } from '@/store/documentStore'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { createDraftBackend } from '@/test/aiDraftBackend'
import { AssistantPanel } from './AiPanel'

// 点击写 override 的真实实现要排渲染；这里换成只把 override 落进文档的替身（调用实参由用例断言）
const setOverride = vi.hoisted(() => vi.fn())
vi.mock('@/store/actions', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/store/actions')>()),
  setOverride,
}))
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchAiHistory: vi.fn(async () => ({ sessions: [], total: 0 })),
}))

const draftBackend = createDraftBackend()
globalThis.fetch = (async (url: unknown, init?: RequestInit) =>
  draftBackend.handle(String(url), init) ?? new Response('{}', { status: 200 })) as typeof fetch
Element.prototype.scrollIntoView ??= function scrollIntoView() {}
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const GID = 'axes_0.lines_0'
const LEGEND = 'axes_0.legend.texts_0'
const SUGGESTION = { kind: 'legend_label' as const, stem: 'Fig1', gid: GID, legend_gid: LEGEND, from: 'B', to: 'B: treated (n=6)' }

const panel = (over: Partial<PanelObject> = {}): PanelObject =>
  ({
    id: 'p1', type: 'panel', x: 0, y: 0, w: 100, h: 75,
    fileId: 'Fig1.pdf', fileKind: 'pdf', nativeW: 100, nativeH: 75,
    name: 'Fig1', script: '/tmp/figs/fig1.py', overrides: [],
    ...over,
  }) as unknown as PanelObject

const T0 = 1_756_000_000_000
const session = (over: Partial<AiSession> = {}): AiSession => ({
  id: 's1',
  project: null,
  agent: 'codex',
  agentLabel: 'Codex',
  prompt: '只把 B 子图的标题改成 B: treated (n=6)',
  script: '/tmp/figs/fig1.py',
  panelId: 'p1',
  fileId: 'Fig1.pdf',
  gid: null,
  scope: 'figure',
  target: '整张图',
  entries: [],
  status: 'done',
  changed: true,
  diff: '--- fig1.py (修改前)\n+++ fig1.py (修改后)\n@@ -1,1 +1,1 @@\n-ax.set_title("B")\n+ax.set_title("B: treated (n=6)")\n',
  task: { task_type: 'figure_edit', outcome: 'completed', suggestions: [SUGGESTION] },
  startedAt: T0,
  finishedAt: T0 + 18_000,
  ...over,
})

let root: Root
let host: HTMLDivElement

/** 面板当前的结构 manifest：曲线 + 绑定它的图例项（`bound` = 图例项绑定的曲线 gid） */
function seedManifest(ps: PanelObject[], bound: string | null = GID, extra: object[] = []) {
  const elements = [
    { gid: GID, role: 'line', editable: [{ prop: 'label', value: 'B' }] },
    ...extra,
    ...(bound
      ? [{ gid: LEGEND, role: 'legend_text', legend_entry: { index: 0, source_gid: bound }, editable: [{ prop: 'text', value: 'B' }] }]
      : []),
  ]
  const byKey = Object.fromEntries(ps.map((p) => [renderKeyOf(p), { manifest: { elements } }]))
  useRenderStore.setState({ byKey, latest: {} } as never)
}

async function mount(sessions: AiSession[], objects: PanelObject[] = [panel()], bound: string | null = GID, extra: object[] = []) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_legend_label_notice')
  useDocumentStore.setState((s) => ({ doc: { ...s.doc, objects } }) as never)
  seedManifest(objects, bound, extra)
  useSelectionStore.setState({ ids: ['p1'] } as never)
  useUiStore.setState({ elementPanelId: null, selectedGids: [] } as never)
  useAiStore.setState({ sessions } as never)
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <AssistantPanel />
      </TooltipProvider>,
    )
  })
  for (let i = 0; i < 20; i++) await act(async () => Promise.resolve())
}

const q = <T extends Element = HTMLElement>(sel: string) => host.querySelector<T>(sel)
const CARD = '[data-ai-suggestion="legend_label"]'

beforeEach(() => {
  localStorage.clear()
  setCurrentProjectId('pa')
  document.body.innerHTML = ''
  setOverride.mockReset()
  setOverride.mockImplementation((panelId: string, gid: string, prop: string, value: unknown) => {
    useDocumentStore.setState((s) => ({
      doc: {
        ...s.doc,
        objects: s.doc.objects.map((o) =>
          o.id === panelId && o.type === 'panel' ? { ...o, overrides: [...o.overrides, { gid, prop, value }] } : o,
        ),
      },
    }) as never)
  })
  useAiStore.setState({ sessions: [], scope: 'figure', caps: capsOf([agentCaps()]), agent: 'codex', models: {}, efforts: {} })
})

afterEach(async () => {
  await act(async () => root?.unmount())
})

describe('图例文字跟进卡', () => {
  it('后端给了 legend_label 建议：DiffView 下面出一张卡，文案与按钮照规定写', async () => {
    await mount([session()])
    const card = q(CARD)!
    expect(card).not.toBeNull()
    expect(card.textContent).toContain(t('notice.legendLabel', { ns: 'ai', from: 'B', to: 'B: treated (n=6)' }))
    expect(card.textContent).toContain('图例里也写着「B」，要一起改成「B: treated (n=6)」吗？')
    expect(q('[data-ai-suggestion-apply]')!.textContent).toBe('一起改')
    expect(card.querySelector('[aria-label]')).toBeNull() // 没有关闭钮
    // 位置：在 DiffView 之后
    const diffRow = q('[data-diff-row]')!
    expect(diffRow.compareDocumentPosition(card) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('没有 suggestions 字段：不出卡', async () => {
    await mount([session({ task: { task_type: 'figure_edit', outcome: 'completed' } })])
    expect(q(CARD)).toBeNull()
  })

  it('该图例项已经有这条 text override（例如重新载入后）：不出卡', async () => {
    await mount([session()], [panel({ overrides: [{ gid: LEGEND, prop: 'text', value: 'B: treated (n=6)' }] as never })])
    expect(q(CARD)).toBeNull()
  })

  it('没有显示这份脚本这张图的面板：不出卡', async () => {
    await mount([session()], [panel({ script: '/tmp/figs/other.py' })])
    expect(q(CARD)).toBeNull()
  })

  it('面板 manifest 里图例项绑定的是别的曲线（或没有图例项）：不出卡', async () => {
    await mount([session()], [panel()], 'axes_0.lines_1')
    expect(q(CARD)).toBeNull()
  })

  it('后端给的 legend_gid 与面板 manifest 按 source_gid 找到的图例项不一致：不出卡', async () => {
    await mount([session({ task: { task_type: 'figure_edit', outcome: 'completed', suggestions: [{ ...SUGGESTION, legend_gid: 'axes_0.legend.texts_5' }] } })])
    expect(q(CARD)).toBeNull()
  })

  it('同一条曲线绑着多个图例项、排在前面的另有其文：按后端给的 legend_gid 取那一项，照常出卡', async () => {
    // 另一份图例里的项（文字不同）同样绑着这条曲线，在 manifest 里排在前面
    const other = { gid: 'axes_1.legend.texts_0', role: 'legend_text', legend_entry: { index: 0, source_gid: GID }, editable: [{ prop: 'text', value: '别的' }] }
    await mount([session()], [panel()], GID, [other])
    expect(q(CARD)).not.toBeNull()
    await act(async () => q<HTMLButtonElement>('[data-ai-suggestion-apply]')!.click())
    expect(setOverride).toHaveBeenCalledWith('p1', LEGEND, 'text', 'B: treated (n=6)', true)
  })

  it('同一张图在两个面板里、其中一份图例文字被用户另改过：只改仍显示原文的那份，不覆盖自定义文字', async () => {
    const custom = panel({ id: 'p2', overrides: [{ gid: LEGEND, prop: 'text', value: '我自己写的' }] as never })
    await mount([session()], [panel(), custom])
    expect(q(CARD)).not.toBeNull()
    await act(async () => q<HTMLButtonElement>('[data-ai-suggestion-apply]')!.click())
    expect(setOverride).toHaveBeenCalledTimes(1)
    expect(setOverride).toHaveBeenCalledWith('p1', LEGEND, 'text', 'B: treated (n=6)', true)
  })

  it('唯一的面板图例文字已被用户另改过：不出卡', async () => {
    await mount([session()], [panel({ overrides: [{ gid: LEGEND, prop: 'text', value: '我自己写的' }] as never })])
    expect(q(CARD)).toBeNull()
  })

  it('对曲线自己的 label override 不算已应用（图例不会因此变）', async () => {
    await mount([session()], [panel({ overrides: [{ gid: GID, prop: 'label', value: 'B: treated (n=6)' }] as never })])
    expect(q(CARD)).not.toBeNull()
  })

  it('点「一起改」：走画布 setOverride(panel, 图例项 gid, text, to, true)，卡片随之消失', async () => {
    await mount([session()])
    await act(async () => q<HTMLButtonElement>('[data-ai-suggestion-apply]')!.click())
    expect(setOverride).toHaveBeenCalledTimes(1)
    expect(setOverride).toHaveBeenCalledWith('p1', LEGEND, 'text', 'B: treated (n=6)', true)
    expect(q(CARD)).toBeNull()
  })
})
