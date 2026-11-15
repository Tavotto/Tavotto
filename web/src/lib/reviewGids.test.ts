import { describe, expect, it } from 'vitest'
import { innerGid, owningPanel } from './reviewGids'
import type { CanvasObject } from '@/types/document'

const panel = (id: string) => ({ id, type: 'panel', fileId: `${id}.pdf`, overrides: [] }) as unknown as CanvasObject
const render = { byKey: {}, latest: {} }

describe('排版里的元素 gid', () => {
  it('面板框 = 对象 id 原样；面板内元素 = <对象id>::<gid> 先按对象拆', () => {
    expect(innerGid('p1::legend')).toBe('legend')
    expect(innerGid('legend')).toBe('legend')
    expect(owningPanel([panel('p1'), panel('p2')], render, 'p2')?.id).toBe('p2')
    expect(owningPanel([panel('p1')], render, 'p9::legend')).toBeNull()
  })
})
