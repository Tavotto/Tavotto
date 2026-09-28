/**
 * 「另存为」的外部修改检测（issue #222 §1）。
 *
 * 2026-09-06（审计 T04）起「另存为」与「打开」是同一个组件的两种形态，按
 * `uiStore.layoutIntent` 分：另存那屏只有名字和位置，打开那屏一个能写盘的
 * 控件都没有。下面每条用例都说清自己进的是哪一屏。
 *
 * 改造前这条路一个基线都不带：两个窗口对同名画布各存一次，后写的整份盖掉
 * 先写的，**而两边都收到 200**。判据在后端只有一份（`_revision_conflict`），
 * 这里守的是**前端真的把基线带过去了**，以及 409 之后的那条出口：
 *
 * 1. 本窗口没确认过这个名字 → 基线是 `absent`（不是"不带基线"）；
 * 2. 载入过 / 存成功过 → 基线是那一份的 hash，不再打扰用户；
 * 3. 409 `external_change` 不是错误，是一个岔口：显示磁盘上那份是什么 +
 *    一个「仍然覆盖」；
 * 4. 覆盖拿 **409 里回的 hash** 当基线，不是清空基线——清空等于用户按一次
 *    覆盖就把这个名字的外部修改检测永久关掉了（ADR 0024 §3c）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  fetchLayout: vi.fn(),
  fetchLayoutNames: vi.fn(),
  saveLayout: vi.fn(),
}))

import { ApiError, REVISION_ABSENT, fetchLayout, fetchLayoutNames, saveLayout } from '@/lib/api'
import { LayoutDialog } from '@/components/LayoutDialog'
import { forgetLayoutRevisions, knownLayoutRevision } from '@/lib/layoutRevision'
import { readProjectFile } from '@/lib/projectFile'
import { setCurrentProjectId } from '@/lib/session'
import { formatMessage } from '@/i18n'
import { setProjectFile, useDocumentStore } from '@/store/documentStore'
import type { ProjectDocument } from '@/types/document'
import { useProjectStore } from '@/store/projectStore'
import { useUiStore } from '@/store/uiStore'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const mockFetch = vi.mocked(fetchLayout)
const mockNames = vi.mocked(fetchLayoutNames)
const mockSave = vi.mocked(saveLayout)

const LAYOUT = {
  schema: 3,
  project: { id: 'p', name: 'Fig 1' },
  canvases: [
    { id: 'c1', name: 'Fig 1', page: { w: 100, h: 100 }, objects: [], guides: [] },
  ],
  activeCanvasId: 'c1',
  createdAt: 0,
  updatedAt: 1,
}

const conflictError = (revision: string) =>
  new ApiError('磁盘上的这份文档已被 Tavotto 之外的改动覆盖过', 409, {
    code: 'external_change',
    revision,
    summary: { schema: 3, canvases: 2, objects: 7, updatedAt: 5, mtime: 6, name: 'x', revision },
  })

let root: Root

async function open(names: string[] = ['Fig 1'], intent: 'save' | 'saveToProject' | 'load' = 'save') {
  mockNames.mockResolvedValue(names)
  useUiStore.setState({ layoutOpen: true, layoutIntent: intent })
  const mountEl = document.createElement('div')
  document.body.appendChild(mountEl)
  root = createRoot(mountEl)
  await act(async () => {
    root.render(<LayoutDialog />)
  })
  await act(async () => {
    await Promise.resolve()
  })
}

const dialog = () => document.querySelector('[role="dialog"]')!
const buttonByText = (text: string) =>
  [...dialog().querySelectorAll('button')].find((b) => b.textContent?.includes(text))

const clickSave = async () => {
  await act(async () => {
    buttonByText('另存为')!.click()
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  forgetLayoutRevisions()
  useProjectStore.setState({ project: { open: true, id: 'p1', document_dir: '/figs/a/tavottofile' } })
  mockSave.mockResolvedValue({ ok: true, revision: 'rev-new' })
  mockFetch.mockResolvedValue({ doc: LAYOUT, revision: 'rev-disk', file: null })
})

afterEach(async () => {
  if (root) await act(async () => root.unmount())
  document.body.innerHTML = ''
  useUiStore.setState({ layoutOpen: false })
})

describe('另存为的基线', () => {
  it('本窗口没确认过这个名字时发 absent 哨兵，而不是不带基线', async () => {
    await open()
    await clickSave()
    expect(mockSave).toHaveBeenCalledTimes(1)
    // 第三个参数就是基线。不带基线（undefined）后端一律放行——那正是这条
    // issue 的现场：两个窗口都不带，双双 200，后写的整份盖掉先写的。
    expect(mockSave.mock.calls[0][2]).toBe(REVISION_ABSENT)
  })

  it('存成功之后再存，基线换成后端交回的那一份', async () => {
    await open()
    await clickSave()
    useUiStore.setState({ layoutOpen: true })
    await act(async () => {
      await Promise.resolve()
    })
    await clickSave()
    expect(mockSave.mock.calls[1][2]).toBe('rev-new')
  })

  it('载入过的名字用它读到的那一份当基线（不再多打扰用户一次）', async () => {
    // 「打开」和「另存为」现在是两屏（审计 T04）：先从打开那屏载入
    await open(['Fig 1'], 'load')
    await act(async () => {
      buttonByText('载入')!.click()
    })
    useUiStore.setState({ layoutOpen: true, layoutIntent: 'save' })
    await act(async () => {
      await Promise.resolve()
    })
    await clickSave()
    expect(mockSave.mock.calls[0][2]).toBe('rev-disk')
  })
})

describe('409 之后的出口', () => {
  it('冲突不显示成普通错误，而是给出磁盘上那份 + 一个「仍然覆盖」', async () => {
    mockSave.mockRejectedValueOnce(conflictError('rev-theirs'))
    await open()
    await clickSave()
    const text = dialog().textContent ?? ''
    expect(text).toContain('由其他窗口或外部工具写入')
    expect(text).toContain('7') // 磁盘上那份的对象数
    expect(buttonByText('仍然覆盖')).toBeTruthy()
    expect(useUiStore.getState().layoutOpen).toBe(true) // 没关掉，用户还要裁决
  })

  it('覆盖拿 409 里回的 hash 当基线（不是清空基线）', async () => {
    mockSave.mockRejectedValueOnce(conflictError('rev-theirs'))
    await open()
    await clickSave()
    await act(async () => {
      buttonByText('仍然覆盖')!.click()
    })
    expect(mockSave).toHaveBeenCalledTimes(2)
    expect(mockSave.mock.calls[1][2]).toBe('rev-theirs')
  })

  it('别的失败仍然按普通错误显示（这条岔口只属于 external_change）', async () => {
    mockSave.mockRejectedValueOnce(new ApiError('磁盘满了', 500, { code: 'write_failed' }))
    await open()
    await clickSave()
    expect(buttonByText('仍然覆盖')).toBeUndefined()
    expect(dialog().textContent).toContain('无法写入磁盘')  // backendErrorText 按 code 翻的那一句
  })
})

/**
 * 另存与打开分成两屏（审计 T04）。
 *
 * 改造前一个弹窗上下两截：上面是保存表单、下面是可载入的文件列表，底部一颗
 * 会写盘的主按钮。从「载入」进来的用户正对着保存表单，而那颗按钮会把当前
 * 文档写到框里的名字下——两件后果相反的事共用一屏。
 */
