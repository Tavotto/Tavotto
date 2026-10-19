/**
 * 助手输入框的草稿与「提交可靠性」界面（ADR 0132）。store 侧的状态机在 `store/aiSubmission.test.ts`；
 * 这里钉组件持有的那一半：
 *   * 右栏切页签会卸载助手面板——草稿落盘，重挂读回；刷新同理；
 *   * 草稿按「项目 × 面板文件」分键：切项目 / 切面板不串稿；
 *   * 「已发出未确认」：普通发送 / 重试一律禁用，只给「查询结果」；查到没收到才给「重新发送」（同一个 rid）；
 *   * 失败会话的「重试」遇到 `changed` 先二次确认。
 *
 * **结构性不变式一律用行为验，不数源码**（仓库纪律：判源码结构用 AST，判不出就别判）：入口同路径 = 五个入口的
 * 还原结果与向后端发的请求集合逐项相同；发送来源 = 四个触发各自的账本戳 / 草稿去向。
 * 盲点（写在明处）：新增第六个对账入口若不加进上面的入口矩阵，这里测不到它是否绕开 `reconcile`；
 * 新增第五个发送触发同理。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import '@/hooks/useServerEvents' // 注册会话收尾提示（setAiSettledNotifier）
import { aiRun, ApiError, fetchAiRequest, fetchAiSession } from '@/lib/api'
import { __resetWriteStampForTests, DRAFT_MAX_BYTES, INSTRUCTION_MAX_BYTES } from '@/lib/aiDraft'
import { createDraftBackend } from '@/test/aiDraftBackend'
import { DRAFT_LOAD_TIMEOUT_MS, RETRY_MS, WRITE_DEBOUNCE_MS } from './useAiDraft'
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

  it('点发送后等草稿写成功的途中又改了输入框：不发（发的文本与快照的版本号 / 戳会指向两份稿），途中打的字留着；重试按当前输入框发', async () => {
    vi.mocked(aiRun).mockResolvedValue({ session: 's-1', script: 'p1.py' })
    await mount()
    await typeText('把线条加粗')
    const release = be.holdDraftPuts()
    await click(q('[data-ai-send="send"]')) // 发送先等草稿写成功：PUT 被扣住
    await typeText('把线条加粗，再改成虚线') // 途中继续打字
    release()
    await settle()
    expect(aiRun).not.toHaveBeenCalled()
    expect(q('[data-ai-error="send"]')?.textContent).toContain('发送途中改过了')
    expect(input().value).toBe('把线条加粗，再改成虚线') // 途中打的字没丢
    await click(q('[data-ai-retry]'))
    await settle()
    expect(aiRun).toHaveBeenCalledTimes(1)
    expect((vi.mocked(aiRun).mock.calls[0][0] as { prompt: string }).prompt).toBe('把线条加粗，再改成虚线')
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

  it('墙钟回拨 + 对账读还没回来就想输入：读完之前输入框只读（输入不被收），读完后输入，戳已被读到的库里戳抬高 → 第一次写就被接受，服务器最终是用户的内容', async () => {
    be.putStamped('pa', 'Fig1.pdf', '库里的旧稿', Date.now() * 1000 + 5_000_000_000)
    const release = be.holdReads() // 对账的读被扣住：本页还不知道服务器戳
    await mount()
    expect(input().readOnly).toBe(true) // 结构上：没读到服务器版本之前不许编辑
    expect(input().placeholder).toBe('正在载入草稿…')
    await typeText('读完之前打的字')
    expect(input().value).toBe('') // 没被收下
    release()
    await settle()
    expect(input().readOnly).toBe(false)
    expect(input().placeholder).not.toBe('正在载入草稿…')
    await typeText('用户这次真实输入的内容') // 读到的库里戳已抬高本页下限
    await act(async () => window.dispatchEvent(new Event('pagehide')))
    await settle()
    expect(be.draft('pa', 'Fig1.pdf')).toBe('用户这次真实输入的内容') // 对照：旧实现采纳服务器旧值、盖掉输入
    expect(input().value).toBe('用户这次真实输入的内容')
    // 读完才能输入 → 本页分配的戳已被读到的库里戳抬高 → 第一次写就被接受，没有 stale 窗口
    expect(be.calls.filter((c) => c.method === 'PUT' && c.path === '/api/ai/drafts')).toHaveLength(1)
  })

  it('A 在途、别的标签页保存了更新的 B、本页对账读到了 B（界面没采纳）：A 的 stale 回来仍按 A 自己的基线判——放弃，B 保留，不被盖掉', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      await mount()
      const releaseA = be.holdNextDraftPut()
      await typeText('本页的稿 A')
      await act(async () => void vi.advanceTimersByTime(WRITE_DEBOUNCE_MS + 50)) // PUT A 在途
      await settle()
      be.putStamped('pa', 'Fig1.pdf', '别的标签页的稿 B', Date.now() * 1000 + 5_000_000_000)
      await act(async () => window.dispatchEvent(new Event('focus'))) // 对账把 B 的戳读进来；本地有未落定写入，文本不覆盖
      await settle()
      releaseA() // A 的 stale 回来：全局「见过 B 的戳」不再算数——A 编辑时不知道 B
      await settle()
      await act(async () => void vi.advanceTimersByTime(RETRY_MS * 3))
      await settle()
      expect(putsOf()).toHaveLength(1) // A 没有被提升重写
      expect(be.draft('pa', 'Fig1.pdf')).toBe('别的标签页的稿 B') // 对照：全局 seen 集合时这里被 A 盖掉
      expect(input().value).toBe('别的标签页的稿 B')
    } finally {
      vi.useRealTimers()
    }
  })

  it('跨标签页冲突放弃本页未落定的版本、输入框被换成另一个窗口的稿：出一句提示「草稿已被另一个窗口的修改替换」，下一次编辑后消失', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      await mount()
      const releaseA = be.holdNextDraftPut()
      await typeText('本页刚输入的字')
      await act(async () => void vi.advanceTimersByTime(WRITE_DEBOUNCE_MS + 50)) // PUT 在途
      await settle()
      be.putStamped('pa', 'Fig1.pdf', '另一个窗口的稿', Date.now() * 1000 + 5_000_000_000)
      releaseA() // stale：别处写了我们编辑时不知道的更新稿 → 放弃本页的版本，对账采纳
      await settle()
      expect(input().value).toBe('另一个窗口的稿')
      expect(q('[data-ai-draft-note="replaced"]')?.textContent).toBe('草稿已被另一个窗口的修改替换') // 对照：以前是静默替换
      await typeText('我接着写') // 下一次编辑
      expect(q('[data-ai-draft-note="replaced"]')).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })

  it('本页有延迟编辑 A、别处保存了 B、本页对账读到 B 但没应用、用户接着输入：新版本的基线仍是旧的——stale 时被放弃，B 保留', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      await mount()
      await typeText('A1（防抖还没到）')
      be.putStamped('pa', 'Fig1.pdf', '别处保存的 B', Date.now() * 1000 + 5_000_000_000)
      await act(async () => window.dispatchEvent(new Event('focus'))) // 对账读到 B，但本地有未落定的版本：不应用
      await settle()
      expect(input().value).toBe('A1（防抖还没到）')
      // 对账读到 B 的戳会抬高本页的写入戳下限；这里把下限放回去，模拟「B 的戳比本页之后分配的戳更新」（时钟领先的写入者）——
      // 这样 A2 才会收到 stale，才量得出它的基线
      __resetWriteStampForTests()
      await typeText('A2（接着输入）') // 对照：读取就推进基线时，A2 把 B 的戳当基线，stale 时盖掉 B
      await act(async () => void vi.advanceTimersByTime(WRITE_DEBOUNCE_MS + 50))
      await settle()
      await act(async () => void vi.advanceTimersByTime(RETRY_MS * 3))
      await settle()
      expect(be.draft('pa', 'Fig1.pdf')).toBe('别处保存的 B')
      expect(input().value).toBe('别处保存的 B')
    } finally {
      vi.useRealTimers()
    }
  })

  it('冲突放弃了本页的版本，但另一个窗口的稿恰好和本页输入的一样：输入框里的字没变，不出提示', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      await mount()
      const releaseA = be.holdNextDraftPut()
      await typeText('一样的字')
      await act(async () => void vi.advanceTimersByTime(WRITE_DEBOUNCE_MS + 50))
      await settle()
      be.putStamped('pa', 'Fig1.pdf', '一样的字', Date.now() * 1000 + 5_000_000_000)
      releaseA()
      await settle()
      expect(input().value).toBe('一样的字')
      expect(q('[data-ai-draft-note="replaced"]')).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })

  it('冲突放弃之后紧接的冲突对账失败：「替换待发生」标记保留；之后 focus 的对账用另一窗口的稿覆盖可见文本时照样提示', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      await mount()
      const releaseA = be.holdNextDraftPut()
      await typeText('本页刚输入的字')
      await act(async () => void vi.advanceTimersByTime(WRITE_DEBOUNCE_MS + 50)) // PUT 在途
      await settle()
      be.putStamped('pa', 'Fig1.pdf', '另一个窗口的稿', Date.now() * 1000 + 5_000_000_000)
      be.setDown(true) // 紧接的冲突对账会失败
      releaseA()
      await settle()
      expect(input().value).toBe('本页刚输入的字') // 对账没成功：输入框还没被换
      expect(q('[data-ai-draft-note="replaced"]')).toBeNull()
      be.setDown(false)
      await act(async () => window.dispatchEvent(new Event('focus'))) // 之后的对账成功，换进另一个窗口的稿
      await settle()
      expect(input().value).toBe('另一个窗口的稿')
      expect(q('[data-ai-draft-note="replaced"]')?.textContent).toBe('草稿已被另一个窗口的修改替换') // 对照：以前标记绑在失败的那次回调上，这里没有提示
    } finally {
      vi.useRealTimers()
    }
  })

  it('超长文本（413）：hook 卸载后槽里的被拒文本仍在——重开面板拿回原文、仍显示「太长」提示、没有后续 PUT；对账不用服务器的空墓碑覆盖它；改短后走新版本', async () => {
    const big = '长'.repeat(INSTRUCTION_MAX_BYTES) // UTF-8 下远超上限
    await mount()
    await typeText(big)
    await unmount() // 防抖没到就卸载：冲刷一次 → 后端 413，留空墓碑
    await settle()
    expect(be.draft('pa', 'Fig1.pdf')).toBeUndefined() // 服务端什么都没存
    const putsBefore = putsOf().length
    await mount() // 重开面板
    expect(input().value).toBe(big) // 对照：以前槽被清掉，重开读到空墓碑，超长文字丢了
    expect(q('[data-ai-draft-note="too-large"]')).toBeTruthy()
    await act(async () => window.dispatchEvent(new Event('focus'))) // 再对账也不覆盖
    await settle()
    expect(input().value).toBe(big)
    expect(putsOf()).toHaveLength(putsBefore) // 没有后续 fetch：被拒的版本不再重试
    // 用户改短：正常走新版本
    await typeText('改短了的指令')
    await act(async () => window.dispatchEvent(new Event('pagehide')))
    await settle()
    expect(be.draft('pa', 'Fig1.pdf')).toBe('改短了的指令')
    expect(q('[data-ai-draft-note="too-large"]')).toBeNull()
  })

  it('没有冲突时不出这句提示（正常保存、对账一致）', async () => {
    await mount()
    await typeText('正常写的字')
    await act(async () => window.dispatchEvent(new Event('pagehide')))
    await settle()
    expect(q('[data-ai-draft-note="replaced"]')).toBeNull()
  })

  it('首读失败（不知道服务器版本）：允许编辑；之后写被判 stale 按「已见」处理，以本地为准重写——用户的输入不丢', async () => {
    be.putStamped('pa', 'Fig1.pdf', '库里的旧稿', Date.now() * 1000 + 5_000_000_000)
    be.setDown(true) // 首读失败
    await mount()
    await settle()
    expect(input().readOnly).toBe(false) // 读失败不锁输入框
    await typeText('读不到服务器时打的字')
    be.setDown(false) // 恢复，之后第一次写：戳比库里的小 → stale，库里的戳本页没见过，但基线未知 → 按已见
    await act(async () => window.dispatchEvent(new Event('pagehide')))
    await settle()
    expect(be.draft('pa', 'Fig1.pdf')).toBe('读不到服务器时打的字')
    expect(input().value).toBe('读不到服务器时打的字')
  })

  it('读成功过一次、卸载后重开时读失败：仍退出 loading、允许编辑（发件箱槽位留着 read=true 也不能让文本框一直只读）', async () => {
    await mount()
    await settle()
    expect(input().readOnly).toBe(false)
    await unmount()
    be.setDown(true)
    await mount()
    await settle()
    expect(input().readOnly).toBe(false)
  })

  // —— 发件箱：未落定的写入由模块级发件箱持有，与 hook 实例 / 挂载的目标无关 ——
  const putsOf = () => be.calls.filter((c) => c.method === 'PUT' && c.path === '/api/ai/drafts')

  it('发件箱：防抖未到就卸载、keepalive 写失败——退役实例之后，发件箱自己重试到成功，重开显示新文本', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      await mount()
      be.setDraftPutFails(true)
      await typeText('防抖没到就切走的新文本')
      await unmount() // 卸载：冲刷一次（失败）；本实例到此为止
      await settle()
      expect(be.draft('pa', 'Fig1.pdf')).toBeUndefined()
      be.setDraftPutFails(false)
      await act(async () => void vi.advanceTimersByTime(RETRY_MS + 50)) // 没有任何实例在了：发件箱自己重试
      await settle()
      expect(be.draft('pa', 'Fig1.pdf')).toBe('防抖没到就切走的新文本') // 对照：旧实现重试只存在于已退役的实例里，这里永远是旧值
      await mount()
      expect(input().value).toBe('防抖没到就切走的新文本')
    } finally {
      vi.useRealTimers()
    }
  })

  it('发件箱：切走后马上切回，输入框显示发件箱里的版本（不是服务器上的旧值），对账也不覆盖它', async () => {
    be.putStamped('pa', 'Fig1.pdf', '服务器上的旧值', 1)
    await mount()
    expect(input().value).toBe('服务器上的旧值')
    be.setDraftPutFails(true)
    await typeText('刚打的新版本')
    await unmount()
    await settle()
    await mount() // 立即切回：PUT 仍在失败
    expect(input().value).toBe('刚打的新版本')
    await act(async () => window.dispatchEvent(new Event('focus'))) // 再对账一次也不许覆盖
    await settle()
    expect(input().value).toBe('刚打的新版本')
    expect(be.draft('pa', 'Fig1.pdf')).toBe('服务器上的旧值')
  })

  it('发件箱：同一目标的新版本取代旧版本，旧版本不再重试（恢复后只写最新的）', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      await mount()
      be.setDraftPutFails(true)
      await typeText('v1 旧版本')
      await unmount() // v1 冲刷失败，留在发件箱
      await settle()
      await mount()
      await typeText('v2 新版本') // 取代 v1
      be.setDraftPutFails(false)
      const before = putsOf().length
      await act(async () => void vi.advanceTimersByTime(RETRY_MS * 3))
      await settle()
      expect(be.draft('pa', 'Fig1.pdf')).toBe('v2 新版本')
      expect(putsOf().slice(before).map((c) => c.body.text)).not.toContain('v1 旧版本') // 恢复之后没有一次是 v1
    } finally {
      vi.useRealTimers()
    }
  })

  describe.each([
    ['草稿 GET 和账本 GET 都挂住', 'all'],
    ['只有草稿 GET 挂住', 'draft'],
    ['只有账本 GET 挂住', 'list'],
  ] as const)('对账读取超时（半开连接）：%s', (_name, scope) => {
    it('5 秒后按读失败处理：ledgerState 变 failed、重试提示出现、发送仍锁；点重试成功后解锁；迟到的旧响应被丢弃', async () => {
      vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
      try {
        const release = be.holdReads(scope)
        await mount() // 挂载时发起对账，读被扣住
        expect(q('[data-ai-ledger="failed"]')).toBeNull() // 才开始读：还没到点
        await act(async () => void vi.advanceTimersByTime(DRAFT_LOAD_TIMEOUT_MS - 100))
        await settle()
        expect(q('[data-ai-ledger="failed"]')).toBeNull()
        await act(async () => void vi.advanceTimersByTime(200)) // 超时
        await settle()
        expect(q('[data-ai-ledger="failed"]')).toBeTruthy() // 对照：旧实现 Promise.all 永远挂着，这里永远不出现
        expect(q<HTMLButtonElement>('[data-ai-send]')!.disabled).toBe(true)
        expect(input().readOnly).toBe(false) // 首读失败：输入框不锁
        release() // 挂住的旧读现在才返回：按代次丢弃，不改任何状态
        await settle()
        expect(q('[data-ai-ledger="failed"]')).toBeTruthy()
        await click(q('[data-ai-ledger-retry]')) // 重试：读成功
        await settle()
        expect(q('[data-ai-ledger="failed"]')).toBeNull()
        await typeText('可以发了')
        expect(q<HTMLButtonElement>('[data-ai-send]')!.disabled).toBe(false) // 解锁
      } finally {
        vi.useRealTimers()
      }
    })
  })

  it('草稿 GET 成功而项目级账本 GET 失败：输入框已经显示服务器文本（可写 ⇒ 服务器草稿已应用），打字不会盲写盖掉它', async () => {
    be.putStamped('pa', 'Fig1.pdf', '别的标签页留下的稿', 5)
    be.setPendingListFails(true)
    await mount()
    expect(input().value).toBe('别的标签页留下的稿') // 对照：旧实现提前 return，这里是空框
    expect(q('[data-ai-ledger="failed"]')).toBeTruthy() // 账本没读到：发送仍锁
    await typeText('别的标签页留下的稿，再加一句')
    await act(async () => void (await new Promise((r) => setTimeout(r, WRITE_DEBOUNCE_MS + 50))))
    await settle()
    expect(be.draft('pa', 'Fig1.pdf')).toBe('别的标签页留下的稿，再加一句') // 基于已见的服务器版本写，不是盲写
  })

  it('本页视图过期（别的标签页刚在兄弟面板留下未确认、任务已结束，本页还没对账）就点发送：后端 409 ai_pending_unresolved——新指令不发，那条被还原并按 rid 查询接回会话', async () => {
    useSelectionStore.setState({ ids: ['p3'] } as never)
    await mount()
    await typeText('新指令')
    expect(q<HTMLButtonElement>('[data-ai-send="send"]')!.disabled).toBe(false) // 本页看来可以发
    be.pendings.set('pa\u0000Fig1.pdf', [{ rid: 'rid-othertab-009', text: '别的标签页发出的话', sent: SENT, script: 'p1.py' }])
    vi.mocked(fetchAiRequest).mockResolvedValue({ session: 's-other', status: 'done', changed: true, script: 'p1.py' })
    await click(q('[data-ai-send="send"]'))
    await settle()
    expect(aiRun).not.toHaveBeenCalled() // 同一修改不会执行第二次
    expect(q('[data-ai-error="send"]')?.textContent).toContain('先看它的结果')
    expect(vi.mocked(fetchAiRequest).mock.calls.some((c) => c[0] === 'rid-othertab-009')).toBe(true)
    expect(useAiStore.getState().sessions.map((x) => x.id)).toContain('s-other') // 接回了那次的结果
  })

  it('409 ai_pending_unresolved 之后、本页再读之前，别的标签页已确认并删掉了挡路的那一行：照着 409 带回的行还原并查询，接回会话、不放开发送', async () => {
    useSelectionStore.setState({ ids: ['p3'] } as never)
    await mount()
    await typeText('新指令')
    be.pendings.set('pa\u0000Fig1.pdf', [{ rid: 'rid-othertab-010', text: '别的标签页发出的话', sent: SENT, script: 'p1.py' }])
    be.afterPendingUnresolved(() => be.dropRejectedPending('rid-othertab-010')) // 回 409 的同时，那一行被别的标签页 accept 掉了
    vi.mocked(fetchAiRequest).mockResolvedValue({ session: 's-other', status: 'running', changed: false, script: 'p1.py' })
    await click(q('[data-ai-send="send"]'))
    await settle()
    be.afterPendingUnresolved(null)
    expect(aiRun).not.toHaveBeenCalled()
    expect(vi.mocked(fetchAiRequest).mock.calls.some((c) => c[0] === 'rid-othertab-010')).toBe(true)
    expect(useAiStore.getState().sessions.map((x) => x.id)).toContain('s-other')
    expect(q('[data-ai-send="send"]')).toBeNull() // 那次任务还在跑：只剩「停止」
  })

  it('对账成功过一次之后，草稿读失败而项目级账本读成功：别的标签页新留在兄弟面板上的未确认提交照样还原、发送锁住', async () => {
    useSelectionStore.setState({ ids: ['p3'] } as never)
    await mount()
    await typeText('一条指令')
    expect(q<HTMLButtonElement>('[data-ai-send]')!.disabled).toBe(false)
    be.pendings.set('pa\u0000Fig1.pdf', [{ rid: 'rid-othertab-001', text: '别的标签页发出的话', sent: SENT, script: 'p1.py' }])
    be.setDraftGetFails(true)
    await act(async () => window.dispatchEvent(new Event('focus')))
    await settle()
    expect(Object.keys(useAiStore.getState().submissions)).toEqual(['rid-othertab-001'])
    expect(q<HTMLButtonElement>('[data-ai-send]')!.disabled).toBe(true)
  })

  it('对账成功过一次之后，项目级待确认读失败：ledgerState 降回 failed、发送锁住；下一次成功的项目级读取才解锁', async () => {
    await mount()
    await typeText('一条指令')
    expect(q<HTMLButtonElement>('[data-ai-send]')!.disabled).toBe(false) // 对账成功过：可发
    be.setPendingListFails(true)
    await act(async () => window.dispatchEvent(new Event('focus')))
    await settle()
    expect(q('[data-ai-ledger="failed"]')).toBeTruthy() // 对照：旧实现 ok 不降级，仍可发
    expect(q<HTMLButtonElement>('[data-ai-send]')!.disabled).toBe(true)
    be.setPendingListFails(false)
    await act(async () => window.dispatchEvent(new Event('focus')))
    await settle()
    expect(q('[data-ai-ledger="failed"]')).toBeNull()
    expect(q<HTMLButtonElement>('[data-ai-send]')!.disabled).toBe(false)
  })

  it('发件箱不因时间放弃：后端断开 15 分钟后恢复，编辑不丢（超过任何「重试 N 次就放弃」的上限）；退避有上限（≤ 30 s 一次），不是 3 s 死磕', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      await mount()
      be.setDraftPutFails(true)
      await typeText('断网十五分钟也不能丢的稿')
      for (let i = 0; i < 90; i++) {
        await act(async () => void vi.advanceTimersByTime(10_000)) // 共 15 分钟
        await settle()
      }
      const attempts = putsOf().length
      expect(attempts).toBeGreaterThanOrEqual(28) // 一直在试：最长间隔 30 s，15 分钟约 34 次——没有「试 N 次就放弃」
      expect(attempts).toBeLessThan(45) // 退避：不是每 3 s 一次（那会有 300 次）
      expect(be.draft('pa', 'Fig1.pdf')).toBeUndefined()
      be.setDraftPutFails(false)
      await act(async () => void vi.advanceTimersByTime(30_000 + 50)) // 退避上限之内一定再试
      await settle()
      expect(be.draft('pa', 'Fig1.pdf')).toBe('断网十五分钟也不能丢的稿') // 对照：旧实现约 1 分钟就放弃，这里永远是 undefined
    } finally {
      vi.useRealTimers()
    }
  })

  it('发件箱：网络恢复（online）/ 页面回到可见时立即重试一次，不等退避', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      await mount()
      be.setDraftPutFails(true)
      await typeText('等网络的稿')
      for (let i = 0; i < 12; i++) {
        await act(async () => void vi.advanceTimersByTime(10_000)) // 退避已拉长到 30 s 档
        await settle()
      }
      be.setDraftPutFails(false)
      await act(async () => window.dispatchEvent(new Event('online'))) // 没有推进任何时间
      await settle()
      expect(be.draft('pa', 'Fig1.pdf')).toBe('等网络的稿')
      // 页面回到可见同理
      be.setDraftPutFails(true)
      await typeText('再来一版')
      for (let i = 0; i < 12; i++) {
        await act(async () => void vi.advanceTimersByTime(10_000))
        await settle()
      }
      be.setDraftPutFails(false)
      await act(async () => document.dispatchEvent(new Event('visibilitychange')))
      await settle()
      expect(be.draft('pa', 'Fig1.pdf')).toBe('再来一版')
    } finally {
      vi.useRealTimers()
    }
  })

  it('发件箱：连续失败超过一分钟才在输入框上方出一行安静的灰字；写成功后消失；短暂失败不打扰', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      await mount()
      be.setDraftPutFails(true)
      await typeText('写不进去的稿')
      await act(async () => void vi.advanceTimersByTime(30_000))
      await settle()
      expect(q('[data-ai-draft-note="stalled"]')).toBeNull() // 才失败 30 s：不打扰
      await act(async () => void vi.advanceTimersByTime(35_000)) // 失败的计时从第一次响应回来才开始，再多给一点
      await settle()
      expect(q('[data-ai-draft-note="stalled"]')).toBeNull() // 失败刚满 35 s：还没到一分钟
      await act(async () => void vi.advanceTimersByTime(35_000))
      await settle()
      expect(q('[data-ai-draft-note="stalled"]')?.textContent).toBe('草稿还没保存到本机项目，正在重试')
      be.setDraftPutFails(false)
      await act(async () => window.dispatchEvent(new Event('online')))
      await settle()
      expect(be.draft('pa', 'Fig1.pdf')).toBe('写不进去的稿')
      expect(q('[data-ai-draft-note="stalled"]')).toBeNull() // 成功后消失
    } finally {
      vi.useRealTimers()
    }
  })

  it('发件箱：页面离开（pagehide）对所有未落定的槽发 keepalive，包括没有实例挂载的目标', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      await mount()
      be.setDraftPutFails(true)
      await typeText('关页面时还没写成的稿')
      await unmount()
      await settle()
      be.setDraftPutFails(false)
      await act(async () => window.dispatchEvent(new Event('pagehide'))) // 没有任何实例挂载
      await settle()
      expect(be.draft('pa', 'Fig1.pdf')).toBe('关页面时还没写成的稿') // 不用等 3 s 重试
    } finally {
      vi.useRealTimers()
    }
  })

  it('stale 的服务器戳本页没见过（别的标签页在我们不知情时写了更新的）：本版不提升、不重写，采纳服务器值——A 晚到不盖掉别处保存的 B', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      await mount()
      const releaseA = be.holdNextDraftPut()
      await typeText('A 版（本页先编辑，PUT 晚到）')
      await act(async () => void vi.advanceTimersByTime(WRITE_DEBOUNCE_MS + 50)) // PUT A 在途
      await settle()
      be.putStamped('pa', 'Fig1.pdf', 'B 版（别的标签页之后保存的）', Date.now() * 1000 + 5_000_000_000) // 本页不知道
      releaseA() // A 晚到：stale，服务器戳本页没见过
      await settle()
      await act(async () => void vi.advanceTimersByTime(RETRY_MS * 3 + WRITE_DEBOUNCE_MS))
      await settle()
      const puts = be.calls.filter((c) => c.method === 'PUT' && c.path === '/api/ai/drafts')
      expect(puts).toHaveLength(1) // A 没有换戳重写
      expect(be.draft('pa', 'Fig1.pdf')).toBe('B 版（别的标签页之后保存的）') // B 被保留
      expect(input().value).toBe('B 版（别的标签页之后保存的）') // 本页采纳服务器值
    } finally {
      vi.useRealTimers()
    }
  })

  it('被判 stale 时本地之后又有更新的内容（编辑时都不知道别处的写入）：旧版本不换戳不重写；更新的版本按自己的基线判——服务器戳更新就放弃，别处的稿保留', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      await mount()
      const releaseA = be.holdNextDraftPut()
      await typeText('v1 版')
      await act(async () => void vi.advanceTimersByTime(WRITE_DEBOUNCE_MS + 50)) // PUT v1 在途
      await settle()
      const HIGH = Date.now() * 1000 + 5_000_000_000
      be.putStamped('pa', 'Fig1.pdf', '别的标签页的稿', HIGH) // v1 落地前，别处写入了更大的戳（本页不知道）
      await typeText('v2 版（v1 在途时又改的）') // v2 编辑时同样不知道 HIGH：基线 < HIGH
      releaseA() // v1 回来：stale。此时本地已有 v2
      await settle()
      await act(async () => void vi.advanceTimersByTime(WRITE_DEBOUNCE_MS + 50))
      await settle()
      const puts = putsOf()
      expect(puts.map((c) => c.body.text)).toEqual(['v1 版', 'v2 版（v1 在途时又改的）']) // v1 的内容只发过那一次；v2 被拒后没有换戳重写
      expect((puts[1].body.stamp as number)).toBeGreaterThan(puts[0].body.stamp as number) // v2 的戳是它编辑时分配的，比 v1 大
      expect((puts[1].body.stamp as number)).toBeLessThan(HIGH)
      expect(be.draft('pa', 'Fig1.pdf')).toBe('别的标签页的稿') // 别处比 v2 更新的稿保留（有我们编辑时不知道的写入 → 放弃，交给对账）
      expect(input().value).toBe('别的标签页的稿')
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
        .mockImplementationOnce(async (req) => {
          be.dropRejectedPending((req as { client_request_id: string }).client_request_id) // 后端明确拒绝时自己删账本行
          throw new ApiError('busy', 409, { code: 'ai_script_busy', params: { script: 'p1.py' } })
        })
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
        .mockImplementationOnce(async (req) => {
          be.dropRejectedPending((req as { client_request_id: string }).client_request_id) // 后端明确拒绝时自己删账本行
          throw new ApiError('busy', 409, { code: 'ai_script_busy', params: { script: 'p1.py' } })
        })
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

    // 来源逐个入口验（行为，不数源码）：输入框的两个触发（发送钮 / ⌘↵）都取草稿快照、账本记草稿戳；
    // 历史会话的两个触发（气泡「重新发送」/ 失败会话的「重试」）都不碰草稿、账本不带戳。
    it.each([
      ['发送钮', async () => click(q('[data-ai-send="send"]'))],
      [
        '⌘↵',
        async () => {
          await act(async () => {
            input().dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', metaKey: true, bubbles: true }))
          })
        },
      ],
    ])('输入框来源（%s）：run 发出那一刻账本里是草稿戳，被接受后草稿被清', async (_name, trigger) => {
      let stampAtRun: unknown = null
      vi.mocked(aiRun).mockImplementation(async () => {
        stampAtRun = ledgerStamp()
        return { session: 's-1', script: 'p1.py' }
      })
      await mount()
      await typeText('把线条加粗一点')
      await trigger()
      await settle()
      await expectAcceptedAndDraftCleared(stampAtRun)
    })

    it.each([
      ['气泡「重新发送」', async () => click(q('[data-ai-resend]'))],
      ['失败会话的「重试」', async () => click(q('[data-ai-error="session"] [data-ai-retry]'))],
    ])('历史会话来源（%s）：不碰草稿，账本不带草稿戳', async (_name, trigger) => {
      let stampAtRun: unknown = 'unset'
      vi.mocked(aiRun).mockImplementation(async () => {
        stampAtRun = ledgerStamp()
        return { session: 's-2', script: 'p1.py' }
      })
      useAiStore.setState({ sessions: [failedSession({ id: 's-old', prompt: '历史里的旧指令', status: 'failed', changed: false })] })
      await mount()
      await typeText('我正在输入框里写的草稿')
      await trigger()
      await settle()
      expect((vi.mocked(aiRun).mock.calls[0][0] as { prompt: string }).prompt).toBe('历史里的旧指令')
      expect(stampAtRun).toBeNull()
      expect(input().value).toBe('我正在输入框里写的草稿')
      await act(async () => window.dispatchEvent(new Event('pagehide')))
      await settle()
      expect(be.draft('pa', 'Fig1.pdf')).toBe('我正在输入框里写的草稿')
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

  it('cancel 之后 SSE 断开：本地 cancelled 但未 finalize 的会话重连对账时继续查——快照还没有 ended_ms 就等，后端写入 changed / diff 后收尾', async () => {
    useAiStore.setState({
      sessions: [failedSession({ id: 's-canc', status: 'cancelled', finalized: false, script: 'p1.py', finishedAt: undefined, changed: false })],
    })
    await mount()
    // 取消已被接受，但后端的 pump 还没收尾：快照是 cancelled、没有 ended_ms（changed / diff 还没算出来）
    vi.mocked(fetchAiSession).mockResolvedValue({ agent: 'codex', status: 'cancelled', changed: false, diff: '', transcript: [] })
    await act(async () => window.dispatchEvent(new Event('mm:sse-open')))
    await settle()
    expect(useAiStore.getState().sessions[0].finalized).toBeFalsy() // 不把没算完的快照当终态
    // 之后 pump 收尾：写入 changed / diff 和 ended_ms；ai.done 因为断线丢了，重连对账拿到
    vi.mocked(fetchAiSession).mockResolvedValue({
      agent: 'codex', status: 'cancelled', ended_ms: 1700000000000, changed: true, diff: 'REAL-DIFF', transcript: [],
    })
    await act(async () => window.dispatchEvent(new Event('mm:sse-open')))
    await settle()
    expect(useAiStore.getState().sessions[0]).toMatchObject({ status: 'cancelled', finalized: true, changed: true, diff: 'REAL-DIFF' })
  })

  it('查询接回的会话还是临时快照（interrupted、没有 ended_ms）：之后的对账继续取详情，拿到终态的 changed / diff 才收尾', async () => {
    be.pendings.set('pa\u0000Fig1.pdf', [{ rid: 'rid-intr-0000001', text: '别的服务进程发的', sent: SENT, script: 'p1.py' }])
    useSelectionStore.setState({ ids: ['p3'] } as never)
    await mount()
    vi.mocked(fetchAiRequest).mockResolvedValue({ session: 's-intr', status: 'interrupted', changed: false, script: 'p1.py' })
    vi.mocked(fetchAiSession).mockResolvedValue({ status: 'interrupted', ended_ms: null, changed: false, diff: '', transcript: [] } as never)
    await click(q('[data-ai-check]'))
    await settle()
    const s0 = useAiStore.getState().sessions.find((x) => x.id === 's-intr')
    expect(s0 && !s0.finalized).toBe(true) // 临时快照：不当终态
    vi.mocked(fetchAiSession).mockResolvedValue({ status: 'interrupted', ended_ms: 9, changed: true, diff: '@@ d', transcript: [] } as never)
    await act(async () => window.dispatchEvent(new Event('mm:sse-open')))
    await settle()
    const s1 = useAiStore.getState().sessions.find((x) => x.id === 's-intr')
    expect(s1?.finalized).toBe(true)
    expect(s1?.changed).toBe(true)
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

  it('戳在编辑时分配：同一版本的失败重试带同一个戳；下一次编辑的戳更大', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      await mount()
      be.setDraftPutFails(true)
      await typeText('第一版')
      await act(async () => void vi.advanceTimersByTime(WRITE_DEBOUNCE_MS + 50)) // 第一次发送（失败）
      await settle()
      await act(async () => void vi.advanceTimersByTime(RETRY_MS + 50)) // 重试（仍失败）
      await settle()
      be.setDraftPutFails(false)
      await act(async () => void vi.advanceTimersByTime(RETRY_MS + 50)) // 再重试（成功）
      await settle()
      const puts = () => be.calls.filter((c) => c.method === 'PUT' && c.path === '/api/ai/drafts')
      expect(puts()).toHaveLength(3)
      expect(new Set(puts().map((c) => c.body.stamp)).size).toBe(1) // 对照：旧实现每次发送重新取戳，三次三个值
      expect(be.draft('pa', 'Fig1.pdf')).toBe('第一版')
      const v1 = puts()[0].body.stamp as number
      await typeText('第二版')
      await act(async () => void vi.advanceTimersByTime(WRITE_DEBOUNCE_MS + 50))
      await settle()
      expect(puts()[3].body.stamp as number).toBeGreaterThan(v1)
    } finally {
      vi.useRealTimers()
    }
  })

  // 退役实例不发任何写入：写入全在发件箱里（hook 只 `post` 版本、订阅结果）。卸载时那次 keepalive 失败后，
  // 重试由发件箱接手；重新挂载并保存新文本后，旧版本已被取代、不会再写（即使发件箱重试，发的也是最新版本）。
  it('卸载时的 keepalive 写入失败：退役实例不再重试；重新挂载并保存新文本后，旧实例之后不再写（即使旧版本重试，服务器也拒）', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      await mount()
      await typeText('旧文本')
      be.setDraftPutFails(true)
      await unmount() // 卸载：keepalive 冲刷「旧文本」，失败
      await settle()
      be.setDraftPutFails(false)
      await mount() // 新实例
      await typeText('新文本')
      await act(async () => window.dispatchEvent(new Event('pagehide'))) // 新文本落地
      await settle()
      expect(be.draft('pa', 'Fig1.pdf')).toBe('新文本')
      const putsBefore = be.calls.filter((c) => c.method === 'PUT' && c.path === '/api/ai/drafts').length
      await act(async () => void vi.advanceTimersByTime(RETRY_MS * 3)) // 旧实例若还会重试，现在早该发了
      await settle()
      const puts = be.calls.filter((c) => c.method === 'PUT' && c.path === '/api/ai/drafts')
      expect(puts.length).toBe(putsBefore) // 退役实例没有再发任何写入
      expect(puts.map((c) => c.body.text)).toEqual(['旧文本', '新文本'])
      expect(be.draft('pa', 'Fig1.pdf')).toBe('新文本') // 对照：旧实现这里会被旧文本覆盖
      await unmount()
      await mount()
      expect(input().value).toBe('新文本') // 重开显示新文本
    } finally {
      vi.useRealTimers()
    }
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

  it('入口矩阵（行为）：挂载 / 聚焦 / 可见 / SSE 重连 / 提交确认五个入口——同样的服务器状态得到完全相同的还原结果，且向后端发的请求集合一模一样', async () => {
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
      accepted: async () => {
        await act(async () => window.dispatchEvent(new Event('mm:ai-draft-changed')))
      },
    }
    const outcomes: Record<string, { rids: string[]; text: string; requests: string[] }> = {}
    for (const [name, trigger] of Object.entries(entries)) {
      useAiStore.setState({ submissions: {}, sessions: [] })
      if (root) await unmount()
      be.pendings.clear()
      be.drafts.clear()
      if (name === 'mount') seed() // 挂载：状态在挂载前就在
      let from = be.calls.length
      await mount()
      if (name !== 'mount') {
        seed() // 其余入口：挂载之后状态才出现，靠各自的入口把它取回来
        from = be.calls.length // 只数入口自己发的请求
        await trigger()
      }
      await settle()
      outcomes[name] = {
        rids: rids(),
        text: input().value,
        requests: be.calls.slice(from).map((c) => `${c.method} ${c.path}`).sort(),
      }
    }
    expect(outcomes.mount.rids).toEqual(['rid-cross-000001', 'rid-orphan-00001', 'rid-own-0000001'])
    expect(outcomes.mount.text).toBe('服务器上的草稿')
    // 请求集合一致 = 每个入口都读全了同样的东西（草稿 + 项目级账本），没有「只取文本」的入口
    expect(outcomes.mount.requests).toEqual(['GET /api/ai/drafts', 'GET /api/ai/drafts/pending'])
    for (const name of ['focus', 'visible', 'reconnect', 'accepted']) expect(outcomes[name], name).toEqual(outcomes.mount)
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

  it.each([
    ['同一种写法', 'p1.py'],
    ['旧数据的绝对路径（sameScript 认作同一份）', 'C:\\proj\\p1.py'],
  ])('兄弟面板上「改写后再发」：A 面板发起的旧等待也随这个面板新行的 PUT 带进 supersedes（%s）', async (_n, oldScript) => {
    be.pendings.set('pa\u0000Fig1.pdf', [{ rid: 'rid-sibling-0002', text: 'A 面板发出的话', sent: SENT, script: oldScript }])
    useSelectionStore.setState({ ids: ['p3'] } as never)
    await mount()
    vi.mocked(fetchAiRequest).mockRejectedValue(new ApiError('nf', 404, { code: 'ai_request_not_found' }))
    await click(q('[data-ai-check]'))
    await settle()
    await click(q('[data-ai-edit-resend]'))
    vi.mocked(aiRun).mockResolvedValue({ session: 's-9', script: 'p1.py' })
    await typeText('新指令')
    await click(q('[data-ai-send="send"]'))
    await settle()
    const rid2 = (vi.mocked(aiRun).mock.calls[0][0] as { client_request_id: string }).client_request_id
    const putOfNew = be.calls.find((c) => c.method === 'PUT' && c.path.endsWith(`/pending/${rid2}`))
    expect(putOfNew?.body.supersedes).toEqual(['rid-sibling-0002'])
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

  it('这个面板未确认的提交已满（后端 409 ai_pending_limit）：不发、不留「发送中」，一句话说明还有 N 条没确认（不淘汰旧的由后端用例钉住）', async () => {
    be.setPendingLimit(8)
    await mount()
    await typeText('新指令')
    await click(q('[data-ai-send="send"]'))
    await settle()
    expect(aiRun).not.toHaveBeenCalled()
    expect(q('[data-ai-error="send"]')?.textContent).toContain('还有 8 条已发出的指令没确认')
    expect(q('[data-ai-submission]')).toBeNull()
    expect(input().value).toBe('新指令') // 草稿还在，用户可以等一等再发
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
    // 客户端没有 DELETE：旧行由新行的 PUT 带 `supersedes`、在后端同一个事务里删
    expect(be.calls.filter((c) => c.method === 'DELETE' && c.path.includes('/pending'))).toEqual([])
    const putOfNew = be.calls.find((c) => c.method === 'PUT' && c.path.endsWith(`/pending/${rid2}`))
    expect(putOfNew?.body.supersedes).toEqual([rid1])
  })

  it('「改写后再发」时旧请求其实已登记（409 ai_supersede_conflict）：放弃的那条原样还原、照旧锁住发送，并按它的 rid 查询接回会话；新指令不发', async () => {
    await mount()
    await sendIt('旧指令')
    const rid1 = (vi.mocked(aiRun).mock.calls[0][0] as { client_request_id: string }).client_request_id
    vi.mocked(fetchAiRequest).mockRejectedValue(new ApiError('nf', 404, { code: 'ai_request_not_found' }))
    await click(q('[data-ai-check]'))
    await settle()
    await click(q('[data-ai-edit-resend]'))
    // 旧请求的慢 POST 这时才到、登记成会话
    be.markRegistered(rid1)
    vi.mocked(fetchAiRequest).mockReset()
    vi.mocked(fetchAiRequest).mockResolvedValue({ session: 's-old', status: 'running', changed: false, script: 'fig1.py' })
    await typeText('新指令')
    await click(q('[data-ai-send="send"]'))
    await settle()
    expect(aiRun).toHaveBeenCalledTimes(1) // 新指令没有发出
    expect(vi.mocked(fetchAiRequest).mock.calls.some((c) => c[0] === rid1)).toBe(true) // 按旧 rid 查询
    expect(useAiStore.getState().sessions.map((x) => x.id)).toContain('s-old') // 接回旧请求的会话
    expect(q('[data-ai-send="send"]')).toBeNull() // 旧任务还在跑：只剩「停止」，不能再发
  })

  it('查询得到 superseded（这条请求已被改写后的新指令取代）：不再提供同 rid 重发，提交被收掉，说一句', async () => {
    await mount()
    await sendIt('旧指令')
    vi.mocked(fetchAiRequest).mockResolvedValue({ session: '', status: 'superseded', changed: false, script: '' })
    await click(q('[data-ai-check]'))
    await settle()
    expect(q('[data-ai-submission]')).toBeNull() // 提交被收掉
    expect(q('[data-ai-resend-same]')).toBeNull() // 没有「重新发送」
    expect(aiRun).toHaveBeenCalledTimes(1) // 没有再发
    expect(JSON.stringify(useUiStore.getState().status)).toContain('status.aiSuperseded')
  })

  it('点发送后、草稿快照还在 await 时切到有同名文件的项目 B：run 既不发到 B 也不发到 A（发送作废），B 的账本里也没有这条', async () => {
    vi.mocked(aiRun).mockResolvedValue({ session: 's-1', script: 'p1.py' })
    await mount()
    await typeText('把线条加粗一点')
    const release = be.holdDraftPuts() // 草稿快照卡在写后端上
    await click(q('[data-ai-send="send"]'))
    await settle()
    setCurrentProjectId('pb') // 此刻切到项目 B（B 里也有 Fig1.pdf）
    await act(async () => useAiStore.getState().clear())
    release()
    await settle()
    expect(aiRun).not.toHaveBeenCalled() // 对照：以前 start 在 await 之后读「当前项目」= B，指令被发到 B
    expect(be.pendings.get('pb\u0000Fig1.pdf') ?? []).toEqual([])
    expect(Object.values(useAiStore.getState().submissions)).toEqual([])
    expect(q('[data-ai-error="send"]')).toBeNull() // 作废是静默的，不是报错
  })

  it('账本 PUT 回 409 ai_pending_unresolved 时已经切到项目 B：A 的挡路行不还原进 B、不查询、不报错（B 不被锁，A 的会话不挂到 B 上）', async () => {
    vi.mocked(aiRun).mockResolvedValue({ session: 's-1', script: 'p1.py' })
    await mount()
    await typeText('新指令')
    be.pendings.set('pa\u0000Fig1.pdf', [{ rid: 'rid-othertab-011', text: '别的标签页发出的话', sent: SENT, script: 'p1.py' }])
    vi.mocked(fetchAiRequest).mockResolvedValue({ session: 's-a', status: 'running', changed: false, script: 'p1.py' })
    be.afterPendingUnresolved(() => {
      setCurrentProjectId('pb') // 响应回来之前切到项目 B（B 里也有同名脚本）
      useAiStore.getState().clear()
    })
    await click(q('[data-ai-send="send"]'))
    await settle()
    be.afterPendingUnresolved(null)
    expect(aiRun).not.toHaveBeenCalled()
    expect(Object.values(useAiStore.getState().submissions)).toEqual([])
    expect(vi.mocked(fetchAiRequest).mock.calls.some((c) => c[0] === 'rid-othertab-011')).toBe(false)
    expect(useAiStore.getState().sessions.map((x) => x.id)).not.toContain('s-a')
    expect(q('[data-ai-error="send"]')).toBeNull()
  })

  it('「改写后再发」的 PUT 回 409 ai_supersede_conflict 时已经切到项目 B：A 放弃的那条不还原进 B、不查询、不报错', async () => {
    await mount()
    await sendIt('旧指令')
    const rid1 = (vi.mocked(aiRun).mock.calls[0][0] as { client_request_id: string }).client_request_id
    vi.mocked(fetchAiRequest).mockRejectedValue(new ApiError('nf', 404, { code: 'ai_request_not_found' }))
    await click(q('[data-ai-check]'))
    await settle()
    await click(q('[data-ai-edit-resend]'))
    be.markRegistered(rid1)
    vi.mocked(fetchAiRequest).mockReset()
    vi.mocked(fetchAiRequest).mockResolvedValue({ session: 's-a', status: 'running', changed: false, script: 'fig1.py' })
    // 两道各自成立的防线钉在这一条上：切项目的 `clear()` 清空放弃队列，`start()` 的拒绝分支先核项目代次——两者都去掉这条才红
    be.afterSupersedeConflict(() => {
      setCurrentProjectId('pb')
      useAiStore.getState().clear()
    })
    await typeText('新指令')
    await click(q('[data-ai-send="send"]'))
    await settle()
    be.afterSupersedeConflict(null)
    expect(aiRun).toHaveBeenCalledTimes(1) // 新指令没发
    expect(Object.values(useAiStore.getState().submissions)).toEqual([])
    expect(vi.mocked(fetchAiRequest).mock.calls.some((c) => c[0] === rid1)).toBe(false)
    expect(useAiStore.getState().sessions.map((x) => x.id)).not.toContain('s-a')
    expect(q('[data-ai-error="send"]')).toBeNull()
  })

  it('放弃旧等待后新行的 PUT 失败：旧行还在（状态仍然一致），之后同一条指令重试成功才一起清掉旧行', async () => {
    await mount()
    await sendIt('旧指令')
    const rid1 = (vi.mocked(aiRun).mock.calls[0][0] as { client_request_id: string }).client_request_id
    vi.mocked(fetchAiRequest).mockRejectedValue(new ApiError('nf', 404, { code: 'ai_request_not_found' }))
    await click(q('[data-ai-check]'))
    await settle()
    await click(q('[data-ai-edit-resend]'))
    vi.mocked(aiRun).mockResolvedValue({ session: 's-3', script: 'fig1.py' })
    await typeText('新指令')
    be.setPendingWriteFails(true)
    await click(q('[data-ai-send="send"]'))
    await settle()
    expect(aiRun).toHaveBeenCalledTimes(1) // 新行没写成：不发
    expect(be.pendings.get('pa\u0000Fig1.pdf')?.map((x) => x.rid)).toEqual([rid1]) // 旧行还在
    be.setPendingWriteFails(false)
    await click(q('[data-ai-retry]'))
    await settle()
    const rid2 = (vi.mocked(aiRun).mock.calls[1][0] as { client_request_id: string }).client_request_id
    const rows = be.pendings.get('pa\u0000Fig1.pdf')?.map((x) => x.rid) ?? []
    expect(rows).not.toContain(rid1) // 这次新行的 PUT 仍带着 supersedes，旧行与新行的写入同事务消失
    expect(rid2).not.toBe(rid1)
  })

  it('账本 PUT 已提交但响应丢了：不当成没写入——不发；点重试用的是**同一个 rid**（幂等重放），最终只执行一次', async () => {
    vi.mocked(aiRun).mockResolvedValue({ session: 's-1', script: 'p1.py' })
    await mount()
    await typeText('把线条加粗一点')
    be.losePendingResponses(1)
    await click(q('[data-ai-send="send"]'))
    await settle()
    expect(aiRun).not.toHaveBeenCalled() // 不知道写没写：不发
    expect(q('[data-ai-error="send"]')).toBeTruthy()
    const written = be.pendings.get('pa\u0000Fig1.pdf')?.map((x) => x.rid) ?? []
    expect(written).toHaveLength(1) // 后端其实已经写了
    await click(q('[data-ai-retry]'))
    await settle()
    expect(aiRun).toHaveBeenCalledTimes(1)
    const rid = (vi.mocked(aiRun).mock.calls[0][0] as { client_request_id: string }).client_request_id
    expect(rid).toBe(written[0]) // 同一个 rid，不是换了新的
    const puts = be.calls.filter((c) => c.method === 'PUT' && c.path.includes('/pending/'))
    expect(new Set(puts.map((c) => c.path)).size).toBe(1) // 两次 PUT 都落在同一个 rid 上
  })

  it('账本写入结果未知的旧 rid 被别的标签页登记了，本页改了指令再发（409 ai_supersede_conflict）：旧 rid 还原成未确认并查询接回会话，新指令不发', async () => {
    vi.mocked(aiRun).mockResolvedValue({ session: 's-1', script: 'p1.py' })
    await mount()
    await typeText('把线条加粗一点')
    be.losePendingResponses(1)
    await click(q('[data-ai-send="send"]'))
    await settle()
    const rid1 = be.pendings.get('pa\u0000Fig1.pdf')![0].rid
    be.markRegistered(rid1) // 别的标签页还原了这条账本行、把它发了出去
    vi.mocked(fetchAiRequest).mockResolvedValue({ session: 's-other', status: 'running', changed: false, script: 'p1.py' })
    await typeText('改成虚线')
    await click(q('[data-ai-send="send"]'))
    await settle()
    expect(aiRun).not.toHaveBeenCalled()
    expect(vi.mocked(fetchAiRequest).mock.calls.some((c) => c[0] === rid1)).toBe(true)
    expect(useAiStore.getState().sessions.map((x) => x.id)).toContain('s-other')
    expect(q('[data-ai-send="send"]')).toBeNull()
  })

  it('账本 PUT 回 5xx 同样是「不知道」：恢复后重试沿用同一个 rid；明确的 4xx 才算确定没写', async () => {
    vi.mocked(aiRun).mockResolvedValue({ session: 's-1', script: 'p1.py' })
    await mount()
    await typeText('指令一')
    be.setPendingWriteFails(true)
    await click(q('[data-ai-send="send"]'))
    await settle()
    expect(aiRun).not.toHaveBeenCalled()
    be.setPendingWriteFails(false)
    await click(q('[data-ai-retry]'))
    await settle()
    const puts = be.calls.filter((c) => c.method === 'PUT' && c.path.includes('/pending/'))
    expect(puts).toHaveLength(2)
    expect(puts[0].path).toBe(puts[1].path) // 同一个 rid
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
