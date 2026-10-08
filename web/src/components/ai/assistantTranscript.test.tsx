/**
 * 改图助手转录重做（2026-10-07 设计审计 §6.1–§6.7，宪法第十八节「2026-10-07 重做」）在 jsdom 里量得到的部分。
 *
 * - 用户消息是右对齐气泡，超 3 行折叠；hover 操作「复制 / 重新发送」真的复制、真的按原话再发一次；
 * - 助手回答不装卡（转录里没有 Card），正文 type-reading；
 * - 过程折成一行「已思考 Ns · N 步」，耗时来自条目到达时刻；进行中那一行是最新一步原位替换（shimmer）；
 * - 失败是 danger Notice + 「重试」；发送失败同样是 Notice + 「重试」；
 * - 改了脚本是显著卡：文件头 + 计数 + 回滚 / 复制补丁 / 放大；行有行号、变更条、hunk 间隔、字级高亮；
 * - 代码块：语言标签 + 复制（✓ 1.2s）+ 语法色；没写语言的围栏块也是代码块；流式时代码块不被逐词切开；
 * - 任务历史是弹层，打开时对话流还在原处。
 *
 * 选择器一律认 `data-*`。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { aiRevert, fetchAiHistory } from '@/lib/api'
import { t } from '@/i18n'
import { STREAM_WORD_CLASS } from '@/lib/streamMarkdown'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { agentCaps, capsOf } from '@/components/settings/testCaps'
import { useAiStore, type AiSession } from '@/store/aiStore'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { AssistantPanel } from './AiPanel'
import { COPIED_MS } from './CopyAction'
import { Markdown } from './Markdown'
import { formatDuration, parseUnifiedDiff, type LineRow } from './transcriptModel'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  aiRevert: vi.fn(async () => ({ ok: true })),
  fetchAiHistory: vi.fn(async () => ({ sessions: [], total: 0 })),
}))

globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch
Element.prototype.scrollIntoView ??= function scrollIntoView() {}
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const ai = (k: string, v?: Record<string, unknown>) => t(k, { ns: 'ai', ...(v ?? {}) })

const panel = (): PanelObject =>
  ({
    id: 'p1', type: 'panel', x: 0, y: 0, w: 100, h: 75,
    fileId: 'Fig1.pdf', fileKind: 'pdf', nativeW: 100, nativeH: 75,
    name: 'Fig1', script: '/tmp/figs/fig1.py', overrides: [],
  }) as unknown as PanelObject

const T0 = 1_756_000_000_000
const session = (over: Partial<AiSession> = {}): AiSession => ({
  id: 's1',
  project: null,
  agent: 'codex',
  agentLabel: 'Codex',
  prompt: '把图例移到左上角',
  script: '/tmp/figs/fig1.py',
  panelId: 'p1',
  fileId: 'Fig1.pdf',
  gid: null,
  scope: 'figure',
  target: '整张图',
  entries: [],
  status: 'done',
  changed: false,
  diff: '',
  startedAt: T0,
  finishedAt: T0 + 18_000,
  ...over,
})

const DIFF = [
  '--- fig1.py (修改前)',
  '+++ fig1.py (修改后)',
  '@@ -41,3 +41,4 @@',
  ' fig, ax = plt.subplots()',
  '-ax.set_yscale("linear")',
  '+ax.set_yscale("log")',
  '+ax.yaxis.set_major_formatter(f)',
  ' ax.legend()',
  '',
].join('\n')

let root: Root
let host: HTMLDivElement
const start = vi.fn(async () => {})
const writeText = vi.fn(async () => {})

async function mount(sessions: AiSession[]) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_assistant_transcript')
  useDocumentStore.setState((s) => ({ doc: { ...s.doc, objects: [panel()] } }) as never)
  useSelectionStore.setState({ ids: ['p1'] } as never)
  useUiStore.setState({ elementPanelId: null, selectedGids: [] } as never)
  useAiStore.setState({ sessions, start } as never)
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
}

const q = <T extends Element = HTMLElement>(sel: string) => host.querySelector<T>(sel)
const qa = <T extends Element = HTMLElement>(sel: string) => Array.from(host.querySelectorAll<T>(sel))

const realStart = useAiStore.getState().start
beforeEach(() => {
  localStorage.clear()
  document.body.innerHTML = ''
  start.mockReset()
  start.mockImplementation(async () => {})
  writeText.mockClear()
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
  useAiStore.setState({
    sessions: [],
    scope: 'figure',
    caps: capsOf([agentCaps()]),
    agent: 'codex',
    models: {},
    efforts: {},
  })
})

afterEach(async () => {
  await act(async () => root?.unmount())
  useAiStore.setState({ start: realStart } as never)
  vi.useRealTimers()
})

describe('用户消息与助手回答分开（§6.1）', () => {
  it('用户消息是右对齐气泡；助手回答不装卡，正文 type-reading', async () => {
    await mount([session({ entries: [{ kind: 'message', text: '已把 y 轴改成对数刻度。' }] })])
    const user = q('[data-ai-user]')!
    expect(user.className).toContain('items-end')
    expect(user.textContent).toContain('把图例移到左上角')
    // 转录里一张卡都没有：一轮不再是一张卡，回答直接坐在面板上
    expect(q('[data-ai-transcript] [data-card]')).toBeNull()
    const p = q('[data-ai-markdown] p')!
    expect(p.className).toContain('type-reading')
    // 轮次节奏由列表容器给：轮 16、项 8
    expect(q('[data-ai-transcript]')!.className).toContain('[--turn-gap:16px]')
    expect(q('[data-ai-session]')!.className).toContain('gap-(--item-gap)')
  })

  it('超过 3 行的提示词折叠，可展开 / 收起；3 行以内不摆折叠钮', async () => {
    const long = ['第一行', '第二行', '第三行', '第四行'].join('\n')
    await mount([session({ prompt: long }), session({ id: 's2', prompt: '短的一句' })])
    const folds = qa<HTMLButtonElement>('[data-ai-user-fold]')
    expect(folds).toHaveLength(1)
    const text = qa('[data-ai-user]')[0].querySelector('p')!
    expect(text.className).toContain('line-clamp-3')
    await act(async () => folds[0].click())
    expect(folds[0].getAttribute('aria-expanded')).toBe('true')
    expect(text.className).not.toContain('line-clamp-3')
  })

  it('右栏变窄后软换行超过 3 行：重新量，摆出折叠钮（Codex #827 P2）', async () => {
    const observers: ResizeObserverCallback[] = []
    vi.stubGlobal(
      'ResizeObserver',
      class {
        constructor(cb: ResizeObserverCallback) {
          observers.push(cb)
        }
        observe() {}
        disconnect() {}
      },
    )
    try {
      await mount([session({ prompt: '一句很长但没有换行的话' })])
      expect(q('[data-ai-user-fold]')).toBeNull()
      const p = q('[data-ai-user] p')!
      Object.defineProperty(p, 'scrollHeight', { configurable: true, value: 120 })
      Object.defineProperty(p, 'clientHeight', { configurable: true, value: 60 })
      await act(async () => observers.forEach((cb) => cb([], {} as ResizeObserver)))
      expect(q('[data-ai-user-fold]')).not.toBeNull()
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('复制：写进剪贴板，图标换 ✓，1.2s 后换回', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    await mount([session()])
    const copy = q<HTMLButtonElement>('[data-ai-user-copy]')!
    await act(async () => copy.click())
    expect(writeText).toHaveBeenCalledWith('把图例移到左上角')
    expect(copy.dataset.copied).toBe('true')
    await act(async () => {
      vi.advanceTimersByTime(COPIED_MS + 10)
    })
    expect(copy.dataset.copied).toBeUndefined()
  })

  it('重新发送：按那一轮的原话再发一次，不动输入框里的草稿', async () => {
    await mount([session()])
    await act(async () => q<HTMLButtonElement>('[data-ai-resend]')!.click())
    expect(start).toHaveBeenCalledTimes(1)
    expect((start.mock.calls[0] as unknown as [{ prompt: string }])[0].prompt).toBe('把图例移到左上角')
  })

  it('同一个脚本正在跑时，重新发送不可点（与发送钮同一条判据）', async () => {
    await mount([session({ status: 'running', finishedAt: undefined })])
    expect(q<HTMLButtonElement>('[data-ai-resend]')!.disabled).toBe(true)
  })
})

describe('过程：环境行（§6.2）', () => {
  const steps = [
    { kind: 'thinking' as const, text: '先看一眼脚本', at: T0 + 1000 },
    { kind: 'action' as const, text: '$ python fig1.py', at: T0 + 7000 },
    { kind: 'action' as const, text: '✎ Edit fig1.py', at: T0 + 8800 },
    { kind: 'message' as const, text: '改好了', at: T0 + 9000 },
  ]

  it('完成后折成一行「已思考 Ns · N 步」，耗时来自条目到达时刻', async () => {
    await mount([session({ entries: steps })])
    const toggle = q('[data-ai-process-toggle]')!
    expect(toggle.textContent).toBe(
      `${ai('process.thought', { duration: formatDuration(6000) })} · ${ai('panel.processSteps', { count: 2 })}`,
    )
    expect(toggle.querySelector('.text-shimmer')).toBeNull()
  })

  it('展开后每步一行，耗时右对齐；动作行可再展开看完整参数', async () => {
    await mount([session({ entries: steps })])
    await act(async () => q<HTMLButtonElement>('[data-ai-process-toggle]')!.click())
    const rows = qa('[data-ai-step]')
    expect(rows.map((r) => r.dataset.aiStep)).toEqual(['thinking', 'action', 'action'])
    expect(rows[1].textContent).toContain(formatDuration(1800))
    expect(rows[2].textContent).toContain(formatDuration(200))
    const stepToggle = rows[1].querySelector<HTMLButtonElement>('[data-ai-step-toggle]')!
    expect(stepToggle.getAttribute('aria-expanded')).toBe('false')
    await act(async () => stepToggle.click())
    expect(stepToggle.getAttribute('aria-expanded')).toBe('true')
    expect(rows[1].querySelector('[data-reveal] pre')!.textContent).toBe('python fig1.py')
  })

  it('store 自己给条目盖到达时刻、给会话盖结束时刻：经 appendDelta / finish 走一遍，耗时照样说得出', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false })
    vi.setSystemTime(T0)
    await mount([session({ status: 'running', finishedAt: undefined })])
    await act(async () => useAiStore.getState().appendDelta('s1', 'thinking', '想一想'))
    vi.setSystemTime(T0 + 6000)
    await act(async () => useAiStore.getState().appendDelta('s1', 'action', '$ python fig1.py'))
    vi.setSystemTime(T0 + 8000)
    await act(async () => {
      useAiStore.getState().finish({ session: 's1', status: 'done', changed: false, diff: '' })
    })
    expect(q('[data-ai-process-toggle]')!.textContent).toBe(
      `${ai('process.thought', { duration: formatDuration(6000) })} · ${ai('panel.processSteps', { count: 1 })}`,
    )
    expect(q('[data-ai-turn-meta]')!.textContent).toContain(formatDuration(8000))
  })

  it('没有到达时刻（旧数据）就不说耗时，不编一个数', async () => {
    await mount([session({ entries: [{ kind: 'thinking', text: '想一想' }] })])
    expect(q('[data-ai-process-toggle]')!.textContent).toBe(ai('process.thoughtNoTime'))
  })

  it('进行中：那一行是最新一步原位替换，带亮带', async () => {
    await mount([
      session({ status: 'running', finishedAt: undefined, entries: steps.slice(0, 2) }),
    ])
    const toggle = q('[data-ai-process-toggle]')!
    const live = toggle.querySelector('.text-shimmer')!
    expect(live.textContent).toBe(`${ai('panel.stepRan')} python fig1.py`)
    // 中止钮上那圈轨道只在跑的时候有
    expect(q('[data-ai-orbit]')).toBeTruthy()
  })

  it('跑着的一轮转成失败：同一组过程就地展开（Codex #827 P2）', async () => {
    await mount([session({ status: 'running', finishedAt: undefined, entries: steps.slice(0, 2) })])
    expect(q('[data-ai-process-toggle]')!.getAttribute('aria-expanded')).toBe('false')
    await act(async () => {
      useAiStore.setState({
        sessions: [session({ status: 'failed', error: 'boom', entries: steps.slice(0, 2) })],
      } as never)
    })
    expect(q('[data-ai-process-toggle]')!.getAttribute('aria-expanded')).toBe('true')
  })

  it('失败的一轮：最后一组过程自动展开，失败说明是 danger Notice + 重试', async () => {
    await mount([
      session({ status: 'failed', error: 'ValueError: no positive values', entries: steps.slice(0, 2) }),
    ])
    expect(q('[data-ai-process-toggle]')!.getAttribute('aria-expanded')).toBe('true')
    const notice = q('[data-ai-error="session"]')!
    expect(notice.dataset.notice).toBe('danger')
    expect(notice.textContent).toContain('ValueError')
    await act(async () => notice.querySelector<HTMLButtonElement>('[data-ai-retry]')!.click())
    expect(start).toHaveBeenCalledTimes(1)
  })
})

describe('发送失败（§6.7）', () => {
  it('是 danger Notice，带「重试」；重试按原话再发', async () => {
    start.mockImplementationOnce(async () => {
      throw new Error('boom')
    })
    await mount([])
    const box = q<HTMLTextAreaElement>('[data-ai-input]')!
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!
    await act(async () => {
      setter.call(box, '加粗线条')
      box.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await act(async () => q<HTMLButtonElement>('[data-ai-send]')!.click())
    const notice = q('[data-ai-error="send"]')!
    expect(notice.dataset.notice).toBe('danger')
    await act(async () => notice.querySelector<HTMLButtonElement>('[data-ai-retry]')!.click())
    expect(start).toHaveBeenCalledTimes(2)
    expect((start.mock.calls[1] as unknown as [{ prompt: string }])[0].prompt).toBe('加粗线条')
    expect(q('[data-ai-error="send"]')).toBeNull()
  })

  it('发送失败钉在那一张图上：换到别的图就不摆它的重试，回来照常重试原处（Codex #827 P1）', async () => {
    start.mockImplementationOnce(async () => {
      throw new Error('boom')
    })
    await mount([])
    const box = q<HTMLTextAreaElement>('[data-ai-input]')!
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!
    await act(async () => {
      setter.call(box, '加粗线条')
      box.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await act(async () => q<HTMLButtonElement>('[data-ai-send]')!.click())
    const first = (start.mock.calls[0] as unknown as [{ scope: string }])[0]
    expect(first.scope).toBe('figure')
    // 失败之后选了另一张图：那条失败与它的「重试」不跟过去（否则会把 p1 的话改到 p2 上）
    await act(async () => {
      useDocumentStore.setState((s) => ({ doc: { ...s.doc, objects: [...s.doc.objects, { ...panel(), id: 'p2', fileId: 'fig2.pdf' }] } }) as never)
      useSelectionStore.setState({ ids: ['p2'] } as never)
    })
    expect(q('[data-ai-error="send"]')).toBeNull()
    // 回到 p1：失败还在，重试发往 p1
    await act(async () => useSelectionStore.setState({ ids: ['p1'] } as never))
    await act(async () => q('[data-ai-error="send"]')!.querySelector<HTMLButtonElement>('[data-ai-retry]')!.click())
    expect((start.mock.calls[1] as unknown as [{ panelId: string }])[0].panelId).toBe('p1')
  })
})

describe('会话失败的重试', () => {
  it('兄弟面板（同一脚本的另一张图）发起的失败轮次：看得到，但不给重试（Codex #827 P1）', async () => {
    await mount([session({ status: 'failed', error: 'boom', panelId: 'p-sibling' })])
    expect(q('[data-ai-error="session"]')).not.toBeNull()
    expect(q('[data-ai-error="session"] [data-ai-retry]')).toBeNull()
  })

  it('按那一轮自己的目标重发（元素 / gid），不跟着此刻的作用范围（Codex #827 P1）', async () => {
    await mount([
      session({ status: 'failed', error: 'boom', scope: 'element', gid: 'g7', target: '图例' }),
    ])
    await act(async () => q('[data-ai-error="session"] [data-ai-retry]')!.dispatchEvent(new MouseEvent('click', { bubbles: true })))
    const call = (start.mock.calls[0] as unknown as [{ scope: string; gid: string | null; label: string | null }])[0]
    expect(call.scope).toBe('element')
    expect(call.gid).toBe('g7')
    expect(call.label).toBe('图例')
  })
})

describe('改了脚本 = 显著卡（§6.2 / §6.4）', () => {
  it('文件头：已修改 · 文件名 · +N −N；回滚在文件头里', async () => {
    await mount([session({ changed: true, diff: DIFF })])
    const card = q('[data-ai-diff]')!
    expect(card.textContent).toContain(ai('diff.edited'))
    expect(card.textContent).toContain('fig1.py')
    expect(card.querySelector('[data-ai-diff-counts]')!.textContent).toBe('+2−1')
    // 转录里不再有游离的那颗回滚钮：唯一的一颗在卡头里
    expect(qa('[data-ai-revert]')).toHaveLength(1)
    await act(async () => card.querySelector<HTMLButtonElement>('[data-ai-revert]')!.click())
    expect(vi.mocked(aiRevert)).toHaveBeenCalledWith('s1', null)
    // 回滚之后会话不再算「改了脚本」，卡随之撤下
    expect(q('[data-ai-diff]')).toBeNull()
  })

  it('行：增删各自的行号与变更条，hunk 之前的未改动行数，字级高亮', async () => {
    await mount([session({ changed: true, diff: DIFF })])
    const rows = qa('[data-diff-row]')
    expect(rows.map((r) => r.dataset.diffRow)).toEqual(['gap', 'ctx', 'del', 'add', 'add', 'ctx'])
    expect(rows[0].textContent).toBe(ai('diff.unchanged', { count: 40 }))
    expect(rows[2].textContent).toContain('42')
    expect(rows[3].textContent).toContain('42')
    expect(rows[4].textContent).toContain('43')
    expect(rows[2].querySelector('[data-diff-word]')!.textContent).toBe('linear')
    expect(rows[3].querySelector('[data-diff-word]')!.textContent).toBe('log')
  })

  it('文件头可收起 / 展开行', async () => {
    await mount([session({ changed: true, diff: DIFF })])
    const toggle = q<HTMLButtonElement>('[data-ai-diff-toggle]')!
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    await act(async () => toggle.click())
    expect(qa('[data-diff-row]')).toHaveLength(0)
  })

  it('复制补丁：整段 diff 进剪贴板', async () => {
    await mount([session({ changed: true, diff: DIFF })])
    await act(async () => q<HTMLButtonElement>('[data-ai-diff-copy]')!.click())
    expect(writeText).toHaveBeenCalledWith(DIFF)
  })
})

describe('parseUnifiedDiff', () => {
  it('两段 hunk 之间的间隔由旧行号算出；无逗号的 hunk 头按 1 行', () => {
    const rows = parseUnifiedDiff(
      ['@@ -1 +1 @@', '-a', '+b', '@@ -10,2 +10,2 @@', ' x', '-y', '+z', ''].join('\n'),
    )
    expect(rows.map((r) => (r.kind === 'gap' ? `gap${r.count}` : `${r.kind}${r.no}`))).toEqual([
      'del1',
      'add1',
      'gap8',
      'ctx10',
      'del11',
      'add11',
    ])
  })

  it('hunk 里以「--」开头的删除行不是文件头', () => {
    const rows = parseUnifiedDiff(['--- a.py', '+++ a.py', '@@ -1,1 +1,1 @@', '--- old', '+++ new', ''].join('\n'))
    expect(rows.map((r) => r.kind)).toEqual(['del', 'add'])
  })

  // 前后缀按 UTF-16 码元比：𝛼/𝛽、😀/🙀 共用高代理位，𝐀(U+1D400)/🐀(U+1F400) 共用低代理位——边界不得落在代理对中间
  const LONE = /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/
  const pieces = (oldLine: string, newLine: string) => {
    const rows = parseUnifiedDiff(['@@ -1 +1 @@', `-${oldLine}`, `+${newLine}`, ''].join('\n')) as LineRow[]
    return rows.map((r) => {
      expect(r.pair).toBeDefined()
      const [a, b] = r.pair!
      return [r.text.slice(0, a), r.text.slice(a, b), r.text.slice(b)]
    })
  }
  it.each([
    ['x = 𝛼 + 1', 'x = 𝛽 + 1', [['x = ', '𝛼', ' + 1'], ['x = ', '𝛽', ' + 1']]],
    ['label="😀 ok"', 'label="🙀 ok"', [['label="', '😀', ' ok"'], ['label="', '🙀', ' ok"']]],
    ['a = "𝐀"', 'a = "🐀"', [['a = "', '𝐀', '"'], ['a = "', '🐀', '"']]],
    ['f(👨‍👩‍👧)', 'f(👨‍👩‍👦)', [['f(', '👨‍👩‍👧', ')'], ['f(', '👨‍👩‍👦', ')']]],
  ])('字级高亮的边界不劈开字：%s → %s', (oldLine, newLine, expected) => {
    const got = pieces(oldLine, newLine)
    for (const piece of got.flat()) expect(LONE.test(piece)).toBe(false)
    expect(got).toEqual(expected)
  })
})

describe('代码块（§6.3）', () => {
  let mroot: Root
  let mhost: HTMLDivElement
  const render = async (text: string, streaming = false) => {
    mhost = document.createElement('div')
    document.body.appendChild(mhost)
    mroot = createRoot(mhost)
    await act(async () => mroot.render(<TooltipProvider><Markdown text={text} streaming={streaming} /></TooltipProvider>))
  }
  afterEach(async () => {
    await act(async () => mroot?.unmount())
  })

  it('有语言标签、语法色、复制（✓ 后换回）', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    await render('```python\nfrom x import y\nax.set_yscale("log")  # note\n```')
    const block = mhost.querySelector<HTMLElement>('[data-ai-code]')!
    expect(block.dataset.aiCode).toBe('python')
    expect(block.className).toContain('rounded-lg')
    expect(block.className).not.toMatch(/\bborder\b/)
    const kinds = Array.from(block.querySelectorAll<HTMLElement>('[data-syntax]')).map((s) => s.dataset.syntax)
    expect(kinds).toEqual(expect.arrayContaining(['keyword', 'function', 'string', 'comment']))
    expect(block.querySelectorAll('[data-code-line]')).toHaveLength(2)
    const copy = block.querySelector<HTMLButtonElement>('[data-ai-code-copy]')!
    await act(async () => copy.click())
    expect(writeText).toHaveBeenCalledWith('from x import y\nax.set_yscale("log")  # note')
    expect(copy.dataset.copied).toBe('true')
    await act(async () => {
      vi.advanceTimersByTime(COPIED_MS + 10)
    })
    expect(copy.dataset.copied).toBeUndefined()
  })

  it('没写语言的围栏块也是代码块（此前落进了行内代码的样式）', async () => {
    await render('```\nprint(1)\n```')
    const block = mhost.querySelector<HTMLElement>('[data-ai-code]')!
    expect(block.dataset.aiCode).toBe('plain')
    expect(block.querySelector('[data-syntax="builtin"]')!.textContent).toBe('print')
  })

  it('流式时代码块不被逐词切开（整块交给高亮），正文照样逐词', async () => {
    await render('正文 words\n\n```python\nx = 1\n```', true)
    expect(mhost.querySelectorAll(`[data-ai-code] .${STREAM_WORD_CLASS}`)).toHaveLength(0)
    expect(mhost.querySelectorAll(`p .${STREAM_WORD_CLASS}`).length).toBeGreaterThan(0)
  })

  it('行内代码 box-decoration-clone；链接 accent（助手转录的例外）', async () => {
    await render('用 `ax.set_yscale` 见 [文档](https://matplotlib.org)')
    expect(mhost.querySelector('code')!.className).toContain('box-decoration-clone')
    expect(mhost.querySelector('[data-ai-link]')!.className).toContain('text-accent')
  })
})

describe('任务历史是弹层（§6.6）', () => {
  it('点历史钮：弹出历史列表，对话流仍在原处', async () => {
    await mount([session()])
    await act(async () => {
      q<HTMLButtonElement>('[data-ai-history-trigger]')!.click()
      await new Promise<void>((r) => setTimeout(r, 0))
    })
    expect(document.querySelector('[data-ai-history]')).toBeTruthy()
    expect(vi.mocked(fetchAiHistory)).toHaveBeenCalled()
    // 弹层不在面板子树里（portal），面板里的转录没有被一层盖住
    expect(host.contains(document.querySelector('[data-ai-history]'))).toBe(false)
    expect(q('[data-ai-transcript]')).toBeTruthy()
    await act(async () => document.querySelector<HTMLButtonElement>('[data-ai-history-close]')!.click())
    expect(document.querySelector('[data-ai-history]')).toBeNull()
  })
})