describe('另存 / 打开是两屏', () => {
  it('另存这屏：只有名字和位置，没有可载入的列表', async () => {
    useProjectStore.setState({
      project: { open: true, id: 'p1', document_dir: '/figs/a/tavottofile' },
    })
    await open(['Fig 1', 'Fig 2'], 'save')
    const d = dialog()
    expect(d.querySelector('#layout-save-name')).not.toBeNull()
    // 位置说的是后端给的那个目录，不是界面自己拼的。行里只写**末级目录**
    // （全面打磨 D29：420 宽的框里绝对路径末尾必被截掉，而末尾正是能认出它的那一段），
    // 完整路径仍在 title 里——两句都钉，只钉可见文字的话，把 title 摘掉也是绿的
    const into = [...d.querySelectorAll('[title]')].find(
      (el) => el.getAttribute('title') === '/figs/a/tavottofile',
    )
    expect(into, '完整路径不在 title 里').toBeTruthy()
    expect(into!.textContent).toBe('tavottofile')
    expect(buttonByText('另存为')).toBeTruthy()
    // 一份都载入不了：那是另一屏的事
    expect(buttonByText('载入')).toBeUndefined()
    expect(d.textContent).not.toContain('Fig 2')
  })

  it('打开这屏：只有文档列表，一个能写盘的控件都没有', async () => {
    await open(['Fig 1', 'Fig 2'], 'load')
    const d = dialog()
    expect(buttonByText('载入')).toBeTruthy()
    expect(d.textContent).toContain('Fig 2')
    expect(buttonByText('另存为')).toBeUndefined()
    expect(d.querySelector('#layout-save-name')).toBeNull()
  })

  it('后端没给目录时不编一个出来', async () => {
    useProjectStore.setState({ project: { open: true, id: 'p1' } })
    await open(['Fig 1'], 'save')
    expect(dialog().textContent).not.toContain('保存到')
  })

  it('名字撞上已有文档时当场说出来（写盘之前）', async () => {
    await open(['Fig 1'], 'save')
    expect(dialog().textContent).toContain('已有同名排版')
    expect(mockSave).not.toHaveBeenCalled()
  })

  it('名字没撞上就不吓唬用户', async () => {
    await open(['Something Else'], 'save')
    expect(dialog().textContent).not.toContain('已有同名排版')
  })
})

