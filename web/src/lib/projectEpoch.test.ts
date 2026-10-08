import { describe, expect, it } from 'vitest'
import { captureProjectEpoch } from '@/lib/projectEpoch'
import { setCurrentProjectId } from '@/lib/session'

describe('captureProjectEpoch', () => {
  it('同一项目内一直有效；切走即失效；A → B → A 也已换代', () => {
    setCurrentProjectId('pj-a')
    const g = captureProjectEpoch()
    expect(g.still()).toBe(true)
    setCurrentProjectId('pj-b')
    expect(g.still()).toBe(false)
    setCurrentProjectId('pj-a')
    expect(g.still()).toBe(false)
    expect(captureProjectEpoch().still()).toBe(true)
  })
  it('extra 代际变了也失效', () => {
    let n = 0
    const g = captureProjectEpoch(() => n)
    expect(g.still()).toBe(true)
    n++
    expect(g.still()).toBe(false)
  })
})
