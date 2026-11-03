import { describe, expect, it } from 'vitest'
import { setLocale, t } from '@/i18n'
import { agentStep } from './agentStep'
import { installErrorText } from './useAgentInstall'

describe('agentStep', () => {
  it('每种状态一句话 + 一个动作', async () => {
    await setLocale('zh-CN')
    expect(agentStep('not_installed', 'Codex')).toMatchObject({ text: '没装 Codex', action: 'install', actionLabel: '安装并登录' })
    expect(agentStep('broken', 'Codex')).toMatchObject({ text: '找到了 Codex，但打不开', action: 'reinstall', actionLabel: '重新安装' })
    expect(agentStep('needs_auth', 'Codex')).toMatchObject({ text: '还没登录', action: 'login', actionLabel: '登录' })
    expect(agentStep('disabled', 'Codex')).toMatchObject({ text: '已在设置里关闭', action: 'enable', actionLabel: '打开' })
    expect(agentStep('ready', 'Codex')).toMatchObject({ text: '已连接', action: null })
    expect(agentStep('installed', 'Codex').action).toBeNull()
    // Claude Code 与 Codex 同一套：没装就是「安装并登录」
    expect(agentStep('not_installed', 'Claude Code')).toMatchObject({ text: '没装 Claude Code', action: 'install', actionLabel: '安装并登录' })
  })

  it('英文界面也有文案', async () => {
    await setLocale('en-US')
    expect(agentStep('not_installed', 'Codex').text).toBe("Codex isn't installed")
    expect(installErrorText('managed_cli_offline', 'Codex')).toMatch(/Codex/)
    expect(installErrorText('managed_cli_offline', 'Claude Code')).toMatch(/Claude Code/)
    expect(installErrorText('managed_cli_offline', 'Claude Code')).not.toMatch(/Codex/)
    expect(installErrorText('weird', 'Claude Code')).toBe(t('settings.agents.install.error.other', { ns: 'dialogs' }))
    await setLocale('zh-CN')
  })
})
