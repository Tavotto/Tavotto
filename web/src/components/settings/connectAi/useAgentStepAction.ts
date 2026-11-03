import { useState } from 'react'
import { backendErrorText, patchAiAgent, type AiAgentCaps, type AiAgentId } from '@/lib/api'
import { useAiStore } from '@/store/aiStore'
import { agentStep } from './agentStep'
import { useAgentInstall } from './useAgentInstall'

/** 一行命令行 AI 的动作句柄：安装 / 重新安装 / 打开的点击逻辑与进度、错误（行右按钮和 AgentStepAction 共用，不复制安装逻辑） */
export function useAgentStepAction(agent: AiAgentCaps | undefined, agentId: AiAgentId, owner: string) {
  const [error, setError] = useState<string | null>(null)
  // 装好后的自动登录由常驻的那一行（`useLoginAfterInstall`）接——按钮可能在 caps 刷新时先被卸掉
  const install = useAgentInstall(agentId, owner, agent?.display_name ?? '')
  const step = agent ? agentStep(agent.state, agent.display_name) : null
  const onClick = async () => {
    if (!agent || !step) return
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
  return { step, running: install.running, progress: install.progress, error: install.error ?? error, onClick: () => void onClick() }
}
