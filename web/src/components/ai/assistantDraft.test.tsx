/**
 * 助手输入框的草稿与「提交可靠性」界面（ADR 0132）。store 侧的状态机在 `store/aiSubmission.test.ts`；
 * 这里钉组件持有的那一半：
 *   * 右栏切页签会卸载助手面板——草稿落盘，重挂读回；刷新同理；
 *   * 草稿按「项目 × 面板文件」分键：切项目 / 切面板不串稿；
 *   * 「已发出未确认」：普通发送 / 重试一律禁用，只给「查询结果」；查到没收到才给「重新发送」（同一个 rid）；
 *   * 失败会话的「重试」遇到 `changed` 先二次确认。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { aiRun, ApiError, fetchAiRequest, fetchAiSession } from '@/lib/api'
import { DRAFT_MAX_BYTES } from '@/lib/aiDraft'
import { createDraftBackend } from '@/test/aiDraftBackend'
import { RETRY_MS, WRITE_DEBOUNCE_MS } from './useAiDraft'
import { setCurrentProjectId } from '@/lib/session'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { agentCaps, capsOf } from '@/components/settings/testCaps'
import { useAiStore, type AiSession } from '@/store/aiStore'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { AssistantPanel } from './AiPanel'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  aiRun: vi.fn(),
  fetchAiRequest: vi.fn(),
  fetchAiSession: vi.fn(),
}))

Element.prototype.scrollIntoView ??= function scrollIntoView() {}
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let root: Root | null = null
let host: HTMLDivElement

const mount = async () => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root!.render(
      <TooltipProvider>
        <AssistantPanel />
      </TooltipProvider>,
    )
  })
  await settle() // 草稿是异步从后端读回来的
}
/** 右栏切页签 = 卸载助手面板 */
const unmount = async () => {
  await act(async () => root!.unmount())
  root = null
  host.remove()
}

const q = <T extends Element = HTMLElement>(sel: string) => host.querySelector<T>(sel)
const input = () => q<HTMLTextAreaElement>('[data-ai-input]')!
const typeText = async (text: string) => {
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!
  await act(async () => {
    setter.call(input(), text)
    input().dispatchEvent(new Event('input', { bubbles: true }))
  })
}
const click = async (el: Element | null) => {
  expect(el, '要点的按钮不在').toBeTruthy()
  await act(async () => (el as HTMLElement).click())
}
const settle = async () => {
  for (let i = 0; i < 20; i++) await act(async () => Promise.resolve())
}

const panelOf = (fileId: string, id: string): PanelObject =>
  ({
    id, type: 'panel', x: 0, y: 0, w: 100, h: 75,
    fileId, fileKind: 'pdf', nativeW: 100, nativeH: 75,
    name: fileId, script: `${id}.py`, overrides: [],
  }) as unknown as PanelObject

const select = async (id: string) => {
  await act(async () => useSelectionStore.setState({ ids: [id] } as never))
}

const failedSession = (over: Partial<AiSession> = {}): AiSession => ({
  id: 's1', project: 'pa', agent: 'codex', agentLabel: 'Codex', prompt: '加粗线条',
  script: 'fig1.py', panelId: 'p1', fileId: 'Fig1.pdf', gid: null, scope: 'figure',
  target: '整张图', entries: [], status: 'failed', changed: false, diff: '', startedAt: 1,
  finishedAt: 2, finalized: true, ...over,
})

let be: ReturnType<typeof createDraftBackend>
const SENT = { scope: 'figure', gid: null, label: null, target: '整张图' }

beforeEach(async () => {
  be = createDraftBackend()
  globalThis.fetch = (async (url: unknown, init?: RequestInit) =>
    be.handle(String(url), init) ?? new Response('{}')) as typeof fetch
  localStorage.clear()
  setCurrentProjectId('pa')
  vi.mocked(aiRun).mockReset()
  vi.mocked(fetchAiRequest).mockReset()
  vi.mocked(fetchAiSession).mockReset()
  vi.mocked(fetchAiSession).mockRejectedValue(new ApiError('nf', 404, {})) // 后端明确没有详情：凭摘要往下走
  useUiStore.setState({ status: null, confirm: null, elementPanelId: null, selectedGids: [] } as never)
  useAiStore.setState({
    sessions: [], submissions: {}, scope: 'figure',
    caps: capsOf([agentCaps()]), agent: 'codex', models: {}, efforts: {},
  })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_ai_draft')
  useDocumentStore.setState((s) => ({ doc: { ...s.doc, objects: [panelOf('Fig1.pdf', 'p1'), panelOf('Fig2.pdf', 'p2'), { ...panelOf('Fig3.pdf', 'p3'), script: 'p1.py' } as PanelObject] } }) as never)
  useSelectionStore.setState({ ids: ['p1'] } as never)
})
afterEach(async () => {
  if (root) await act(async () => root!.unmount())
  root = null
  document.body.innerHTML = ''
})

