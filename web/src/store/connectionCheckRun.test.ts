/** `runConnectionCheck`：只有探测真有结论且通了才算通；没结论（断网 / 缺型号）时后端原样返回旧的 ready 视图，不能当成通了 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { checkAiConnection, type AiConnectResult } from '@/lib/api'
import { connection } from '@/components/settings/testCaps'
import { useAiStore } from '@/store/aiStore'
import { runConnectionCheck } from '@/store/connectAiStore'

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  checkAiConnection: vi.fn(),
}))

const probe = (over: Record<string, unknown>) =>
  ({ checked: true, protocol: null, requested: null, switched: false, reason: null, reasons: {}, ...over }) as never
const reply = (r: Partial<AiConnectResult>) => vi.mocked(checkAiConnection).mockResolvedValue({ ok: true, ...r } as AiConnectResult)

beforeEach(() => {
  useAiStore.setState({ loadCaps: vi.fn(async () => {}) } as never)
})

describe('runConnectionCheck', () => {
  it('没结论（checked=false，旧的 ready 视图）：不算通，原因是连不上', async () => {
    reply({ connection: connection('c1'), probe: probe({ checked: false }) })
    expect(await runConnectionCheck('c1')).toEqual({ ok: false, reason: 'network' })
  })

  it('有结论且协议有值：通了', async () => {
    reply({ connection: connection('c1'), probe: probe({ protocol: 'openai-chat' }) })
    expect(await runConnectionCheck('c1')).toEqual({ ok: true, reason: null })
  })

  it('有结论但全不通：不看连接视图是不是 ready，原因取探测的主要原因', async () => {
    reply({ connection: connection('c1'), probe: probe({ reason: 'quota' }) })
    expect(await runConnectionCheck('c1')).toEqual({ ok: false, reason: 'quota' })
  })

  it('老后端没给 probe：看连接状态', async () => {
    reply({ connection: connection('c1') })
    expect((await runConnectionCheck('c1')).ok).toBe(true)
    reply({ connection: connection('c1', { state: 'error', error: 'unauthorized' }) })
    expect(await runConnectionCheck('c1')).toEqual({ ok: false, reason: 'unauthorized' })
  })
})
