/**
 * 「这个项目按哪套规范检查」的判据（ADR 0029）。
 *
 * 三条纪律各一组用例：
 *
 * 1. **项目结果稳定**：文档里有快照时它说了算，全局后来改了也不动旧项目；
 * 2. **「有没有新版」的判据是内容不等**，不是版本号——版本号是人写的；
 * 3. **绑的规范不在了 ≠ 这个项目没有规范**：快照还在，界面得说清是哪一种。
 */
import { describe, expect, it } from 'vitest'
import { DEFAULT_PROFILE_ID, loadProfile } from './profile'
import {
  bindingFor,
  builtinCatalog,
  resolveDocumentSpec,
  sameRules,
  type SpecCatalogEntry,
} from './specBinding'

const builtin = () => builtinCatalog().find((e) => e.id === DEFAULT_PROFILE_ID)!

/** 一条内容被改过的"全局现值"（把最小字号放宽到 6pt）。 */
function loosened(base: SpecCatalogEntry, version = '2.0.0'): SpecCatalogEntry {
  return {
    ...base,
    version,
    data: { ...structuredClone(base.data), min_effective_font_size_pt: 6 },
  }
}

describe('快照优先：全局改了不动旧项目', () => {
  it('有快照时用快照，即使全局那份已经换了规则', () => {
    const entry = builtin()
    const binding = bindingFor(entry)
    const got = resolveDocumentSpec(binding, [loosened(entry)])
    expect(got.source).toBe('snapshot')
    expect(got.profile.min_effective_font_size_pt).toBe(8)
    expect(got.updateAvailable).toBe(true)
    expect(got.globalVersion).toBe('2.0.0')
    expect(got.snapshotVersion).toBe(entry.version)
  })

  it('明确同步之后才跟上新规则，且是一条新的绑定', () => {
    const entry = builtin()
    const next = loosened(entry)
    const synced = resolveDocumentSpec(bindingFor(next), [next])
    expect(synced.profile.min_effective_font_size_pt).toBe(6)
    expect(synced.updateAvailable).toBe(false)
  })

  it('全局没动过就不提示新版（提示疲劳比不提示更糟）', () => {
    const entry = builtin()
    expect(resolveDocumentSpec(bindingFor(entry), [entry]).updateAvailable).toBe(false)
  })

  it('用户选了跟随全局时，全局现值说了算', () => {
    const entry = builtin()
    const binding = bindingFor(entry, { follow: true })
    const got = resolveDocumentSpec(binding, [loosened(entry)])
    expect(got.source).toBe('global')
    expect(got.profile.min_effective_font_size_pt).toBe(6)
    // 已经跟着走了，就没有"可同步"这回事
    expect(got.updateAvailable).toBe(false)
  })

  it('没选过跟随时字段根本不写进文档（恒为 false 的字段只会让 diff 变脏）', () => {
    expect('follow' in bindingFor(builtin())).toBe(false)
    expect('follow' in bindingFor(builtin(), { follow: false })).toBe(false)
    expect(bindingFor(builtin(), { follow: true }).follow).toBe(true)
  })

  it('换一套规范之后跟随的表态还在（它跟着项目走，不跟着某一套规范走）', () => {
    const a = builtin()
    const b = { ...a, id: 'other-spec', display_name: '别的规范' }
    const before = bindingFor(a, { follow: true })
    const after = bindingFor(b, { follow: before.follow })
    expect(after.follow).toBe(true)
    expect(after.id).toBe('other-spec')
  })
})

describe('老文档与找不到的规范', () => {
  it('没有快照的老文档按全局现值走（"未显式保存的旧默认"迁到新规则）', () => {
    const entry = builtin()
    const got = resolveDocumentSpec({ id: entry.id }, [loosened(entry)])
    expect(got.source).toBe('global')
    expect(got.profile.min_effective_font_size_pt).toBe(6)
  })

  it('连 id 都没有时退到内置默认，而不是崩掉', () => {
    const got = resolveDocumentSpec(undefined, [])
    expect(got.source).toBe('builtin')
    expect(got.profile.profile_id).toBe(DEFAULT_PROFILE_ID)
    expect(got.globalMissing).toBe(false)
  })

  it('绑的规范被删了：快照仍然管用，但如实报 globalMissing', () => {
    const got = resolveDocumentSpec(bindingFor(builtin()), [])
    expect(got.source).toBe('snapshot')
    expect(got.globalMissing).toBe(true)
    // 全局都没了就没有"新版"可言——那时该说的是另一句话
    expect(got.updateAvailable).toBe(false)
    expect(got.globalVersion).toBeNull()
  })

  it('id 认不出且没有快照：退默认规范并报 globalMissing', () => {
    const got = resolveDocumentSpec({ id: '早就删了' }, [])
    expect(got.source).toBe('builtin')
    expect(got.globalMissing).toBe(true)
    expect(got.profile.profile_id).toBe(DEFAULT_PROFILE_ID)
  })
})

describe('期刊覆盖', () => {
  it('覆盖照样合并进快照，且只覆盖点名的键', () => {
    const entry = builtin()
    const binding = bindingFor(entry, { journal: { widths_mm: { double: 178 } } })
    const got = resolveDocumentSpec(binding, [entry])
    expect(got.profile.widths_mm.double).toBe(178)
    expect(got.profile.widths_mm.single).toBe(80)
    expect(got.profile.min_effective_font_size_pt).toBe(8)
  })

  it('覆盖不算"全局改了"：判据比的是规范本身，不是合并结果', () => {
    const entry = builtin()
    const binding = bindingFor(entry, { journal: { widths_mm: { double: 178 } } })
    expect(resolveDocumentSpec(binding, [entry]).updateAvailable).toBe(false)
  })
})

describe('内容判据本身', () => {
  it('键序不同不算不同', () => {
    expect(sameRules({ a: 1, b: { c: 2, d: 3 } }, { b: { d: 3, c: 2 }, a: 1 })).toBe(true)
  })

  it('值差一点点就算不同', () => {
    expect(sameRules({ a: 8 }, { a: 8.5 })).toBe(false)
  })

  it('版本号一样但规则改了 —— 仍然报有新版', () => {
    const entry = builtin()
    const sneaky = loosened(entry, entry.version) // 版本号原封不动
    const got = resolveDocumentSpec(bindingFor(entry), [sneaky])
    expect(got.updateAvailable).toBe(true)
  })

  it('版本号变了但规则一个字没改 —— 不打扰用户', () => {
    const entry = builtin()
    const renumbered = { ...entry, version: '9.9.9' }
    expect(resolveDocumentSpec(bindingFor(entry), [renumbered]).updateAvailable).toBe(false)
  })
})

describe('快照的内容', () => {
  it('存的是规则全文，不是一个 id', () => {
    const snap = bindingFor(builtin()).snapshot!
    expect(snap.min_effective_font_size_pt).toBe(8)
    expect(snap.severity).toBeTruthy()
  })

  it('快照与内置那份逐条相同（不是"差不多"）', () => {
    expect(sameRules(bindingFor(builtin()).snapshot, loadProfile(DEFAULT_PROFILE_ID))).toBe(true)
  })
})
