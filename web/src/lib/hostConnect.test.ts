import { describe, expect, it } from 'vitest'
import { hostErrorText, hostStepLabel, hostStepNote, parseHostList } from '@/lib/hostConnect'

describe('hostConnect 技能', () => {
  it('列表里的 skill 字段被解析，认不出的当 missing', () => {
    const raw = JSON.stringify({
      ok: true,
      action: 'list',
      server: true,
      hosts: [
        { id: 'cursor', label: 'Cursor', installed: true, connected: true, skill: 'modified' },
        { id: 'zcode', label: 'ZCode', installed: false, connected: false, skill: 'weird' },
        { id: 'codex', label: 'Codex', installed: true, connected: true },
      ],
    })
    expect(parseHostList(raw).hosts.map((h) => h.skill)).toEqual(['modified', 'missing', 'missing'])
  })

  it('skill 步骤有译名，不显示英文 key', () => {
    expect(hostStepLabel('skill')).not.toContain('step.skill')
    expect(hostStepLabel('skill')).not.toBe('skill')
  })

  it('用户改过的技能说一句，其余不说', () => {
    const base = { step: 'skill', ok: true, skipped: true }
    expect(hostStepNote({ ...base, skill: 'modified' })).toBeTruthy()
    expect(hostStepNote({ ...base, skill: 'written' })).toBeNull()
    expect(hostStepNote({ step: 'config', ok: true, skipped: false })).toBeNull()
  })

  it('技能失败码有译文', () => {
    for (const c of ['skill_source_missing', 'skill_write_failed']) {
      expect(hostErrorText(c, 'Cursor')).not.toBe(hostErrorText('nope', 'Cursor'))
    }
  })
})
