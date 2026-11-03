import { describe, expect, it, vi } from 'vitest'
import { EngineError } from '@/lib/api'
import { exportNeedsConfirm, exportWithAutoConfirm } from './exportConfirm'

const blocked = () => new EngineError('预检有阻断', '', 'preflight_blocked', '')

describe('内嵌画布导出的自动确认', () => {
  it('干净缓存 → 编辑引入阻断（预检过期）：第一次就带确认，一次成功导出', async () => {
    const call = vi.fn(async (confirm: boolean) => {
      if (!confirm) throw blocked()
      return { ok: true }
    })
    expect(exportNeedsConfirm({ errors: [], not_verifiable: [] }, true)).toBe(true)
    await expect(exportWithAutoConfirm(call, exportNeedsConfirm({ errors: [], not_verifiable: [] }, true))).resolves.toEqual({ ok: true })
    expect(call.mock.calls).toEqual([[true]])
  })

  it('缓存说干净、实际被后端挡回 preflight_blocked：带确认重发一次就成功', async () => {
    const call = vi.fn(async (confirm: boolean) => {
      if (!confirm) throw blocked()
      return { ok: true }
    })
    await expect(exportWithAutoConfirm(call, false)).resolves.toEqual({ ok: true })
    expect(call.mock.calls).toEqual([[false], [true]])
  })

  it('只重发一次：带确认仍失败就抛出，不循环；别的错误不重发', async () => {
    const always = vi.fn(async () => {
      throw blocked()
    })
    await expect(exportWithAutoConfirm(always, false)).rejects.toThrow('预检有阻断')
    expect(always).toHaveBeenCalledTimes(2)
    const other = vi.fn(async () => {
      throw new EngineError('别的错', '', 'bad_dpi', '')
    })
    await expect(exportWithAutoConfirm(other, false)).rejects.toThrow('别的错')
    expect(other).toHaveBeenCalledTimes(1)
  })

  it('缓存里有阻断 / 无法核验项也直接带确认；干净且未过期才不带', () => {
    expect(exportNeedsConfirm({ errors: [1], not_verifiable: [] }, false)).toBe(true)
    expect(exportNeedsConfirm({ errors: [], not_verifiable: [1] }, false)).toBe(true)
    expect(exportNeedsConfirm({ errors: [], not_verifiable: [] }, false)).toBe(false)
    expect(exportNeedsConfirm(null, false)).toBe(false)
  })
})
