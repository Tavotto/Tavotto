/**
 * 「找回本机排版」入口（ADR 0108 §四）：打开排版对话框里的两组——本项目的本机排版、折叠的「归属待确认」。
 * 真组件 + 真 store + 真 api，有状态的假 fetch（`test/fakeSessionBackend`）；断言实际 GET / PUT 序列、
 * 源槽位字节不变、取消零写入。命名避开 QA 的 `recovery.*.test.ts`。
 *
 * 覆盖验收 A15（未知归属的 runtime 旧文档：列表 + 摘要 → 恢复副本 → 原文件与归属保留，取消零写入）、
 * A28（找回入口可发现、不强迫、分页有界）、A30（坏文件不阻塞别的行、不运行脚本、确认时重验 revision）。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { createFakeBackend } from '@/test/fakeSessionBackend'
import { LayoutDialog } from '@/components/LayoutDialog'
import { recoveryCopyId } from '@/lib/recoveryCandidates'
import { setCurrentProjectId } from '@/lib/session'
import { useDocumentStore } from '@/store/documentStore'
import { useProjectStore } from '@/store/projectStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject } from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const PJ = 'p_a'
const backend = createFakeBackend()
const realFetch = globalThis.fetch

/** 一份有内容的 runtime 面板排版：脚本在项目里，owners / last / 时间线都没有 */
const runtimeDoc = (name: string, extra: Record<string, unknown> = {}) => {
  const pd = emptyProject()
  return {
    ...pd,
    project: { id: 'x', name },
    canvases: [
      {
        ...pd.canvases[0],
        objects: [
          {
            id: 'o1',
            type: 'panel',
            fileId: 'runtime:fig1.py#Fig1_PRISMA_Flowchart',
            fileKind: 'runtime',
            x: 5,
            y: 6,
            w: 40,
            h: 30,
            overrides: [{ gid: 'g1', prop: 'title', value: '改过的标题' }],
          },
        ],
      },
    ],
    ...extra,
  }
}

let root: Root
let degradeProjectCandidates = false
/** 在途的 fetch 数：`flush` 靠它知道「请求都落地了」，不靠固定时长的 sleep（负载高时固定时长不够） */
let inFlight = 0

async function mount(intent: 'load' | 'save' = 'load', settle = true) {
  globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
    inFlight += 1
    try {
      if (new URL(String(url), 'http://x').pathname === '/api/layouts') {
        return new Response(JSON.stringify({ layouts: [], modified: {} }), { status: 200 })
      }
      const u = new URL(String(url), 'http://x')
      if (degradeProjectCandidates && u.pathname === '/api/layout-session/candidates' && u.searchParams.get('scope') === 'project') {
        const res = await backend.fetch(url as RequestInfo, init)
        const payload = await res.json()
        return new Response(JSON.stringify({ ...payload, items: [], next_cursor: null, index: 'degraded' }), { status: res.status })
      }
      return await backend.fetch(url as RequestInfo, init)
    } finally {
      inFlight -= 1
    }
  }) as typeof fetch
  useUiStore.setState({ layoutOpen: true, layoutIntent: intent })
  const el = document.createElement('div')
  document.body.appendChild(el)
  root = createRoot(el)
  await act(async () => {
    root.render(<LayoutDialog />)
  })
  if (settle) await flush()
}

/**
 * 等到没有在途请求、且连续几轮 React 更新都没有再发起新请求。被 `hold` 扣住的请求算在途，
 * 所以要先放行再 flush（需要扣住时的断言用 `until` 等具体条件）。
 */
async function flush() {
  for (let quiet = 0; quiet < 4; ) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0))
    })
    quiet = inFlight === 0 ? quiet + 1 : 0
  }
}

/** 等某个条件成立（事件驱动轮询，有界；超时就让用例带着当时的状态失败） */
async function until(pred: () => boolean) {
  for (let i = 0; i < 400 && !pred(); i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 5))
    })
  }
}

