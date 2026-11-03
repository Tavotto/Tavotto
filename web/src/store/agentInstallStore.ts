import { create } from 'zustand'
import { backendErrorText, fetchAiInstallStatus, startAiInstall, type AiAgentId, type AiInstallState } from '@/lib/api'

/** 应用内安装的轮询间隔（毫秒） */
export const INSTALL_POLL_MS = 2000

export interface AgentInstallEntry {
  status: 'running' | 'done' | 'error'
  progress?: number
  /** 失败码（后端 `managedcli.ERROR_*` 等）；文案由展示层翻译 */
  code?: AiInstallState['code'] | string
  /** 网络层失败的一句话（POST 起装就抛了） */
  message?: string
}

interface AgentInstallState {
  byAgent: Partial<Record<AiAgentId, AgentInstallEntry>>
  /** 「装好后要登录」的意图：agent id → 发起安装的那一行（owner）。装好后由那一行的登录流程消费 */
  loginDue: Partial<Record<AiAgentId, string>>
  /** 起装：登记意图并开始轮询。轮询住在 store 里——capabilities 先报「已安装」把按钮卸掉也照样走完 */
  begin: (agent: AiAgentId, owner: string, afterDone: () => Promise<void> | void) => void
  /** 取走并清掉登录意图；owner 对不上返回 false */
  takeLoginDue: (agent: AiAgentId, owner: string) => boolean
}

const timers = new Map<AiAgentId, number>()

function stop(agent: AiAgentId): void {
  const t = timers.get(agent)
  if (t !== undefined) window.clearInterval(t)
  timers.delete(agent)
}

export const useAgentInstallStore = create<AgentInstallState>((set, get) => {
  const put = (agent: AiAgentId, entry: AgentInstallEntry | undefined) =>
    set((s) => ({ byAgent: { ...s.byAgent, [agent]: entry } }))

  const finish = async (agent: AiAgentId, owner: string, afterDone: () => Promise<void> | void) => {
    put(agent, { status: 'done' })
    // 先重读状态（拿到 installed / needs_auth），再留下「该登录了」的意图
    try {
      await afterDone()
    } finally {
      set((s) => ({ loginDue: { ...s.loginDue, [agent]: owner } }))
    }
  }

  return {
    byAgent: {},
    loginDue: {},
    begin: (agent, owner, afterDone) => {
      stop(agent)
      put(agent, { status: 'running' })
      set((s) => {
        const { [agent]: _drop, ...rest } = s.loginDue
        return { loginDue: rest }
      })
      const poll = () => {
        timers.set(
          agent,
          window.setInterval(() => {
            void (async () => {
              try {
                const s = await fetchAiInstallStatus(agent)
                if (!timers.has(agent)) return
                if (s.status === 'running') return put(agent, { status: 'running', progress: s.progress })
                stop(agent)
                if (s.status === 'error') put(agent, { status: 'error', code: s.code })
                else if (s.status === 'done') await finish(agent, owner, afterDone)
              } catch {
                /* 网络抖动：下一轮再问 */
              }
            })()
          }, INSTALL_POLL_MS),
        )
      }
      void (async () => {
        try {
          const s = await startAiInstall(agent)
          if (s.status === 'done') await finish(agent, owner, afterDone)
          else if (s.status === 'error') put(agent, { status: 'error', code: s.code })
          else {
            put(agent, { status: 'running', progress: s.progress })
            poll()
          }
        } catch (e) {
          put(agent, { status: 'error', message: backendErrorText(e) })
        }
      })()
    },
    takeLoginDue: (agent, owner) => {
      if (get().loginDue[agent] !== owner) return false
      set((s) => {
        const { [agent]: _drop, ...rest } = s.loginDue
        return { loginDue: rest }
      })
      return true
    },
  }
})

/** 测试用：清掉轮询与状态 */
export function resetAgentInstallStore(): void {
  for (const a of [...timers.keys()]) stop(a)
  useAgentInstallStore.setState({ byAgent: {}, loginDue: {} })
}
