/**
 * 起步那一屏（审计 T03）。
 *
 * 改造前四段提示各配一颗按钮——空画布、空素材、未选中对象、未选中可编辑图，
 * 每一段都在教用户一件事，而新用户此刻只需要知道**先做哪一件**。
 *
 * 这里守三件事：
 *   1. 画布中央只有**一个**主行动（「添加图」），次级入口画成文字链接而不是
 *      第二颗填色按钮（UI 视觉纪律：每个上下文最多一个填色主动作）；
 *   2. 「试用示例」走 `runTutorialEntry` 的统一入口——教程项目里自己不再出现；
 *   3. 项目里一张图都没有时，说明改成告诉用户**把什么文件放进项目目录**，
 *      因为「添加图」在那个状态下没有东西可添。素材清单还没回来时按「有」说。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { CanvasStage } from '@/canvas/CanvasStage'
import { CARD_W, EmptyPickCards, pickColumns, pickRows } from '@/canvas/EmptyPickCards'
import { LayerTree } from '@/components/left/LayerTree'
import { RestoreIssueBody } from '@/components/RestoreIssueBody'
import type { RestoreIssue } from '@/store/documentRestore'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { t } from '@/i18n'
import type { PanelInfo } from '@/lib/api'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useProjectStore } from '@/store/projectStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject } from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch

/** jsdom 没有 ResizeObserver；CanvasStage 的视口上报靠它 */
class NoopResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver = NoopResizeObserver as unknown as typeof ResizeObserver

vi.mock('@/lib/onboarding/tutorial', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  runTutorialEntry: vi.fn(async () => ({ ok: true }) as const),
}))
const { runTutorialEntry } = await import('@/lib/onboarding/tutorial')

const sg = (key: string) => t(`stage.${key}`, { ns: 'workspace' })

const asset = (id: string): PanelInfo => ({
  id,
  name: id,
  folder: '.',
  kind: 'pdf',
  native_w_mm: 80,
  native_h_mm: 60,
  mtime: 1,
})

let root: Root

async function mount(node: React.ReactNode) {
  const el = document.createElement('div')
  document.body.appendChild(el)
  root = createRoot(el)
  await act(async () => {
    root.render(<TooltipProvider>{node}</TooltipProvider>)
  })
}

const byText = (text: string) =>
  [...document.querySelectorAll('button')].filter((b) => b.textContent?.trim() === text)

/** 画成按钮的那些（`Button` 的次按钮是灰底胶囊、主按钮黑底——静态就有底色；次级入口是裸文字链接，
 *  只有 hover 时才有底，所以按「不带前缀的底色类」判，`hover:bg-surface-hover` 不算） */
const chromedButtons = () =>
  [...document.querySelectorAll('button')].filter((b) => /(^|\s)(bg-surface-hover|bg-ink)(\s|$)/.test(b.className))

beforeEach(async () => {
  document.body.innerHTML = ''
  vi.clearAllMocks()
  useAssetStore.setState({ panels: [], byId: {}, loaded: false })
  useProjectStore.setState({ project: { open: true, id: 'p1', figures_dir: '/figs' } })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_empty_start')
  useSelectionStore.getState().clear()
  useUiStore.setState({ leftTab: 'assets', tool: 'select', cropTargetId: null })
})

afterEach(async () => {
  await act(async () => root?.unmount())
})

