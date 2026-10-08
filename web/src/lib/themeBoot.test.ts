/**
 * 外观偏好的开机落点（宪法第二十八节）：`index.html` 的 `<head>` 里有一段同步脚本，在入口模块（type="module"，
 * 延迟执行）跑到 `applyTheme` 之前就把 `data-theme` 挂上——否则保存的偏好与系统外观相反时，冷启动会先画一帧错的那套。
 *
 * 那段脚本不能 import，键 / 形状 / 合法值只能在 HTML 里再写一遍。主语：**这段脚本原文**在某一份本机存储下
 * 留给 `<html>` 的 `data-theme`，与 `uiStore` 读出的偏好经 `applyTheme` 落下的是否同一个——逐例执行两边对拍。
 * 键、合法值、容错（坏 JSON / 未知值 / 存储抛错）任一侧单改，这里红。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import ts from 'typescript'
import indexHtml from '/index.html?raw'
import { UI_PREFS_KEY } from '@/store/uiStore'
import { THEME_PREFS, applyTheme } from './theme'

/** `<head>` 里的经典（非 module、非 async / defer）内联脚本：开机同步执行的那一段 */
function bootScripts(html: string) {
  const doc = new DOMParser().parseFromString(html, 'text/html')
  return [...doc.head.querySelectorAll('script')].filter(
    (s) => !s.src && !s.type && !s.hasAttribute('async') && !s.hasAttribute('defer'),
  )
}

const BOOT = (() => {
  const s = bootScripts(indexHtml)
  if (s.length !== 1) throw new Error(`index.html <head> 里应有且只有一段开机内联脚本，实际 ${s.length}`)
  return s[0].textContent ?? ''
})()

/** 在一个干净的 `<html>` 上执行那段原文，返回它留下的 data-theme */
function runBoot(): string | null {
  document.documentElement.removeAttribute('data-theme')
  new Function(BOOT)()
  return document.documentElement.getAttribute('data-theme')
}

/** uiStore 自己读出的偏好经 applyTheme 落到一个新元素上的 data-theme（对照组） */
async function viaStore(): Promise<string | null> {
  vi.resetModules()
  const { useUiStore } = await import('@/store/uiStore')
  const el = document.createElement('html')
  applyTheme(useUiStore.getState().theme, el)
  return el.getAttribute('data-theme')
}

const CASES: [string, string | null][] = [
  ['没存过', null],
  ['坏 JSON', '{"theme":'],
  ['不是对象', '"dark"'],
  ['null', 'null'],
  ['数组', '[]'],
  ['未知值', JSON.stringify({ theme: 'blue' })],
  ['大小写不对', JSON.stringify({ theme: 'Dark' })],
  ['没有 theme 键', JSON.stringify({ leftOpen: false })],
  ...THEME_PREFS.map((p): [string, string] => [p, JSON.stringify({ theme: p, prefsVersion: 2 })]),
  ['老 blob（v0）+ 深色', JSON.stringify({ theme: 'dark', rightPinned: false })],
]

afterEach(() => {
  vi.restoreAllMocks()
  localStorage.clear()
  document.documentElement.removeAttribute('data-theme')
})

describe('index.html 的开机外观脚本与 uiStore / lib/theme 同源', () => {
  it('脚本在 <head> 里、同步（经典脚本），且排在入口模块之前', () => {
    const doc = new DOMParser().parseFromString(indexHtml, 'text/html')
    const all = [...doc.querySelectorAll('script')]
    const boot = bootScripts(indexHtml)[0]
    const entry = all.find((s) => s.type === 'module')
    expect(entry).toBeTruthy()
    expect(all.indexOf(boot)).toBeLessThan(all.indexOf(entry!))
    // 是能执行的 JS（不是被注释掉 / 语法坏了的一段）
    const diag = ts.transpileModule(BOOT, { reportDiagnostics: true }).diagnostics ?? []
    expect(diag.map((d) => ts.flattenDiagnosticMessageText(d.messageText, '\n'))).toEqual([])
  })

  for (const [name, raw] of CASES) {
    it(`本机存储「${name}」：开机脚本挂的 data-theme = uiStore + applyTheme 落的那个`, async () => {
      if (raw !== null) localStorage.setItem(UI_PREFS_KEY, raw)
      const expected = await viaStore()
      expect(runBoot()).toBe(expected)
    })
  }

  it('存的是深色 / 浅色时真的挂上了（对照组不是恒 null）', async () => {
    for (const p of ['dark', 'light'] as const) {
      localStorage.setItem(UI_PREFS_KEY, JSON.stringify({ theme: p }))
      expect(await viaStore()).toBe(p)
      expect(runBoot()).toBe(p)
    }
  })

  it('读存储抛错（隐私模式 / 存储被禁）：不挂、不抛，交给媒体查询', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('denied', 'SecurityError')
    })
    expect(() => runBoot()).not.toThrow()
    expect(document.documentElement.getAttribute('data-theme')).toBeNull()
  })
})
