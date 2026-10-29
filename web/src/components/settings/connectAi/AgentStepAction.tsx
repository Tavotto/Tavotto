import type { AiAgentCaps } from '@/lib/api'
import { Button } from '../../ui/Button'
import { installProgressText } from './useAgentInstall'
import { useAgentStepAction } from './useAgentStepAction'

/**
 * 命令行 AI（login 方式）一行的「一句话 + 一个按钮」：没装 / 打不开 / 没登录 / 已关闭。
 * 「安装并登录 / 重新安装」先走安装接口，装好后自动接登录（`beginLogin`）；安装中显示进度，失败给一句话。
 * 「登录」不在这里：agent 已装好时行里的登录按钮（`useLoginFlow`）负责。
 */
export function AgentStepAction({ agent, owner }: { agent: AiAgentCaps; owner: string }) {
  const h = useAgentStepAction(agent, agent.id, owner)
  const step = h.step
  if (!step || !step.action || step.action === 'login') return null
  return (
    <span data-connect-step-action={step.action} className="flex flex-col items-end gap-1">
      <span className="flex items-center gap-2">
        <span className="type-caption" data-connect-step-text>
          {h.running ? installProgressText(h.progress) : step.text}
        </span>
        <Button variant="secondary" size="sm" data-connect-action={step.action} loading={h.running} disabled={h.running} onClick={h.onClick}>
          {step.actionLabel}
        </Button>
      </span>
      {h.error && (
        <span role="alert" className="text-xs text-danger">
          {h.error}
        </span>
      )}
    </span>
  )
}
