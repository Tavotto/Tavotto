/**
 * 元素树的重画预算（2026-09-24 松手卡顿剖析）。
 *
 * 用户拖完图内元素松手，前端在「松手那一刻」与「新图到达那一刻」各有一次整树提交；
 * 58 个元素的图上元素树每次 4 ms 左右，全花在把每一行重画一遍上——行收的是整个
 * `panel`（每次 commit 新引用）、回调每次新建、行没有 memo。这里钉两件事：
 *
 * 1. 行只在**自己显示的东西**变了时重画（A1–A3）；
 * 2. 新图到达（HTTP 响应入库 + SSE `render.done` 两路）时元素树只提交一次（A4）
 *    ——第二次来自 `prune` 在同步 effect 里清掉掉出近期档的旧变体，而同步器的渲染态
 *    订阅曾经挂在 Workspace 上，一变整棵树跟着重画。
 *
 * **主语**：「这一行重画了」= 这一行的 ElementRow 函数体这一轮执行了。观测点是 `<li>`
 * 上 React 记的 props 对象（`__reactProps$…`）：行函数体执行一次就产出一个新的 `<li>`
 * props 对象，memo 挡下来时 `<li>` 连同它的 props 原样不动。前提写在这里：行没有自己的
 * state，语言不变时它的 `useTranslation` 也不会单独触发重画——所以 props 对象换没换
 * 与「函数体执行没有」一一对应。依赖的是 React 内部键名，读不到时 `propsOf` 直接抛，
 * 不会静默量成「零次重画」。
 */
import { act, Profiler } from 'react'
import ts from 'typescript'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { literal, t } from '@/i18n'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { EngineRenderSync, useEngineDocumentSync } from '@/hooks/useEngineSync'
import { handleServerEvent } from '@/hooks/useServerEvents'
import type { Manifest } from '@/lib/api'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { seedExactRender } from '@/test/renderFixtures'
import { emptyProject, type CanvasObject, type PanelObject } from '@/types/document'
import { ElementTree } from './ElementTree'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
Element.prototype.scrollIntoView ??= function scrollIntoView() {}

const panel: PanelObject = {
  id: 'p1',
  type: 'panel',
  fileId: 'Fig1.pdf',
  fileKind: 'pdf',
  nativeW: 80,
  nativeH: 60,
  overrides: [],
  x: 0,
  y: 0,
  w: 80,
  h: 60,
  script: 'fig1.py',
} as PanelObject

const el = (gid: string, role: string, label: string) => ({
  gid,
  role,
  label,
  bbox: [0.1, 0.1, 0.2, 0.2],
  draggable: true,
  editable: [
    { prop: 'visible', type: 'bool', value: true },
    { prop: 'fontsize', type: 'number', value: 7 },
  ],
})

/** 子图 7 个直属元素 → 有聚类层；刻度组默认收起，下面挂一个刻度文字 */
const manifest = (): Manifest =>
  ({
    stem: 'Fig1',
    size_mm: [80, 60],
    elements: [
      el('figure', 'figure', '整张图'),
      el('axes_0', 'axes', '子图 1'),
      el('axes_0.title', 'title', '标题'),
      el('axes_0.xlabel', 'axis_label', 'X 轴标题'),
      el('axes_0.ylabel', 'axis_label', 'Y 轴标题'),
      el('axes_0.yticks', 'ticks', 'Y 刻度'),
      el('axes_0.yticks.label_3', 'ticklabel', '刻度文字 0.75'),
      el('axes_0.lines_0', 'line', '曲线 1'),
      el('axes_0.lines_1', 'line', '曲线 2'),
      el('axes_0.lines_2', 'line', '曲线 3'),
    ],
  }) as unknown as Manifest

const cur = () => useDocumentStore.getState().doc.objects[0] as PanelObject

let host: HTMLDivElement
let root: Root

async function mount(node: React.ReactNode) {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(<TooltipProvider>{node}</TooltipProvider>)
  })
  await act(async () => {})
}

const rowEls = () => [...host.querySelectorAll<HTMLElement>('li[data-el]')]

function propsOf(li: HTMLElement): object {
  const k = Object.keys(li).find((x) => x.startsWith('__reactProps$'))
  if (!k) throw new Error('读不到 <li> 上 React 记的 props：观测点失效，别把它量成「零次重画」')
  return (li as unknown as Record<string, object>)[k]
}

