// 壳自带的失败页 `src-tauri/shell/error.html`（原生救援页，ADR 0130）的行为测试。
//
// 这张页面不在前端构建里（`tauri://` 源下的静态 HTML），所以没有别的地方会跑它的逻辑。这里把页面的
// 脚本原样取出来，在 jsdom 里对着一个假的 `__TAURI_INTERNALS__.invoke` 跑——判据的主语是**页面向壳发了哪些
// 命令、带了什么参数**，以及用户看到了什么，不是页面里某个变量。
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

// vitest 在 web/ 下跑（cwd = web/）；jsdom 环境里 import.meta.url 不是 file: 地址
const SHELL = resolve(process.cwd(), '../src-tauri')
const HTML = readFileSync(resolve(SHELL, 'shell/error.html'), 'utf-8')
// 用 HTML 解析器取，不用正则：页面只有一个内联 <script>，body 里其余节点就是页面骨架
const PARSED = new DOMParser().parseFromString(HTML, 'text/html')
const SCRIPT = PARSED.querySelector('script')!.textContent!
PARSED.querySelectorAll('script').forEach((el) => el.remove())
const BODY = PARSED.body.innerHTML

type Call = { cmd: string; args?: Record<string, unknown> }
type Handler = (args?: Record<string, unknown>) => unknown

const ELIGIBLE = {
  eligible: true,
  reason: null,
  stage: 'handshake',
  code: 'sidecar_exited',
  exit_code: 1,
  update: true,
  upload: true,
}

let calls: Call[]

async function flush() {
  for (let i = 0; i < 8; i++) await new Promise((r) => setTimeout(r, 0))
}

