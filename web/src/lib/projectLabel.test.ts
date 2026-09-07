/**
 * 「最近文档」里标出所属项目（审计 T04）。
 *
 * 索引 `tavotto.docIndex` 跨项目共用一份，所以列表里混着别的项目的文档。
 * 标记的判据只有三档：**属于当前项目 / 属于别的项目 / 不知道**——最后一档是
 * 旧条目（那时还没记归属），把它并进任何一档都是替它编一个归属出来。
 */
import { describe, expect, it } from 'vitest'
import { applyLocale } from '@/i18n'
import { currentProjectLabel, foreignProjectLabel, setCurrentProjectLabel } from './projectLabel'

describe('当前项目名的投影', () => {
  it('写进去读得出来；空值一律归成 null', () => {
    setCurrentProjectLabel('kinetics')
    expect(currentProjectLabel()).toBe('kinetics')
    setCurrentProjectLabel(undefined)
    expect(currentProjectLabel()).toBeNull()
    setCurrentProjectLabel('')
    expect(currentProjectLabel()).toBeNull()
  })
})

describe('别的项目的文档', () => {
  it('同一个项目不标', () => {
    expect(foreignProjectLabel({ projectId: 'p1', projectName: 'A' }, 'p1')).toBeNull()
  })

  it('别的项目：指名那个项目', () => {
    const out = foreignProjectLabel({ projectId: 'p2', projectName: 'kinetics' }, 'p1')
    expect(out).toContain('kinetics')
  })

  it('别的项目但名字没记下：说「其他项目」，不编一个名字', () => {
    const out = foreignProjectLabel({ projectId: 'p2' }, 'p1')
    expect(out).not.toBeNull()
    expect(out).not.toContain('undefined')
  })

  it('旧条目（没记归属）什么都不标', () => {
    expect(foreignProjectLabel({}, 'p1')).toBeNull()
    expect(foreignProjectLabel({ projectName: 'kinetics' }, 'p1')).toBeNull()
  })

  it('当前没打开项目时，记了归属的条目仍然算「别的项目」', () => {
    expect(foreignProjectLabel({ projectId: 'p2', projectName: 'A' }, null)).not.toBeNull()
  })

  it('两种语言下都出得来', async () => {
    await applyLocale('en-US')
    try {
      const out = foreignProjectLabel({ projectId: 'p2', projectName: 'kinetics' }, 'p1')
      expect(out).toContain('kinetics')
      expect(out).not.toContain('topbar.')
    } finally {
      await applyLocale('zh-CN')
    }
  })
})