/**
 * ⌘S 保存到项目（ADR 0096）：第一次 ⌘S 的「存进项目」与另存为是同一个表单；
 * 存进项目的、从项目里打开的都绑定那个文件，之后 ⌘S 直接写回。
 */
describe('存进项目与绑定', () => {
  beforeEach(async () => {
    setCurrentProjectId('p1')
    localStorage.clear()
    const pd: ProjectDocument = {
      ...LAYOUT,
      schema: 3,
      project: { id: 'p', name: '我的排版' },
      canvases: [{ ...LAYOUT.canvases[0], name: 'Figure 1' }],
    }
    await useDocumentStore.getState().switchDocument(pd, 'd_bind')
  })
  afterEach(() => setCurrentProjectId(null))

  it('「存进项目」预填排版名（不是画布名 Figure 1），要求写进项目，写成即绑定', async () => {
    mockSave.mockResolvedValue({
      ok: true,
      revision: 'rev-p',
      name: '我的排版',
      file: 'tavottofile/我的排版.json',
    })
    await open([], 'saveToProject')
    expect(dialog().textContent).toContain('存进项目')
    const input = dialog().querySelector<HTMLInputElement>('#layout-save-name')!
    expect(input.value).toBe('我的排版')
    await act(async () => {
      buttonByText('存进项目')!.click()
    })
    expect(mockSave.mock.calls[0][0]).toBe('我的排版')
    expect(mockSave.mock.calls[0][3]).toEqual({ target: 'project' })
    expect(useDocumentStore.getState().projectFile).toEqual({
      projectId: 'p1',
      name: '我的排版',
      file: 'tavottofile/我的排版.json',
      revision: 'rev-p',
      dirty: false,
    })
    expect(formatMessage(useUiStore.getState().status)).toContain('tavottofile/我的排版.json')
  })

  it('另存为进了项目同样绑定；没打开项目（后端不给 file）就不绑定', async () => {
    mockSave.mockResolvedValue({ ok: true, revision: 'r', name: '我的排版', file: 'tavottofile/我的排版.json' })
    await open([], 'save')
    await clickSave()
    expect(mockSave.mock.calls[0][3]).toBeUndefined() // 另存为不带 target，行为不变
    expect(useDocumentStore.getState().projectFile?.file).toBe('tavottofile/我的排版.json')

    await act(async () => root.unmount())
    setProjectFile(null)
    mockSave.mockResolvedValue({ ok: true, revision: 'r', name: '我的排版' })
    await open([], 'save')
    await clickSave()
    expect(useDocumentStore.getState().projectFile).toBeNull()
  })

  it('从「项目里的排版」打开：新会话绑定那个文件，基线是这次读到的那一份', async () => {
    mockFetch.mockResolvedValue({ doc: LAYOUT, revision: 'rev-disk', file: 'tavottofile/Fig 1.json' })
    await open(['Fig 1'], 'load')
    await act(async () => {
      buttonByText('载入')!.click()
    })
    const s = useDocumentStore.getState()
    expect(s.documentId).not.toBe('d_bind')
    expect(s.projectFile).toEqual({
      projectId: 'p1',
      name: 'Fig 1',
      file: 'tavottofile/Fig 1.json',
      revision: 'rev-disk',
      dirty: false,
    })
  })

  it('⌘S 写回撞上外部修改：带着冲突打开，「仍然覆盖」拿 409 的 hash、仍写进项目', async () => {
    useUiStore.getState().setLayoutOpen(true, 'saveToProject', {
      name: '我的排版',
      conflict: { name: '我的排版', revision: 'rev-theirs', summary: null },
    })
    mockNames.mockResolvedValue([])
    const mountEl = document.createElement('div')
    document.body.appendChild(mountEl)
    root = createRoot(mountEl)
    await act(async () => {
      root.render(<LayoutDialog />)
    })
    expect(buttonByText('仍然覆盖')).toBeTruthy()
    await act(async () => {
      buttonByText('仍然覆盖')!.click()
    })
    expect(mockSave.mock.calls[0][2]).toBe('rev-theirs')
    expect(mockSave.mock.calls[0][3]).toEqual({ target: 'project' })
  })
})

