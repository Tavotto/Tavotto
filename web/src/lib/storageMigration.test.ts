import { beforeEach, describe, expect, it } from 'vitest'
import { migrateLegacyStorage } from './storageMigration'

/** mm2.* / mm3.ui → magplot.* 一次性搬迁（Magic Matplot 时代的本机数据兼容）。 */
describe('migrateLegacyStorage', () => {
  beforeEach(() => localStorage.clear())

  it('把旧键搬到新键并删除旧键', () => {
    localStorage.setItem('mm2.docIndex', '[{"id":"d1","savedAt":1}]')
    localStorage.setItem('mm2.currentDoc', 'd1')
    localStorage.setItem('mm2.autosave.d1', '{"schema":2,"objects":[]}')
    localStorage.setItem('mm3.ui', '{"leftOpen":true}')
    localStorage.setItem('mm2.ai.agent', 'claude')
    migrateLegacyStorage()
    expect(localStorage.getItem('magplot.docIndex')).toBe('[{"id":"d1","savedAt":1}]')
    expect(localStorage.getItem('magplot.currentDoc')).toBe('d1')
    expect(localStorage.getItem('magplot.autosave.d1')).toBe('{"schema":2,"objects":[]}')
    expect(localStorage.getItem('magplot.ui')).toBe('{"leftOpen":true}')
    expect(localStorage.getItem('magplot.ai.agent')).toBe('claude')
    expect(localStorage.getItem('mm2.docIndex')).toBeNull()
    expect(localStorage.getItem('mm2.autosave.d1')).toBeNull()
    expect(localStorage.getItem('mm3.ui')).toBeNull()
  })

  it('新键已存在时不覆盖（幂等，重复执行安全）', () => {
    localStorage.setItem('mm2.currentDoc', 'old')
    localStorage.setItem('magplot.currentDoc', 'new')
    migrateLegacyStorage()
    expect(localStorage.getItem('magplot.currentDoc')).toBe('new')
  })

  it('更老的单槽 mm2.autosave（无点后缀）不动，留给 documentStore 的结构迁移', () => {
    localStorage.setItem('mm2.autosave', '{"schema":2,"objects":[]}')
    migrateLegacyStorage()
    expect(localStorage.getItem('mm2.autosave')).toBe('{"schema":2,"objects":[]}')
    expect(localStorage.getItem('magplot.autosave')).toBeNull()
  })

  it('无旧键时什么都不发生', () => {
    localStorage.setItem('magplot.ui', '{"a":1}')
    migrateLegacyStorage()
    expect(localStorage.getItem('magplot.ui')).toBe('{"a":1}')
    expect(localStorage.length).toBe(1)
  })
})