const dialog = () => document.querySelector('[role="dialog"]')!
const rows = (group: 'project' | 'unknown') =>
  [...dialog().querySelectorAll<HTMLButtonElement>(`[data-recover-group="${group}"] [data-recover-row]`)]
const byText = (text: string) => [...dialog().querySelectorAll('button')].find((b) => b.textContent?.includes(text))

async function expandOther() {
  const det = dialog().querySelector<HTMLDetailsElement>('details[data-recover-group="unknown"]')!
  await act(async () => {
    det.open = true
    det.dispatchEvent(new Event('toggle'))
  })
  await flush()
  await until(() => !dialog().textContent?.includes('正在查找'))
}

beforeEach(() => {
  backend.reset()
  degradeProjectCandidates = false
  localStorage.clear()
  setCurrentProjectId(PJ)
  useProjectStore.setState({ project: { open: true, id: PJ, document_dir: '/figs/a/tavottofile' } })
  useDocumentStore.setState({ documentId: 'd_current' })
  useUiStore.setState({ status: null })
  inFlight = 0
})

afterEach(async () => {
  if (root) await act(async () => root.unmount())
  document.body.innerHTML = ''
  useUiStore.setState({ layoutOpen: false })
  globalThis.fetch = realFetch
})

describe('找回入口的可发现性与只读', () => {
  it('归属索引退化且项目列表为空时显示重试，恢复后不用重开对话框就能找回', async () => {
    backend.putSlot('d_mine', runtimeDoc('我的排版'), PJ)
    backend.state.candidates.push({ doc_id: 'd_mine', scope: 'project', name: '我的排版' })
    degradeProjectCandidates = true
    await mount()
    expect(rows('project')).toHaveLength(0)
    expect(dialog().textContent).toContain('本机排版暂时读不出来')
    degradeProjectCandidates = false
    await act(async () => byText('重试')!.click())
    await flush()
    expect(rows('project').map((r) => r.dataset.recoverRow)).toEqual(['d_mine'])
  })

  it('打开那一屏先读本项目的一组；归属待确认的折叠着、展开才读（A28），列表全程零写入', async () => {
    backend.putSlot('d_mine', runtimeDoc('我的排版'), PJ)
    backend.putSlot('d_unk', runtimeDoc('不知道谁的'))
    backend.state.candidates.push({ doc_id: 'd_mine', scope: 'project', name: '我的排版' }, { doc_id: 'd_unk', scope: 'unknown', name: '不知道谁的' })
    await mount()
    expect(rows('project').map((r) => r.dataset.recoverRow)).toEqual(['d_mine'])
    expect(backend.state.calls).toContain('GET candidates project')
    expect(backend.state.calls).not.toContain('GET candidates unknown') // 折叠着：不读
    expect(dialog().querySelector('details[data-recover-group="unknown"]')?.hasAttribute('open')).toBe(false)

    await expandOther()
    expect(backend.state.calls).toContain('GET candidates unknown')
    expect(rows('unknown').map((r) => r.dataset.recoverRow)).toEqual(['d_unk'])
    expect(backend.writes()).toEqual([])
  })

  it('行上有名字、画布 / 对象数、脚本·figure 名提示；没有草图就不画缩略图，有就画结构化草图（不编造）', async () => {
    backend.putSlot('d_a', runtimeDoc('带草图'))
    backend.putSlot('d_b', runtimeDoc('无草图'))
    backend.state.candidates.push(
      {
        doc_id: 'd_a',
        scope: 'unknown',
        name: '带草图',
        canvases: 2,
        objects: 7,
        runtime: [{ script: 'fig1.py', figure: 'Fig1_PRISMA_Flowchart' }],
        sketch: { page: { w: 100, h: 80 }, objects: [{ type: 'shape', x: 1, y: 2, w: 10, h: 10, shape: 'rect' }] },
      },
      { doc_id: 'd_b', scope: 'unknown', name: '无草图' },
    )
    await mount()
    await expandOther()
    const [a, b] = rows('unknown')
    expect(a.textContent).toContain('带草图')
    expect(a.textContent).toContain('2 张画布')
    expect(a.textContent).toContain('7 个对象')
    expect(a.textContent).toContain('脚本 fig1.py · Fig1_PRISMA_Flowchart')
    expect(a.querySelector('[data-canvas-thumb]')).not.toBeNull()
    expect(b.querySelector('[data-canvas-thumb]')).toBeNull()
    // 列表不为预览去读任何正文（不读槽位、不渲染）
    expect(backend.state.calls.filter((c) => c.startsWith('GET autosave'))).toEqual([])
  })

  it('保存时间按毫秒渲染（相对时间），不是几万年后', async () => {
    backend.putSlot('d_t', runtimeDoc('t'))
    backend.state.candidates.push({ doc_id: 'd_t', scope: 'unknown', name: 't', mtime: Date.now() - 3 * 3600 * 1000 })
    await mount()
    await expandOther()
    const text = rows('unknown')[0].textContent ?? ''
    expect(text).toMatch(/3\s?小时前/)
  })

  it('坏 / 来自更新版本 / 太大的行置灰并说原因，不阻塞别的行（A30）', async () => {
    backend.putSlot('d_ok', runtimeDoc('好的'))
    backend.state.candidates.push(
      { doc_id: 'd_bad', scope: 'unknown', status: 'invalid', name: '坏的' },
      { doc_id: 'd_new', scope: 'unknown', status: 'too_new', name: '新的' },
      { doc_id: 'd_big', scope: 'unknown', status: 'too_large', name: '大的' },
      { doc_id: 'd_ok', scope: 'unknown', name: '好的' },
    )
    await mount()
    await expandOther()
    const [bad, tooNew, big, ok] = rows('unknown')
    expect(bad.disabled).toBe(true)
    expect(bad.textContent).toContain('读不了')
    expect(tooNew.disabled).toBe(true)
    expect(tooNew.textContent).toContain('更新版本')
    expect(big.disabled).toBe(true)
    expect(big.textContent).toContain('太大')
    expect(ok.disabled).toBe(false)
  })

  it('旧后端没有这组端点（404）：入口整个不出现；读失败：一句话 + 重试，重试读到就出现', async () => {
    await mount()
    expect(dialog().querySelector('[data-layout-recover]')).not.toBeNull() // 空列表也有折叠组（可发现）
    await act(async () => root.unmount())
    document.body.innerHTML = ''

    backend.state.fail.candidates = 404
    await mount()
    expect(dialog().querySelector('[data-layout-recover]')).toBeNull()
    await act(async () => root.unmount())
    document.body.innerHTML = ''

    backend.state.fail.candidates = undefined
    backend.putSlot('d_mine', runtimeDoc('我的排版'), PJ)
    backend.state.candidates.push({ doc_id: 'd_mine', scope: 'project', name: '我的排版' })
    backend.state.failOnce.candidates = 500
    await mount()
    expect(dialog().textContent).toContain('暂时读不出来')
    await act(async () => byText('重试')!.click())
    await flush()
    expect(rows('project')).toHaveLength(1)
  })

  it('分页：一批最多 200 行并说「已显示 200 份，还有更多」，「加载更多」再读下一批，读完按钮消失', async () => {
    for (let i = 0; i < 250; i++) {
      backend.putSlot(`d_${i}`, runtimeDoc(`n${i}`), PJ)
      backend.state.candidates.push({ doc_id: `d_${i}`, scope: 'project', name: `n${i}` })
    }
    await mount()
    expect(rows('project')).toHaveLength(200)
    expect(dialog().textContent).toContain('已显示 200 份')
    await act(async () => byText('加载更多')!.click())
    await until(() => rows('project').length === 250)
    await flush()
    expect(rows('project')).toHaveLength(250)
    expect(byText('加载更多')).toBeUndefined()
    expect(dialog().textContent).not.toContain('已显示')
  })

  it('扫描碰到后端上限（truncated）：没有行也不说「没有」，说只扫描了一部分；有行时行下面也提示', async () => {
    backend.state.truncated = true
    await mount()
    expect(dialog().querySelector('[data-recover-group="project"] [data-recover-truncated]')).not.toBeNull()
    await expandOther()
    expect(dialog().textContent).toContain('只扫描了一部分')
    expect(dialog().textContent).not.toContain('没有找到其他排版')
    await act(async () => root.unmount())
    document.body.innerHTML = ''

    backend.putSlot('d_mine', runtimeDoc('我的排版'), PJ)
    backend.state.candidates.push({ doc_id: 'd_mine', scope: 'project', name: '我的排版' })
    await mount()
    expect(rows('project')).toHaveLength(1)
    expect(dialog().querySelector('[data-recover-truncated]')).not.toBeNull()
  })

  it('没截断时 0 行仍说「没有找到其他排版」', async () => {
    await mount()
    await expandOther()
    expect(dialog().textContent).toContain('没有找到其他排版')
    expect(dialog().querySelector('[data-recover-truncated]')).toBeNull()
  })

  it('「另存为」那一屏没有这个入口', async () => {
    await mount('save')
    expect(dialog().querySelector('[data-layout-recover]')).toBeNull()
    expect(backend.state.calls).not.toContain('GET candidates project')
  })
})

