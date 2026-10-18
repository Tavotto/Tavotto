import { describe, expect, it } from 'vitest'

import { visiblePrepSurface } from '@/lib/prepSurface'

const entries = { 'script:a.py': { key: 'script:a.py' } as never, 'script:b.py': { key: 'script:b.py' } as never }

describe('visiblePrepSurface：此刻可见的准备面', () => {
  const base = { focus: 'script:a.py', entries, guideCard: 'card' as const, hidden: false }

  it('展开：card；缩成角标：pill；key 是聚焦的那个条目（不是 entries 里别的）', () => {
    expect(visiblePrepSurface(base)).toEqual({ key: 'script:a.py', mode: 'card' })
    expect(visiblePrepSurface({ ...base, guideCard: 'pill' })).toEqual({ key: 'script:a.py', mode: 'pill' })
    expect(visiblePrepSurface({ ...base, focus: 'script:b.py' })?.key).toBe('script:b.py')
  })

  it('收起：没有可见的面', () => {
    expect(visiblePrepSurface({ ...base, guideCard: 'closed' })).toBeNull()
  })

  it('教程 / 引导中：整个藏起来，不论展开还是角标', () => {
    expect(visiblePrepSurface({ ...base, hidden: true })).toBeNull()
    expect(visiblePrepSurface({ ...base, hidden: true, guideCard: 'pill' })).toBeNull()
  })

  it('没有聚焦 / 聚焦的条目不存在：没有可见的面（entries 里有别的条目也不算）', () => {
    expect(visiblePrepSurface({ ...base, focus: null })).toBeNull()
    expect(visiblePrepSurface({ ...base, focus: 'script:gone.py' })).toBeNull()
  })
})
