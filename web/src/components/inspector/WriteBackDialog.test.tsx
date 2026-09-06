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
import { i18n } from '@/i18n'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useProjectStore } from '@/store/projectStore'
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
  // 有用例会切到 en-US；不还原的话下一条断言中文的用例会莫名其妙地红
  if (i18n.language !== 'zh-CN') await i18n.changeLanguage('zh-CN')
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_writeback')
  useAssetStore.setState({ byId: { 'Fig1.pdf': { mtime: 1755000000 } } } as never)
  // 备份目录是**每项目**设置：上一条用例塞进去的绝对路径不能漏给下一条
  useProjectStore.setState({ project: null } as never)
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

const confirm = async (label = '写回原始文件') => {
  const btn = [...document.body.querySelectorAll('button')].find((b) =>
    b.textContent?.includes(label),
  )
  expect(btn, `找不到「${label}」按钮`).toBeTruthy()
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

  /*
   * file_locked 的**界面**这半场（issue #30）。后端的 `error` 字段是中文原句，
   * 而 `errors.json` 两侧都登记过 `backend.file_locked`——不查它，英文界面上
   * 就会原样吐出一句中文。这两条钉的正是「查了没有」。
   *
   * 这里是 jsdom 的快线；真实浏览器 + 真实独占锁那半场在
   * `web/e2e/error-recovery-en.spec.ts`，只在 windows-exe-smoke 上执行。
   */
  const LOCKED = {
    // app.py 的 _write_back_error 原样：**中文**，且带文件名
    error: 'Fig1.pdf 被其他程序占用。请关闭正在打开它的程序（PDF 阅读器 / 看图工具）后重试。',
    code: 'file_locked',
    file: 'Fig1.pdf',
    updated: [],
    rolled_back: [],
    rollback_failed: [],
  }

  it('file_locked（en-US）：说英文、给下一步，不漏中文', async () => {
    stubFetch(409, LOCKED)
    await i18n.changeLanguage('en-US')
    render()
    await confirm('Write back')
    expect(text()).toContain('The file is locked by another program')
    expect(text()).toContain('retry')
    // 后端原句一个字都不许出现在英文界面上
    expect(text()).not.toMatch(/[\u4e00-\u9fff]/)
  })

  it('file_locked（zh-CN）：走的是文案表那句，不是后端拼好的原句', async () => {
    stubFetch(409, LOCKED)
    render()
    await confirm()
    // 文案表那句以「文件被其他程序占用」起头；后端原句是「Fig1.pdf 被其他程序占用」
    expect(text()).toContain('文件被其他程序占用')
    expect(text()).not.toContain('Fig1.pdf 被其他程序占用')
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

/**
 * 确认页的信息结构（审计 T34）。
 *
 * 原来这里是三段并排的散文：覆盖什么、备份到哪、怎么恢复，每段都以一个粗体
 * 词起头，路径整条铺在句子中间。三件事都是真的，但要**读完**才建立得起
 * 「我按下去会发生什么」——而这正是一个覆盖磁盘原件的确认框最该一眼给出的。
 *
 * 现在：短摘要（多少项、写进哪张图）→ 目标文件清单 → 备份与恢复折叠项。
 * 折叠的是**说明**不是事实：收起时备份到哪个目录就写在那一行上。
 *
 * **这几条判据只管信息呈现。** 事务保护（prepare → verify → commit）由上面
 * 那几个 describe 钉住，任何一条都不许因为「文案更短了」而松口。
 */
describe('确认页的信息结构', () => {
  /** 对话框正文里那个「备份与恢复」折叠项 */
  const backupDetails = () => {
    const all = [...document.body.querySelectorAll('details')]
    return all.find((d) => d.querySelector('summary')?.textContent?.includes('备份')) ?? null
  }

  it('短摘要说清「多少项修改」与「写进哪张图」，不必读正文', async () => {
    render()
    // 1 条 override、面板 Fig1.pdf
    expect(text()).toContain('将把 1 项修改写回「Fig1」的原始文件')
  })

  it('目标文件成清单列出，一眼能数出覆盖的是哪几个', async () => {
    render()
    const items = [...document.body.querySelectorAll('li')].map((li) => li.textContent)
    expect(items).toContain('Fig1.pdf')
    expect(items).toContain('Fig1.png')
    expect(text()).toContain('将覆盖 figures 目录里的这些文件')
  })

  it('多个面板时摘要报「几张图」，清单把每张图的两个后缀都列出来', async () => {
    const second: PanelObject = { ...panel, id: 'p2', fileId: 'Fig2.pdf', overrides: [
      { gid: 'axes_0', prop: 'facecolor', value: '#fff' },
      { gid: 'axes_0.title', prop: 'text', value: 'x' },
    ] }
    act(() =>
      root.render(
        <TooltipProvider>
          <WriteBackDialog panels={[panel, second]} open onOpenChange={() => {}} />
        </TooltipProvider>,
      ),
    )
    // 1 + 2 项修改、2 张图
    expect(text()).toContain('将把 3 项修改写回 2 张图的原始文件')
    const items = [...document.body.querySelectorAll('li')].map((li) => li.textContent)
    expect(items).toEqual(
      expect.arrayContaining(['Fig1.pdf', 'Fig1.png', 'Fig2.pdf', 'Fig2.png']),
    )
  })

  it('备份位置就在这个对话框里：收起时写着末级目录，不是只藏在设置页的问号后面', () => {
    useProjectStore.setState({
      project: {
        backup_dir: '/Users/somebody/Library/Application Support/Tavotto/cache/original_backups',
      },
    } as never)
    render()
    const summary = backupDetails()?.querySelector('summary')
    expect(summary, '确认页里找不到备份折叠项').toBeTruthy()
    expect(summary!.textContent).toContain('original_backups')
    // 摘要行上不铺全路径——读完那条才看得见「备份」两个字的正是原来的毛病
    expect(summary!.textContent).not.toContain('/Users/somebody')
  })

  it('展开项里是全路径 + 复制入口 + 恢复办法', () => {
    useProjectStore.setState({
      project: {
        backup_dir: '/Users/somebody/Library/Application Support/Tavotto/cache/original_backups',
      },
    } as never)
    render()
    const d = backupDetails()!
    const body = d.textContent ?? ''
    expect(body).toContain('/Users/somebody/Library/Application Support/Tavotto/cache/original_backups')
    expect(body).toContain('复制')
    // 恢复路径仍然完整可查：历史 + 备份目录 + 脚本不受影响
    expect(body).toContain('历史')
    expect(body).toContain('备份目录')
    expect(body).toContain('脚本不会被改动')
  })

  it('主动作按钮直说「写回原始文件」，不是含糊的「确认」', () => {
    render()
    const primary = [...document.body.querySelectorAll('button')].find((b) =>
      b.textContent?.includes('写回原始文件'),
    )
    expect(primary, '找不到主动作按钮').toBeTruthy()
    expect(text()).not.toContain('确认写回')
  })

  it('成功回执里的备份位置同样是「末级目录 + 展开看全路径」', async () => {
    stubFetch(200, OK_BODY)
    render()
    await confirm()
    const d = backupDetails()
    expect(d, '回执里找不到备份折叠项').toBeTruthy()
    expect(d!.querySelector('summary')!.textContent).toContain('0818_101010')
    expect(d!.querySelector('summary')!.textContent).not.toContain('/data/original_backups')
    expect(d!.textContent).toContain(OK_BODY.backup_dir)
  })
})
