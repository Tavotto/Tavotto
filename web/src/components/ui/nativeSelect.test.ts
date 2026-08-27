/**
 * 全仓库不再有原生 `<select>`（#145）。
 *
 * 「同一类操作在相邻界面用不同控件」是这条 issue 的根因：AI 历史抽屉、模型服务、
 * 默认 Agent、接口预设与 wire 五处还留着手写 `<select>`，与旁边的 `ui/Select`
 * 视觉、键盘行为、弹层定位全都不一样。逐处迁完之后需要一道门禁，否则下一次
 * 「顺手写个 select」几分钟就能把它加回来。
 *
 * 读文件走 `import.meta.glob('?raw')` 而**不是** node:fs：src 归 tsconfig.app.json
 * 管，那儿 `types` 只有 vite/client，加 node 会让应用代码误用 process/Buffer 也能
 * 编译通过（与 `lib/modKey.test.ts` 同一手法与同一理由）。
 */
import { describe, expect, it } from 'vitest'

const SOURCES = import.meta.glob('/src/**/*.{ts,tsx}', {
  eager: true,
  query: '?raw',
  import: 'default',
}) as Record<string, string>

/** 注释里写 `<select>` 是可以的（正是在解释为什么不用它） */
const stripComments = (src: string) =>
  src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[\s;{}()])\/\/.*$/gm, '$1')

describe('原生下拉的唯一去处是 ui/Select', () => {
  it('src 里没有任何 JSX 用原生 <select>', () => {
    const offenders = Object.entries(SOURCES)
      .filter(([path]) => !path.endsWith('.test.ts') && !path.endsWith('.test.tsx'))
      .filter(([, src]) => /<select[\s/>]/.test(stripComments(src)))
      .map(([path]) => path)
    expect(
      offenders,
      '这些文件还在用原生 <select>；统一控件是 components/ui/Select（键盘行为、'
        + '弹层定位、视觉都跟别处一致）',
    ).toEqual([])
  })

  it('自检：判据认得出一个原生 select（不是空门禁）', () => {
    expect(/<select[\s/>]/.test(stripComments('const a = <select value={x}>'))).toBe(true)
    expect(/<select[\s/>]/.test(stripComments('// 原生 <select> 的说明'))).toBe(false)
    expect(/<select[\s/>]/.test(stripComments('const a = <Select value={x} />'))).toBe(false)
  })
})