describe('恢复副本（A15 / A30）', () => {
  async function pick(id: string) {
    await mount()
    await expandOther()
    const row = rows('unknown').find((r) => r.dataset.recoverRow === id)!
    await act(async () => row.click())
    return row
  }

  it('点一行只是展开确认：一句话 + 「恢复副本」+ 取消；取消零写入，也不读源正文', async () => {
    const src = backend.putSlot('d_old', runtimeDoc('旧排版'))
    backend.state.candidates.push({ doc_id: 'd_old', scope: 'unknown', name: '旧排版' })
    await pick('d_old')
    const confirm = dialog().querySelector('[data-recover-confirm]')!
    expect(confirm.textContent).toContain('原排版保持不动')
    expect(confirm.querySelector('[data-recover-copy]')).not.toBeNull()
    await act(async () => [...confirm.querySelectorAll('button')].find((b) => b.textContent === '取消')!.click())
    expect(dialog().querySelector('[data-recover-confirm]')).toBeNull()
    expect(backend.writes()).toEqual([])
    expect(backend.state.calls.filter((c) => c.startsWith('GET autosave'))).toEqual([])
    expect(backend.state.slots.get('d_old')).toEqual(src)
    expect(useDocumentStore.getState().documentId).toBe('d_current')
  })

  it('确认：以固定目标 id 装进文档并走正常保存链；源槽位与归属不动；对话框关闭；overrides 与对象原样', async () => {
    const src = backend.putSlot('d_old', runtimeDoc('旧排版'))
    backend.state.candidates.push({ doc_id: 'd_old', scope: 'unknown', name: '旧排版' })
    await pick('d_old')
    const item = (await (await backend.fetch('/api/layout-session/candidates?scope=unknown'))).json()
    const revision = ((await item) as { items: { revision: string }[] }).items[0].revision
    const target = recoveryCopyId('d_old', revision, PJ)
    backend.state.calls.length = 0
    await act(async () => dialog().querySelector<HTMLButtonElement>('[data-recover-copy]')!.click())
    await flush()

    expect(useDocumentStore.getState().documentId).toBe(target)
    expect(useUiStore.getState().layoutOpen).toBe(false)
    const panel = useDocumentStore.getState().doc.objects[0] as unknown as { fileId: string; overrides: unknown; x: number; w: number }
    expect(panel).toMatchObject({ id: 'o1', fileId: 'runtime:fig1.py#Fig1_PRISMA_Flowchart', x: 5, w: 40 })
    expect(panel.overrides).toContainEqual({ gid: 'g1', prop: 'title', value: '改过的标题' })
    // 序列：目标不存在（404）→ 读源 → 正常保存链对目标的写前确认（GET，404=absent）→ PUT 目标。源从不被写
    expect(backend.state.calls.filter((c) => c.startsWith('GET autosave')).slice(0, 2)).toEqual([`GET autosave ${target}`, 'GET autosave d_old'])
    expect(backend.puts().filter((c) => c.startsWith('PUT autosave'))).toEqual([`PUT autosave ${target}`])
    expect(backend.session(PJ).last?.doc_id).toBe(target) // 重启时副本就是这个项目 durable 的「上次开着的」
    expect(backend.state.slots.get('d_old')).toEqual(src)
    expect(backend.state.owners.has('d_old')).toBe(false)
    expect(backend.state.owners.get(target)).toBe(PJ) // 归属由正常保存链建立
    expect(useUiStore.getState().status).toMatchObject({ key: 'layout.recoverDone' })
  })

  it('已有恢复副本目标的本机索引误记旧项目：重开前按目标 owners 纠正归属', async () => {
    backend.putSlot('d_old', runtimeDoc('源排版'))
    backend.state.candidates.push({ doc_id: 'd_old', scope: 'unknown', name: '源排版' })
    await pick('d_old')
    const page = await (await backend.fetch('/api/layout-session/candidates?scope=unknown')).json() as { items: { revision: string }[] }
    const target = recoveryCopyId('d_old', page.items[0].revision, PJ)
    backend.putSlot(target, runtimeDoc('已有的副本'), PJ)
    localStorage.setItem('tavotto.docIndex', JSON.stringify([
      { id: target, name: '已有的副本', savedAt: 1, objects: 1, projectId: 'p_g', projectName: 'G' },
    ]))
    await act(async () => dialog().querySelector<HTMLButtonElement>('[data-recover-copy]')!.click())
    await flush()
    expect(useDocumentStore.getState().documentId).toBe(target)
    expect(JSON.parse(localStorage.getItem('tavotto.docIndex') ?? '[]').find((e: { id: string }) => e.id === target).projectId).toBe(PJ)
    expect(backend.state.owners.get(target)).toBe(PJ)
    expect(backend.session(PJ).last?.doc_id).toBe(target)
  })

  it('预览之后源被改了：不恢复，一句话说明，列表刷新，文档不变（A30）', async () => {
    backend.putSlot('d_old', runtimeDoc('旧排版'))
    backend.state.candidates.push({ doc_id: 'd_old', scope: 'unknown', name: '旧排版' })
    await pick('d_old')
    backend.putSlot('d_old', runtimeDoc('旧排版', { updatedAt: 777 })) // 别处又存了一次
    backend.state.calls.length = 0
    await act(async () => dialog().querySelector<HTMLButtonElement>('[data-recover-copy]')!.click())
    await flush()
    expect(dialog().textContent).toContain('又变了')
    expect(useDocumentStore.getState().documentId).toBe('d_current')
    expect(backend.puts().filter((c) => c.startsWith('PUT autosave'))).toEqual([])
    expect(backend.state.calls).toContain('GET candidates unknown') // 刷新了列表
    expect(dialog().querySelector('[data-recover-confirm]')).toBeNull() // 要重新点一次
  })

  it('双击「恢复副本」只装一份；再点一次（已恢复过）直接打开那份，不生出第二份', async () => {
    backend.putSlot('d_old', runtimeDoc('旧排版'))
    backend.state.candidates.push({ doc_id: 'd_old', scope: 'unknown', name: '旧排版' })
    await pick('d_old')
    const btn = dialog().querySelector<HTMLButtonElement>('[data-recover-copy]')!
    await act(async () => {
      btn.click()
      btn.click()
    })
    await flush()
    const copies = [...backend.state.slots.keys()].filter((k) => k.startsWith('d_rc_'))
    expect(copies).toHaveLength(1)
    expect(backend.puts().filter((c) => c.startsWith('PUT autosave d_rc_'))).toHaveLength(1)
  })
})