/** 此刻每一行的 props 对象；`changedSince` 回答「哪些**原有的**行重画了」 */
function snapshot(): Map<string, object> {
  return new Map(rowEls().map((li) => [li.dataset.el!, propsOf(li)]))
}
function changedSince(before: Map<string, object>): string[] {
  return rowEls()
    .filter((li) => before.has(li.dataset.el!) && before.get(li.dataset.el!) !== propsOf(li))
    .map((li) => li.dataset.el!)
}
const row = (gid: string) => host.querySelector<HTMLElement>(`li[data-el="${gid}"]`)!

async function commit(recipe: (p: PanelObject) => void) {
  await act(async () => {
    useDocumentStore.getState().commit(literal('用例'), (d) => {
      recipe(d.objects[0] as PanelObject)
    })
  })
  await act(async () => {})
}

beforeEach(async () => {
  document.body.innerHTML = ''
  localStorage.clear()
  useRenderStore.getState().clear()
  useUiStore.setState({ leftTab: 'elements', selectedGids: [] })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_eltree_rerender')
  useDocumentStore.getState().commit(literal('准备'), (d) => {
    d.objects = [structuredClone(panel) as CanvasObject]
  })
  useAssetStore.setState({ byId: { 'Fig1.pdf': { id: 'Fig1.pdf', mtime: 1 } } } as never)
  seedExactRender(cur(), manifest())
  useSelectionStore.getState().set(['p1'])
  useUiStore.getState().setElementPanel('p1')
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  vi.unstubAllGlobals()
})

describe('观测点本身是活的', () => {
  it('行真的重画时 props 对象会换（否则下面的「零次」都是恒真）', async () => {
    await mount(<ElementTree />)
    const before = snapshot()
    expect(before.size).toBeGreaterThan(5)
    // 选中一行：那一行的 selected 变了，必须被量成「重画了」
    await act(async () => useUiStore.getState().setSelectedGid('axes_0.title'))
    expect(changedSince(before)).toContain('axes_0.title')
  })
})

describe('A1 与树无关的提交：一行都不重画', () => {
  it('标题的 pos_frac override 提交之后，行重画数 = 0', async () => {
    await mount(<ElementTree />)
    const before = snapshot()
    await commit((p) => {
      p.overrides.push({ gid: 'axes_0.title', prop: 'pos_frac', value: [0.5, 1.05] } as never)
    })
    // 前提：这次提交确实换了 panel 引用（否则「没重画」什么都没证明）
    expect(cur().overrides).toHaveLength(1)
    expect(changedSince(before)).toEqual([])
  })
})

describe('A2 新 manifest 到达', () => {
  it('元素全是新对象、显示内容不变：行重画数 = 0', async () => {
    await mount(<ElementTree />)
    const before = snapshot()
    const next = manifest()
    await act(async () => seedExactRender(cur(), next))
    // 前提：树拿到的确实是新 manifest（元素对象是新引用）
    expect(useRenderStore.getState().byKey[renderKeyOf(cur())].manifest).toBe(next)
    expect(changedSince(before)).toEqual([])
  })

  it('只有一个元素的 label 变了：只有那一行重画，并显示新名字', async () => {
    await mount(<ElementTree />)
    const before = snapshot()
    const next = manifest()
    next.elements.find((e) => e.gid === 'axes_0.lines_1')!.label = '曲线 二'
    await act(async () => seedExactRender(cur(), next))
    expect(changedSince(before)).toEqual(['axes_0.lines_1'])
    expect(row('axes_0.lines_1').textContent).toContain('曲线 二')
  })

  it('label 不变、行上显示的名字变了（刻度文字的 text 字段）：只有那一行重画', async () => {
    // 刻度文字的行名取 editable 里的 text，不取 label——改了坐标范围之后刻度值会变而
    // 引擎给的 label 可能没跟着变；比较时只看 label 就会漏掉它
    await act(async () => useUiStore.getState().setSelectedGid('axes_0.yticks.label_3'))
    await mount(<ElementTree />)
    const before = snapshot()
    const next = manifest()
    const tick = next.elements.find((e) => e.gid === 'axes_0.yticks.label_3')!
    tick.editable = [...tick.editable, { prop: 'text', type: 'text', value: '0.80' } as never]
    await act(async () => seedExactRender(cur(), next))
    expect(changedSince(before)).toEqual(['axes_0.yticks.label_3'])
    expect(row('axes_0.yticks.label_3').textContent).toContain('0.80')
  })

  it('反过来：行名不变、label 变了（可达名与 title 用它）：只有那一行重画', async () => {
    const withText = (m: Manifest, label: string) => {
      const tick = m.elements.find((e) => e.gid === 'axes_0.yticks.label_3')!
      tick.label = label
      tick.editable = [...tick.editable, { prop: 'text', type: 'text', value: '0.75' } as never]
      return m
    }
    await act(async () => {
      seedExactRender(cur(), withText(manifest(), '刻度文字 0.75'))
      useUiStore.getState().setSelectedGid('axes_0.yticks.label_3')
    })
    await mount(<ElementTree />)
    const before = snapshot()
    await act(async () => seedExactRender(cur(), withText(manifest(), '刻度文字 0.750')))
    expect(changedSince(before)).toEqual(['axes_0.yticks.label_3'])
    expect(row('axes_0.yticks.label_3').getAttribute('aria-label')).toContain('0.750')
  })
})

