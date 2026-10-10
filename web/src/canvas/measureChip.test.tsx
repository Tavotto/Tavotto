/**
 * 贴着选区的尺寸芯片（2026-10-07 设计审计 §10.1）。此前读数在画布左下角，移动时报的是**指针**坐标；
 * 现在贴在被改的那个框下面，说的是那个框：
 *   - 移动 → 对象的 X, Y（不是指针）；
 *   - 缩放 / 画框 → W × H；
 *   - 方向键微调 → Δ；
 *   - 没有交互 / 没有可报的框 → 不渲染。
 * 主语：`data-measure-chip` 的取值与它的文字；位置按视口变换算（zoom 1、pan 0 时 1 mm = mmToWorld(1) px）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { MeasureChip } from './MeasureChip'
import { nudgeKeyDown, nudgeKeyUp, resetNudge } from './nudge'
import { MATPLOTLIB_SVG } from '@/lib/__fixtures__/matplotlibSvg'
import { literal, setLocale } from '@/i18n'
import type { Manifest } from '@/lib/api'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { renderKeyOf, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useWorkspaceStore } from '@/store/workspace'
import { mmToWorld, TOOLBAR_FIT_CLEARANCE, useViewportStore } from '@/store/viewportStore'
import { emptyProject, type PanelObject, type ShapeObject } from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let container: HTMLDivElement
let root: Root

const rect = (): ShapeObject => ({
  id: 'r1', type: 'shape', shape: 'rect', x: 10, y: 20, w: 100, h: 8,
  strokePt: 1, color: '#111111', fill: null,
})

const chip = () => container.querySelector<HTMLElement>('[data-measure-chip]')

beforeEach(async () => {
  useInteractionStore.getState().end()
  useInteractionStore.getState().setNudge(null)
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_chip_measure')
  useDocumentStore.getState().commit(literal('加'), (d) => {
    d.objects.push(rect())
  })
  useSelectionStore.getState().set(['r1'])
  useViewportStore.setState({ zoom: 1, panX: 0, panY: 0, viewW: 800, viewH: 600, fitBottomClear: 0 })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  act(() => root.render(<MeasureChip />))
})

afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
  useInteractionStore.getState().end()
  useInteractionStore.getState().setNudge(null)
  useUiStore.setState({ elementPanelId: null, selectedGids: [] })
  useRenderStore.getState().clear()
  await setLocale('zh-CN')
})

describe('MeasureChip', () => {
  it('没有交互：不渲染', () => {
    expect(chip()).toBeNull()
  })

  it('移动：报对象的 X, Y（不是指针坐标），贴在框下方居中', () => {
    act(() => {
      useInteractionStore.getState().begin('move')
      useInteractionStore.getState().setCursor({ x: 99, y: 99 })
    })
    expect(chip()!.dataset.measureChip).toBe('position')
    expect(chip()!.textContent).toBe('10.0, 20.0 mm')
    expect(chip()!.style.left).toBe(`${mmToWorld(10) + mmToWorld(100) / 2}px`)
    expect(chip()!.style.top).toBe(`${mmToWorld(28) + 8}px`)
  })

  it('缩放：W × H', () => {
    act(() => useInteractionStore.getState().begin('resize'))
    expect(chip()!.dataset.measureChip).toBe('size')
    expect(chip()!.textContent).toBe('100.0 × 8.0 mm')
  })

  // Codex #833：旋转 90° 的 100×8 横条转出来是竖条（中心 60, 24：x 56–64、y −26–74）；读数仍是逻辑的 W × H，
  // 芯片贴在看得见的外接框下面——按逻辑盒（底边 y=28）摆会压在竖条中段
  it('旋转对象：读数是逻辑 W × H，位置按看得见的外接框（visualBounds）', () => {
    act(() => {
      useDocumentStore.getState().commit(literal('转'), (d) => {
        ;(d.objects[0] as ShapeObject).rotationDeg = 90
      })
      useViewportStore.setState({ viewH: 2000 })
      useInteractionStore.getState().begin('resize')
    })
    expect(chip()!.textContent).toBe('100.0 × 8.0 mm')
    expect(chip()!.style.left).toBe(`${mmToWorld(56) + mmToWorld(8) / 2}px`)
    expect(chip()!.style.top).toBe(`${mmToWorld(74) + 8}px`)
  })

  // Codex #833：快速编辑里舞台只画那一张图；从图层抽屉 ⇧ 选进来的别的对象看不见，读数与锚点都不算它
  it('快速编辑：只量舞台上画着的那一张，选区里看不见的对象不并进来', () => {
    const before = useWorkspaceStore.getState()
    try {
      act(() => {
        useDocumentStore.getState().commit(literal('加'), (d) => {
          d.objects.push({ ...rect(), id: 'r2', x: 0, y: 0, w: 300, h: 200 })
        })
        useSelectionStore.getState().set(['r1', 'r2'])
        useWorkspaceStore.setState({ mode: 'fast_edit', activePanelId: 'r1' })
        useInteractionStore.getState().begin('resize')
      })
      expect(chip()!.textContent).toBe('100.0 × 8.0 mm')
      expect(chip()!.style.left).toBe(`${mmToWorld(10) + mmToWorld(100) / 2}px`)
    } finally {
      useWorkspaceStore.setState({ mode: before.mode, activePanelId: before.activePanelId })
    }
  })

  it('方向键微调：Δ 带正负号', () => {
    act(() => useInteractionStore.getState().setNudge({ dx: 0.5, dy: -1 }))
    expect(chip()!.dataset.measureChip).toBe('offset')
    expect(chip()!.textContent).toBe('Δ +0.5, −1.0 mm')
  })

  it('框贴到视口底：芯片翻到框上面', () => {
    act(() => {
      useViewportStore.setState({ viewH: mmToWorld(28) + 10 })
      useInteractionStore.getState().begin('resize')
    })
    expect(parseFloat(chip()!.style.top)).toBeLessThan(mmToWorld(20))
  })

  // Codex #833：选区几乎占满视口（y 2–102 mm，视口只比它高一点）——下面放不下、翻上去落到负坐标被舞台裁掉。
  // 夹回舞台里：上沿不出界，下沿也不出界
  it('选区占满视口：上下都放不下，芯片夹在舞台里', () => {
    const viewH = mmToWorld(102) + 10
    act(() => {
      useDocumentStore.getState().commit(literal('拉高'), (d) => {
        Object.assign(d.objects[0], { y: 2, h: 100 })
      })
      useViewportStore.setState({ viewH })
      useInteractionStore.getState().begin('resize')
    })
    const top = parseFloat(chip()!.style.top)
    expect(top).toBeGreaterThanOrEqual(0)
    expect(top + 22).toBeLessThanOrEqual(viewH)
  })

  // Codex #833：底部浮动工具条（层级高于芯片）显示时占着舞台底部 TOOLBAR_FIT_CLEARANCE。框底（28 mm）离视口底
  // 只剩 70 px：不算工具条放得下（8 + 22），算上就放不下——芯片翻到框上面，不钻到工具条底下
  describe('底部浮动工具条', () => {
    const viewH = mmToWorld(28) + 70
    it('显示时：框底落进工具条那一带，芯片翻到框上面', () => {
      act(() => {
        useViewportStore.setState({ viewH, fitBottomClear: TOOLBAR_FIT_CLEARANCE })
        useInteractionStore.getState().begin('resize')
      })
      const top = parseFloat(chip()!.style.top)
      expect(top).toBe(mmToWorld(20) - 8 - 22)
      expect(top + 22).toBeLessThanOrEqual(viewH - TOOLBAR_FIT_CLEARANCE)
    })

    it('对照：工具条不在（快速编辑 / 隐藏时为 0），芯片照旧贴在框下面', () => {
      act(() => {
        useViewportStore.setState({ viewH, fitBottomClear: 0 })
        useInteractionStore.getState().begin('resize')
      })
      expect(parseFloat(chip()!.style.top)).toBe(mmToWorld(28) + 8)
    })

    // 框的上沿就在工具条那一带（离视口底 40 px），下面放不下、翻上去的芯片下沿仍压在工具条上：夹到工具条之上
    it('框上沿落进工具条那一带：翻上去之后再夹到工具条之上', () => {
      act(() => {
        useDocumentStore.getState().commit(literal('挪低'), (d) => {
          Object.assign(d.objects[0], { y: (600 - 40) / mmToWorld(1) })
        })
        useViewportStore.setState({ viewH: 600, fitBottomClear: TOOLBAR_FIT_CLEARANCE })
        useInteractionStore.getState().begin('resize')
      })
      expect(parseFloat(chip()!.style.top)).toBeCloseTo(600 - TOOLBAR_FIT_CLEARANCE - 22, 6)
    })

    it('选区占满视口、上下都放不下：夹取也让开工具条', () => {
      const tall = mmToWorld(102) + 10
      act(() => {
        useDocumentStore.getState().commit(literal('拉高'), (d) => {
          Object.assign(d.objects[0], { y: 2, h: 100 })
        })
        useViewportStore.setState({ viewH: tall, fitBottomClear: TOOLBAR_FIT_CLEARANCE })
        useInteractionStore.getState().begin('resize')
      })
      const top = parseFloat(chip()!.style.top)
      expect(top).toBeGreaterThanOrEqual(0)
      expect(top + 22).toBeLessThanOrEqual(tall - TOOLBAR_FIT_CLEARANCE)
    })
  })

  // 横向同理：框中心平移到视口左外 / 右外，芯片（桩宽 80px，按中心定位）整条留在舞台里
  it.each([
    ['左外', -mmToWorld(200)],
    ['右外', mmToWorld(200)],
  ])('框中心在视口%s：芯片横向夹在舞台里', (_side, panX) => {
    const desc = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetWidth')!
    Object.defineProperty(HTMLElement.prototype, 'offsetWidth', {
      configurable: true,
      get(this: HTMLElement) {
        return this.hasAttribute('data-measure-chip') ? 80 : 0
      },
    })
    try {
      act(() => {
        useViewportStore.setState({ panX })
        useInteractionStore.getState().begin('move')
      })
      const left = parseFloat(chip()!.style.left)
      expect(left - 40).toBeGreaterThanOrEqual(0)
      expect(left + 40).toBeLessThanOrEqual(800)
    } finally {
      Object.defineProperty(HTMLElement.prototype, 'offsetWidth', desc)
    }
  })

  // Codex #833：图内编辑态里选中标题按方向键，`nudge.ts` 推的是图内元素（只动预览平面），画布选区不变——
  // 芯片要贴在**标题**下面，不是整块面板下面（面板没选中时也不能消失）。
  // 面板 20,30 起、页面 100 × 80 mm；标题 bbox [0.3, 0.05, 0.2, 0.03] → 页面 x 50–70、y 34–36.4；Δ +2 → x 52–72。
  // 位置与 Δ 取的是 `InFigureMove.preview` 发布的那份（标题走 `gidDrag`，子图走 `elementPreview`），与 ElementBoxes 同源
  describe('图内元素微调', () => {
    const title = {
      gid: 'axes_0.title', role: 'title', label: '标题', bbox: [0.3, 0.05, 0.2, 0.03] as [number, number, number, number],
      editable: [], draggable: true, anchor: [0.3, 0.08] as [number, number], drag_prop: 'pos_frac',
    }
    const axes = {
      gid: 'axes_0', role: 'axes', label: '子图 1', bbox: [0.1, 0.1, 0.6, 0.6] as [number, number, number, number],
      editable: [{ prop: 'position', type: 'rect', value: [0.1, 0.3, 0.6, 0.6] }], draggable: false, resizable: true,
    }
    const manifest = {
      stem: 'Fig1', size_mm: [200, 160],
      elements: [
        { gid: 'figure', role: 'figure', label: '整图', bbox: [0, 0, 1, 1], editable: [], draggable: false },
        axes,
        title,
      ],
    } as unknown as Manifest
    const panel = {
      id: 'p1', type: 'panel', x: 20, y: 30, w: 100, h: 80, fileId: 'Fig1.pdf', fileKind: 'pdf',
      nativeW: 200, nativeH: 160, script: 'fig.py', overrides: [],
    } as unknown as PanelObject

    beforeEach(() => {
      useDocumentStore.getState().commit(literal('加图'), (d) => {
        d.objects.push(panel)
      })
      useRenderStore.getState().clear()
      useUiStore.setState({ elementPanelId: 'p1', selectedGids: [title.gid] })
    })

    afterEach(() => {
      resetNudge()
      document.querySelector('[data-element-svg]')?.remove()
    })

    const seedRender = () => {
      const p = useDocumentStore.getState().doc.objects.find((o) => o.id === 'p1') as PanelObject
      useRenderStore.getState().patch(renderKeyOf(p), {
        fileId: 'Fig1.pdf', manifest, svg: '<svg/>', rev: 1, status: 'ready', lastPatches: JSON.stringify(p.overrides),
      })
      useRenderStore.setState({ latest: { 'Fig1.pdf': renderKeyOf(p) } })
    }

    it.each([
      ['面板也在画布选区里', ['p1']],
      ['画布选区里没有面板', []],
    ])('%s：芯片贴在被推的标题下面（权威框 + Δ），读数仍是 Δ', (_name, ids) => {
      act(() => {
        seedRender()
        useSelectionStore.getState().set(ids)
        useInteractionStore.getState().setNudge({ dx: 2, dy: 0 })
        useInteractionStore.getState().setGidDrag({ gid: title.gid, dfx: 0.02, dfy: 0 })
      })
      expect(chip()!.dataset.measureChip).toBe('offset')
      expect(chip()!.textContent).toBe('Δ +2.0, 0.0 mm')
      expect(parseFloat(chip()!.style.left)).toBeCloseTo(mmToWorld(62), 6)
      expect(parseFloat(chip()!.style.top)).toBeCloseTo(mmToWorld(36.4) + 8, 6)
    })

    // 面板转 90°（版上 80 × 100，中心 60, 80；内容 100 × 80 落在 x 10–110、y 40–120）：标题内容框中心 50, 45.2
    // 绕面板中心顺时针转 90° → 中心 94.8, 70、宽高互换成 2.4 × 20（y 60–80）；Δ (0, +1) → 底边 81
    it('面板旋转 90°：元素框随面板朝向转到页面上', () => {
      act(() => {
        useDocumentStore.getState().commit(literal('转'), (d) => {
          Object.assign(d.objects.find((o) => o.id === 'p1')!, { rotation: 90, w: 80, h: 100 })
        })
        seedRender()
        useViewportStore.setState({ viewH: 2000 })
        useInteractionStore.getState().setNudge({ dx: 0, dy: 1 })
        // 页面向下 1 mm = 内容里向右 1 mm（内容宽 100 mm）
        useInteractionStore.getState().setGidDrag({ gid: title.gid, dfx: 0.01, dfy: 0 })
      })
      expect(chip()!.textContent).toBe('Δ 0.0, +1.0 mm')
      expect(parseFloat(chip()!.style.left)).toBeCloseTo(mmToWorld(94.8), 6)
      expect(parseFloat(chip()!.style.top)).toBeCloseTo(mmToWorld(81) + 8, 6)
    })

    // #832 评审：翻转过的面板，画布按 `panelContentTransform` 镜像画这张图（先翻转、再旋转，绕面板中心 70, 70）。
    // 芯片得贴在元素**看得见的**镜像位置，Δ 报页面上看得见的方向：
    //   - flipH：标题内容 x 52–72（Δ 内容 +2）中心 62 → 镜像到 78；页面上是往左挪了 2；
    //   - flipV：标题内容 y 35–37.4（Δ 内容 +1）中心 36.2 → 镜像到 103.8、底边 105；页面上是往上挪了 1
    it.each([
      ['flipH', { flipH: true }, { dfx: 0.02, dfy: 0 }, 78, 36.4, 'Δ −2.0, 0.0 mm'],
      ['flipV', { flipV: true }, { dfx: 0, dfy: 0.0125 }, 60, 105, 'Δ 0.0, −1.0 mm'],
      // 先翻转、再旋转（顺序反了中心会落到 25.2）：版上 80 × 100、中心 60, 80；标题内容中心偏移 (−8, −34.8)
      // → 翻转 (8, −34.8) → 转 90° (34.8, 8) → 中心 94.8, 88、2.4 × 20、底边 98；Δ 内容 (+2, 0) → (−2, 0) → (0, −2)
      ['flipH + 90°', { flipH: true, rotation: 90, w: 80, h: 100 }, { dfx: 0.02, dfy: 0 }, 94.8, 98, 'Δ 0.0, −2.0 mm'],
    ])('%s 面板：芯片贴在镜像后的元素下面，Δ 是页面上看得见的方向', (_n, flip, drag, cxMm, bottomMm, text) => {
      act(() => {
        useDocumentStore.getState().commit(literal('翻'), (d) => {
          Object.assign(d.objects.find((o) => o.id === 'p1')!, flip)
        })
        seedRender()
        useViewportStore.setState({ viewH: 2000 })
        useInteractionStore.getState().setNudge({ dx: drag.dfx * 100, dy: drag.dfy * 80 })
        useInteractionStore.getState().setGidDrag({ gid: title.gid, ...drag })
      })
      expect(chip()!.textContent).toBe(text)
      expect(parseFloat(chip()!.style.left)).toBeCloseTo(mmToWorld(cxMm), 6)
      expect(parseFloat(chip()!.style.top)).toBeCloseTo(mmToWorld(bottomMm) + 8, 6)
    })

    // Codex #833：子图 x 0.1、宽 0.6（页面 x 30–90、y 38–86）往左推 30 下 × 0.5 mm = 15 mm，可左边只有 10 mm：
    // `axesMove` 把框钳在 x = 0，后面的按键子图不动——芯片也得停在那儿，Δ 报 −10，不是按键总和 −15。
    // 走真的 `nudge.ts` → `axesMove.preview`，芯片读的正是它发布的那份 elementPreview
    it('子图推到图边被钳住：芯片位置与 Δ 停在钳住的地方，不按按键总和漂走', () => {
      const host = document.createElement('div')
      host.dataset.elementSvg = 'p1'
      host.innerHTML = MATPLOTLIB_SVG
      document.body.appendChild(host)
      act(() => {
        seedRender()
        useUiStore.setState({ elementPanelId: 'p1', selectedGids: [axes.gid] })
        useViewportStore.setState({ viewH: 2000 })
      })
      act(() => {
        for (let i = 0; i < 30; i++) {
          nudgeKeyDown(new KeyboardEvent('keydown', { key: 'ArrowLeft' }))
          nudgeKeyUp(new KeyboardEvent('keyup', { key: 'ArrowLeft' }))
        }
      })
      expect(useInteractionStore.getState().nudge).toEqual({ dx: -15, dy: 0 })
      expect(chip()!.textContent).toBe('Δ −10.0, 0.0 mm')
      expect(parseFloat(chip()!.style.left)).toBeCloseTo(mmToWorld(50), 6)
      expect(parseFloat(chip()!.style.top)).toBeCloseTo(mmToWorld(86) + 8, 6)
    })

    // Codex #833：多选（子图 + 标题）走 `groupMove`，标题是主选（末位）且此前被挪过——文档里它的 `pos_frac`
    // 是 0.35，与这一版 manifest 的锚点 0.3 差 0.05（页面 5 mm）。`groupMove` 的起手框是 `alignEntries` 按锚点
    // 修正过的框（`elementBoxOf`），芯片的 Δ 必须从同一个框量起：推 4 × 0.5 mm 报 +2.0，不能把那 5 mm 旧位移算进来
    it('成组微调、主选是挪过的标题：Δ 从 groupMove 同一个起手框量起，不含旧 override', () => {
      const host = document.createElement('div')
      host.dataset.elementSvg = 'p1'
      host.innerHTML = MATPLOTLIB_SVG
      document.body.appendChild(host)
      act(() => {
        useDocumentStore.getState().commit(literal('挪标题'), (d) => {
          const p = d.objects.find((o) => o.id === 'p1') as PanelObject
          p.overrides = [{ gid: title.gid, prop: 'pos_frac', value: [0.35, 0.08] }]
        })
        seedRender()
        useUiStore.setState({ elementPanelId: 'p1', selectedGids: [axes.gid, title.gid] })
        useViewportStore.setState({ viewH: 2000 })
      })
      act(() => {
        for (let i = 0; i < 4; i++) {
          nudgeKeyDown(new KeyboardEvent('keydown', { key: 'ArrowRight' }))
          nudgeKeyUp(new KeyboardEvent('keyup', { key: 'ArrowRight' }))
        }
      })
      expect(useInteractionStore.getState().nudge).toEqual({ dx: 2, dy: 0 })
      expect(useInteractionStore.getState().elementPreview?.boxes[title.gid]).toBeDefined()
      expect(chip()!.textContent).toBe('Δ +2.0, 0.0 mm')
    })

    it('几何权威缺席（上一段刚提交、渲染没回来）：不报，不拿面板的框顶替', () => {
      act(() => {
        useSelectionStore.getState().set(['p1'])
        useInteractionStore.getState().setNudge({ dx: 2, dy: 0 })
      })
      expect(chip()).toBeNull()
    })

    it('对照：没在图内编辑态，微调推的是画布选区，芯片仍贴在选区下面', () => {
      act(() => {
        seedRender()
        useUiStore.setState({ elementPanelId: null, selectedGids: [] })
        useSelectionStore.getState().set(['r1'])
        useInteractionStore.getState().setNudge({ dx: 0.5, dy: 0 })
      })
      expect(chip()!.style.left).toBe(`${mmToWorld(10) + mmToWorld(100) / 2}px`)
      expect(chip()!.style.top).toBe(`${mmToWorld(28) + 8}px`)
    })
  })
})