/** ADR 0096 评审第 1 轮：await 之后的落账属于发请求那一刻的项目；回车连按不并发写 */
describe('保存 / 打开途中切项目、连按回车', () => {
  beforeEach(async () => {
    setCurrentProjectId('p1')
    localStorage.clear()
    await useDocumentStore.getState().switchDocument(LAYOUT as ProjectDocument, 'd_race')
  })
  afterEach(() => setCurrentProjectId(null))

  const deferred = <T,>() => {
    let resolve!: (v: T) => void
    let reject!: (e: unknown) => void
    const promise = new Promise<T>((a, b) => {
      resolve = a
      reject = b
    })
    return { promise, resolve, reject }
  }

  it('另存为途中切了项目：修订号记在原项目名下', async () => {
    const d = deferred<Awaited<ReturnType<typeof saveLayout>>>()
    mockSave.mockReturnValueOnce(d.promise)
    await open([], 'save')
    await clickSave()
    setCurrentProjectId('p2')
    await act(async () => {
      d.resolve({ ok: true, revision: 'rev-a', name: 'Fig 1' })
    })
    expect(knownLayoutRevision('Fig 1', 'p2')).toBeUndefined()
    expect(knownLayoutRevision('Fig 1', 'p1')).toBe('rev-a')
  })

  it('另存为途中切了项目又撞上冲突：不给「仍然覆盖」，说清是切了项目', async () => {
    const d = deferred<Awaited<ReturnType<typeof saveLayout>>>()
    mockSave.mockReturnValueOnce(d.promise)
    await open([], 'save')
    await clickSave()
    setCurrentProjectId('p2')
    await act(async () => {
      d.reject(conflictError('rev-theirs'))
    })
    expect(buttonByText('仍然覆盖')).toBeUndefined()
    // 切走了：话不进此刻的对话框，进状态条
    expect(useUiStore.getState().statusTone).toBe('error')
    expect(formatMessage(useUiStore.getState().status)).toContain('切换了排版或项目')
  })

  it('打开途中切了项目：不把原项目的排版当成新项目的文件打开', async () => {
    const d = deferred<Awaited<ReturnType<typeof fetchLayout>>>()
    mockFetch.mockReturnValueOnce(d.promise)
    await open(['Fig 1'], 'load')
    await act(async () => {
      buttonByText('载入')!.click()
    })
    setCurrentProjectId('p2')
    await act(async () => {
      d.resolve({ doc: LAYOUT, revision: 'rev-disk', file: 'tavottofile/Fig 1.json' })
    })
    expect(useDocumentStore.getState().documentId).toBe('d_race')
    // 没打开要说出来
    expect(useUiStore.getState().statusTone).toBe('error')
    expect(formatMessage(useUiStore.getState().status)).toContain('没有打开 Fig 1')
    expect(knownLayoutRevision('Fig 1', 'p1')).toBe('rev-disk')
    expect(knownLayoutRevision('Fig 1', 'p2')).toBeUndefined()
  })

  it.each([
    ['途中没再改', false],
    ['切走前又改过', true],
  ] as const)('存进项目途中换到别的排版，原排版%s：圆点按它自己的编辑代次判', async (_, editedBefore) => {
    const d = deferred<Awaited<ReturnType<typeof saveLayout>>>()
    mockSave.mockReturnValueOnce(d.promise)
    await open([], 'saveToProject')
    await act(async () => {
      buttonByText('存进项目')!.click()
    })
    if (editedBefore) useDocumentStore.getState().renameProject('改过的名字')
    await useDocumentStore.getState().switchDocument(LAYOUT as ProjectDocument, 'd_other')
    await act(async () => {
      d.resolve({ ok: true, revision: 'rev-p', name: 'Fig 1', file: 'tavottofile/Fig 1.json' })
    })
    expect(readProjectFile('d_race')).toMatchObject({
      file: 'tavottofile/Fig 1.json',
      revision: 'rev-p',
      dirty: editedBefore,
    })
  })

  it('回车连按两下只发一次保存', async () => {
    const d = deferred<Awaited<ReturnType<typeof saveLayout>>>()
    mockSave.mockReturnValueOnce(d.promise)
    await open([], 'save')
    const input = dialog().querySelector<HTMLInputElement>('#layout-save-name')!
    await act(async () => {
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    })
    await act(async () => {
      d.resolve({ ok: true, revision: 'r' })
    })
    expect(mockSave).toHaveBeenCalledTimes(1)
  })
})