/** 对话框开着时项目 A → B（桌面交接等）：会话里的 pj 和项目 store 一起换 */
async function switchToB() {
  await act(async () => {
    setCurrentProjectId('p_b')
    useProjectStore.setState({ project: { open: true, id: 'p_b', document_dir: '/figs/b/tavottofile' } })
  })
}

describe('对话框开着时切了项目：A 的行不留在 B 里（Codex P1）', () => {
  it('A 的行已加载：切到 B 后 A 的行消失，按 B 重新读；B 没有行就不显示本项目那组', async () => {
    backend.putSlot('d_a', runtimeDoc('A 的排版'), PJ)
    backend.state.candidates.push({ doc_id: 'd_a', scope: 'project', name: 'A 的排版' })
    await mount()
    expect(rows('project').map((r) => r.dataset.recoverRow)).toEqual(['d_a'])
    backend.state.candidates.length = 0 // B 里什么都没有
    await switchToB()
    await flush()
    expect(rows('project')).toEqual([])
    expect(backend.state.candidatePjs).toEqual([PJ, 'p_b'])
  })

  it('A 的读取还在路上时切到 B：A 的结果回来也不显示', async () => {
    backend.putSlot('d_a', runtimeDoc('A 的排版'), PJ)
    backend.state.candidates.push({ doc_id: 'd_a', scope: 'project', name: 'A 的排版' })
    let release!: () => void
    backend.state.hold.candidates = new Promise<void>((r) => (release = r))
    await mount('load', false) // 请求被扣住：不等它落地
    await until(() => backend.state.candidatePjs.length === 1)
    await switchToB()
    backend.state.candidates.length = 0
    release()
    await flush()
    expect(rows('project')).toEqual([])
    expect(backend.state.candidatePjs).toEqual([PJ, 'p_b'])
  })

  it('点击时再核对：行属于别的项目（界面还没来得及换）就拒绝，不装、不写，状态条说没打开', async () => {
    backend.putSlot('d_a', runtimeDoc('A 的排版'), PJ)
    backend.state.candidates.push({ doc_id: 'd_a', scope: 'project', name: 'A 的排版' })
    await mount()
    const row = rows('project')[0]
    setCurrentProjectId('p_b') // 只换会话里的项目：行还留在界面上
    backend.state.calls.length = 0
    await act(async () => row.click())
    await flush()
    expect(useDocumentStore.getState().documentId).toBe('d_current')
    expect(backend.state.calls.filter((c) => c.startsWith('GET autosave') || c.startsWith('PUT'))).toEqual([])
    expect(useUiStore.getState().status).toMatchObject({ key: 'layout.openSkipped' })
  })

  it('列表是在 B 下读的、会话切回 A 而 B 的行还在界面上：点击被拒，不装、不读不写，随后按 A 重读', async () => {
    setCurrentProjectId('p_b')
    useProjectStore.setState({ project: { open: true, id: 'p_b', document_dir: '/figs/b/tavottofile' } })
    backend.putSlot('d_b', runtimeDoc('B 的排版'), 'p_b')
    backend.state.candidates.push({ doc_id: 'd_b', scope: 'project', name: 'B 的排版' })
    await mount()
    const row = rows('project')[0]
    expect(backend.state.candidatePjs).toEqual(['p_b'])
    setCurrentProjectId(PJ) // 只换会话里的项目：B 的行还留在界面上
    backend.state.calls.length = 0
    await act(async () => row.click())
    await flush()
    expect(useDocumentStore.getState().documentId).toBe('d_current')
    expect(backend.state.calls.filter((c) => c.startsWith('GET autosave') || c.startsWith('PUT'))).toEqual([])
    expect(useUiStore.getState().status).toMatchObject({ key: 'layout.openSkipped' })
    expect(backend.state.candidatePjs).toEqual(['p_b', PJ])
  })

  it('归属待确认那组同样：切到 B 之后 A 的行不在', async () => {
    backend.putSlot('d_u', runtimeDoc('谁的'))
    backend.state.candidates.push({ doc_id: 'd_u', scope: 'unknown', name: '谁的' })
    await mount()
    await expandOther()
    expect(rows('unknown')).toHaveLength(1)
    backend.state.candidates.length = 0
    await switchToB()
    await flush()
    expect(rows('unknown')).toEqual([])
  })
})

