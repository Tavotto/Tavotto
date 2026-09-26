/**
 * coachmark 层（ADR 0040）：只在教程进行中出现；欢迎页居中且「开始」是真动作；
 * 锚点在 → 贴着锚点、画高亮环；锚点缺 → 等一会儿再说「找不到」并给返回 / 跳过；
 * 前置缺 → 说清缺什么 + 一颗真实行动按钮，**不「等待」**（审计 T36）；
 * Esc 暂停；锚点在对话框里 → portal 进对话框；reduced motion 下不带位移过渡。
 */
import { act, Profiler } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DURATION } from '@/lib/motion'
import { REAL_STEP_IDS, STEP_IDS } from '@/lib/onboarding/stepIds'
import { useTutorialStore } from '@/lib/onboarding/tutorial'
import type { TutorialMetadata } from '@/lib/api'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { configureOnboardingPersistence, useOnboardingStore } from '@/store/onboardingStore'
import { useProjectStore } from '@/store/projectStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { OnboardingLayer, WAIT_MS } from './OnboardingLayer'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const META: TutorialMetadata = {
  schema: 1,
  tutorial_version: 1,
  project_name: 'Tutorial',
  document_name: 'Tutorial',
  document_id: 'tavotto-tutorial',
  expected_stems: ['Fig2_correlation'],
  editable_role_preferences: ['title'],
  panels: [
    {
      key: 'second',
      file: 'Fig2_correlation.pdf',
      stem: 'Fig2_correlation',
      script: 'fig2_correlation.py',
      editable_roles: ['title', 'text'],
      spec_issue: { code: 'font-below-absolute-floor', role: 'text', text_prefix: 'n = 60' },
    },
  ],
}

/** 两张图的元数据：第一张不在文档里（add_to_layout 的「还缺一张」用例） */
const META2: TutorialMetadata = {
  ...META,
  expected_stems: ['Fig1_kinetics', 'Fig2_correlation'],
  panels: [
    {
      key: 'first',
      file: 'Fig1_kinetics.pdf',
      stem: 'Fig1_kinetics',
      script: 'fig1_kinetics.py',
      editable_roles: ['title', 'line'],
      spec_issue: null,
    },
    ...META.panels,
  ],
}

let container: HTMLDivElement
let root: Root
let reduced = false

const ob = () => useOnboardingStore.getState()
const card = () => document.querySelector<HTMLElement>('[data-onboarding-coachmark]')
// 计时器是假的：flush 要推假时钟，不能等真的 setTimeout
const flush = async () => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0)
  })
}

/** jsdom 里所有盒子都是 0×0：给锚点一个真实矩形 */
function giveRect(el: Element, r: { x: number; y: number; w: number; h: number }) {
  Object.defineProperty(el, 'getBoundingClientRect', {
    value: () => ({ left: r.x, top: r.y, width: r.w, height: r.h, right: r.x + r.w, bottom: r.y + r.h, x: r.x, y: r.y }),
    configurable: true,
  })
}

beforeEach(async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] })
  reduced = false
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: (q: string) => ({ matches: q.includes('reduce') && reduced, addEventListener() {}, removeEventListener() {} }),
  })
  configureOnboardingPersistence(null)
  ob().resetOnboarding()
  useTutorialStore.setState({ meta: META })
  useProjectStore.setState({ phase: 'open', project: { open: true, id: 'p_tut', tutorial: true } })
  useUiStore.setState({ elementPanelId: null, selectedGids: [], exportOpen: false, leftOpen: false, layout: 'wide' })
  const pd = emptyProject()
  const p2: PanelObject = {
    id: 'p2',
    type: 'panel',
    fileId: 'Fig2_correlation.pdf',
    fileKind: 'pdf',
    nativeW: 73,
    nativeH: 58,
    x: 95,
    y: 18,
    w: 73,
    h: 58,
    script: 'fig2_correlation.py',
    overrides: [],
  }
  pd.canvases[0].objects = [p2]
  await useDocumentStore.getState().switchDocument(pd, META.document_id)
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
  document.querySelectorAll('[data-object-id], [data-card], [role="dialog"]').forEach((n) => n.remove())
  vi.useRealTimers()
})

const mount = async () => {
  await act(async () => {
    root.render(<OnboardingLayer />)
  })
  await flush()
}

