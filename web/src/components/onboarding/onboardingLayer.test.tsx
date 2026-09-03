/**
 * coachmark 层（ADR 0040）：只在教程进行中出现；欢迎页居中且「开始」是真动作；
 * 锚点在 → 贴着锚点、画高亮环；锚点缺 → 等一会儿再说「找不到」并给返回 / 跳过；
 * Esc 暂停；锚点在对话框里 → portal 进对话框；reduced motion 下不带位移过渡。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { STEP_IDS } from '@/lib/onboarding/stepIds'
import { useTutorialStore } from '@/lib/onboarding/tutorial'
import type { TutorialMetadata } from '@/lib/api'
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
  document.querySelectorAll('[data-object-id], [role="dialog"]').forEach((n) => n.remove())
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

  it('锚点缺：先等，超时后说「找不到」，界面没有被锁', async () => {
    await mount()
    await act(async () => {
      ob().start({ projectId: 'p_tut', documentId: META.document_id })
      ob().goTo('change_typography')
    })
    await flush()
    const c = card()!
    expect(c.textContent).toContain('正在等待目标出现')
    await act(async () => {
      await vi.advanceTimersByTimeAsync(WAIT_MS + 400)
    })
    await flush()
    expect(card()!.textContent).toContain('找不到这一步的目标')
    // 返回真的回到上一步（change_typography 的上一步是 select_text）
    await act(async () => {
      card()!.querySelector<HTMLButtonElement>('[data-onboarding-back]')!.click()
    })
    expect(ob().currentStep).toBe('select_text')
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