describe('A3 一行自己的状态变了：那一行重画并显示新状态', () => {
  it('selected：新选中的行（连同交出 Tab 停靠点的那一行）重画，别的不动', async () => {
    await mount(<ElementTree />)
    const firstTabbable = rowEls().find((li) => li.tabIndex === 0)!.dataset.el!
    const before = snapshot()
    await act(async () => useUiStore.getState().setSelectedGid('axes_0.lines_2'))
    await act(async () => {})
    expect(changedSince(before).sort()).toEqual(['axes_0.lines_2', firstTabbable].sort())
    expect(row('axes_0.lines_2').getAttribute('aria-selected')).toBe('true')
    expect(row('axes_0.lines_2').tabIndex).toBe(0)
  })

  it('selected 单独变（Tab 停靠点不动）：多选里取消一个非主选，只有那一行重画', async () => {
    // 上一条里 selected 与 tabbable 一起变，比较函数漏比 selected 也照样重画——
    // 这里挑一个只有 selected 变的时刻（主选 = 末位，没动）
    await act(async () => {
      useUiStore.getState().setSelectedGid('axes_0.lines_0')
      useUiStore.getState().toggleSelectedGid('axes_0.lines_2')
    })
    await mount(<ElementTree />)
    expect(row('axes_0.lines_0').getAttribute('aria-selected')).toBe('true')
    expect(row('axes_0.lines_2').tabIndex).toBe(0)
    const before = snapshot()
    await act(async () => useUiStore.getState().toggleSelectedGid('axes_0.lines_0'))
    await act(async () => {})
    expect(useUiStore.getState().selectedGids).toEqual(['axes_0.lines_2'])
    expect(changedSince(before)).toEqual(['axes_0.lines_0'])
    expect(row('axes_0.lines_0').getAttribute('aria-selected')).toBe('false')
  })

  it('tabbable 单独变：主选换到新加的一行，交出 Tab 停靠点的那一行（仍选中）也重画', async () => {
    await act(async () => {
      useUiStore.getState().setSelectedGid('axes_0.lines_0')
      useUiStore.getState().toggleSelectedGid('axes_0.lines_2')
    })
    await mount(<ElementTree />)
    expect(row('axes_0.lines_2').tabIndex).toBe(0)
    const before = snapshot()
    await act(async () => useUiStore.getState().toggleSelectedGid('axes_0.lines_1'))
    await act(async () => {})
    expect(changedSince(before).sort()).toEqual(['axes_0.lines_1', 'axes_0.lines_2'])
    expect(row('axes_0.lines_2').getAttribute('aria-selected')).toBe('true')
    expect(row('axes_0.lines_2').tabIndex).toBe(-1)
    expect(row('axes_0.lines_1').tabIndex).toBe(0)
  })

  it('hidden（visible=false 的 override）：那一行重画、出现隐藏标记', async () => {
    await mount(<ElementTree />)
    const hiddenMark = t('elementTree.hiddenState', { ns: 'workspace' })
    expect(row('axes_0.lines_0').querySelector(`[aria-label="${hiddenMark}"]`)).toBeNull()
    const before = snapshot()
    await commit((p) => {
      p.overrides.push({ gid: 'axes_0.lines_0', prop: 'visible', value: false } as never)
    })
    expect(changedSince(before)).toEqual(['axes_0.lines_0'])
    expect(row('axes_0.lines_0').querySelector(`[aria-label="${hiddenMark}"]`)).not.toBeNull()
    expect(row('axes_0.lines_0').getAttribute('aria-label')).toContain(
      t('elementTree.rowAriaHidden', { ns: 'workspace' }),
    )
  })

  it('locked：那一行重画、出现锁定标记', async () => {
    await mount(<ElementTree />)
    const lockedMark = t('elementTree.lockedState', { ns: 'workspace' })
    expect(row('axes_0.ylabel').querySelector(`[aria-label="${lockedMark}"]`)).toBeNull()
    const before = snapshot()
    await commit((p) => {
      p.lockedGids = ['axes_0.ylabel']
    })
    expect(changedSince(before)).toEqual(['axes_0.ylabel'])
    expect(row('axes_0.ylabel').querySelector(`[aria-label="${lockedMark}"]`)).not.toBeNull()
  })

  it('expanded：展开刻度组，只有那一行重画，子行出现', async () => {
    await mount(<ElementTree />)
    expect(row('axes_0.yticks').getAttribute('aria-expanded')).toBe('false')
    expect(row('axes_0.yticks.label_3')).toBeNull()
    const before = snapshot()
    await act(async () => {
      row('axes_0.yticks').dispatchEvent(
        new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }),
      )
    })
    expect(changedSince(before)).toEqual(['axes_0.yticks'])
    expect(row('axes_0.yticks').getAttribute('aria-expanded')).toBe('true')
    expect(row('axes_0.yticks.label_3')).not.toBeNull()
  })

  it('聚类行的展开也一样：只有那一行重画', async () => {
    await mount(<ElementTree />)
    const cluster = rowEls().find((li) => li.dataset.el!.endsWith('#series'))!
    const key = cluster.dataset.el!
    expect(cluster.getAttribute('aria-expanded')).toBe('true')
    const before = snapshot()
    await act(async () => {
      cluster.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }))
    })
    expect(changedSince(before)).toEqual([key])
    expect(row(key).getAttribute('aria-expanded')).toBe('false')
    expect(row('axes_0.lines_0')).toBeNull()
  })
})

