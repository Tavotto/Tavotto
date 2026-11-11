import { describe, expect, it } from 'vitest'
import { connectCapsOf, connection } from '@/components/settings/testCaps'
import { findSightedChoice, modelSeesNothing } from './aiVision'

describe('模型看不看得了图（只认后端清单里写明的）', () => {
  it('清单里有才算看不了；没有清单 / 判不出 = 当能看', () => {
    expect(modelSeesNothing({ no_vision: ['deepseek-chat'] }, 'deepseek-chat')).toBe(true)
    expect(modelSeesNothing({ no_vision: ['deepseek-chat'] }, 'gpt-5')).toBe(false)
    expect(modelSeesNothing({}, 'deepseek-chat')).toBe(false)
    expect(modelSeesNothing(null, 'x')).toBe(false)
    expect(modelSeesNothing({ no_vision: ['x'] }, '')).toBe(false)
  })

  it('连接视图：先在当前连接里找别的型号，再找别的连接；全是文字模型就回 null', () => {
    const caps = connectCapsOf([
      connection('a', { models: ['deepseek-chat', 'deepseek-v4'], default_model: 'deepseek-chat', no_vision: ['deepseek-chat', 'deepseek-v4'] }),
      connection('b', { label: 'B', models: ['m1', 'gpt-5'], default_model: 'm1', no_vision: ['m1'] }),
    ])
    expect(findSightedChoice(caps, 'a')).toMatchObject({ kind: 'connection', id: 'b', model: 'gpt-5' })
    const allBlind = connectCapsOf([connection('a', { models: ['x'], default_model: 'x', no_vision: ['x'] })])
    expect(findSightedChoice(allBlind, 'a')).toBeNull()
  })
})
