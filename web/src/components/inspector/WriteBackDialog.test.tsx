/**
 * 写回对话框的阻断分支与成功回执。
 *
 * 写回是本工具唯一会覆盖用户磁盘原件的动作，后端把它做成了
 * prepare → verify → commit 的事务：素材被外部改过、脚本在会话背后改过、
 * 热态与干净重放对不上，三种都以 409 + 专属 code 阻断（原文件零改动）。
 *
 * 这三条**都不是「再点一次就好」的错误**。文案要么说清该去做什么（刷新素材 /
 * 重新渲染），要么说清这是引擎级问题、该报告给开发者——否则用户面对一句
 * 「更新失败：HTTP 409」只会反复点确认，而每一次都注定失败。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { WriteBackDialog } from '@/components/inspector/UpdateSourceButton'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { emptyProject, type PanelObject } from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const panel: PanelObject = {
  id: 'p1',
  type: 'panel',
  fileId: 'Fig1.pdf',
  fileKind: 'pdf',
  nativeW: 80,
  nativeH: 60,
  overrides: [{ gid: 'axes_0.title', prop: 'text', value: '改过的标题' }],
  x: 0,
  y: 0,
  w: 80,
  h: 60,
}

const OK_BODY = {
  updated: ['Fig1.pdf', 'Fig1.png'],
  backup_dir: '/data/original_backups/0818_101010',
  warnings: [],
  baked: true,
  patch_hash: 'sha256:abc',
  source_sha1: { 'Fig1.pdf': 'aa', 'Fig1.png': 'bb' },
  manifest_hash: 'sha256:def',
  verification: { replay: 'ok', elements: 17 },
}

/** 写回端点回这个响应；其余端点（重拉素材列表）一律给空成功。 */
function stubFetch(status: number, body: unknown) {
  const calls: string[] = []
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const url = String(input)
    calls.push(url)
    if (url.includes('/api/engine/update_source')) {
      return new Response(JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json' },
      })
    }
    return new Response(JSON.stringify({ figures_dir: '/figs', panels: [] }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }) as typeof fetch
  return calls
}

let container: HTMLDivElement
let root: Root

beforeEach(async () => {
  localStorage.clear()
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_writeback')
  useAssetStore.setState({ byId: { 'Fig1.pdf': { mtime: 1755000000 } } } as never)
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
  vi.restoreAllMocks()
})

const render = () =>
  act(() =>
    root.render(
      <TooltipProvider>
        <WriteBackDialog panels={[panel]} open onOpenChange={() => {}} />
      </TooltipProvider>,
    ),
  )

/** 对话框走 Portal，落在 document.body 上 */
const text = () => document.body.textContent ?? ''

const confirm = async () => {
  const btn = [...document.body.querySelectorAll('button')].find((b) =>
    b.textContent?.includes('确认写回'),
  )
  expect(btn, '找不到「确认写回」按钮').toBeTruthy()
  await act(async () => {
    btn!.click()
    await Promise.resolve()
  })
  // 状态更新排在 fetch 之后的一轮微任务里
  await act(async () => {
    await new Promise<void>((r) => setTimeout(r, 0))
  })
}

describe('写回被阻断时的文案', () => {
  it('source_changed：告诉用户去刷新素材面板，不是「重试」', async () => {
    stubFetch(409, {
      error: 'Fig1.pdf 已被外部修改（本工具之外）…',
      code: 'source_changed',
      file: 'Fig1.pdf',
      expected: 1,
      actual: 2,
    })
    render()
    await confirm()
    expect(text()).toContain('刷新素材面板')
    expect(text()).toContain('Fig1.pdf')
  })

  it('script_changed：告诉用户当前渲染的还是旧代码，需重新渲染', async () => {
    stubFetch(409, {
      error: '脚本已改动…',
      code: 'script_changed',
      script: 'fig1.py',
    })
    render()
    await confirm()
    expect(text()).toContain('旧代码')
    expect(text()).toContain('重新渲染')
    expect(text()).toContain('fig1.py')
  })

  it('replay_divergence：醒目警示 + 列出前几条分歧元素', async () => {
    stubFetch(409, {
      error: '热编辑状态与全新重放不一致…',
      code: 'replay_divergence',
      diffs: [
        { gid: 'axes_0', field: 'bbox', hot: [0, 0, 1, 1], fresh: [0, 0.4, 1, 1] },
        { gid: 'axes_0.title', field: 'anchor', hot: [0.5, 0.1], fresh: [0.5, 0.6] },
      ],
    })
    render()
    await confirm()
    expect(text()).toContain('写回已阻断')
    expect(text()).toContain('报告给开发者')
    expect(text()).toContain('axes_0.bbox')
    expect(text()).toContain('axes_0.title.anchor')
  })

  it('未知错误仍旧原样呈现，不吞掉后端说了什么', async () => {
    stubFetch(500, { error: 'worker 炸了' })
    render()
    await confirm()
    expect(text()).toContain('worker 炸了')
  })
})

describe('写回成功的回执', () => {
  it('带上「已通过干净重放校验」与元素数', async () => {
    stubFetch(200, OK_BODY)
    render()
    await confirm()
    expect(text()).toContain('已更新以下文件')
    expect(text()).toContain('已通过干净重放校验（17 个元素一致）')
    expect(text()).toContain(OK_BODY.backup_dir)
  })

  it('热态无从对照时不谎称校验过', async () => {
    stubFetch(200, {
      ...OK_BODY,
      verification: { replay: 'fresh_only', elements: 0, reason: 'hot_state_differs' },
    })
    render()
    await confirm()
    expect(text()).toContain('已更新以下文件')
    expect(text()).not.toContain('已通过干净重放校验')
  })

  it('落盘后尺寸对不上要说出来（文件已换、备份仍在）', async () => {
    stubFetch(200, { ...OK_BODY, post_check: 'size_mismatch' })
    render()
    await confirm()
    expect(text()).toContain('页面尺寸与重放结果对不上')
    expect(text()).toContain('备份')
  })
})

describe('前置校验的入参', () => {
  it('请求带上素材当前的 mtime（后端据此判 source_changed）', async () => {
    const bodies: string[] = []
    globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).includes('/api/engine/update_source')) {
        bodies.push(String(init?.body))
        return new Response(JSON.stringify(OK_BODY), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      return new Response(JSON.stringify({ figures_dir: '/figs', panels: [] }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }) as typeof fetch
    render()
    await confirm()
    expect(JSON.parse(bodies[0]).expected_mtime).toBe(1755000000)
  })
})
