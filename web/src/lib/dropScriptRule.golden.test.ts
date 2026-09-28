/**
 * 主页拖放「什么算脚本、打开哪个目录」与壳严格同源：两侧各自与
 * `tests/golden/drop_script_rule.json` 比，不读对方源码（壳那一侧是
 * `src-tauri/src/drop_paths.rs` 的 `the_script_rule_matches_the_golden_pair`）。
 *
 * 页面只看得到路径串、不能 stat，所以量的是 golden 的 `page` 一栏；与壳不同的条目
 * golden 里必须写明 `page_blind`，没写就得与 `shell` 相同——差异只许出现在明处。
 */
import { describe, expect, it } from 'vitest'

import golden from '../../../tests/golden/drop_script_rule.json'
import { dropTargetOf, type DropData } from './scriptImport'

type Case = (typeof golden.cases)[number] & { page_blind?: string }
const cases = golden.cases as Case[]

const DIR = '/Users/a/drop'

const data = (opts: { uris?: string; name: string }): DropData => ({
  types: opts.uris === undefined ? ['Files'] : ['text/uri-list', 'Files'],
  getData: (f) => (f === 'text/uri-list' ? (opts.uris ?? '') : ''),
  files: [{ name: opts.name }],
})

/** 页面的结论换成 golden 的三档 */
function verdict(name: string): string {
  const path = `${DIR}/${name}`
  const uri = `file://${DIR}/${encodeURIComponent(name)}`
  const t = dropTargetOf(data({ uris: uri, name }))
  if (t.kind === 'not-script') return 'unsupported'
  if (t.kind !== 'path') return `unexpected:${t.kind}`
  if (t.folder === DIR) return 'script'
  if (t.folder === path) return 'folder'
  return `unexpected-folder:${t.folder}`
}

describe('拖放的脚本判据 ↔ 壳的 drop_paths::classify', () => {
  it('golden 自洽：页面与壳不同的条目、且只有这些条目写了 page_blind', () => {
    expect(cases.length).toBeGreaterThan(10)
    for (const c of cases) expect(!!c.page_blind, c.name).toBe(c.page !== c.shell)
    // 三档都真的出现过，否则某一档判错了也量不出来
    expect(new Set(cases.map((c) => c.page))).toEqual(new Set(['script', 'folder', 'unsupported']))
  })

  it.each(cases.map((c) => [JSON.stringify(c.name), c] as const))('带路径放下 %s', (_, c) => {
    expect(verdict(c.name)).toBe(c.page)
  })

  // 路径以分隔符结尾时页面也看得出是目录：这时不许再有 page_blind 那种差异（壳那侧同一条）
  it.each(cases.filter((c) => c.on_disk === 'dir').map((c) => [JSON.stringify(c.name), c] as const))(
    '目录带结尾分隔符放下 %s：与壳同一个结论',
    (_, c) => {
      const t = dropTargetOf(data({ uris: `file://${DIR}/${encodeURIComponent(c.name)}/`, name: c.name }))
      expect(c.shell).toBe('folder')
      expect(t).toEqual({ kind: 'path', folder: `${DIR}/${c.name}/` })
    },
  )

  it.each(cases.map((c) => [JSON.stringify(c.name), c] as const))('只有文件名放下 %s：只有脚本退回选择器', (_, c) => {
    const t = dropTargetOf(data({ name: c.name }))
    expect(t).toEqual({ kind: c.page === 'script' ? 'no-path' : 'not-script', name: c.name })
  })
})