describe('出现与欢迎页', () => {
  it('不在教程里什么都不画；开始后欢迎页居中、有「开始」、没有遮罩', async () => {
    await mount()
    expect(card()).toBeNull()
    await act(async () => {
      ob().start({ projectId: 'p_tut', documentId: META.document_id })
    })
    await flush()
    const c = card()!
    expect(c).not.toBeNull()
    expect(c.getAttribute('role')).toBe('dialog')
    expect(c.getAttribute('aria-modal')).toBe('false')
    expect(c.dataset.side).toBe('center')
    expect(document.querySelector('[data-onboarding-mask]')).toBeNull()
    expect(document.querySelector('[data-onboarding-ring]')).toBeNull()
    // 标题 / 正文经 aria 关联；读屏区常驻
    expect(document.getElementById(c.getAttribute('aria-labelledby')!)?.textContent).toBe('用示例了解 Tavotto')
    expect(document.querySelector('[aria-live="polite"]')?.textContent).toContain('用示例了解 Tavotto')
    // 「开始」是真动作：完成 welcome、进入第一步
    await act(async () => {
      c.querySelector<HTMLButtonElement>('[data-onboarding-primary]')!.click()
    })
    expect(ob().completedSteps).toEqual(['welcome'])
    expect(ob().currentStep).toBe('open_fast_edit')
  })

  it('关闭键与 Esc 都是暂停，不是完成', async () => {
    await mount()
    await act(async () => {
      ob().start({ projectId: 'p_tut', documentId: META.document_id })
    })
    await flush()
    await act(async () => {
      card()!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    })
    expect(ob().status).toBe('paused')
    expect(ob().pausedBy).toBe('user')
    expect(card()).toBeNull()
    await act(async () => {
      ob().resume()
    })
    await flush()
    await act(async () => {
      card()!.querySelector<HTMLButtonElement>('button[aria-label="暂停教程"]')!.click()
    })
    expect(ob().status).toBe('paused')
  })
})