describe('读的途中切了项目：不把 A 的排版装进 B（Codex P1）', () => {
  it('本项目那组：读正文被扣住时切到 B，放行后什么都不装、没有对 B 的 autosave 写，状态条说没打开', async () => {
    backend.putSlot('d_mine', runtimeDoc('我的排版'), PJ)
    backend.state.candidates.push({ doc_id: 'd_mine', scope: 'project', name: '我的排版' })
    await mount()
    let release!: () => void
    backend.state.hold.autosaveGet = new Promise<void>((r) => (release = r))
    await act(async () => rows('project')[0].click())
    setCurrentProjectId('p_b') // 对话框还开着时用户把项目切走了
    backend.state.calls.length = 0
    release()
    await until(() => useUiStore.getState().status !== null)
    await flush()
    expect(useDocumentStore.getState().documentId).toBe('d_current')
    expect(backend.state.calls.filter((c) => c.startsWith('PUT autosave'))).toEqual([])
    expect(useUiStore.getState().status).toMatchObject({ key: 'layout.openSkipped' })
  })

  it('恢复副本：重读源正文被扣住时切到 B，放行后不装副本、不写', async () => {
    backend.putSlot('d_old', runtimeDoc('旧排版'))
    backend.state.candidates.push({ doc_id: 'd_old', scope: 'unknown', name: '旧排版' })
    await mount()
    await expandOther()
    await act(async () => rows('unknown')[0].click())
    let release!: () => void
    backend.state.hold.autosaveGet = new Promise<void>((r) => (release = r))
    await until(() => dialog().querySelector('[data-recover-copy]') !== null)
    await act(async () => dialog().querySelector<HTMLButtonElement>('[data-recover-copy]')!.click())
    setCurrentProjectId('p_b')
    backend.state.calls.length = 0
    release()
    await until(() => !rows('unknown')[0]?.disabled) // 确认流程走完（busy 复位）
    await flush()
    expect(useDocumentStore.getState().documentId).toBe('d_current')
    expect(backend.state.calls.filter((c) => c.startsWith('PUT autosave'))).toEqual([])
  })
})

