/**
 * 打开 / 新建项目之后按文档页面适配一次视口（审计 T03）。
 *
 * 改造前只有 `CanvasStage` 挂载时的那一次「首次拿到尺寸后自动适应页面」，
 * 它挂在组件的 `fittedRef` 上、一辈子只跑一次。顶栏的项目切换器
 * （`ProjectSwitcher`）**不经过 `phase: 'none'`**，工作台整个不卸载，于是
 * 新项目沿用上一个项目留下的缩放——审计里 175% 那一屏就是这么来的。
 *
 * 现在这一次适配挂在「项目就位」这件事上，与舞台挂没挂载无关：量得到视口
 * 就当场做，量不到就挂起（`viewportStore` 的 pendingFit），舞台第一次上报
 * 尺寸时补上。
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { setCurrentProjectId } from '@/lib/session'
import { BASE_PX_PER_MM } from '@/lib/units'
import { emptyProject, type ProjectDocument } from '@/types/document'
import { useDocumentStore } from './documentStore'
import { useProjectStore } from './projectStore'
import { useViewportStore } from './viewportStore'

const PROJECT_IDS: Record<string, string> = { '/figs/a': 'p_a', '/figs/b': 'p_b' }
globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
  const u = String(url)
  if (u.includes('/api/projects/recent')) return new Response('{"recent":[]}', { status: 200 })
  if (u.includes('/api/projects/open')) {
    const path = (JSON.parse(String(init?.body)) as { path: string }).path
    return new Response(
      JSON.stringify({ open: true, id: PROJECT_IDS[path], figures_dir: path, name: path.slice(6) }),
      { status: 200 },
    )
  }
  if (u.includes('/api/panels')) {
    return new Response('{"figures_dir":"/figs","panels":[]}', { status: 200 })
  }
  return new Response('{}', { status: 200 })
}) as typeof fetch

const VIEW = { left: 0, top: 0, width: 800, height: 600 }

/** 与 `viewportStore.fit` 逐位同一个算法：判据不许抄一个近似值 */
function expectedZoom(pageW: number, pageH: number, padding = 72) {
  const wPx = pageW * BASE_PX_PER_MM
  const hPx = pageH * BASE_PX_PER_MM
  return Math.min((VIEW.width - padding) / wPx, (VIEW.height - padding) / hPx)
}

const docWithPage = (w: number, h: number): ProjectDocument => {
  const pd = emptyProject()
  pd.canvases[0].page = { w, h }
  return pd
}

beforeEach(() => {
  localStorage.clear()
  setCurrentProjectId('p_a')
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0 })
  useViewportStore.getState().setViewRect(VIEW)
  useProjectStore.setState({ phase: 'open', project: null, recent: [], opened: [] })
})

afterEach(() => {
  setCurrentProjectId(null)
})

describe('打开项目后的视口', () => {
  it('工作台没卸载时也适配一次：新项目不沿用上一个项目的缩放', async () => {
    // 上一个项目里用户放大到 175%（顶栏切换项目不会卸载舞台）
    useViewportStore.getState().zoomAt(1.75, 0, 0)
    expect(useViewportStore.getState().zoom).toBeCloseTo(1.75, 6)

    await useProjectStore.getState().open('/figs/b')
    expect(useViewportStore.getState().zoom).toBeCloseTo(expectedZoom(150, 100), 6)
  })

  it('适配的是**这份文档**的页面，不是上一份的', async () => {
    await useDocumentStore.getState().switchDocument(docWithPage(40, 30), 'd_small')
    await useProjectStore.getState().adoptOpenedProject(
      { open: true, id: 'p_b', figures_dir: '/figs/b' },
      {
        prepareDocument: async () => {
          await useDocumentStore.getState().switchDocument(docWithPage(200, 160), 'd_big')
        },
      },
    )
    expect(useViewportStore.getState().zoom).toBeCloseTo(expectedZoom(200, 160), 6)
  })

  it('舞台还没挂载（Project Picker → 工作台）：等它第一次量到尺寸再适配', async () => {
    useViewportStore.getState().setViewRect({ left: 0, top: 0, width: 0, height: 0 })
    await useProjectStore.getState().open('/figs/b')
    // 量不到视口时一个字段都不动
    expect(useViewportStore.getState().viewW).toBe(0)

    useViewportStore.getState().setViewRect(VIEW)
    expect(useViewportStore.getState().zoom).toBeCloseTo(expectedZoom(150, 100), 6)
  })
})