describe('A4 新图到达：元素树只提交一次', () => {
  /**
   * 与 App 的 Workspace 同一个形状：文档一侧的同步挂在宿主上，渲染态一侧挂在叶子
   * `<EngineRenderSync />` 上，元素树是宿主的孩子。宿主一旦订阅了渲染态，下面这棵
   * 树就会跟着每一次渲染态变化重画——这正是要钉住的东西。
   */
  function Host({ onTreeCommit }: { onTreeCommit: () => void }) {
    useEngineDocumentSync()
    return (
      <>
        <Profiler id="tree" onRender={onTreeCommit}>
          <ElementTree />
        </Profiler>
        <EngineRenderSync />
      </>
    )
  }

  it('HTTP 响应入库 + SSE render.done + prune 清旧变体 → 元素树提交 1 次、行重画 0', async () => {
    // 近期档塞满（RECENT_VARIANTS = 4）：新变体入库时挤掉最老那档，同步 effect 里的
    // prune 就会真的清掉一条——这是现场第二次重提交的来源，不塞满就量不到它
    const base = cur()
    const stale = [1, 2, 3].map((i) => ({
      ...base,
      overrides: [{ gid: 'axes_0.title', prop: 'fontsize', value: 7 + i }],
    })) as PanelObject[]
    for (const p of stale) seedExactRender(p, manifest())
    seedExactRender(base, manifest())
    // 近期档的顺序 = 渲染成功的先后（新的在前）：当前那版最新，stale[0] 最老
    useRenderStore.setState((s) => ({
      latest: { ...s.latest, 'Fig1.pdf': renderKeyOf(base) },
      recent: {
        ...s.recent,
        'Fig1.pdf': [base, stale[2], stale[1], stale[0]].map((p) => renderKeyOf(p)),
      },
    }))
    const oldest = renderKeyOf(stale[0])
    expect(useRenderStore.getState().recent['Fig1.pdf']).toHaveLength(4)

    let resolve!: (r: Response) => void
    const fetchMock = vi.fn(
      (url: string) =>
        new Promise<Response>((r) => {
          if (String(url).includes('/api/engine/render')) resolve = r
          else r(new Response('{}', { status: 200 }))
        }),
    )
    vi.stubGlobal('fetch', fetchMock)

    let commits = 0
    await mount(<Host onTreeCommit={() => commits++} />)

    // 松手：一条与树无关的 override → 同步器立刻发渲染（在途）
    await commit((p) => {
      p.overrides.push({ gid: 'axes_0.title', prop: 'pos_frac', value: [0.5, 1.05] } as never)
    })
    const key = renderKeyOf(cur())
    expect(useRenderStore.getState().byKey[key]?.status).toBe('rendering')
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes('/api/engine/render'))).toBe(true)

    const before = snapshot()
    commits = 0
    // 两路分属不同的任务：先 SSE，再 HTTP 响应
    await act(async () => handleServerEvent({ kind: 'render.done', id: 'Fig1.pdf' } as never))
    await act(async () => {
      resolve(
        new Response(
          JSON.stringify({ rev: 2, manifest: manifest(), svg: '<svg xmlns="http://www.w3.org/2000/svg"/>' }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      )
    })
    await act(async () => {})
    await act(async () => {})

    // 前提：新图确实入库了，prune 也确实清掉了掉出近期档的那一条
    const rs = useRenderStore.getState()
    expect(rs.byKey[key]?.status).toBe('ready')
    expect(rs.latest['Fig1.pdf']).toBe(key)
    expect(oldest in rs.byKey).toBe(false)

    expect(commits).toBe(1)
    expect(changedSince(before)).toEqual([])
  })
})

