import { beforeEach, describe, expect, it } from 'vitest'
import { useEnvStore } from './envStore'
import { useRenderStore } from './renderStore'
import { useUiStore } from './uiStore'

/**
 * 设置里的「恢复原脚本」（ADR 0110 §七）：在 A 项目发起、请求在飞时换到 B。完成回调不能把 A 的状态 /
 * 报错写到 B 的通知轨、重排 B 的失败面板、刷新 B 的备份列表（Codex 评 #730 P2）——与其它按项目隔离的
 * 环境操作同一条代际纪律。
 */

let release: (res: Response) => void = () => {}
globalThis.fetch = ((url: unknown) => {
  if (String(url).includes('/api/script-backups/restore')) {
    return new Promise<Response>((resolve) => {
      release = resolve
    })
  }
  return Promise.resolve(new Response('{}', { status: 200 }))
}) as typeof fetch

const BACKUP = { id: 'fig.py-0123456789ab/0929_101010', script: 'fig.py' }

function seedFailedPanel() {
  const entry = { ...useRenderStore.getState().byKey['b.py'], fileId: 'b.py', status: 'error', code: 'missing_input', stale: false }
  useRenderStore.setState({ byKey: { 'b.py': entry } as never, tracked: {} })
}

describe('复原脚本时换了项目', () => {
  beforeEach(() => {
    seedFailedPanel()
    useUiStore.setState({ status: null })
  })

  it('成功回来：B 的状态、失败面板与备份列表一个都不动', async () => {
    const generation = useEnvStore.getState().scriptBackupGeneration
    const pending = useEnvStore.getState().restoreScriptBackup(BACKUP, 'full')
    useEnvStore.getState().resetProject()
    release(new Response(JSON.stringify({ ok: true, script: 'fig.py' }), { status: 200 }))
    expect(await pending).toBeNull()
    expect(useUiStore.getState().status).toBeNull()
    expect(useRenderStore.getState().byKey['b.py']?.stale).toBe(false)
    expect(useEnvStore.getState().scriptBackupGeneration).toBe(generation)
  })

  it('失败回来：A 的报错不回给 B', async () => {
    const generation = useEnvStore.getState().scriptBackupGeneration
    const pending = useEnvStore.getState().restoreScriptBackup(BACKUP, 'undo_edits')
    useEnvStore.getState().resetProject()
    release(
      new Response(JSON.stringify({ error: 'x', code: 'script_restore_conflict', params: {} }), { status: 409 }),
    )
    expect(await pending).toBeNull()
    expect(useEnvStore.getState().scriptBackupGeneration).toBe(generation)
  })

  it('没换项目：照常报状态、重排失败面板、刷新列表', async () => {
    const generation = useEnvStore.getState().scriptBackupGeneration
    const pending = useEnvStore.getState().restoreScriptBackup(BACKUP, 'full')
    release(new Response(JSON.stringify({ ok: true, script: 'fig.py' }), { status: 200 }))
    expect(await pending).toBeNull()
    expect(useUiStore.getState().status).not.toBeNull()
    expect(useRenderStore.getState().byKey['b.py']?.stale).toBe(true)
    expect(useEnvStore.getState().scriptBackupGeneration).toBe(generation + 1)
  })
})