describe('扫描之后正文被别的项目改了：不把别人的正文装进来（Codex P1）', () => {
  it('行扫描时是 R1，G 在点击之前存了 R2（owner=G）：不装、不读不写，状态条说被改过，列表刷新', async () => {
    backend.putSlot('d_mine', runtimeDoc('我的排版'), PJ)
    backend.state.candidates.push({ doc_id: 'd_mine', scope: 'project', name: '我的排版' })
    await mount()
    const row = rows('project')[0]
    backend.putSlot('d_mine', runtimeDoc('G 的排版', { updatedAt: 99 }), 'p_g') // 同一个 doc id，G 存了新的一版
    backend.state.calls.length = 0
    await act(async () => row.click())
    await flush()
    expect(useDocumentStore.getState().documentId).toBe('d_current')
    expect(backend.puts()).toEqual([])
    expect(useUiStore.getState().status).toMatchObject({ key: 'layout.recoverChanged' })
    expect(backend.state.calls).toContain('GET candidates project') // 列表刷新了
  })

  it('归属结论是对更早一版正文下的（revision ≠ 刚探测到的）：即使 owners=this 且已核对也不装', async () => {
    backend.putSlot('d_mine', runtimeDoc('我的排版'), PJ)
    backend.state.candidates.push({ doc_id: 'd_mine', scope: 'project', name: '我的排版' })
    await mount()
    const row = rows('project')[0]
    backend.putSlot('d_mine', runtimeDoc('G 又存了', { updatedAt: 99 }), PJ)
    backend.state.ownerAnswers.set(`${PJ}|d_mine`, { owner: 'this', evidence: 'owners', verified: true, revision: 'verdict-on-older' })
    backend.state.calls.length = 0
    await act(async () => row.click())
    await flush()
    expect(useDocumentStore.getState().documentId).toBe('d_current')
    expect(backend.puts()).toEqual([])
    expect(useUiStore.getState().status).toMatchObject({ key: 'layout.recoverChanged' })
  })

  it('响应头被吃了（读不到 revision）而行扫描时有 revision：无法核对就不装，不写，状态条说现在没法确认，列表刷新', async () => {
    backend.putSlot('d_mine', runtimeDoc('我的排版'), PJ)
    backend.state.candidates.push({ doc_id: 'd_mine', scope: 'project', name: '我的排版' })
    await mount()
    const row = rows('project')[0]
    backend.state.stripRevisionHeader = true
    backend.state.calls.length = 0
    await act(async () => row.click())
    await flush()
    expect(useDocumentStore.getState().documentId).toBe('d_current')
    expect(backend.puts()).toEqual([])
    expect(useUiStore.getState().status).toMatchObject({ key: 'layout.recoverUnverified' })
    expect(backend.state.calls).toContain('GET candidates project')
  })

  it('扫描之后是本项目自己又存了一版（owners=this 且已核对）：照常打开', async () => {
    backend.putSlot('d_mine', runtimeDoc('我的排版'), PJ)
    backend.state.candidates.push({ doc_id: 'd_mine', scope: 'project', name: '我的排版' })
    await mount()
    const row = rows('project')[0]
    backend.putSlot('d_mine', runtimeDoc('我的排版', { updatedAt: 99 }), PJ)
    await act(async () => row.click())
    await flush()
    expect(useDocumentStore.getState().documentId).toBe('d_mine')
  })
})

