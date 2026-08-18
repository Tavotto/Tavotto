import { afterAll, describe, expect, it, vi } from 'vitest'

/**
 * 提示文案里的组合键必须按平台渲染——Windows 用户的键盘上没有 ⌘ 这个键，
 * 「⌘Z 可撤销」对他们等于没说。审计（docs/audit/2026-08-17-ux-audit.md）
 * 数出六处硬编码，这批用例守住修复。
 *
 * 平台判断 `isMac` 在模块求值时读 navigator.platform，所以要换平台只能
 * 改 navigator 之后 resetModules 重新 import——mock 掉整个 utils 会把 cn()
 * 一起换掉，反而测不到真实拼接。
 */

const realPlatform = Object.getOwnPropertyDescriptor(navigator, 'platform')

const asPlatform = (value: string) => {
  Object.defineProperty(navigator, 'platform', { value, configurable: true })
  vi.resetModules()
}

afterAll(() => {
  if (realPlatform) Object.defineProperty(navigator, 'platform', realPlatform)
  else Reflect.deleteProperty(navigator as object, 'platform')
})

/** 自动保存会 PUT 到后端；这里只要不抛就行 */
globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch

describe('modKey', () => {
  it('Mac 连写 ⌘Z，其余平台写成 Ctrl+Z', async () => {
    asPlatform('MacIntel')
    expect((await import('@/lib/utils')).modKey('Z')).toBe('⌘Z')

    asPlatform('Win32')
    expect((await import('@/lib/utils')).modKey('Z')).toBe('Ctrl+Z')

    asPlatform('Linux x86_64')
    expect((await import('@/lib/utils')).modKey('↵')).toBe('Ctrl+↵')
  })
})

describe('ALT / combo', () => {
  it('⌥ 同样按平台给名字：Mac ⌥⏎，其余平台 Alt+⏎', async () => {
    asPlatform('MacIntel')
    let u = await import('@/lib/utils')
    expect(u.ALT).toBe('⌥')
    expect(u.combo(u.ALT, '⏎')).toBe('⌥⏎')

    asPlatform('Win32')
    u = await import('@/lib/utils')
    expect(u.ALT).toBe('Alt')
    expect(u.combo(u.ALT, '⏎')).toBe('Alt+⏎')
  })
})

describe('状态提示里的撤销键', () => {
  /** 真跑一个会发 toast 的动作，断言文案是平台化拼出来的而不是写死的 */
  const insertOnPlatform = async (platform: string) => {
    asPlatform(platform)
    const [{ insertSymbol }, { useUiStore }, { useDocumentStore }, { emptyProject }, i18n] =
      await Promise.all([
        import('@/lib/presets'),
        import('@/store/uiStore'),
        import('@/store/documentStore'),
        import('@/types/document'),
        import('@/i18n'),
      ])
    await useDocumentStore.getState().switchDocument(emptyProject(), 'd_modkey')
    insertSymbol('α')
    // status 存的是描述符（切语言要跟着变），断言前先按当前语言翻出来
    return i18n.formatMessage(useUiStore.getState().status)
  }

  it('Windows 上不出现 ⌘', async () => {
    const status = await insertOnPlatform('Win32')
    expect(status).toContain('Ctrl+Z')
    expect(status).not.toContain('⌘')
  })

  it('Mac 上仍是 ⌘Z', async () => {
    expect(await insertOnPlatform('MacIntel')).toContain('⌘Z')
  })
})

/**
 * 兜底的源码级看护：上面那条只跑得到 presets 这一个入口，其余各处要么要
 * 铺一套 store、要么得挂载组件才叫得动，成本远高于收益。这里直接读源码
 * 断言「非注释代码里不再出现 ⌘/⌥」——手法糙，但正是它挡住「再手写一个
 * ⌘Z」的复发。注释里写 ⌘C/⌘V 说明键位是可以的，那是给开发者看的。
 *
 * 读文件走 `import.meta.glob('?raw')` 而**不是** node:fs：src 归
 * tsconfig.app.json 管，那儿 `types` 只有 vite/client——也不该加 node，
 * 一旦加上，应用代码误用 process/Buffer 就能编译通过。`pnpm build` 的
 * `tsc -b` 会真编译 src，node:fs 在那儿直接 TS2591（`tsc --noEmit` 反而
 * 放过：根 tsconfig 是 `files: []` 的方案文件，什么都没编）。
 *
 * 顺手把范围从「几个改过的文件」放大成**整个 src**：唯一豁免是 utils.ts
 * 里 MOD/ALT 两个常量的定义，那是这两个字符的唯一合法出处。
 * 表里剩下的 ⇧ / ⏎ / ⌫ 是键面图形（Windows 键盘上也这么印），不是
 * Mac 专有的修饰键名，故不在看护范围内。
 */
describe('源码里不再硬编码 ⌘ / ⌥', () => {
  const stripComments = (src: string) =>
    src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[\s;{}()])\/\/.*$/gm, '$1')

  /** 测试文件自己要写 ⌘ 做断言，排除掉 */
  const sources = import.meta.glob<string>(
    ['../**/*.ts', '../**/*.tsx', '!../**/*.test.ts', '!../**/*.test.tsx'],
    { query: '?raw', import: 'default', eager: true },
  )

  /** glob 的 key 是相对本文件的规范化路径，同目录就是 `./x.ts` */
  const DEFINITION = './utils.ts'

  it('全 src 扫描：只有 utils.ts 的常量定义可以出现 ⌘/⌥', () => {
    expect(Object.keys(sources).length).toBeGreaterThan(50) // glob 没匹配上就等于空转
    expect(sources[DEFINITION]).toContain("isMac ? '⌘' : 'Ctrl'")

    const offenders = Object.entries(sources)
      .filter(([path]) => path !== DEFINITION)
      .filter(([, src]) => /[⌘⌥]/.test(stripComments(src)))
      .map(([path]) => path)

    expect(offenders).toEqual([])
  })
})
