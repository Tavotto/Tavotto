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

import '@/hooks/useServerEvents' // 注册会话收尾提示（setAiSettledNotifier）
import { aiRun, ApiError, fetchAiRequest, fetchAiSession } from '@/lib/api'
import { __resetWriteStampForTests, DRAFT_MAX_BYTES, INSTRUCTION_MAX_BYTES } from '@/lib/aiDraft'
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
  __resetWriteStampForTests() // 每条用例都是「新开的页面」：写入戳下限从 0 起步
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

  it('写入戳：A 在途时 keepalive 发出 B，B 先落地、A 后落地——服务器最终保留 B，重开显示 B（旧请求赢在服务端不可能发生）', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      await mount()
      const releaseA = be.holdNextDraftPut() // 只扣住下一个 PUT（A）
      await typeText('A 版')
      await act(async () => void vi.advanceTimersByTime(WRITE_DEBOUNCE_MS + 50)) // 防抖到点：PUT A 在途
      await settle()
      await typeText('B 版（更新）')
      await act(async () => window.dispatchEvent(new Event('pagehide'))) // keepalive 冲刷：PUT B 并发发出，先落地
      await settle()
      expect(be.draft('pa', 'Fig1.pdf')).toBe('B 版（更新）')
      releaseA() // A 现在才落地：戳更旧，必须被拒
      await settle()
      expect(be.draft('pa', 'Fig1.pdf')).toBe('B 版（更新）') // 对照：没有写入戳时这里会被 A 覆盖
      expect(input().value).toBe('B 版（更新）')
      // 重开面板：显示 B
      await unmount()
      await settle()
      await mount()
      expect(input().value).toBe('B 版（更新）')
    } finally {
      vi.useRealTimers()
    }
  })

  it('墙钟回拨 / 新开页面（lastStamp 从 0 起步）：挂载读到的服务器戳抬高下限，用户的输入第一次写就被接受，不丢', async () => {
    be.putStamped('pa', 'Fig1.pdf', '库里的旧稿', Date.now() * 1000 + 5_000_000_000) // 库里的戳比本机墙钟大（回拨）
    await mount() // 对账的 GET 读到这个戳 → 抬高本页下限
    await typeText('用户这次真实输入的内容')
    await act(async () => window.dispatchEvent(new Event('pagehide')))
    await settle()
    expect(be.draft('pa', 'Fig1.pdf')).toBe('用户这次真实输入的内容')
    expect(be.calls.filter((c) => c.method === 'PUT' && c.path === '/api/ai/drafts')).toHaveLength(1) // 一次就被接受，没经过 stale
    expect(input().value).toBe('用户这次真实输入的内容')
  })

  it('对账还没读回来就输入、第一次写被判 stale（墙钟回拨）：本地是用户真实输入的，以本地为准用更大的戳重写，不采纳服务器的旧值', async () => {
    be.putStamped('pa', 'Fig1.pdf', '库里的旧稿', Date.now() * 1000 + 5_000_000_000)
    const release = be.holdReads() // 对账的读被扣住：本页还不知道服务器戳
    await mount()
    await typeText('用户这次真实输入的内容')
    await act(async () => window.dispatchEvent(new Event('pagehide'))) // 第一次写：戳比库里的小 → stale → 抬高下限 → 重写
    await settle()
    expect(be.draft('pa', 'Fig1.pdf')).toBe('用户这次真实输入的内容') // 对照：旧实现采纳服务器旧值、盖掉输入
    release()
    await settle()
    expect(input().value).toBe('用户这次真实输入的内容')
    expect(be.calls.filter((c) => c.method === 'PUT' && c.path === '/api/ai/drafts').length).toBeGreaterThanOrEqual(2)
  })

  it('被判 stale 时本地之后又有更新的内容：再写一次（新戳大于服务器的），以本地为准', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      await mount()
      const releaseA = be.holdNextDraftPut()
      await typeText('A 版')
      await act(async () => void vi.advanceTimersByTime(WRITE_DEBOUNCE_MS + 50)) // PUT A 在途
      await settle()
      be.putStamped('pa', 'Fig1.pdf', '别的标签页的稿', Date.now() * 1000 + 5_000_000_000) // A 落地前，别的标签页写了更新的戳
      await typeText('B 版（A 之后本地又改的）')
      releaseA() // A 被拒（stale）；本地有更新的内容 → 以本地为准再写一次
      await settle()
      await act(async () => void vi.advanceTimersByTime(WRITE_DEBOUNCE_MS + 50))
      await settle()
      expect(be.draft('pa', 'Fig1.pdf')).toBe('B 版（A 之后本地又改的）')
      expect(input().value).toBe('B 版（A 之后本地又改的）')
    } finally {
      vi.useRealTimers()
    }
  })

  it('发送前先把草稿写成功并把草稿戳记进账本：run 发出那一刻后端已有这份草稿，账本带着它的戳', async () => {
    let seenAtRun: { draft?: string; stamp: number | null | undefined } | null = null
    vi.mocked(aiRun).mockImplementation(async () => {
      seenAtRun = { draft: be.draft('pa', 'Fig1.pdf'), stamp: be.pendings.get('pa\u0000Fig1.pdf')?.[0]?.draftStamp }
      return { session: 's-1', script: 'p1.py' }
    })
    await mount()
    await typeText('刚打完就发')
    await click(q('[data-ai-send="send"]')) // 防抖还没到点就点发送
    await settle()
    expect(seenAtRun).toMatchObject({ draft: '刚打完就发' })
    expect(typeof seenAtRun!.stamp).toBe('number') // 账本里记了草稿戳
  })

  it('提交文本是 trim 过的、草稿是原样（前后带空白 / 换行）：被接受后输入框清空、后端草稿也清掉——比的是戳，不是文本', async () => {
    vi.mocked(aiRun).mockResolvedValue({ session: 's-1', script: 'p1.py' })
    await mount()
    await typeText('  把线条加粗一点\n\n')
    await click(q('[data-ai-send="send"]'))
    await settle()
    expect((vi.mocked(aiRun).mock.calls[0][0] as { prompt: string }).prompt).toBe('把线条加粗一点') // 发的是 trim 后的
    expect(input().value).toBe('') // 对照：旧实现按文本比，原样草稿对不上，已被接受的指令留在输入框里
    expect(be.draft('pa', 'Fig1.pdf')).toBeUndefined()
    expect(be.pending('pa', 'Fig1.pdf')).toEqual([])
  })

  describe('发送来源显式标明：失败后的重试仍走草稿快照（#1098）', () => {
    const ledgerStamp = () => be.pendings.get('pa\u0000Fig1.pdf')?.[0]?.draftStamp
    const expectAcceptedAndDraftCleared = async (stampAtRun: unknown) => {
      expect(typeof stampAtRun).toBe('number') // 账本记的是草稿戳，不是 null
      expect(input().value).toBe('') // 被接受后草稿清掉（旧实现 stamp 为 null → 草稿清不掉，已执行的指令还能再发）
      expect(be.draft('pa', 'Fig1.pdf')).toBeUndefined()
      expect(be.pending('pa', 'Fig1.pdf')).toEqual([])
    }

    it('账本写失败后点重试：账本里记的是草稿戳，被接受后草稿被清', async () => {
      let stampAtRun: unknown = null
      vi.mocked(aiRun).mockImplementation(async () => {
        stampAtRun = ledgerStamp()
        return { session: 's-1', script: 'p1.py' }
      })
      await mount()
      be.setPendingWriteFails(true)
      await typeText('把线条加粗一点')
      await click(q('[data-ai-send="send"]'))
      await settle()
      expect(aiRun).not.toHaveBeenCalled()
      expect(q('[data-ai-error="send"]')).toBeTruthy()
      be.setPendingWriteFails(false)
      await click(q('[data-ai-retry]'))
      await settle()
      expect(aiRun).toHaveBeenCalledTimes(1)
      await expectAcceptedAndDraftCleared(stampAtRun)
    })

    it('run 回 409 后点重试：同样记草稿戳，被接受后草稿被清', async () => {
      let stampAtRun: unknown = null
      vi.mocked(aiRun)
        .mockRejectedValueOnce(new ApiError('busy', 409, { code: 'ai_script_busy', params: { script: 'p1.py' } }))
        .mockImplementation(async () => {
          stampAtRun = ledgerStamp()
          return { session: 's-1', script: 'p1.py' }
        })
      await mount()
      await typeText('把线条加粗一点')
      await click(q('[data-ai-send="send"]'))
      await settle()
      expect(q('[data-ai-error="send"]')).toBeTruthy()
      await click(q('[data-ai-retry]'))
      await settle()
      expect(aiRun).toHaveBeenCalledTimes(2)
      await expectAcceptedAndDraftCleared(stampAtRun)
    })

    it('失败后输入框被改过再点重试：按失败的那段原文发（history 来源），不碰草稿——用户新写的草稿不被当成「已发送」清掉', async () => {
      let stampAtRun: unknown = 'unset'
      vi.mocked(aiRun)
        .mockRejectedValueOnce(new ApiError('busy', 409, { code: 'ai_script_busy', params: { script: 'p1.py' } }))
        .mockImplementation(async () => {
          stampAtRun = ledgerStamp()
          return { session: 's-1', script: 'p1.py' }
        })
      await mount()
      await typeText('第一版指令')
      await click(q('[data-ai-send="send"]'))
      await settle()
      await typeText('失败之后我又写了新的草稿')
      await click(q('[data-ai-retry]'))
      await settle()
      expect((vi.mocked(aiRun).mock.calls[1][0] as { prompt: string }).prompt).toBe('第一版指令') // 重试的是失败的那一条
      expect(stampAtRun).toBeNull() // history 来源：不带草稿戳
      expect(input().value).toBe('失败之后我又写了新的草稿')
      await act(async () => window.dispatchEvent(new Event('pagehide')))
      await settle()
      expect(be.draft('pa', 'Fig1.pdf')).toBe('失败之后我又写了新的草稿')
    })

    it('从历史会话重发（气泡「重新发送」）：不碰草稿，账本不带草稿戳', async () => {
      let stampAtRun: unknown = 'unset'
      vi.mocked(aiRun).mockImplementation(async () => {
        stampAtRun = ledgerStamp()
        return { session: 's-2', script: 'p1.py' }
      })
      useAiStore.setState({ sessions: [failedSession({ id: 's-old', prompt: '历史里的旧指令', status: 'done' })] })
      await mount()
      await typeText('我正在输入框里写的草稿')
      await click(q('[data-ai-resend]'))
      await settle()
      expect((vi.mocked(aiRun).mock.calls[0][0] as { prompt: string }).prompt).toBe('历史里的旧指令')
      expect(stampAtRun).toBeNull()
      expect(input().value).toBe('我正在输入框里写的草稿') // 草稿原封不动
      await act(async () => window.dispatchEvent(new Event('pagehide')))
      await settle()
      expect(be.draft('pa', 'Fig1.pdf')).toBe('我正在输入框里写的草稿')
    })

    it('快照拿不到戳（草稿 PUT 5xx）：composer 发送中止——不发 run、不写账本，一句话 + 重试，输入框原样；恢复后重试才发', async () => {
      vi.mocked(aiRun).mockResolvedValue({ session: 's-1', script: 'p1.py' })
      await mount()
      await typeText('把线条加粗一点')
      be.setDraftPutFails(true)
      await click(q('[data-ai-send="send"]'))
      await settle()
      expect(aiRun).not.toHaveBeenCalled() // 对照：旧实现 stamp 为 null 仍继续 start()，被接受后草稿清不掉、同一条指令还能再发
      expect(be.pendings.get('pa\u0000Fig1.pdf') ?? []).toEqual([]) // 账本也没写
      expect(q('[data-ai-error="send"]')?.textContent).toContain('没有发出')
      expect(q('[data-ai-submission]')).toBeNull()
      expect(input().value).toBe('把线条加粗一点')
      be.setDraftPutFails(false)
      await click(q('[data-ai-retry]'))
      await settle()
      expect(aiRun).toHaveBeenCalledTimes(1)
      expect(input().value).toBe('')
      expect(be.draft('pa', 'Fig1.pdf')).toBeUndefined()
    })

    it('指令超过上限：输入时就提示并禁用发送（不等发送时才失败）；恰好等于上限可以发', async () => {
      vi.mocked(aiRun).mockResolvedValue({ session: 's-1', script: 'p1.py' })
      await mount()
      await typeText('a'.repeat(INSTRUCTION_MAX_BYTES + 1))
      expect(q('[data-ai-draft-note="too-large"]')?.textContent).toContain('指令太长')
      expect(q<HTMLButtonElement>('[data-ai-send]')!.disabled).toBe(true)
      await act(async () => {
        input().dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', metaKey: true, bubbles: true }))
      })
      await settle()
      expect(aiRun).not.toHaveBeenCalled()
      expect(be.pendings.get('pa\u0000Fig1.pdf') ?? []).toEqual([])
      // 对照：恰好等于上限
      await typeText('a'.repeat(INSTRUCTION_MAX_BYTES))
      expect(q('[data-ai-draft-note="too-large"]')).toBeNull()
      expect(q<HTMLButtonElement>('[data-ai-send]')!.disabled).toBe(false)
      await click(q('[data-ai-send="send"]'))
      await settle()
      expect(aiRun).toHaveBeenCalledTimes(1)
    })

    it('结构性（源码）：发送的每个调用点都显式标明来源，没有无参 / 裸文本调用', async () => {
      const src = (await import('./AiPanel.tsx?raw')).default as string
      const calls = src.match(/\bsend\(\s*[^)]*\)/g)!.filter((c) => !c.includes('req: SendRequest'))
      expect(calls.length).toBeGreaterThanOrEqual(5)
      for (const c of calls) expect(c, c).toMatch(/source:\s*'(composer|history)'/)
    })
  })

  it('提交之后、被接受之前用户又改了草稿：确认只清账本，新写的留着（输入框与后端都是新的）', async () => {
    let resolveRun!: (v: { session: string; script: string }) => void
    vi.mocked(aiRun).mockImplementation(() => new Promise((r) => (resolveRun = r)))
    await mount()
    await typeText('要发的话')
    await click(q('[data-ai-send="send"]'))
    await settle() // 账本已写、run 在路上
    await typeText('要发的话，等待期间又补了一句') // 等待里继续写（输入框可编辑）
    resolveRun({ session: 's-1', script: 'p1.py' })
    await settle()
    await act(async () => window.dispatchEvent(new Event('pagehide')))
    await settle()
    expect(input().value).toBe('要发的话，等待期间又补了一句')
    expect(be.draft('pa', 'Fig1.pdf')).toBe('要发的话，等待期间又补了一句')
  })

  it.each([
    ['SSE 重连', () => window.dispatchEvent(new Event('mm:sse-open'))],
    ['窗口重新获得焦点', () => window.dispatchEvent(new Event('focus'))],
    ['页面回到前台', () => document.dispatchEvent(new Event('visibilitychange'))],
  ])('accept 之后任务在 SSE 断线期间结束：%s 的对账按快照把本地 running 会话收尾成终态，带 refresh 警告，发送解锁', async (_name, trigger) => {
    vi.mocked(aiRun).mockResolvedValue({ session: 's-1', script: 'p1.py' })
    await mount()
    await typeText('把线条加粗一点')
    await click(q('[data-ai-send="send"]'))
    await settle()
    expect(q('[data-ai-session="running"]')).toBeTruthy() // accept 之后是 running，账本已清
    expect(q('[data-ai-send="stop"]')).toBeTruthy()
    // SSE 断开期间任务结束了（事件流不重放）；后端的会话详情已是终态
    vi.mocked(fetchAiSession).mockResolvedValue({
      agent: 'codex', status: 'done', changed: true, diff: 'D', transcript: [], refresh: { status: 'failed', code: 'scan_failed' },
    })
    await act(async () => trigger())
    await settle()
    expect(q('[data-ai-session="running"]')).toBeNull() // 对照：旧实现只重读 pendings，会话永远停在 running
    expect(q('[data-ai-session="done"]')).toBeTruthy()
    expect(JSON.stringify(useUiStore.getState().status)).toContain('aiChangedRefreshFailed')
    await typeText('再来一条')
    expect(q<HTMLButtonElement>('[data-ai-send]')!.disabled).toBe(false) // 发送解锁
  })

  it('读不到快照 / 快照仍是 running：本地会话不动（不把「查不到」当结束）', async () => {
    vi.mocked(aiRun).mockResolvedValue({ session: 's-1', script: 'p1.py' })
    await mount()
    await typeText('把线条加粗一点')
    await click(q('[data-ai-send="send"]'))
    await settle()
    vi.mocked(fetchAiSession).mockRejectedValue(new TypeError('down'))
    await act(async () => window.dispatchEvent(new Event('mm:sse-open')))
    await settle()
    expect(q('[data-ai-session="running"]')).toBeTruthy()
    vi.mocked(fetchAiSession).mockResolvedValue({ status: 'running', changed: false, diff: '', transcript: [] })
    await act(async () => window.dispatchEvent(new Event('mm:sse-open')))
    await settle()
    expect(q('[data-ai-session="running"]')).toBeTruthy()
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
