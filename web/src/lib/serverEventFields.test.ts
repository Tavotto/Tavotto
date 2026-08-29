/**
 * SSE 事件的字段解码（Prompt 06 §三）。
 *
 * 这三个函数存在的理由是**可选字段与兼容字段**：批量的 `scripts` 与单脚本的
 * `script` 并存、`assets.changed` 的并集与三个细分并存。用例里同时钉两件事：
 * 兼容字段不能漏（那是 probe 与手工登记走的形状），畸形载荷不能抛
 * （payload 是 `JSON.parse` 出来的，类型声明不是运行时保证）。
 */
import { describe, expect, it } from 'vitest'
import {
  affectedAssetIdsOf,
  affectedScriptsOf,
  affectedStemsOf,
  type ServerEvent,
} from './api'

/** 事件体来自网络：用例照样从「一坨 JSON」出发，不从类型出发 */
const ev = (raw: unknown): ServerEvent => raw as ServerEvent

describe('affectedScriptsOf', () => {
  it('收批量字段', () => {
    expect(
      affectedScriptsOf(ev({ kind: 'registry.changed', scripts: ['a.py', 'b.py'] })),
    ).toEqual(['a.py', 'b.py'])
  })

  it('收单脚本兼容字段（probe / 手工登记的形状）', () => {
    expect(affectedScriptsOf(ev({ kind: 'registry.changed', script: 'one.py' }))).toEqual([
      'one.py',
    ])
  })

  it('两种字段并存时取并集且去重', () => {
    expect(
      affectedScriptsOf(
        ev({ kind: 'registry.changed', scripts: ['one.py'], script: 'one.py' }),
      ),
    ).toEqual(['one.py'])
  })

  it('panel.file_changed 也有脚本键', () => {
    expect(affectedScriptsOf(ev({ kind: 'panel.file_changed', scripts: ['f.py'] }))).toEqual([
      'f.py',
    ])
  })

  it('与脚本无关的事件是空表，不是 undefined', () => {
    expect(affectedScriptsOf(ev({ kind: 'render.done', id: 'x' }))).toEqual([])
    expect(affectedScriptsOf(ev({ kind: 'assets.changed', ids: ['a.pdf'] }))).toEqual([])
  })
})

describe('affectedStemsOf', () => {
  it('两种带 stem 的事件都收', () => {
    expect(affectedStemsOf(ev({ kind: 'registry.changed', stems: ['Fig1'] }))).toEqual(['Fig1'])
    expect(affectedStemsOf(ev({ kind: 'panel.file_changed', stems: ['Fig1', 'Fig2'] }))).toEqual([
      'Fig1',
      'Fig2',
    ])
  })

  it('其它事件是空表', () => {
    expect(affectedStemsOf(ev({ kind: 'assets.changed', ids: [] }))).toEqual([])
  })
})

describe('affectedAssetIdsOf', () => {
  it('并集 = ids ∪ added ∪ removed ∪ changed，且去重', () => {
    expect(
      affectedAssetIdsOf(
        ev({
          kind: 'assets.changed',
          ids: ['a.pdf', 'b.png'],
          added: ['a.pdf'],
          removed: ['c.pdf'],
          changed: ['b.png'],
        }),
      ),
    ).toEqual(['a.pdf', 'b.png', 'c.pdf'])
  })

  it('只有细分、没有并集字段时照样收得到（老/新后端都不假设）', () => {
    expect(
      affectedAssetIdsOf(ev({ kind: 'assets.changed', removed: ['gone.pdf'] })),
    ).toEqual(['gone.pdf'])
  })

  it('其它事件是空表', () => {
    expect(affectedAssetIdsOf(ev({ kind: 'registry.changed', scripts: ['a.py'] }))).toEqual([])
  })
})

describe('畸形载荷', () => {
  it('字段不是数组时当作「这一维没有信息」，不抛', () => {
    expect(affectedScriptsOf(ev({ kind: 'registry.changed', scripts: 'a.py' }))).toEqual([])
    expect(affectedStemsOf(ev({ kind: 'panel.file_changed', stems: null }))).toEqual([])
    expect(affectedAssetIdsOf(ev({ kind: 'assets.changed', ids: { a: 1 } }))).toEqual([])
  })

  it('数组里混了非字符串时只留字符串', () => {
    expect(
      affectedAssetIdsOf(ev({ kind: 'assets.changed', ids: ['a.pdf', 3, null, 'b.pdf'] })),
    ).toEqual(['a.pdf', 'b.pdf'])
  })

  it('单脚本字段不是字符串时不进结果', () => {
    expect(affectedScriptsOf(ev({ kind: 'registry.changed', script: 7 }))).toEqual([])
  })
})