/**
 * 上面的宿主是 Workspace 的**同构替身**：真的 Workspace 挂着整个应用，jsdom 里挂不起来。
 * 替身绿不等于 Workspace 真是那个形状——有人把 `App.tsx` 改回 `useEngineSync()`（渲染态
 * 订阅回到 Workspace 上），A4 照样绿。这里按 AST 钉住 Workspace 本身：函数体里不调
 * `useEngineSync` / `useRenderStore`（宿主订阅渲染态 = 新图到达一次整树重画），调
 * `useEngineDocumentSync`，JSX 里挂着 `<EngineRenderSync />`。
 */
describe('A4 的前提：App 的 Workspace 就是替身那个形状', () => {
  const src = Object.values(
    import.meta.glob('/src/App.tsx', { query: '?raw', import: 'default', eager: true }),
  )[0] as string

  function workspaceFacts() {
    const sf = ts.createSourceFile('App.tsx', src, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
    const fn = sf.statements.find(
      (s): s is ts.FunctionDeclaration => ts.isFunctionDeclaration(s) && s.name?.text === 'Workspace',
    )
    const calls = new Set<string>()
    const tags = new Set<string>()
    const walk = (n: ts.Node) => {
      if (ts.isCallExpression(n) && ts.isIdentifier(n.expression)) calls.add(n.expression.text)
      if (ts.isJsxSelfClosingElement(n) || ts.isJsxOpeningElement(n)) tags.add(n.tagName.getText(sf))
      ts.forEachChild(n, walk)
    }
    if (fn?.body) walk(fn.body)
    return { found: !!fn?.body, calls, tags }
  }

  it('读到了 Workspace（判据的前提）', () => {
    const f = workspaceFacts()
    expect(f.found).toBe(true)
    expect(f.calls.has('useServerEvents')).toBe(true)
  })

  it('Workspace 只挂文档一侧，渲染态一侧在叶子 <EngineRenderSync /> 上', () => {
    const f = workspaceFacts()
    expect(f.calls.has('useEngineDocumentSync')).toBe(true)
    expect(f.tags.has('EngineRenderSync')).toBe(true)
    expect(f.calls.has('useEngineSync')).toBe(false)
    expect(f.calls.has('useRenderStore')).toBe(false)
  })
})
