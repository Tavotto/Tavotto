/**
 * 「把这些修改用到同脚本的其他图…」窗口（2026-10-01，设计稿 C5 / C11）。
 *
 * 入口搬到右键菜单（`canvas/objectContextMenu.test.tsx` 看护出现条件），这里守窗口自己：
 *   - 目标图在画布上：合并进那个面板，一条历史，**不碰磁盘**——行为与搬家前一致；
 *   - 目标图不在画布上：「同步并写回」走标准写回窗口（`WriteBackDialog` → `updateSourceFiles`，
 *     与属性栏 / ⋯ 同一条写回事务），不再另有一条写回路径；写进文件的是
 *     「目标自己的基线 + 同步来的」，同名 gid+prop 同步来的赢；
 *   - 在标准窗口里取消 / 关掉：回到映射结果那一步，什么都没写。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { literal } from '@/i18n'
import { updateSourceFiles, syncOverrides, type PanelInfo } from '@/lib/api'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useProjectStore } from '@/store/projectStore'
import { openSyncOverrides, useSyncOverrides } from '@/store/syncOverridesStore'
import { emptyProject, type PanelObject } from '@/types/document'
import { SyncOverridesHost } from './SyncOverridesDialog'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  syncOverrides: vi.fn(),
  updateSourceFiles: vi.fn(),
  fetchHistory: vi.fn(async () => ({ versions: [] })),
}))

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const info = (id: string, over: Partial<PanelInfo> = {}): PanelInfo => ({
  id,
  name: id.replace('.pdf', ''),
  folder: '.',
  kind: 'pdf',
  native_w_mm: 80,
  native_h_mm: 60,
  mtime: 111,
  script: 'fig.py',
  ...over,
})

const ASSETS = [
  info('Fig1.pdf'),
  info('Fig2.pdf', {
    mtime: 222,
    baked_overrides: [
      { gid: 'axes_0.title', prop: 'text', value: '旧标题' },
      { gid: 'axes_0.xlabel', prop: 'text', value: '保留我' },
    ],
  }),
  info('Fig3.pdf', { mtime: 333 }),
]

const MAPPED = [{ gid: 'axes_0.title', prop: 'text', value: '新标题' }]

const panel = (id: string, fileId: string, overrides: PanelObject['overrides'] = []): PanelObject =>
  ({
    id, type: 'panel', fileId, fileKind: 'pdf', nativeW: 80, nativeH: 60,
    x: 0, y: 0, w: 80, h: 60, script: 'fig.py', name: fileId.replace('.pdf', ''), overrides,
  }) as unknown as PanelObject

let root: Root
let host: HTMLDivElement

const text = () => document.body.textContent ?? ''
const click = async (el: Element | null | undefined) => {
  expect(el, '找不到要点的元素').toBeTruthy()
  await act(async () => {
    ;(el as HTMLElement).click()
    await new Promise<void>((r) => setTimeout(r, 0))
  })
}
const target = (id: string) => document.body.querySelector(`[data-sync-target="${id}"]`)
const syncDialog = () => document.body.querySelector('[data-dialog="sync-overrides"]')

async function start(canvas: PanelObject[]) {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_sync_dialog')
  useDocumentStore.getState().commit(literal('放面板'), (d) => {
    d.objects.push(panel('p1', 'Fig1.pdf', [{ gid: 'axes_0.title', prop: 'text', value: '新标题' }]), ...canvas)
  })
  useDocumentStore.setState({ past: [], future: [] })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  await act(async () => {
    root.render(
      <TooltipProvider>
        <SyncOverridesHost />
      </TooltipProvider>,
    )
  })
  await act(async () => openSyncOverrides('p1'))
}

beforeEach(() => {
  document.body.innerHTML = ''
  vi.mocked(syncOverrides).mockReset().mockResolvedValue({ mapped: MAPPED, skipped: [], unmatched: [] })
  vi.mocked(updateSourceFiles).mockReset().mockResolvedValue({
    updated: ['Fig2.pdf', 'Fig2.png'],
    backup_dir: '/b/0101',
    verification: { replay: 'ok', elements: 5 },
  } as never)
  globalThis.fetch = (async () =>
    new Response(JSON.stringify({ figures_dir: '/figs', panels: ASSETS }), { status: 200 })) as typeof fetch
  useProjectStore.setState({ project: null } as never)
  useAssetStore.setState({
    panels: ASSETS,
    byId: Object.fromEntries(ASSETS.map((a) => [a.id, a])),
  } as never)
})

afterEach(async () => {
  await act(async () => root?.unmount())
  useSyncOverrides.setState({ panelId: null })
  document.body.innerHTML = ''
})

describe('选目标图', () => {
  it('列出同脚本的兄弟图，不含它自己；点一张就算映射', async () => {
    await start([])
    expect(target('Fig1.pdf')).toBeNull()
    expect(target('Fig2.pdf')).toBeTruthy()
    await click(target('Fig2.pdf'))
    expect(syncOverrides).toHaveBeenCalledWith('Fig1.pdf', 'Fig2.pdf', [
      { gid: 'axes_0.title', prop: 'text', value: '新标题' },
    ])
    expect(text()).toContain('可映射')
  })

  it('出发的面板没了（撤销 / 删除）：窗口跟着关', async () => {
    await start([])
    expect(syncDialog()).not.toBeNull()
    await act(async () => {
      useDocumentStore.getState().commit(literal('删'), (d) => {
        d.objects = d.objects.filter((o) => o.id !== 'p1')
      })
    })
    expect(syncDialog()).toBeNull()
  })
})

describe('映射请求的竞态：返回后换目标，旧响应不许记到新目标名下', () => {
  const dfd = <T,>() => {
    let resolve!: (v: T) => void
    const promise = new Promise<T>((r) => (resolve = r))
    return { promise, resolve }
  }

  it('A 慢、B 快、A 后到：结果仍是 B 的，「同步并写回」写进 B 的文件用的是 B 的映射', async () => {
    const A = dfd<never>()
    vi.mocked(syncOverrides).mockImplementation(((_from: string, to: string) =>
      to === 'Fig2.pdf'
        ? A.promise
        : Promise.resolve({
            mapped: [{ gid: 'axes_0.title', prop: 'text', value: 'B的映射' }],
            skipped: [],
            unmatched: [],
          })) as never)
    await start([])
    await click(target('Fig2.pdf')) // A：一直不回
    await click([...document.body.querySelectorAll('button')].find((b) => b.textContent?.trim() === '返回'))
    await click(target('Fig3.pdf')) // B：立刻回
    expect(text()).toContain('可映射')
    // A 这时才到
    await act(async () => {
      A.resolve({
        mapped: [{ gid: 'axes_0.title', prop: 'text', value: 'A的映射' }],
        skipped: [],
        unmatched: [],
      } as never)
      await new Promise<void>((r) => setTimeout(r, 0))
    })
    await click(document.body.querySelector('[data-sync="write-back"]'))
    await click(document.body.querySelector('[data-write-back="confirm"]'))
    expect(updateSourceFiles).toHaveBeenCalledTimes(1)
    const [id, patches] = vi.mocked(updateSourceFiles).mock.calls[0]
    expect(id).toBe('Fig3.pdf')
    expect(patches).toEqual([{ gid: 'axes_0.title', prop: 'text', value: 'B的映射' }])
  })

  it('A 在途时点返回、不再选别的：A 后到也不会冒出结果', async () => {
    const A = dfd<never>()
    vi.mocked(syncOverrides).mockReturnValue(A.promise as never)
    await start([])
    await click(target('Fig2.pdf'))
    await click([...document.body.querySelectorAll('button')].find((b) => b.textContent?.trim() === '返回'))
    await act(async () => {
      A.resolve({ mapped: MAPPED, skipped: [], unmatched: [] } as never)
      await new Promise<void>((r) => setTimeout(r, 0))
    })
    expect(target('Fig2.pdf')).toBeTruthy() // 仍在选目标那一步
    expect(text()).not.toContain('可映射')
  })
})

describe('目标图在画布上：合并，不碰磁盘', () => {
  it('「合并到画布上的图」→ 目标面板的修改被同步来的覆盖，一条历史，窗口关掉', async () => {
    await start([panel('p3', 'Fig3.pdf')])
    await click(target('Fig3.pdf'))
    await click(document.body.querySelector('[data-sync="merge"]'))
    const p3 = useDocumentStore.getState().doc.objects.find((o) => o.id === 'p3') as PanelObject
    expect(p3.overrides.map((o) => [o.gid, o.prop, o.value])).toEqual([['axes_0.title', 'text', '新标题']])
    expect(useDocumentStore.getState().past).toHaveLength(1)
    expect(updateSourceFiles).not.toHaveBeenCalled()
    expect(syncDialog()).toBeNull()
  })
})

describe('目标图不在画布上：同步并写回走标准写回窗口', () => {
  it('打开的是写回窗口（目标文件清单 + 备份 + 确认钮），不再自己调写回', async () => {
    await start([])
    await click(target('Fig2.pdf'))
    await click(document.body.querySelector('[data-sync="write-back"]'))
    expect(document.body.querySelector('[data-write-back="confirm"]')).toBeTruthy()
    expect(text()).toContain('Fig2.pdf')
    expect(text()).toContain('Fig2.png')
    // 到确认之前一个字都没写
    expect(updateSourceFiles).not.toHaveBeenCalled()
    // 同步窗口先让位，不叠两层
    expect(syncDialog()).toBeNull()
    // 临时面板不提供写回记录页、不收画布标注
    expect(document.body.querySelector('[role="tablist"]')).toBeNull()
  })

  it('确认 → 写进文件的是「目标自己的基线 + 同步来的」，同名 gid+prop 同步来的赢；不带标注', async () => {
    await start([])
    await click(target('Fig2.pdf'))
    await click(document.body.querySelector('[data-sync="write-back"]'))
    await click(document.body.querySelector('[data-write-back="confirm"]'))
    expect(updateSourceFiles).toHaveBeenCalledTimes(1)
    expect(updateSourceFiles).toHaveBeenCalledWith(
      'Fig2.pdf',
      [
        { gid: 'axes_0.title', prop: 'text', value: '新标题' },
        { gid: 'axes_0.xlabel', prop: 'text', value: '保留我' },
      ],
      undefined,
      222, // 目标自己的 mtime：409 source_changed 的依据
    )
    // 结果页：已更新的文件
    expect(text()).toContain('Fig2.pdf')
    // 点「完成」整条流程结束
    await click([...document.body.querySelectorAll('button')].find((b) => b.textContent?.trim() === '完成'))
    expect(useSyncOverrides.getState().panelId).toBeNull()
    expect(syncDialog()).toBeNull()
  })

  it('在写回窗口里取消：回到映射结果，什么都没写', async () => {
    await start([])
    await click(target('Fig2.pdf'))
    await click(document.body.querySelector('[data-sync="write-back"]'))
    await click([...document.body.querySelectorAll('button')].find((b) => b.textContent?.trim() === '取消'))
    expect(updateSourceFiles).not.toHaveBeenCalled()
    expect(document.body.querySelector('[data-write-back="confirm"]')).toBeNull()
    expect(syncDialog()).not.toBeNull()
    expect(text()).toContain('可映射')
  })

  it('没有可同步的项：两颗主按钮都灰着', async () => {
    vi.mocked(syncOverrides).mockResolvedValue({ mapped: [], skipped: [], unmatched: [] })
    await start([])
    await click(target('Fig2.pdf'))
    expect(document.body.querySelector<HTMLButtonElement>('[data-sync="write-back"]')?.disabled).toBe(true)
    expect(text()).toContain('没有可同步的项')
  })
})
