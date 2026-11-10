import { hasConnectionView, readyConnections, usableAgents, type AiAgentCaps, type AiCapabilities, type AiConnection } from '@/lib/api'

/**
 * 参考图要不要提醒「这个模型看不了图」（ADR 0175）。判据只有后端一份（`engine/ai_refimages.known_text_only`，随能力清单的
 * `no_vision` 下发）：这里**只认清单里写明的**，判不出的当能看（照常发送、不拦）。
 */
export function modelSeesNothing(info: Pick<AiAgentCaps, 'no_vision'> | null | undefined, model: string | null | undefined): boolean {
  return !!model && !!info?.no_vision?.includes(model)
}

/** 换到哪一个：连接视图下是 `{ connection, model }`；老后端只有执行器时是 `{ agent, model }` */
export type VisionChoice = { kind: 'connection'; id: string; model: string; label: string } | { kind: 'agent'; id: string; model: string; label: string }

const firstSighted = (models: string[], blind: string[] | undefined, prefer?: string | null): string | null => {
  const ok = models.filter((m) => !blind?.includes(m))
  return (prefer && ok.includes(prefer) ? prefer : ok[0]) ?? null
}

/**
 * 找一个不在「确定看不了图」名单里的去处：先看当前这条连接的别的型号，再看其它就绪的连接（取它们的默认型号优先）。
 * 找不到回 null（界面就只说一句，不出按钮）。
 */
export function findSightedChoice(caps: AiCapabilities | null | undefined, currentId: string | null): VisionChoice | null {
  if (hasConnectionView(caps)) {
    const conns: AiConnection[] = readyConnections(caps)
    const ordered = [...conns.filter((c) => c.id === currentId), ...conns.filter((c) => c.id !== currentId)]
    for (const c of ordered) {
      const m = firstSighted(c.models, c.no_vision, c.default_model)
      if (m) return { kind: 'connection', id: c.id, model: m, label: c.label }
    }
    return null
  }
  const agents = usableAgents(caps ?? null)
  const ordered = [...agents.filter((a) => a.id === currentId), ...agents.filter((a) => a.id !== currentId)]
  for (const a of ordered) {
    const m = firstSighted(a.models, a.no_vision, a.default_model)
    if (m) return { kind: 'agent', id: a.id, model: m, label: a.display_name }
  }
  return null
}
