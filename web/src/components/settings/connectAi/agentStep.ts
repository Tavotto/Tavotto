import type { AiAgentState } from '@/lib/api'
import { cx } from './copy'

/** 一句话 + 一个动作：行里显示的就是这两样（`null` 动作 = 只有状态、没有按钮） */
export type AgentStepAction = 'install' | 'reinstall' | 'login' | 'enable'
export interface AgentStep {
  text: string
  action: AgentStepAction | null
  /** 动作按钮上的字 */
  actionLabel: string | null
}

/**
 * 把一个命令行 AI 的状态映射成「一句话 + 一个动作」（纯函数）。
 * 状态判断在后端，这里只翻译：没装 → 安装并登录；打不开 → 重新安装；没登录 → 登录；关了 → 打开；已连 → 无动作。
 * 每个键都写成字面量，i18n 检查才数得到。
 */
export function agentStep(state: AiAgentState, product: string): AgentStep {
  switch (state) {
    case 'not_installed':
      return { text: cx('step.notInstalled', { product }), action: 'install', actionLabel: cx('step.install') }
    case 'broken':
      return { text: cx('step.broken', { product }), action: 'reinstall', actionLabel: cx('step.reinstall') }
    case 'needs_auth':
      return { text: cx('step.needsAuth'), action: 'login', actionLabel: cx('signIn') }
    case 'disabled':
      return { text: cx('step.disabled'), action: 'enable', actionLabel: cx('step.enable') }
    case 'ready':
    case 'installed':
      return { text: cx('connected'), action: null, actionLabel: null }
  }
}