describe('空画布的起步提示', () => {
  it('只有一个主行动「添加图」，「试用示例」是次级链接不是第二颗按钮', async () => {
    await mount(<CanvasStage />)
    expect(byText(sg('addFigure'))).toHaveLength(1)
    expect(byText(sg('tryTutorial'))).toHaveLength(1)
    // 一屏最多一个填色主动作：次级入口不许也是填色按钮
    // 一屏只有一个「行动」的样子：主行动是按钮，次级只是一条文字链接
    expect(chromedButtons().map((b) => b.textContent?.trim())).toEqual([sg('addFigure')])
  })

  it('「添加图」打开素材库；「试用示例」走教程的统一入口', async () => {
    useUiStore.setState({ leftTab: 'outline' })
    await mount(<CanvasStage />)
    await act(async () => byText(sg('addFigure'))[0].click())
    expect(useUiStore.getState().leftTab).toBe('assets')

    await act(async () => byText(sg('tryTutorial'))[0].click())
    expect(runTutorialEntry).toHaveBeenCalledWith('canvas')
  })

  it('教程项目里不再出现「试用示例」', async () => {
    useProjectStore.setState({
      project: { open: true, id: 'p1', figures_dir: '/figs', tutorial: true },
    })
    await mount(<CanvasStage />)
    expect(byText(sg('addFigure'))).toHaveLength(1)
    expect(byText(sg('tryTutorial'))).toHaveLength(0)
  })

  it('项目里一张图都没有：说明改成「把文件放进项目目录」', async () => {
    useAssetStore.setState({ panels: [], byId: {}, loaded: true })
    await mount(<CanvasStage />)
    expect(document.body.textContent).toContain(sg('emptyHintNoAssets'))
    expect(document.body.textContent).not.toContain(sg('emptyHint'))
  })

  it('素材清单还没回来时先按「有图」说，不许先吓唬用户', async () => {
    useAssetStore.setState({ panels: [], byId: {}, loaded: false })
    await mount(<CanvasStage />)
    expect(document.body.textContent).toContain(sg('emptyHint'))
    expect(document.body.textContent).not.toContain(sg('emptyHintNoAssets'))
  })

  it('有图时直接摆选图卡（最多 4 张），点一下放上画布；没有「添加图」按钮', async () => {
    const mk = (id: string, status?: 'editable' | 'layout_only'): PanelInfo => ({
      ...asset(id),
      ...(status
        ? {
            capability: {
              status,
              reason_code: status === 'editable' ? 'registered_source' : 'no_source_candidate',
              script: null,
              candidates: [],
              can_probe: false,
              can_manual_link: false,
            },
          }
        : {}),
    })
    const list = ['a.pdf', 'b.pdf', 'c.pdf', 'd.pdf', 'e.pdf'].map((id, i) => mk(id, i === 1 ? 'layout_only' : 'editable'))
    useAssetStore.setState({ panels: list, byId: Object.fromEntries(list.map((p) => [p.id, p])), loaded: true })
    await mount(<CanvasStage />)
    // 面板整块落在纸面内：摆几张按纸面大小定（jsdom 里纸面小，只摆得下前两张），绝不超过 4
    expect(document.querySelectorAll('[data-pick-card]').length).toBeGreaterThanOrEqual(2)
    expect(document.querySelectorAll('[data-pick-card]').length).toBeLessThanOrEqual(4)
    expect(byText(sg('addFigure'))).toHaveLength(0)
    expect(document.body.textContent).toContain(sg('pickTitle'))
    // 只有不可改图内的卡打状态标签；悬停在它上面时，下方说明那一行换成「只能整体移动、缩放、裁剪」（卡不加行）
    expect(document.querySelectorAll('[data-card-status]')).toHaveLength(1)
    expect(document.body.textContent).not.toContain(sg('pickLayoutOnly'))
    await act(async () => {
      document.querySelector('[data-pick-card="b.pdf"]')!.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }))
    })
    expect(document.querySelector('[data-empty-pick-note]')!.textContent).toBe(sg('pickLayoutOnly'))
    expect(document.querySelectorAll('[data-pick-el]')).toHaveLength(0)
  })
})

describe('选图卡的版面', () => {
  it('纸面够宽 4 张一排，窄了两列再一列；纸面矮就只摆放得下的行', () => {
    expect(pickColumns(700, 4)).toBe(4)
    expect(pickColumns(480, 4)).toBe(2)
    expect(pickColumns(860, 3)).toBe(3)
    expect(pickColumns(400, 3)).toBe(2)
    expect(pickColumns(200, 4)).toBe(1)
    expect(pickRows(317)).toBe(1)
    expect(pickRows(600)).toBe(3)
  })

  it('每张卡的包装宽度就是版面计算用的卡宽，长文件名不把卡撑开', async () => {
    const long = `${'a-very-long-figure-file-name-'.repeat(4)}.pdf`
    const list = [asset(long), asset('b.pdf')]
    await mount(<EmptyPickCards panels={list} paperW={700} paperH={600} />)
    const slots = [...document.querySelectorAll<HTMLElement>('[data-pick-slot]')]
    expect(slots).toHaveLength(2)
    for (const el of slots) expect(el.style.width).toBe(`${CARD_W}px`)
  })
})

describe('空画布提示坐在什么底上（宪法第二十八节）', () => {
  const hintBox = () => document.querySelector<HTMLElement>('[data-canvas-empty-hint] > div')!

  it('默认白纸：字直接落在纸上，用纸上的墨（data-on-paper），不垫底', async () => {
    await mount(<CanvasStage />)
    expect(hintBox().hasAttribute('data-on-paper')).toBe(true)
    expect(hintBox().className).not.toContain('bg-surface')
  })

  it('用户把页面背景设成深色：纸上的墨压不住，退回主题面板底 + 界面墨', async () => {
    act(() => useDocumentStore.setState((st) => ({ doc: { ...st.doc, page: { ...st.doc.page, bg: '#1f2430' } } })))
    await mount(<CanvasStage />)
    expect(hintBox().hasAttribute('data-on-paper')).toBe(false)
    expect(hintBox().className).toContain('bg-surface')
  })
})

