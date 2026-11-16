import { t as translate } from '@/i18n'
import type { AiConnection } from '@/lib/api'

const cx = (key: string) => translate(`settings.connectAi.${key}`, { ns: 'dialogs' })

/**
 * 「这个连接为什么用不了」的一句话。入参是探测原因闭集（`AiConnection.error_reason` / `AiProbeSummary.reason`）
 * 或连接的 `error` 码；不认识的 / 没有的 → 通用一句。每个键都写成字面量，i18n 检查才数得到。
 */
export function probeReasonText(reason: string | null | undefined): string {
  switch (reason) {
    case 'unauthorized':
      return cx('probe.unauthorized')
    case 'quota':
      return cx('probe.quota')
    case 'no_model':
      return cx('probe.no_model')
    case 'web_page':
    case 'wrong_protocol':
    case 'bad_reply':
      return cx('probe.unusable')
    case 'network':
    case 'timeout':
      return cx('probe.network')
    default:
      return cx('probe.generic')
  }
}

/** 连接卡 / 失败卡认的「检查连接」场景：地址跑不了对话，或只走内置却走不通 */
export const isCheckableError = (c: Pick<AiConnection, 'error'> | undefined | null): boolean =>
  c?.error === 'endpoint_unusable' || c?.error === 'builtin_only'

/** 这条连接能不能发 `/check`：只有 Key 类（订阅 / 账号登录类没有「地址 + Key」可探） */
export const canCheckConnection = (c: Pick<AiConnection, 'id'> | undefined | null): boolean =>
  !!c && !c.id.startsWith('sub:') && !c.id.endsWith('-oauth')