describe('草稿存后端：落盘与恢复', () => {
  it('打字 → 切页签（卸载）→ 切回（重挂）：草稿还在，并说一句「已恢复」', async () => {
    await mount()
    await typeText('把图例移到左上角')
    await unmount() // 卸载时 keepalive 冲刷
    await settle()
    expect(be.draft('pa', 'Fig1.pdf')).toBe('把图例移到左上角')
    await mount()
    expect(input().value).toBe('把图例移到左上角')
    expect(q('[data-ai-draft-note="restored"]')).toBeTruthy()
    await typeText('把图例移到左上角，再大一点')
    expect(q('[data-ai-draft-note]')).toBeNull() // 一动手就收
  })

  it('换端口（换 origin）也不丢：浏览器存储清空后重挂，草稿仍从后端读回', async () => {
    await mount()
    await typeText('写了一半的指令')
    await unmount()
    await settle()
    localStorage.clear()
    sessionStorage.clear() // 端口一变，这些全是空的新 origin
    await mount()
    expect(input().value).toBe('写了一半的指令')
    // 整个过程没往浏览器存储里写过任何东西
    expect(localStorage.length).toBe(0)
  })

  it('防抖 600 ms：停手前不发请求；到点只发一次 PUT；pagehide 立刻 keepalive 冲刷', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      await mount()
      const puts = () => be.calls.filter((c) => c.method === 'PUT' && c.path === '/api/ai/drafts')
      await typeText('第一')
      await typeText('第一句')
      await act(async () => void vi.advanceTimersByTime(WRITE_DEBOUNCE_MS - 50))
      expect(puts()).toHaveLength(0) // 对照：防抖期内没写
      await act(async () => void vi.advanceTimersByTime(100))
      await settle()
      expect(puts()).toHaveLength(1) // 两次输入合并成一次
      expect(be.draft('pa', 'Fig1.pdf')).toBe('第一句')
      expect(puts()[0].keepalive).toBe(false)
      await typeText('第二句')
      await act(async () => window.dispatchEvent(new Event('pagehide')))
      await settle()
      expect(puts()).toHaveLength(2)
      expect(puts()[1].keepalive).toBe(true) // 页面正在关：keepalive
      expect(be.draft('pa', 'Fig1.pdf')).toBe('第二句')
    } finally {
      vi.useRealTimers()
    }
  })

  it('后端暂时不可用：文本留在内存里，3 秒后重试，恢复后写成；期间不丢', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      await mount()
      be.setDown(true)
      await typeText('断网时写的话')
      await act(async () => void vi.advanceTimersByTime(WRITE_DEBOUNCE_MS + 10))
      await settle()
      expect(be.draft('pa', 'Fig1.pdf')).toBeUndefined() // 没写成
      expect(input().value).toBe('断网时写的话') // 内存里还在
      await act(async () => void vi.advanceTimersByTime(RETRY_MS + 10))
      await settle()
      expect(be.draft('pa', 'Fig1.pdf')).toBeUndefined() // 重试仍失败，不抛、继续留着
      be.setDown(false)
      await act(async () => void vi.advanceTimersByTime(RETRY_MS + 10))
      await settle()
      expect(be.draft('pa', 'Fig1.pdf')).toBe('断网时写的话')
    } finally {
      vi.useRealTimers()
    }
  })

  it('切面板：旧面板的稿存下、新面板读它自己的；切回来找得到，不串稿', async () => {
    await mount()
    await typeText('给 Fig1 的话')
    await select('p2')
    await settle()
    expect(input().value).toBe('') // Fig2 没有稿
    await typeText('给 Fig2 的话')
    await select('p1')
    await settle()
    expect(input().value).toBe('给 Fig1 的话')
    await select('p2')
    await settle()
    expect(input().value).toBe('给 Fig2 的话')
  })

  it('切项目：B 看不到 A 的稿（对照：切回 A 找得回）', async () => {
    await mount()
    await typeText('A 项目的话')
    setCurrentProjectId('pb')
    await act(async () => useAiStore.getState().clear()) // 面板按代际重挂
    await settle()
    expect(input().value).toBe('')
    expect(be.draft('pb', 'Fig1.pdf')).toBeUndefined()
    setCurrentProjectId('pa')
    await act(async () => useAiStore.getState().clear())
    await settle()
    expect(input().value).toBe('A 项目的话')
  })

  it('刷新后挂载那一刻项目认领还没到（null）：不读不写，认领到了才读', async () => {
    be.put('pa', 'Fig1.pdf', '上次没发出的话')
    setCurrentProjectId(null)
    await mount()
    expect(input().value).toBe('')
    expect(be.calls).toEqual([]) // 没认领 = 一个请求都不发（不会落进后端的默认项目）
    setCurrentProjectId('pa')
    await settle()
    expect(input().value).toBe('上次没发出的话')
  })

  it('多标签页：窗口重新获得焦点时以后端为准再读一次；本地有没写出去的改动时不覆盖', async () => {
    await mount()
    await typeText('这个标签页的话')
    await act(async () => window.dispatchEvent(new Event('pagehide')))
    await settle()
    be.put('pa', 'Fig1.pdf', '另一个标签页后来写的话') // 另一个标签页改了
    await act(async () => window.dispatchEvent(new Event('focus')))
    await settle()
    expect(input().value).toBe('另一个标签页后来写的话') // 对照：没有本地改动时后端为准
    await typeText('我又接着写') // 本地有 dirty
    be.put('pa', 'Fig1.pdf', '另一个标签页又写了')
    await act(async () => window.dispatchEvent(new Event('focus')))
    await settle()
    expect(input().value).toBe('我又接着写') // 最后写入为准：不被覆盖
  })

  it('超过 20 KB：给「过长」提示；后端 413 后不存、不截断', async () => {
    await mount()
    await typeText('图'.repeat(Math.ceil(DRAFT_MAX_BYTES / 3) + 1))
    expect(q('[data-ai-draft-note="too-large"]')).toBeTruthy()
    await act(async () => window.dispatchEvent(new Event('pagehide')))
    await settle()
    expect(be.draft('pa', 'Fig1.pdf')).toBeUndefined()
  })
})

