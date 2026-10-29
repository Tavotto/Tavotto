import { useAiStore } from '@/store/aiStore'
import { patchAiAgent, backendErrorText, type AiAgentCaps } from '@/lib/api'
import { useState } from 'react'
import { Button } from '../../ui/Button'
import { agentStep } from './agentStep'
import { installProgressText, useAgentInstall } from './useAgentInstall'

/**
 * 命令行 AI（login 方式）一行的「一句话 + 一个按钮」：没装 / 打不开 / 没登录 / 已关闭。
 * 「安装并登录 / 重新安装」先走安装接口，装好后自动接登录（`beginLogin`）；安装中显示进度，失败给一句话。
 * 「登录」不在这里：agent 已装好时行里的登录按钮（`useLoginFlow`）负责。
 */
export function AgentStepAction({ agent, owner }: { agent: AiAgentCaps; owner: string }) {
  const [error, setError] = useState<string | null>(null)
  // 装好后的自动登录由常驻的那一行（`useLoginAfterInstall`）接——这个按钮可能在 caps 刷新时先被卸掉
  const install = useAgentInstall(agent.id, owner, agent.display_name)
  const step = agentStep(agent.state, agent.display_name)
  if (!step.action || step.action === 'login') return null

  const onClick = async () => {
    if (step.action === 'enable') {
      setError(null)
      try {
        await patchAiAgent(agent.id, { enabled: true })
        await useAiStore.getState().loadCaps(true)
      } catch (e) {
        setError(backendErrorText(e))
      }
      return
    }
    install.begin()
  }

  const err = install.error ?? error
  return (
    <span data-connect-step-action={step.action} className="flex flex-col items-end gap-1">
      <span className="flex items-center gap-2">
        <span className="type-caption" data-connect-step-text>
          {install.running ? installProgressText(install.progress) : step.text}
        </span>
        <Button
          variant="secondary"
          size="sm"
          data-connect-action={step.action}
          loading={install.running}
          disabled={install.running}
          onClick={() => void onClick()}
        >
          {step.actionLabel}
        </Button>
      </span>
      {err && (
        <span role="alert" className="text-xs text-danger">
          {err}
        </span>
      )}
    </span>
  )
}