/** ADR 0096 评审第 2 轮：修订号按后端净化后的名字记，按输入名也要查得到 */
describe('后端净化过的名字', () => {
  beforeEach(async () => {
    setCurrentProjectId('p1')
    localStorage.clear()
    await useDocumentStore
      .getState()
      .switchDocument({ ...LAYOUT, project: { id: 'p', name: 'Untitled layout' } } as ProjectDocument, 'd_norm')
  })
  afterEach(() => setCurrentProjectId(null))

  it('Untitled layout 存成 Untitled_layout：第二次另存为带着第一次写成的修订号，并提示同名', async () => {
    mockSave.mockResolvedValue({ ok: true, revision: 'rev-1', name: 'Untitled_layout' })
    await open([], 'save')
    expect(dialog().querySelector<HTMLInputElement>('#layout-save-name')!.value).toBe('Untitled layout')
    await clickSave()
    mockNames.mockResolvedValue(['Untitled_layout'])
    useUiStore.setState({ layoutOpen: true })
    await act(async () => {
      await Promise.resolve()
    })
    await act(async () => {
      await Promise.resolve()
    })
    expect(dialog().textContent).toContain('已有同名')
    await clickSave()
    expect(mockSave.mock.calls[1][0]).toBe('Untitled layout')
    expect(mockSave.mock.calls[1][2]).toBe('rev-1')
    expect(knownLayoutRevision('Untitled_layout', 'p1')).toBe('rev-1')
  })
})

/**
 * ADR 0096 评审第 5 轮：对话框里每个 await 之后的界面变更（开关、错误、冲突岔口、打开）都看保存
 * 上下文；切走了就不碰此刻的对话框，结果改在状态条上说。清单见 `store/saveContext.ts`。
 */
describe('对话框里每个 await 之后的界面变更都看保存上下文', () => {
  beforeEach(async () => {
    setCurrentProjectId('p1')
    localStorage.clear()
    await useDocumentStore.getState().switchDocument(LAYOUT as ProjectDocument, 'd_ui')
  })
  afterEach(() => setCurrentProjectId(null))

  const deferred = <T,>() => {
    let resolve!: (v: T) => void
    let reject!: (e: unknown) => void
    const promise = new Promise<T>((a, b) => {
      resolve = a
      reject = b
    })
    return { promise, resolve, reject }
  }

  type Intent = 'save' | 'load'
  type Outcome = 'ok' | 'conflict' | 'error'
  const cases: [string, Intent, Outcome, boolean][] = []
  for (const [intent, outcomes] of [
    ['save', ['ok', 'conflict', 'error']],
    ['load', ['ok', 'error']],
  ] as const) {
    for (const outcome of outcomes) {
      for (const switched of [false, true]) {
        cases.push([`${intent === 'save' ? '另存为' : '打开'} · ${outcome} · ${switched ? '途中切走' : '没切走'}`, intent, outcome, switched])
      }
    }
  }

  it.each(cases)('%s', async (_, intent, outcome, switched) => {
    const d = deferred<never>()
    if (intent === 'save') mockSave.mockReturnValueOnce(d.promise)
    else mockFetch.mockReturnValueOnce(d.promise)
    await open(['Fig 1'], intent)
    await act(async () => {
      buttonByText(intent === 'save' ? '另存为' : '载入')!.click()
    })
    if (switched) {
      await act(async () => {
        await useDocumentStore
          .getState()
          .switchDocument({ ...LAYOUT, project: { id: 'p', name: 'B 排版' } } as ProjectDocument, 'd_b')
      })
    }
    useUiStore.setState({ status: null, statusTone: 'info' })
    await act(async () => {
      if (outcome === 'ok') {
        ;(d.resolve as (v: unknown) => void)(
          intent === 'save'
            ? { ok: true, revision: 'r', name: 'Fig 1', file: 'tavottofile/Fig 1.json' }
            : { doc: LAYOUT, revision: 'r', file: 'tavottofile/Fig 1.json' },
        )
      } else {
        d.reject(
          outcome === 'conflict' ? conflictError('rev-x') : new ApiError('ro', 403, { code: 'layout_read_only' }),
        )
      }
    })
    const ui = useUiStore.getState()
    const text = document.querySelector('[role="dialog"]')?.textContent ?? ''
    const status = formatMessage(ui.status)
    if (!switched) {
      if (outcome === 'ok') expect(ui.layoutOpen).toBe(false)
      if (outcome === 'conflict') expect(text).toContain('仍然覆盖')
      if (outcome === 'error') expect(text).toContain('只读')
    } else {
      // 此刻的对话框（B 的）不被 A 的结果关掉、也不被塞进 A 的错误 / 岔口
      expect(ui.layoutOpen).toBe(true)
      expect(text).not.toContain('仍然覆盖')
      expect(text).not.toContain('只读')
      expect(useDocumentStore.getState().documentId).toBe('d_b')
      // 结果换个地方说：状态条
      if (intent === 'save' && outcome === 'ok') {
        expect(status).toContain('tavottofile/Fig 1.json')
      } else {
        expect(ui.statusTone).toBe('error')
        expect(status).toContain(outcome === 'error' ? '只读' : intent === 'load' ? '没有打开 Fig 1' : '切换了排版或项目')
      }
    }
    // 对话框自己发出的请求结束了：busy 一律复位
    if (ui.layoutOpen) expect(buttonByText('关闭')!.disabled).toBe(false)
  })
})