describe('本项目的本机排版：原 id 打开', () => {
  it('后端 owners 证明属本项目但本机索引仍记旧项目：打开前纠正，保存仍归本项目', async () => {
    backend.putSlot('d_mine', runtimeDoc('F 的排版'), PJ)
    backend.state.candidates.push({ doc_id: 'd_mine', scope: 'project', name: 'F 的排版' })
    localStorage.setItem('tavotto.docIndex', JSON.stringify([
      { id: 'd_mine', name: 'F 的排版', savedAt: 1, objects: 1, projectId: 'p_g', projectName: 'G' },
    ]))
    await mount()
    await act(async () => rows('project')[0].click())
    await flush()
    expect(useDocumentStore.getState().documentId).toBe('d_mine')
    expect(JSON.parse(localStorage.getItem('tavotto.docIndex') ?? '[]').find((e: { id: string }) => e.id === 'd_mine').projectId).toBe(PJ)
    expect(backend.state.owners.get('d_mine')).toBe(PJ)
  })

  it('旧项目索引与权威归属冲突且复核暂不可用：不打开、不纠正索引', async () => {
    backend.putSlot('d_mine', runtimeDoc('F 的排版'), PJ)
    backend.state.candidates.push({ doc_id: 'd_mine', scope: 'project', name: 'F 的排版' })
    localStorage.setItem('tavotto.docIndex', JSON.stringify([
      { id: 'd_mine', name: 'F 的排版', savedAt: 1, objects: 1, projectId: 'p_g', projectName: 'G' },
    ]))
    await mount()
    backend.state.fail.owner = 500
    expect(JSON.parse(localStorage.getItem('tavotto.docIndex') ?? '[]').find((e: { id: string }) => e.id === 'd_mine').projectId).toBe('p_g')
    await act(async () => rows('project')[0].click())
    await flush()
    expect(backend.state.calls).toContain('GET owner d_mine')
    expect(useDocumentStore.getState().documentId).toBe('d_current')
    expect(JSON.parse(localStorage.getItem('tavotto.docIndex') ?? '[]').find((e: { id: string }) => e.id === 'd_mine').projectId).toBe('p_g')
    expect(backend.writes().filter((c) => c.includes('d_mine'))).toEqual([])
  })

  it('点一行 = 按原 id 打开（与最近文档同一条路），对话框关闭', async () => {
    backend.putSlot('d_mine', runtimeDoc('我的排版'), PJ)
    backend.state.candidates.push({ doc_id: 'd_mine', scope: 'project', name: '我的排版' })
    await mount()
    await act(async () => rows('project')[0].click())
    await flush()
    expect(useDocumentStore.getState().documentId).toBe('d_mine')
    expect(useUiStore.getState().layoutOpen).toBe(false)
  })
})
