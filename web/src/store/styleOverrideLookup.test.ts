/**
 * 样式跟随（ADR 0081）碰过的前端代码里，**按 (gid, prop) 取 override 一律走生效的那条**
 * （`lib/effectiveOverride`，last-wins，#587）。`find` / `findIndex` 回的是第一条：老文档里有
 * 重复条目、而第一条恰好等于样式值时，「已合样式」「样式写的」「用户写过」三个判断都会读错
 * （Codex #547 r4109745742）。行为由 `styleBinding.test.ts` / `styleOwnedIssueFix.test.ts` 的
 * 重复条目用例量；这里是结构性的一道：这几份文件里不许再出现按 gid 比较的 `find` / `findIndex`。
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const FILES = [
  'lib/stylePresets.ts',
  'lib/styleOwned.ts',
  'lib/stylePanelModel.ts',
  'store/styleBinding.ts',
  'store/actions.ts',
  'store/issueFixActions.ts',
  'components/left/StylePanel.tsx',
]

/**
 * `xs.find((o) => o.gid === g && o.prop === p` / `.findIndex(x => x.gid === … && x.prop === …`（参数名任意、括号可省）。
 * 只认**同时比 gid 与 prop** 的：那是在取一条 override；只比 gid 的是在 manifest 里找元素（gid 唯一），不在此列。
 */
const FIRST_MATCH = /\.(find|findIndex)\(\s*\(?\s*(\w+)\s*\)?\s*=>\s*\2\.gid\s*===[^;]*?&&\s*\2\.prop\s*===/g

/** 抹掉注释（说明文字里提到 `find` 的反例不算） */
const code = (src: string): string => src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1')

describe('按 (gid, prop) 取 override 走生效的那条（#587 last-wins）', () => {
  it('尺子是活的：认得出几种写法', () => {
    const hits = (s: string) => [...code(s).matchAll(FIRST_MATCH)].length
    expect(hits('p.overrides.find((o) => o.gid === g && o.prop === p)')).toBe(1)
    expect(hits('list.findIndex(x => x.gid === g && x.prop === p)')).toBe(1)
    expect(hits('m.elements.find((e) => e.gid === g)'), '找元素不算').toBe(0)
    expect(hits('// 反例：overrides.find((o) => o.gid === g && o.prop === p)')).toBe(0)
    expect(hits('effectiveOverride(p.overrides, g, prop)')).toBe(0)
  })

  it.each(FILES)('%s 里没有按 gid 取第一条的写法', (file) => {
    const src = fs.readFileSync(path.join(SRC, file), 'utf8')
    const found = [...code(src).matchAll(FIRST_MATCH)].map((m) => m[0])
    expect(found).toEqual([])
  })
})