/** ADR 0096 评审第 5 轮：裁决冲突不改文档标题 */
describe('冲突岔口「仍然覆盖」', () => {
  beforeEach(async () => {
    setCurrentProjectId('p1')
    localStorage.clear()
    await useDocumentStore
      .getState()
      .switchDocument({ ...LAYOUT, project: { id: 'p', name: 'Untitled layout' } } as ProjectDocument, 'd_ow')
  })
  afterEach(() => setCurrentProjectId(null))

  it('⌘S 撞上冲突带着规范名打开岔口，点覆盖：写到 Untitled_layout，标题仍是 Untitled layout', async () => {
    mockSave.mockResolvedValueOnce({
      ok: true,
      revision: 'rev-2',
      name: 'Untitled_layout',
      file: 'tavottofile/Untitled_layout.json',
    })
    await open([], 'saveToProject')
    // ⌘S 写回撞上 409 时 projectSave 就是这样打开命名框的（名字 = 绑定里的规范名）
    useUiStore.getState().setLayoutOpen(true, 'saveToProject', {
      name: 'Untitled_layout',
      conflict: { name: 'Untitled_layout', revision: 'rev-theirs', summary: null },
    })
    await act(async () => {
      await Promise.resolve()
    })
    await act(async () => {
      buttonByText('仍然覆盖')!.click()
    })
    expect(mockSave).toHaveBeenCalledTimes(1)
    expect(mockSave.mock.calls[0][0]).toBe('Untitled_layout')
    expect(mockSave.mock.calls[0][2]).toBe('rev-theirs')
    expect(useDocumentStore.getState().projectMeta.name).toBe('Untitled layout')
  })

  it('岔口开着时改了名字框再点覆盖：覆盖的仍是冲突那份文件（基线是它的 hash），不拿新名字去撞', async () => {
    mockSave.mockResolvedValueOnce({ ok: true, revision: 'rev-2', name: 'Untitled_layout' })
    await open([], 'saveToProject')
    useUiStore.getState().setLayoutOpen(true, 'saveToProject', {
      name: 'Untitled_layout',
      conflict: { name: 'Untitled_layout', revision: 'rev-theirs', summary: null },
    })
    await act(async () => {
      await Promise.resolve()
    })
    const input = dialog().querySelector<HTMLInputElement>('#layout-save-name')!
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, '别的名字')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    expect(input.value).toBe('别的名字')
    await act(async () => {
      buttonByText('仍然覆盖')!.click()
    })
    expect(mockSave.mock.calls[0][0]).toBe('Untitled_layout')
    expect(mockSave.mock.calls[0][2]).toBe('rev-theirs')
    expect(useDocumentStore.getState().projectMeta.name).toBe('Untitled layout')
  })
})