describe('侧栏的空态', () => {
  it('图层树只留轻量占位：起步的那颗按钮只在画布中央出现一次', async () => {
    await mount(<LayerTree />)
    expect(document.body.textContent).toContain(t('layerTree.emptyTitle', { ns: 'workspace' }))
    expect(document.querySelectorAll('button')).toHaveLength(0)
  })
})

/**
 * 上次的排版没能恢复（ADR 0120 修订 2026-11-10）：这份空白是不许写的占位——画布正中摆的是恢复的出口
 * （一句话 + 一个主按钮），不是「添加图」/ 选图卡（点了也放不进去）。主语：`data-canvas-restore-issue` 那一块。
 */
describe('恢复没成时的空画布', () => {
  const needsUser = (kind: RestoreIssue['kind']) =>
    act(async () =>
      useDocumentStore.setState({
        restoreGate: { state: 'needs_user', gen: 7, pj: 'p1', docId: null, reason: 'failed' },
        restoreIssue: { pj: 'p1', gen: 7, kind, ref: { id: 'd_x', name: 'Fig X' }, alternatives: [] },
      }),
    )
  afterEach(() =>
    useDocumentStore.setState({
      restoreGate: { state: 'ready', gen: 8, pj: 'p1', docId: 'd_empty_start', reason: 'explicit' },
      restoreIssue: null,
    }),
  )

  it('画布正中是恢复卡（一个主按钮），不摆「添加图」与选图卡', async () => {
    useAssetStore.setState({ panels: [asset('a.pdf')], byId: {}, loaded: true })
    await mount(<CanvasStage />)
    expect(document.querySelector('[data-canvas-empty-hint]')).not.toBeNull()
    await needsUser('missing')
    const card = document.querySelector<HTMLElement>('[data-canvas-restore-issue]')
    expect(card).not.toBeNull()
    expect(document.querySelector('[data-canvas-empty-hint]')).toBeNull()
    const actions = [...card!.querySelectorAll<HTMLElement>('[data-restore-action]')]
    expect(actions.map((a) => a.dataset.restoreAction)).toEqual(['continue-new'])
    expect(actions[0].dataset.variant).toBe('primary')
    expect(card!.dataset.restoreHeld).toBeUndefined()
  })

  it('选着画图工具点卡片、选区里留着东西：卡片都还在（Codex #1839）', async () => {
    await mount(<CanvasStage />)
    await needsUser('missing')
    await act(async () => useUiStore.getState().setTool('rect'))
    const btn = document.querySelector<HTMLElement>('[data-canvas-restore-issue] [data-restore-action]')!
    await act(async () => {
      btn.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, button: 0, clientX: 10, clientY: 10 }))
      btn.dispatchEvent(new MouseEvent('pointerup', { bubbles: true, button: 0, clientX: 10, clientY: 10 }))
    })
    expect(useSelectionStore.getState().ids).toEqual([])
    await act(async () => useSelectionStore.getState().set(['ghost']))
    expect(document.querySelector('[data-canvas-restore-issue] [data-restore-action]')).not.toBeNull()
    await act(async () => useUiStore.getState().setTool('select'))
  })

  it('暂时读不到且有动作在等：「继续新建」从 ⋯ 里拿出来摆在「重试」旁边；没有动作在等时仍折着', async () => {
    const issue: RestoreIssue = { pj: 'p1', gen: 7, kind: 'unavailable', ref: { id: 'd_x', name: 'Fig X' }, alternatives: [] }
    const shown = () =>
      [...document.querySelectorAll<HTMLElement>('[data-restore-action]')].map((a) => a.dataset.restoreAction)
    await mount(<RestoreIssueBody issue={issue} close={() => {}} />)
    expect(shown()).toEqual(['retry'])
    await act(async () => root.unmount())
    await mount(<RestoreIssueBody issue={issue} close={() => {}} held />)
    expect(shown()).toEqual(['retry', 'continue-new'])
    expect(document.querySelector('[data-restore-action="retry"]')!.getAttribute('data-variant')).toBe('primary')
    expect(document.querySelector('[data-restore-action="continue-new"]')!.getAttribute('data-variant')).not.toBe('primary')
  })
})