async function openPage(handlers: Record<string, Handler>, query = 'lang=zh-CN&msg=boom&log=%2Ftmp%2Fx.log') {
  calls = []
  document.documentElement.innerHTML = '<head></head><body></body>'
  document.body.innerHTML = BODY
  window.history.replaceState({}, '', `/error.html?${query}`)
  ;(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {
    invoke: async (cmd: string, args?: Record<string, unknown>) => {
      calls.push({ cmd, args })
      const h = handlers[cmd]
      if (!h) throw 'unexpected_command'
      return h(args)
    },
  }
  new Function(SCRIPT)()
  await flush()
}

const $ = (id: string) => document.getElementById(id) as HTMLElement
const click = async (id: string) => {
  ;($(id) as HTMLButtonElement).click()
  await flush()
}
const cmds = () => calls.map((c) => c.cmd)

beforeEach(() => {
  calls = []
})
afterEach(() => {
  delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__
})

describe('error.html 默认只露一句话和一个主按钮', () => {
  it('可用时：一个主按钮可见，其余动作折叠在「其他选项」里', async () => {
    await openPage({ rescue_status: () => ELIGIBLE })
    expect(($('retryRow') as HTMLElement).hidden).toBe(false)
    const more = $('more') as HTMLDetailsElement
    expect(more.hidden).toBe(false)
    expect(more.open).toBe(false)
    expect(more.contains($('check')) && more.contains($('diag'))).toBe(true)
    expect(($('tech') as HTMLDetailsElement).open).toBe(false)
    expect($('title').textContent).toBe('Tavotto 启动失败')
    // 打开页面本身只问状态，什么都不做
    expect(cmds()).toEqual(['rescue_status'])
  })

  it('壳的报错原文原样显示且不进任何命令参数', async () => {
    await openPage({ rescue_status: () => ELIGIBLE }, 'lang=zh-CN&msg=SECRET_STDERR_TEXT&log=%2Fhome%2Fu%2Fx.log')
    expect($('msg').textContent).toBe('SECRET_STDERR_TEXT')
    await click('check')
    await click('diag')
    expect(JSON.stringify(calls)).not.toContain('SECRET_STDERR_TEXT')
    expect(JSON.stringify(calls)).not.toContain('/home/u')
  })

  it('进过编辑态（工作状态未知）：只说明原因，不提供任何动作', async () => {
    await openPage({ rescue_status: () => ({ ...ELIGIBLE, eligible: false, reason: 'editing_state_unknown' }) })
    expect(($('retryRow') as HTMLElement).hidden).toBe(true)
    expect(($('more') as HTMLElement).hidden).toBe(true)
    expect(($('unsafe') as HTMLElement).hidden).toBe(false)
    expect($('unsafe').textContent).toContain('未保存')
    expect(cmds()).toEqual(['rescue_status'])
  })

  it('壳不认这个页面（status 被拒）：同样没有动作', async () => {
    await openPage({
      rescue_status: () => {
        throw 'not_trusted_caller'
      },
    })
    expect(($('retryRow') as HTMLElement).hidden).toBe(true)
    expect(($('more') as HTMLElement).hidden).toBe(true)
  })

  it('英文界面不残留中文', async () => {
    await openPage({ rescue_status: () => ELIGIBLE }, 'lang=en-US&msg=boom')
    await click('diag')
    const visible = [...document.querySelectorAll('h1,p,button,summary,.status')]
      .map((e) => e.textContent)
      .join('\n')
    expect(visible).not.toMatch(/[一-鿿]/)
    expect($('title').textContent).toBe('Tavotto failed to start')
  })
})

describe('重新尝试', () => {
  it('点主按钮只调一次 rescue_retry，重复点击不再发', async () => {
    await openPage({ rescue_status: () => ELIGIBLE, rescue_retry: () => new Promise(() => {}) })
    ;($('retry') as HTMLButtonElement).click()
    ;($('retry') as HTMLButtonElement).click()
    await flush()
    expect(cmds().filter((c) => c === 'rescue_retry')).toHaveLength(1)
    expect($('retryStatus').textContent).toContain('重新启动')
  })

  it('壳拒绝重启时如实显示，按钮恢复可点', async () => {
    await openPage({
      rescue_status: () => ELIGIBLE,
      rescue_retry: () => {
        throw 'editing_state_unknown'
      },
    })
    await click('retry')
    expect($('retryStatus').textContent).toContain('editing_state_unknown')
    expect(($('retry') as HTMLButtonElement).disabled).toBe(false)
  })
})

describe('检查并安装修复', () => {
  it('检查失败（网络 / 清单）显示「检查失败」，绝不说已是最新', async () => {
    await openPage({
      rescue_status: () => ELIGIBLE,
      rescue_update_check: () => {
        throw { code: 'all_sources_failed', attempts: [] }
      },
    })
    await click('check')
    const t = $('updateStatus').textContent!
    expect(t).toContain('检查失败')
    expect(t).toContain('all_sources_failed')
    expect(t).not.toContain('最新')
    expect(($('installRow') as HTMLElement).hidden).toBe(true)
  })

  it('只有「检查成功且没有更新」才说已是最新', async () => {
    await openPage({ rescue_status: () => ELIGIBLE, rescue_update_check: () => null })
    await click('check')
    expect($('updateStatus').textContent).toContain('最新')
    expect(($('installRow') as HTMLElement).hidden).toBe(true)
  })

  it('发现新版本：先展示版本，确认后安装的是带着该版本号的那一次请求', async () => {
    await openPage({
      rescue_status: () => ELIGIBLE,
      rescue_update_check: () => ({ version: '9.9.9', notes: null, date: null, source: 'github' }),
      rescue_update_install: () => 'github',
      rescue_retry: () => new Promise(() => {}),
    })
    await click('check')
    expect($('install').textContent).toContain('9.9.9')
    expect(cmds()).not.toContain('rescue_update_install')
    await click('install')
    const inst = calls.find((c) => c.cmd === 'rescue_update_install')!
    expect(inst.args).toEqual({ expectedVersion: '9.9.9' })
    expect(cmds().indexOf('rescue_retry')).toBeGreaterThan(cmds().indexOf('rescue_update_install'))
  })

  it('候选已被换掉（candidate_changed）：不重启，收起安装按钮，提示重新检查', async () => {
    await openPage({
      rescue_status: () => ELIGIBLE,
      rescue_update_check: () => ({ version: '9.9.9' }),
      rescue_update_install: () => {
        throw { code: 'candidate_changed', attempts: [] }
      },
    })
    await click('check')
    await click('install')
    expect(cmds()).not.toContain('rescue_retry')
    expect(($('installRow') as HTMLElement).hidden).toBe(true)
    expect($('updateStatus').textContent).toContain('重新检查')
  })

  it('签名 / 下载失败：显示没有完成安装，不重启', async () => {
    await openPage({
      rescue_status: () => ELIGIBLE,
      rescue_update_check: () => ({ version: '9.9.9' }),
      rescue_update_install: () => {
        throw { code: 'all_sources_failed', attempts: [] }
      },
    })
    await click('check')
    await click('install')
    expect($('updateStatus').textContent).toContain('没有完成安装')
    expect(cmds()).not.toContain('rescue_retry')
  })

  it('没配置更新通道：不放检查按钮，如实说明', async () => {
    await openPage({ rescue_status: () => ({ ...ELIGIBLE, update: false }) })
    expect(($('check') as HTMLElement).hidden).toBe(true)
    expect($('updateStatus').textContent).toContain('没有配置')
  })
})

describe('启动诊断：预览 / 保存不联网，只有单独点「发送」才上传', () => {
  it('打开预览与保存都不会触发 rescue_diag_send', async () => {
    await openPage({
      rescue_status: () => ELIGIBLE,
      rescue_diag_preview: () => ({ entries: [{ name: 'report.json', bytes: 10 }], report: '{"source":"shell"}', bytes: 100 }),
      rescue_diag_save: () => 'x.zip',
    })
    await click('diag')
    expect($('preview').textContent).toContain('"source":"shell"')
    await click('save')
    expect($('diagStatus').textContent).toContain('x.zip')
    expect(cmds()).not.toContain('rescue_diag_send')
  })

  it('发送失败可重试；成功后给出编号并不再允许重复发送', async () => {
    let n = 0
    await openPage({
      rescue_status: () => ELIGIBLE,
      rescue_diag_preview: () => ({ entries: [], report: '{}', bytes: 1 }),
      rescue_diag_send: () => {
        if (++n === 1) throw { code: 'network', retryable: true, retry_after: null }
        return { report_id: 'TVD-ABCD-EFGH-JKMN-PQRS' }
      },
    })
    await click('diag')
    await click('send')
    expect($('diagStatus').textContent).toContain('可重试')
    expect(($('send') as HTMLButtonElement).disabled).toBe(false)
    await click('send')
    expect($('diagStatus').textContent).toContain('TVD-ABCD-EFGH-JKMN-PQRS')
    expect(($('send') as HTMLButtonElement).disabled).toBe(true)
    await click('send')
    expect(cmds().filter((c) => c === 'rescue_diag_send')).toHaveLength(2)
  })

  it('不可重试的失败引导改为保存到文件', async () => {
    await openPage({
      rescue_status: () => ELIGIBLE,
      rescue_diag_preview: () => ({ entries: [], report: '{}', bytes: 1 }),
      rescue_diag_send: () => {
        throw { code: 'rejected', retryable: false, retry_after: null }
      },
    })
    await click('diag')
    await click('send')
    expect($('diagStatus').textContent).toContain('保存到文件')
  })

  it('发送被关闭（环境开关）：发送键不可用，保存仍可用', async () => {
    await openPage({
      rescue_status: () => ({ ...ELIGIBLE, upload: false }),
      rescue_diag_preview: () => ({ entries: [], report: '{}', bytes: 1 }),
    })
    await click('diag')
    expect(($('send') as HTMLButtonElement).disabled).toBe(true)
    expect(($('save') as HTMLButtonElement).disabled).toBe(false)
    expect($('diagStatus').textContent).toContain('关闭')
  })
})

describe('页面自己不联网', () => {
  it('脚本里没有任何直接的网络 API，也没有写死的地址', () => {
    for (const banned of ['fetch(', 'XMLHttpRequest', 'sendBeacon', 'WebSocket', 'EventSource', 'http://', 'https://']) {
      expect(SCRIPT).not.toContain(banned)
    }
  })

  it('页面只调 rescue_* 命令，且都在救援 capability 的清单里', () => {
    const used = [...SCRIPT.matchAll(/call\('([a-z_]+)'/g)].map((m) => m[1])
    expect(new Set(used).size).toBeGreaterThanOrEqual(7)
    const cap = JSON.parse(readFileSync(resolve(SHELL, 'capabilities/rescue-page.json'), 'utf-8')) as {
      permissions: string[]
    }
    for (const c of used) {
      expect(c.startsWith('rescue_')).toBe(true)
      expect(cap.permissions).toContain('allow-' + c.replace(/_/g, '-'))
    }
  })
})