describe('同步状态机：单一 reconcile + 写入版本（#1098）', () => {
  const OWN = { rid: 'rid-own-0000001', text: '本面板发出的话', sent: SENT, script: 'p1.py' }
  const CROSS = { rid: 'rid-cross-000001', text: '同脚本别的面板发出的话', sent: SENT, script: 'p1.py' }
  const ORPHAN = { rid: 'rid-orphan-00001', text: '已删面板发出的话', sent: SENT, script: 'p1.py' }
  const OTHER = { rid: 'rid-other-000001', text: '别的脚本的话', sent: SENT, script: 'zzz.py' }
  const seed = () => {
    be.pendings.set('pa\u0000Fig1.pdf', [OWN])
    be.pendings.set('pa\u0000Fig3.pdf', [CROSS])
    be.pendings.set('pa\u0000Gone.pdf', [ORPHAN])
    be.pendings.set('pa\u0000Fig2.pdf', [OTHER])
    be.put('pa', 'Fig1.pdf', '服务器上的草稿')
  }
  const rids = () => Object.keys(useAiStore.getState().submissions).sort()

  it('场景一：B 标签页先挂载、A 之后提交且丢了响应——B 聚焦时 pendings（含跨面板）被还原，发送被锁，重发不会换新 rid', async () => {
    await mount() // 此刻后端什么都没有
    expect(q('[data-ai-submission]')).toBeNull()
    seed() // 标签页 A 发出了请求（本面板 + 同脚本别的面板 + 已删面板）
    await act(async () => window.dispatchEvent(new Event('focus')))
    await settle()
    expect(rids()).toEqual(['rid-cross-000001', 'rid-orphan-00001', 'rid-own-0000001']) // 别的脚本的不混进来
    expect(q('[data-ai-submission="unconfirmed"]')).toBeTruthy()
    expect(q<HTMLButtonElement>('[data-ai-send]')!.disabled).toBe(true)
    await typeText('在 B 上再说一遍')
    await act(async () => {
      input().dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', metaKey: true, bubbles: true }))
    })
    expect(aiRun).not.toHaveBeenCalled() // 没有用新的请求 id 再发一次
  })

  it('场景二：PUT 在途时聚焦触发的 GET 拿到旧值——不覆盖输入框；PUT 落地后界面仍是新的，下次按键写的是新文本', async () => {
    await mount()
    const release = be.holdDraftPuts()
    await typeText('第一版')
    await act(async () => window.dispatchEvent(new Event('pagehide'))) // 冲刷：PUT 在途，后端还没收到
    await settle()
    expect(be.draft('pa', 'Fig1.pdf')).toBeUndefined()
    await act(async () => window.dispatchEvent(new Event('focus'))) // GET 读到的是旧值（空）
    await settle()
    expect(input().value).toBe('第一版') // 对照：旧实现在这里把输入框盖成了旧值
    release()
    await settle()
    expect(be.draft('pa', 'Fig1.pdf')).toBe('第一版')
    expect(input().value).toBe('第一版')
    await typeText('第一版加字')
    await act(async () => window.dispatchEvent(new Event('pagehide')))
    await settle()
    expect(be.draft('pa', 'Fig1.pdf')).toBe('第一版加字') // 没有把旧文本写回去
  })

  it('PUT 在途时本地又有更新的写入：防抖到点时因为在途而被跳过，落地后以本地为准补写一次（不靠再来一次防抖）', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      await mount()
      const release = be.holdDraftPuts()
      await typeText('v1')
      await act(async () => window.dispatchEvent(new Event('pagehide'))) // v1 在途
      await settle()
      await typeText('v1 又改成 v2') // 在途期间本地更新，防抖开始计时
      await act(async () => void vi.advanceTimersByTime(WRITE_DEBOUNCE_MS + 50)) // 到点：在途中，跳过
      await settle()
      expect(be.draft('pa', 'Fig1.pdf')).toBeUndefined()
      release() // 落地之后不会再有任何计时器——v2 只能靠「落地时发现本地更新、补写」
      await settle()
      expect(be.draft('pa', 'Fig1.pdf')).toBe('v1 又改成 v2')
      expect(input().value).toBe('v1 又改成 v2')
    } finally {
      vi.useRealTimers()
    }
  })

  it('已被接受的文本，其草稿 PUT 若迟于确认才落地：落地后被条件删掉，不会作为草稿复活', async () => {
    vi.mocked(aiRun).mockResolvedValue({ session: 's-1', script: 'p1.py' })
    await mount()
    const release = be.holdDraftPuts()
    await typeText('要发出去的话')
    await act(async () => window.dispatchEvent(new Event('pagehide'))) // 草稿 PUT 在途
    await settle()
    await click(q('[data-ai-send="send"]')) // 发出、被接受 → 清输入框
    await settle()
    expect(input().value).toBe('')
    release() // 迟到的 PUT 现在才落地
    await settle()
    expect(be.draft('pa', 'Fig1.pdf')).toBeUndefined() // 没有复活成「已恢复的草稿」
  })

  it('账本还在读的那一段不许发（原任务可能早已结束、后端忙碌守卫已失效）：读完、pendings 还原之后仍锁；读空才放开', async () => {
    be.pendings.set('pa\u0000Fig1.pdf', [OWN])
    const release = be.holdReads()
    await mount() // 对账在读（被扣住）
    expect(q<HTMLButtonElement>('[data-ai-send]')!.disabled).toBe(true) // 读着：不许发
    await typeText('刷新后立刻粘贴的指令')
    await act(async () => {
      input().dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', metaKey: true, bubbles: true }))
    })
    expect(aiRun).not.toHaveBeenCalled() // 对照：旧实现这里 canSend 为真，会用新 rid 发出去
    release()
    await settle()
    expect(q('[data-ai-submission="unconfirmed"]')).toBeTruthy() // 读完：账本里的未结提交还原，仍锁
    await act(async () => {
      input().dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', metaKey: true, bubbles: true }))
    })
    expect(aiRun).not.toHaveBeenCalled()
  })

  it('账本读完且确实为空：放开发送（对照）', async () => {
    vi.mocked(aiRun).mockResolvedValue({ session: 's-1', script: 'p1.py' })
    const release = be.holdReads()
    await mount()
    await typeText('正常的指令')
    expect(q<HTMLButtonElement>('[data-ai-send]')!.disabled).toBe(true)
    release()
    await settle()
    expect(q<HTMLButtonElement>('[data-ai-send]')!.disabled).toBe(false)
    await click(q('[data-ai-send="send"]'))
    await settle()
    expect(aiRun).toHaveBeenCalledTimes(1)
  })

  it('账本读失败 = 不知道（不是空账本）：继续锁发送，一句话 + 重试；重试读成功才放开', async () => {
    be.setDown(true)
    await mount()
    expect(q('[data-ai-ledger="failed"]')).toBeTruthy()
    expect(q<HTMLButtonElement>('[data-ai-send]')!.disabled).toBe(true)
    await typeText('读失败时想发的指令')
    await act(async () => {
      input().dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', metaKey: true, bubbles: true }))
    })
    expect(aiRun).not.toHaveBeenCalled()
    be.setDown(false)
    await click(q('[data-ai-ledger-retry]'))
    await settle()
    expect(q('[data-ai-ledger]')).toBeNull()
    expect(q<HTMLButtonElement>('[data-ai-send]')!.disabled).toBe(false)
  })

  it('只有项目级账本列表读失败（草稿读成功）：同样是「不知道」，不当成空列表放行', async () => {
    be.setPendingListFails(true)
    await mount()
    expect(q('[data-ai-ledger="failed"]')).toBeTruthy()
    expect(q<HTMLButtonElement>('[data-ai-send]')!.disabled).toBe(true)
    be.setPendingListFails(false)
    await click(q('[data-ai-ledger-retry]'))
    await settle()
    expect(q('[data-ai-ledger]')).toBeNull()
  })

  it('成功对账过之后，一次读失败不把账本状态降回去（不平白锁住发送）', async () => {
    await mount()
    expect(q<HTMLButtonElement>('[data-ai-send]')!.disabled).toBe(true) // 没文字
    await typeText('x')
    expect(q<HTMLButtonElement>('[data-ai-send]')!.disabled).toBe(false)
    be.setDown(true)
    await act(async () => window.dispatchEvent(new Event('focus')))
    await settle()
    expect(q('[data-ai-ledger]')).toBeNull()
    expect(q<HTMLButtonElement>('[data-ai-send]')!.disabled).toBe(false)
  })

  it('没有未落定的本地写入时，对账读回的文本照常应用（另一个标签页的新稿进来）', async () => {
    await mount()
    be.put('pa', 'Fig1.pdf', '别的标签页写的')
    await act(async () => window.dispatchEvent(new Event('focus')))
    await settle()
    expect(input().value).toBe('别的标签页写的')
  })

  it('读失败（后端暂时不可用）不是「服务器说空」：输入框不被清掉', async () => {
    await mount()
    await typeText('写到一半')
    await act(async () => window.dispatchEvent(new Event('pagehide')))
    await settle()
    be.setDown(true)
    await act(async () => window.dispatchEvent(new Event('focus')))
    await settle()
    expect(input().value).toBe('写到一半')
  })

  it('结构性：挂载 / 聚焦 / 可见 / SSE 重连四个入口走同一条路径——同样的服务器状态得到完全相同的还原结果', async () => {
    const entries: Record<string, () => Promise<void>> = {
      mount: async () => {},
      focus: async () => {
        await act(async () => window.dispatchEvent(new Event('focus')))
      },
      visible: async () => {
        await act(async () => document.dispatchEvent(new Event('visibilitychange')))
      },
      reconnect: async () => {
        await act(async () => window.dispatchEvent(new Event('mm:sse-open')))
      },
    }
    const outcomes: Record<string, { rids: string[]; text: string }> = {}
    for (const [name, trigger] of Object.entries(entries)) {
      useAiStore.setState({ submissions: {}, sessions: [] })
      if (root) await unmount()
      be.pendings.clear()
      be.drafts.clear()
      if (name === 'mount') seed() // 挂载：状态在挂载前就在
      await mount()
      if (name !== 'mount') {
        seed() // 其余入口：挂载之后状态才出现，靠各自的入口把它取回来
        await trigger()
      }
      await settle()
      outcomes[name] = { rids: rids(), text: input().value }
    }
    expect(outcomes.mount.rids).toEqual(['rid-cross-000001', 'rid-orphan-00001', 'rid-own-0000001'])
    expect(outcomes.mount.text).toBe('服务器上的草稿')
    for (const name of ['focus', 'visible', 'reconnect']) expect(outcomes[name], name).toEqual(outcomes.mount)
  })

  it('结构性（源码）：全部入口都调 reconcile，且服务器状态只在 reconcile 里读（没有只取文本的第二条路）', async () => {
    const src = (await import('./useAiDraft.ts?raw')).default as string
    expect(src.match(/fetchDraftState\(/g)).toHaveLength(1)
    expect(src.match(/loadProjectPendings\(/g)).toHaveLength(1)
    expect(src).not.toMatch(/\bloadDraft\(/)
    for (const source of ['mount', 'focus', 'visible', 'reconnect']) expect(src).toContain(`reconcile('${source}')`)
  })
})

describe('已发出未确认：不能盲目重发', () => {
  beforeEach(() => {
    vi.mocked(aiRun).mockRejectedValue(new TypeError('Failed to fetch'))
  })

  const sendIt = async (text = '把图例移到左上角') => {
    await typeText(text)
    await click(q('[data-ai-send="send"]'))
    await settle()
  }

  it('超时/断网后：出「查询结果」，没有普通重试；发送钮禁用；待确认的原文在那一行里、输入框仍是草稿', async () => {
    await mount()
    await sendIt()
    expect(q('[data-ai-submission="unconfirmed"]')).toBeTruthy()
    expect(q('[data-ai-check]')).toBeTruthy()
    expect(q('[data-ai-retry]')).toBeNull()
    expect(q('[data-ai-error="send"]')).toBeNull() // 不是「发送失败」
    expect(q<HTMLButtonElement>('[data-ai-send]')!.disabled).toBe(true)
    expect(q('[data-ai-submission-text]')?.textContent).toBe('把图例移到左上角') // 待确认的那条在行里展示
    expect(input().value).toBe('把图例移到左上角') // 草稿还在（它是另一份状态）
    expect(aiRun).toHaveBeenCalledTimes(1)
    // ⌘↵ 也发不出去
    await act(async () => {
      input().dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', metaKey: true, bubbles: true }))
    })
    expect(aiRun).toHaveBeenCalledTimes(1)
  })

  it('刷新后（草稿里的 rid）恢复成未确认，不是一条可以普通重发的草稿', async () => {
    be.pendings.set('pa\u0000Fig1.pdf', [{ rid: 'rid-restore-0001', text: '加粗线条', sent: SENT }])
    await mount()
    expect(q('[data-ai-submission="unconfirmed"]')).toBeTruthy()
    expect(q('[data-ai-submission-text]')?.textContent).toBe('加粗线条') // 待确认原文在行里
    expect(input().value).toBe('') // 没有草稿 = 输入框是空的（待确认原文不冒充草稿）
    expect(q<HTMLButtonElement>('[data-ai-send]')!.disabled).toBe(true)
  })

  it('两个标签页：A 待确认、B 之后存了更新的草稿——重载后输入框是 B；A 被确认后仍是 B；再按键保存的是 B + 新字符', async () => {
    // 标签页 A：点发送（账本里是 A 的原文）；标签页 B：随后存了更新的未发草稿
    be.pendings.set('pa\u0000Fig1.pdf', [{ rid: 'rid-two-tabs-001', text: 'A 发出去的话', sent: SENT, script: 'p1.py' }])
    be.put('pa', 'Fig1.pdf', 'B 之后写的新草稿')
    await mount()
    expect(input().value).toBe('B 之后写的新草稿') // 对照：旧实现在这里用 A 覆盖了输入框
    expect(q('[data-ai-submission-text]')?.textContent).toBe('A 发出去的话')
    // A 被查询到、已被后端接受
    vi.mocked(fetchAiRequest).mockResolvedValue({ session: 's-5', status: 'running', changed: false, script: 'p1.py' })
    await click(q('[data-ai-check]'))
    await settle()
    expect(q('[data-ai-submission]')).toBeNull()
    expect(input().value).toBe('B 之后写的新草稿') // 确认只清待确认，不碰草稿
    expect(be.draft('pa', 'Fig1.pdf')).toBe('B 之后写的新草稿') // 后端也保留了 B
    // 再按一个键：保存的是 B + 新字符，不是被覆盖的空 / A
    await typeText('B 之后写的新草稿！')
    await act(async () => window.dispatchEvent(new Event('pagehide')))
    await settle()
    expect(be.draft('pa', 'Fig1.pdf')).toBe('B 之后写的新草稿！')
  })

  it('待确认期间输入框可编辑（草稿是草稿）；确认后只有「仍等于提交原文」才清输入框', async () => {
    await mount()
    await sendIt('同一句话')
    expect(input().readOnly).toBe(false)
    await typeText('同一句话，又补了几个字') // 用户在等待里继续写
    vi.mocked(fetchAiRequest).mockResolvedValue({ session: 's-6', status: 'running', changed: false, script: 'p1.py' })
    await click(q('[data-ai-check]'))
    await settle()
    expect(input().value).toBe('同一句话，又补了几个字') // 对照：旧的 clearIf 只比本地文本，这里不等所以不清
  })

  it('查询：后端没收到 → 出「重新发送」，用【同一个】rid；成功后清输入框与草稿', async () => {
    await mount()
    await sendIt()
    const rid = (vi.mocked(aiRun).mock.calls[0][0] as { client_request_id: string }).client_request_id
    vi.mocked(fetchAiRequest).mockRejectedValue(new ApiError('nf', 404, { code: 'ai_request_not_found' }))
    await click(q('[data-ai-check]'))
    await settle()
    expect(q('[data-ai-submission="not_received"]')).toBeTruthy()
    expect(q('[data-ai-check]')).toBeNull()
    vi.mocked(aiRun).mockResolvedValue({ session: 's-9', script: 'fig1.py' })
    await click(q('[data-ai-resend-same]'))
    await settle()
    expect(aiRun).toHaveBeenCalledTimes(2)
    expect((vi.mocked(aiRun).mock.calls[1][0] as { client_request_id: string }).client_request_id).toBe(rid)
    expect(input().value).toBe('')
    expect(be.draft('pa', 'Fig1.pdf')).toBeUndefined()
    expect(be.pending('pa', 'Fig1.pdf')).toEqual([])
    expect(useAiStore.getState().sessions.map((s) => s.id)).toEqual(['s-9'])
  })

  it('查询：后端已登记 → 进会话、不再发；联系不上 → 说「稍后再查」且仍禁用', async () => {
    await mount()
    await sendIt()
    vi.mocked(fetchAiRequest).mockRejectedValue(new TypeError('down'))
    await click(q('[data-ai-check]'))
    await settle()
    expect(q('[data-ai-submission="unconfirmed"]')?.textContent).toContain('联系不上')
    expect(q('[data-ai-resend-same]')).toBeNull()

    vi.mocked(fetchAiRequest).mockResolvedValue({ session: 's-5', status: 'running', changed: false, script: 'fig1.py' })
    await click(q('[data-ai-check]'))
    await settle()
    expect(aiRun).toHaveBeenCalledTimes(1)
    expect(useAiStore.getState().sessions.map((s) => s.id)).toEqual(['s-5'])
    expect(q('[data-ai-submission]')).toBeNull()
    expect(input().value).toBe('')
  })

  it('查询：后端说「已执行过、记录已删」→ 说一句、清输入框、不出现重新发送，也不再发', async () => {
    await mount()
    await sendIt()
    vi.mocked(fetchAiRequest).mockResolvedValue({ session: 's-5', status: 'history_deleted', changed: false, script: null as never })
    await click(q('[data-ai-check]'))
    await settle()
    expect(aiRun).toHaveBeenCalledTimes(1)
    expect(q('[data-ai-submission]')).toBeNull()
    expect(q('[data-ai-resend-same]')).toBeNull()
    expect(input().value).toBe('')
    expect(useUiStore.getState().status).not.toBeNull()
  })

  it('同一脚本的另一个面板：A 面板未确认时切到 B，提示还在、发送仍锁住（按脚本判）', async () => {
    await mount()
    await sendIt()
    expect(aiRun).toHaveBeenCalledTimes(1)
    await select('p3') // Fig3 与 Fig1 共用脚本 p1.py
    await settle()
    expect(q('[data-ai-submission="unconfirmed"]')).toBeTruthy()
    expect(q<HTMLButtonElement>('[data-ai-send]')!.disabled).toBe(true)
    await typeText('在 B 面板上再说一遍')
    await act(async () => {
      input().dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', metaKey: true, bubbles: true }))
    })
    expect(aiRun).toHaveBeenCalledTimes(1)
    // 对照：别的脚本的面板（p2）不受影响
    await select('p2')
    await settle()
    expect(q('[data-ai-submission]')).toBeNull()
  })

  it('刷新后在兄弟面板上挂载：A 面板的待确认账本也还原，发送仍锁住', async () => {
    be.pendings.set('pa\u0000Fig1.pdf', [{ rid: 'rid-sibling-0001', text: 'A 面板发出的话', sent: SENT, script: 'p1.py' }])
    useSelectionStore.setState({ ids: ['p3'] } as never)
    await mount()
    expect(q('[data-ai-submission="unconfirmed"]')).toBeTruthy()
    expect(q<HTMLButtonElement>('[data-ai-send]')!.disabled).toBe(true)
  })

  it('发起请求的面板已不在（删了 / 换图）：同脚本的面板上仍还原这条未确认，说明来源不在了，且能查询', async () => {
    // Gone.pdf 这个面板文档里没有；它的账本带着 script，与 p3（脚本 p1.py）同脚本
    be.pendings.set('pa\u0000Gone.pdf', [{ rid: 'rid-gone-000001', text: '孤儿面板发出的话', sent: SENT, script: 'p1.py' }])
    be.pendings.set('pa\u0000Other.pdf', [{ rid: 'rid-other-000001', text: '别的脚本的话', sent: SENT, script: 'zzz.py' }])
    useSelectionStore.setState({ ids: ['p3'] } as never)
    await mount()
    const notice = q('[data-ai-submission="unconfirmed"]')
    expect(notice).toBeTruthy()
    expect(notice!.textContent).toContain('已不在画布')
    expect(q<HTMLButtonElement>('[data-ai-send]')!.disabled).toBe(true)
    expect(Object.keys(useAiStore.getState().submissions)).toEqual(['rid-gone-000001']) // 别的脚本的不混进来
    // 能查：后端已登记 → 进会话、不再发
    vi.mocked(fetchAiRequest).mockResolvedValue({ session: 's-5', status: 'running', changed: false, script: 'p1.py' })
    await click(q('[data-ai-check]'))
    await settle()
    expect(aiRun).not.toHaveBeenCalled()
    expect(useAiStore.getState().sessions.map((x) => x.id)).toEqual(['s-5'])
  })

  it('账本写不进后端：不发、一句话 + 重试；后端恢复后重试才发', async () => {
    vi.mocked(aiRun).mockResolvedValue({ session: 's-1', script: 'p1.py' })
    be.setPendingWriteFails(true)
    await mount()
    await typeText('把线条加粗一点')
    await click(q('[data-ai-send="send"]'))
    await settle()
    expect(aiRun).not.toHaveBeenCalled()
    expect(q('[data-ai-error="send"]')?.textContent).toContain('没有发出')
    expect(q('[data-ai-submission]')).toBeNull()
    expect(input().readOnly).toBe(false)
    be.setPendingWriteFails(false)
    await click(q('[data-ai-retry]'))
    await settle()
    expect(aiRun).toHaveBeenCalledTimes(1)
  })

  it('「改写后再发」：放弃这条等待、输入框可改，下一次发送是新 rid', async () => {
    await mount()
    await sendIt('旧指令')
    const rid1 = (vi.mocked(aiRun).mock.calls[0][0] as { client_request_id: string }).client_request_id
    vi.mocked(fetchAiRequest).mockRejectedValue(new ApiError('nf', 404, { code: 'ai_request_not_found' }))
    await click(q('[data-ai-check]'))
    await settle()
    await click(q('[data-ai-edit-resend]'))
    expect(input().readOnly).toBe(false)
    vi.mocked(aiRun).mockResolvedValue({ session: 's-3', script: 'fig1.py' })
    await typeText('新指令')
    await click(q('[data-ai-send="send"]'))
    await settle()
    const rid2 = (vi.mocked(aiRun).mock.calls[1][0] as { client_request_id: string }).client_request_id
    expect(rid2).not.toBe(rid1)
  })

  it('明确失败（409 busy）：是「没有发出去」+ 普通重试，不是未确认', async () => {
    vi.mocked(aiRun).mockRejectedValue(new ApiError('busy', 409, { code: 'ai_script_busy', params: { script: 'fig1.py' } }))
    await mount()
    await sendIt()
    expect(q('[data-ai-error="send"]')).toBeTruthy()
    expect(q('[data-ai-submission]')).toBeNull()
    expect(input().readOnly).toBe(false)
    expect(input().value).toBe('把图例移到左上角') // 文本留着
  })
})

describe('失败会话的「重试」遇到 changed', () => {
  const retryBtn = () => q('[data-ai-error="session"] [data-ai-retry]')

  it('changed=true：先弹确认；取消 → 不发；确认 → 才发（新任务）', async () => {
    useAiStore.setState({ sessions: [failedSession({ changed: true })] })
    vi.mocked(aiRun).mockResolvedValue({ session: 's-2', script: 'fig1.py' })
    await mount()
    await click(retryBtn())
    const req = useUiStore.getState().confirm
    expect(req, '应该弹确认').toBeTruthy()
    expect(aiRun).not.toHaveBeenCalled()
    await act(async () => {
      useUiStore.getState().setConfirm(null)
      req!.resolve(false)
    })
    await settle()
    expect(aiRun).not.toHaveBeenCalled()

    await click(retryBtn())
    const req2 = useUiStore.getState().confirm
    await act(async () => {
      useUiStore.getState().setConfirm(null)
      req2!.resolve(true)
    })
    await settle()
    expect(aiRun).toHaveBeenCalledTimes(1)
  })

  it('对照 changed=false：照旧直接重试，不打扰', async () => {
    useAiStore.setState({ sessions: [failedSession({ changed: false })] })
    vi.mocked(aiRun).mockResolvedValue({ session: 's-2', script: 'fig1.py' })
    await mount()
    await click(retryBtn())
    await settle()
    expect(useUiStore.getState().confirm).toBeNull()
    expect(aiRun).toHaveBeenCalledTimes(1)
  })
})
