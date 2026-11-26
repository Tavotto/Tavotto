import { t as translate } from '@/i18n'
import type { AiConnection } from '@/lib/api'

const cx = (key: string) => translate(`settings.connectAi.${key}`, { ns: 'dialogs' })

/**
 * 「这个连接为什么用不了」的一句话（不带句号）。入参是探测原因闭集（`AiConnection.error_reason` / `AiProbeSummary.reason`）
 * 或连接的 `error` 码；认得的四种各一句，其余（连不上 / 回网页 / 协议不对 / 坏回复 / 没有）统一「连不上这个地址」。
 * 每个键都写成字面量，i18n 检查才数得到。
 */
export function probeReasonText(reason: string | null | undefined): string {
  switch (reason) {
    case 'unauthorized':
      return cx('probe.unauthorized')
    case 'quota':
      return cx('probe.quota')
    case 'no_model':
      return cx('probe.no_model')
    default:
      return cx('probe.unreachable')
  }
}

/** 原因 → 用户下一步该点的那一个按钮；检查过一次仍不通（`checked`）就不再给「检查连接」，改去改地址 */
export type ConnAction = 'refill_key' | 'switch_ai' | 'edit_model' | 'check' | 'edit_address'
export function actionForReason(reason: string | null | undefined, checked: boolean): ConnAction {
  if (reason === 'unauthorized') return 'refill_key'
  if (reason === 'quota') return 'switch_ai'
  if (reason === 'no_model') return 'edit_model'
  return checked ? 'edit_address' : 'check'
}

/** 按钮上的字（连接卡用 dialogs 里的这一套；失败卡在 ai 里有同样的几句） */
export function connActionLabel(a: ConnAction): string {
  switch (a) {
    case 'refill_key':
      return cx('action.refillKey')
    case 'switch_ai':
      return cx('action.switchAi')
    case 'edit_model':
      return cx('action.editModel')
    case 'edit_address':
      return cx('action.editAddress')
    case 'check':
      return cx('checkConnection')
  }
}

/** 会话错误码 → 探测原因闭集里对应的那一个（失败卡在还没检查前就能说清是密钥、额度还是地址） */
export const reasonOfAgentError = (e: string | null | undefined): string =>
  e === 'agent_auth' ? 'unauthorized' : e === 'agent_quota' ? 'quota' : e === 'agent_network' ? 'network' : 'unknown'

/** 连接卡 / 失败卡认的「检查连接」场景：地址跑不了对话，或只走内置却走不通 */
export const isCheckableError = (c: Pick<AiConnection, 'error'> | undefined | null): boolean =>
  c?.error === 'endpoint_unusable' || c?.error === 'builtin_only'

/** 这条连接能不能发 `/check`：只有 Key 类（订阅 / 账号登录 / ChatGPT 登录类没有「地址 + Key」可探） */
export const canCheckConnection = (c: Pick<AiConnection, 'id'> | undefined | null): boolean =>
  !!c && !c.id.startsWith('sub:') && !c.id.startsWith('siwc:') && !c.id.endsWith('-oauth')