describe('锚点', () => {
  it('锚点在：卡片贴在锚点下方、画高亮环、有进度与返回 / 跳过', async () => {
    const anchor = document.createElement('div')
    anchor.setAttribute('data-object-id', 'p2')
    document.body.appendChild(anchor)
    giveRect(anchor, { x: 200, y: 100, w: 80, h: 40 })
    await mount()
    await act(async () => {
      ob().start({ projectId: 'p_tut', documentId: META.document_id })
      ob().markStep('welcome')
      ob().goTo('open_fast_edit')
    })
    await flush()
    const c = card()!
    expect(c.dataset.side).toBe('bottom')
    expect(c.style.position).toBe('fixed')
    expect(parseFloat(c.style.top)).toBe(100 + 40 + 10)
    expect(parseFloat(c.style.left)).toBe(200)
    const ring = document.querySelector<HTMLElement>('[data-onboarding-ring]')!
    expect(ring.style.left).toBe('196px')
    expect(ring.style.width).toBe('88px')
    expect(c.querySelector('[data-onboarding-progress]')?.textContent).toBe(`第 1 步，共 ${STEP_IDS.length - 2} 步`)
    expect(c.querySelector('[data-onboarding-back]')).not.toBeNull()
    expect(c.querySelector('[data-onboarding-skip]')).not.toBeNull()
    // 跳过此步 = 当完成处理，前进一步
    await act(async () => {
      c.querySelector<HTMLButtonElement>('[data-onboarding-skip]')!.click()
    })
    expect(ob().currentStep).toBe('select_text')
    anchor.remove()
  })

  it('前置满足、锚点缺：先等，超时后说「找不到」，界面没有被锁', async () => {
    // open_fast_edit 没有前置；素材抽屉关着时锚点是画布上那张图，jsdom 里没有它
    await mount()
    await act(async () => {
      ob().start({ projectId: 'p_tut', documentId: META.document_id })
      ob().markStep('welcome')
      ob().goTo('open_fast_edit')
    })
    await flush()
    const c = card()!
    expect(c.textContent).toContain('正在等待目标出现')
    expect(c.querySelector('[data-onboarding-precondition]')).toBeNull()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(WAIT_MS + 400)
    })
    await flush()
    expect(card()!.textContent).toContain('找不到这一步的目标')
    // 返回真的回到上一步
    await act(async () => {
      card()!.querySelector<HTMLButtonElement>('[data-onboarding-back]')!.click()
    })
    expect(ob().currentStep).toBe('welcome')
  })

  it('前置缺（跳过第 1 步到了「选一个文字」）：不「等待」，说清要先进图内编辑，按钮就是打开那张图', async () => {
    await mount()
    await act(async () => {
      ob().start({ projectId: 'p_tut', documentId: META.document_id })
      ob().markStep('welcome')
      ob().markStep('open_fast_edit', 'skipped')
      ob().goTo('select_text')
    })
    await flush()
    const c = card()!
    // 标题仍是这一步的标题；正文**只**说缺什么——这一步自己的指令（「点击图里的
    // 标题」）在前置满足之前不出现，用户不会同时收到两条互相矛盾的指示（审计 A06 / A07）；
    // 等多久都不出现「等待」
    expect(c.textContent).toContain('选中文字')
    expect(c.textContent).not.toContain('点击图里的标题')
    const note = c.querySelector<HTMLElement>('[data-onboarding-precondition]')!
    expect(note.dataset.onboardingPrecondition).toBe('notInElementEdit')
    expect(note.textContent).toContain('要在 Fig2_correlation 的图内编辑里进行')
    expect(c.textContent).not.toContain('正在等待目标出现')
    await act(async () => {
      await vi.advanceTimersByTimeAsync(WAIT_MS * 4)
    })
    await flush()
    expect(card()!.textContent).not.toContain('正在等待目标出现')
    expect(card()!.textContent).not.toContain('找不到这一步的目标')
    // 跳过仍然可用；主按钮是真实动作
    expect(card()!.querySelector('[data-onboarding-skip]')).not.toBeNull()
    const primary = card()!.querySelector<HTMLButtonElement>('[data-onboarding-primary]')!
    expect(primary.textContent).toBe('打开 Fig2_correlation')
    await act(async () => {
      primary.click()
    })
    await flush()
    // 进了 Fig2 的图内编辑：前置满足，状态行换成找锚点（jsdom 里没有 SVG → 等待）
    expect(useUiStore.getState().elementPanelId).toBe('p2')
    expect(card()!.querySelector('[data-onboarding-precondition]')).toBeNull()
    expect(card()!.textContent).toContain('正在等待目标出现')
    // 等待计时从前置满足那一刻起算：不会因为前面那段时间已经过了就立刻说「找不到」
    await act(async () => {
      await vi.advanceTimersByTimeAsync(WAIT_MS / 2)
    })
    await flush()
    expect(card()!.textContent).toContain('正在等待目标出现')
    await act(async () => {
      await vi.advanceTimersByTimeAsync(WAIT_MS)
    })
    await flush()
    expect(card()!.textContent).toContain('找不到这一步的目标')
  })

  it('add_to_layout 只剩一张图：说「还缺 Fig1_kinetics」、指着它的素材卡、按钮把它加进画布', async () => {
    useTutorialStore.setState({ meta: META2 })
    useAssetStore.setState({
      byId: {
        'Fig1_kinetics.pdf': {
          id: 'Fig1_kinetics.pdf',
          name: 'Fig1_kinetics',
          folder: '.',
          kind: 'pdf',
          native_w_mm: 75,
          native_h_mm: 58,
          mtime: 1,
          script: 'fig1_kinetics.py',
        },
      },
      panels: [],
      loaded: true,
    })
    const cardEl = document.createElement('div')
    cardEl.setAttribute('data-card', 'Fig1_kinetics.pdf')
    document.body.appendChild(cardEl)
    giveRect(cardEl, { x: 40, y: 200, w: 120, h: 90 })
    await mount()
    await act(async () => {
      ob().start({ projectId: 'p_tut', documentId: META.document_id })
      ob().markStep('welcome')
      ob().goTo('add_to_layout')
    })
    await flush()
    const c = card()!
    expect(c.textContent).toContain('还缺 Fig1_kinetics')
    expect(c.textContent).not.toContain('两张图都已经在画布上')
    // 抽屉关着 → 步骤的 reveal 把素材页露出来 → 锚点是那张卡
    expect(useUiStore.getState().leftOpen && useUiStore.getState().leftTab === 'assets').toBe(true)
    expect(c.dataset.side).toBe('bottom')
    expect(parseFloat(c.style.left)).toBe(40)
    const primary = c.querySelector<HTMLButtonElement>('[data-onboarding-primary]')!
    expect(primary.textContent).toBe('加入 Fig1_kinetics')
    await act(async () => {
      primary.click()
    })
    await flush()
    const objects = useDocumentStore.getState().doc.objects
    expect(objects.filter((o) => o.type === 'panel').map((o) => (o as PanelObject).fileId).sort()).toEqual([
      'Fig1_kinetics.pdf',
      'Fig2_correlation.pdf',
    ])
    // 两张都在了：回到原来那句，多余的按钮消失
    expect(card()!.textContent).toContain('两张图都已经在画布上')
    expect(card()!.querySelector('[data-onboarding-primary]')).toBeNull()
    cardEl.remove()
  })

  it('结束页按账说话：全跳过不用完成式；部分跳过说「完成 n 步，跳过 m 步」', async () => {
    await mount()
    await act(async () => {
      ob().start({ projectId: 'p_tut', documentId: META.document_id })
      ob().markStep('welcome')
      for (const id of REAL_STEP_IDS) ob().markStep(id, 'skipped')
      ob().goTo('done')
    })
    await flush()
    let c = card()!
    expect(c.textContent).toContain('教程已结束')
    expect(c.textContent).toContain(`你跳过了全部 ${REAL_STEP_IDS.length} 步`)
    expect(c.textContent).not.toContain('你已经走过')
    // 两颗出口按钮照旧
    expect(c.querySelector('[data-onboarding-primary]')?.textContent).toBe('继续探索')
    expect(c.querySelector('[data-onboarding-secondary]')?.textContent).toBe('打开自己的项目')
    await act(async () => {
      ob().markStep('open_fast_edit')
      ob().markStep('select_text')
    })
    await flush()
    c = card()!
    expect(c.textContent).toContain('教程结束')
    expect(c.textContent).toContain(`完成 2 步，跳过 ${REAL_STEP_IDS.length - 2} 步`)
    // 全做完才是「教程完成」
    await act(async () => {
      for (const id of REAL_STEP_IDS) ob().markStep(id)
    })
    await flush()
    expect(card()!.textContent).toContain('教程完成')
    expect(card()!.textContent).toContain('你已经走过')
  })

  it('锚点在对话框里：portal 进对话框、用绝对定位', async () => {
    const dialog = document.createElement('div')
    dialog.setAttribute('role', 'dialog')
    giveRect(dialog, { x: 300, y: 50, w: 500, h: 400 })
    const scope = document.createElement('div')
    scope.setAttribute('data-onboarding-anchor', 'export-scope')
    giveRect(scope, { x: 360, y: 120, w: 120, h: 24 })
    dialog.appendChild(scope)
    document.body.appendChild(dialog)
    useUiStore.setState({ exportOpen: true })
    await mount()
    await act(async () => {
      ob().start({ projectId: 'p_tut', documentId: META.document_id })
      ob().goTo('export_original')
    })
    await flush()
    const c = card()!
    expect(c.closest('[role="dialog"]:not([data-onboarding-coachmark])')).toBe(dialog)
    expect(c.style.position).toBe('absolute')
    expect(parseFloat(c.style.left)).toBe(360 - 300)
    expect(parseFloat(c.style.top)).toBe(120 - 50 + 24 + 10)
    expect(c.textContent).toContain('原图')
    dialog.remove()
  })

  it('落位不扫过锚点：第一次直接出现；之后路上碰到锚点就跳过去，碰不到才滑（issue #581）', async () => {
    // 曾经：挂载那一帧卡片在 -9999，落位与 left/top 过渡同一帧生效 → 从屏幕外斜着飞进来，
    // 半路扫过它指着的素材卡；慢机器上双击的第二下落在飞过来的「跳过此步」上。
    // 看的是**位置变化那一次提交**带没带过渡：停在原地时过渡开着无所谓（没东西可滑）。
    // 一次提交里 React 逐条改 style 属性，MutationObserver 看到的中间态不是任何一帧会画出来
    // 的东西，微任务分批又会把几次提交并成一批——所以用 Profiler：每次提交之后取一次
    const styles: string[] = ['left: -9999px; top: -9999px;']
    const onCommit = () => {
      const v = card()?.getAttribute('style')
      if (v && v !== styles.at(-1)) styles.push(v)
    }
    // 每一次位置变化的那一版：挪到哪、带没带过渡
    const pos = (v: string) => /left: ([^;]+);.*top: ([^;]+);/.exec(v)?.slice(1).join(',')
    const moves = () =>
      styles
        .filter((v, i) => i > 0 && pos(v) !== pos(styles[i - 1]))
        .map((v) => ({ at: pos(v), glide: /transition: left/.test(v) }))
    const anchor = document.createElement('div')
    anchor.setAttribute('data-object-id', 'p2')
    document.body.appendChild(anchor)
    giveRect(anchor, { x: 200, y: 100, w: 80, h: 40 })
    await act(async () => {
      root.render(
        <Profiler id="coachmark" onRender={onCommit}>
          <OnboardingLayer />
        </Profiler>,
      )
    })
    await flush()
    await act(async () => {
      ob().start({ projectId: 'p_tut', documentId: META.document_id })
      ob().goTo('open_fast_edit')
    })
    await flush()
    // 锚点横着挪：卡片（jsdom 量不出尺寸，按 300×120 算）从 (200,150) 到 (500,150)，
    // 路上碰不到锚点 → 滑
    giveRect(anchor, { x: 500, y: 100, w: 80, h: 40 })
    await act(async () => {
      window.dispatchEvent(new Event('resize'))
    })
    // 锚点往下挪进卡片原来那片的范围：卡片要从它上面经过 → 跳
    giveRect(anchor, { x: 500, y: 240, w: 80, h: 40 })
    await act(async () => {
      window.dispatchEvent(new Event('resize'))
    })
    await flush()
    expect(moves()).toEqual([
      // 从 -9999 出来：直接出现（挂载那一刻锚点还没量，先居中）
      { at: '362px,324px', glide: false },
      // 量到锚点：从居中处挪到锚点下方，整段都在锚点下沿之下 → 滑
      { at: '200px,150px', glide: true },
      { at: '500px,150px', glide: true },
      { at: '500px,290px', glide: false },
    ])
    anchor.remove()
  })

  it('滑行中卡片不接指针：过渡结束或兜底计时到了才恢复（#581）', async () => {
    const anchor = document.createElement('div')
    anchor.setAttribute('data-object-id', 'p2')
    document.body.appendChild(anchor)
    giveRect(anchor, { x: 200, y: 100, w: 80, h: 40 })
    await mount()
    await act(async () => {
      ob().start({ projectId: 'p_tut', documentId: META.document_id })
      ob().goTo('open_fast_edit')
    })
    await flush()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(DURATION.fast + 50)
    })
    // 停着的卡片照常可点
    expect(card()!.style.pointerEvents).toBe('')
    const glideTo = async (x: number) => {
      giveRect(anchor, { x, y: 100, w: 80, h: 40 })
      await act(async () => {
        window.dispatchEvent(new Event('resize'))
      })
    }
    // 横着滑：路上不碰锚点 → 滑，滑的这段不接指针
    await glideTo(500)
    expect(card()!.style.transition).toContain('left')
    expect(card()!.style.pointerEvents).toBe('none')
    // 过渡结束事件到了就恢复（别的属性的 transitionend 不算）
    const end = (prop: string) => {
      const e = new Event('transitionend')
      Object.defineProperty(e, 'propertyName', { value: prop })
      card()!.dispatchEvent(e)
    }
    await act(async () => end('opacity'))
    expect(card()!.style.pointerEvents).toBe('none')
    await act(async () => end('left'))
    expect(card()!.style.pointerEvents).toBe('')
    // 事件不来（被打断 / 元素被挪走）：兜底计时器从最后一次滑行起算
    await glideTo(300)
    expect(card()!.style.pointerEvents).toBe('none')
    await act(async () => {
      await vi.advanceTimersByTimeAsync(DURATION.fast + 49)
    })
    expect(card()!.style.pointerEvents).toBe('none')
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1)
    })
    expect(card()!.style.pointerEvents).toBe('')
    anchor.remove()
  })

  it('reduced motion：卡片不带位移过渡、高亮环不带进场动画', async () => {
    reduced = true
    const anchor = document.createElement('div')
    anchor.setAttribute('data-object-id', 'p2')
    document.body.appendChild(anchor)
    giveRect(anchor, { x: 10, y: 10, w: 20, h: 20 })
    await mount()
    await act(async () => {
      ob().start({ projectId: 'p_tut', documentId: META.document_id })
      ob().goTo('open_fast_edit')
    })
    await flush()
    expect(card()!.style.transition).toBe('')
    expect(document.querySelector('[data-onboarding-ring]')!.className).not.toContain('animate-')
    anchor.remove()
  })
})
