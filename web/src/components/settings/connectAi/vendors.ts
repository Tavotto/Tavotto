import { currentLocale } from '@/i18n'
import { isCheckableError, probeReasonText } from '@/lib/connectionCheck'
import { ag, cx } from './copy'
import { sortVendors, type AiCapabilities, type AiConnection, type AiVendor, type AiVendorMethod } from '@/lib/api'

/** 只给没有 `methods` 的老后端兜底：订阅登录类厂商 → 登录家族 id；有 methods 时一律读 `method.agent` */
const LOGIN_AGENT: Record<string, string> = { chatgpt: 'codex', claude: 'claude' }
export const loginAgentOf = (v: AiVendor, m?: AiVendorMethod): string => m?.agent ?? LOGIN_AGENT[v.id] ?? v.id

/** 中文界面国内在前，其余海外在前（以界面语言为准，不看后端的 `locale_region` 提示） */
export const isCnFirst = (): boolean => currentLocale() === 'zh-CN'

/** 列表里出现的厂商：「自定义」另走手动入口；账号登录类 client_id 没配置（`available=false`）时不出现 */
export function listedVendors(caps: AiCapabilities | null | undefined): AiVendor[] {
  const vendors = (caps?.vendors ?? []).filter((v) => v.region !== 'custom' && !(v.connect === 'oauth' && !v.available))
  return sortVendors(vendors, isCnFirst())
}

/** 用户自填地址保存下来的连接（vendor = custom）：每条一行，列在厂商列表末尾 */
export const customConnections = (caps: AiCapabilities | null | undefined): AiConnection[] =>
  (caps?.connections ?? []).filter((c) => c.vendor === 'custom')

/** 给一条自填连接合成一行用的「厂商」：名字用连接名、图标是通用图标；Key 方式的目录行 id 仍是 custom（换 Key 时当厂商 id 发） */
export const customVendorOf = (c: AiConnection): AiVendor => ({
  id: c.id,
  label: c.label,
  logo: 'custom',
  region: 'custom',
  connect: 'key',
  available: true,
  methods: [{ id: 'custom:key', kind: 'key', catalog_id: 'custom', available: true }],
})

/** 这家厂商当前的连接（同厂商多条时取最该显示的：ready 优先） */
export function connectionOf(caps: AiCapabilities | null | undefined, vendor: AiVendor): AiConnection | undefined {
  const own = (caps?.connections ?? []).filter((c) => c.vendor === vendor.id)
  return own.find((c) => c.state === 'ready') ?? own[0]
}

/** 厂商的界面名：有 `label_key` 走已有的目录文案，否则（订阅类 / 老后端）用 `label` */
export const vendorName = (v: AiVendor): string => (v.label_key ? ag(v.label_key) : v.label)

/** 连接异常的一句人话（只认几个常见原因码，其余统一一句） */
export const connectionErrorText = (c: AiConnection): string =>
  isCheckableError(c)
    ? probeReasonText(c.error_reason) // 地址跑不了对话：按探测原因说一句（没有原因就是通用一句）
    : c.error === 'plan_usage_disabled'
      ? cx('err.planUsageDisabled') // SIWC：账号没允许 Tavotto 用订阅额度
      : c.error && ['unauthorized', 'network', 'timeout'].includes(c.error)
        ? cx(`err.${c.error}`)
        : cx('error')

/** 这家厂商的连接方式；没有 `methods`（老后端）就按旧的 `connect` 合成唯一一种 */
export const methodsOf = (v: AiVendor): AiVendorMethod[] =>
  v.methods && v.methods.length > 0
    ? v.methods
    : [{ id: v.connect, kind: v.connect, key_url: v.key_url, key_hint_key: v.key_hint_key, available: v.available }]

/** 方式名：后端给的 `label_key` 认得就用它，否则按类型兜底（每个键都写成字面量，i18n 检查才数得到） */
export function methodName(m: AiVendorMethod): string {
  switch (m.label_key) {
    case 'method.kimiCode':
      return cx('method.kimiCode')
    case 'method.codingPlan':
      return cx('method.codingPlan')
    case 'method.subscription':
      return cx('method.subscription')
    case 'method.apiKey':
      return cx('method.apiKey')
    case 'method.account':
      return cx('method.account')
    case 'method.chatgptPlan':
      return cx('method.subscription')
  }
  return m.kind === 'key' ? cx('method.apiKey') : m.kind === 'login' || m.kind === 'siwc' ? cx('method.subscription') : cx('method.account')
}

/**
 * 方式的一句说明：说**产品**而不是厂商（「用 ChatGPT 订阅登录」「用 Kimi Code 会员的 Key」）。
 * 产品由方式自己决定——订阅登录看它的登录家族（`agent`），Key 类看 `label_key`——不按厂商 id 硬编码。
 */
export function methodHint(v: AiVendor, m: AiVendorMethod): string {
  const name = vendorName(v)
  if (m.kind === 'key') {
    if (m.label_key === 'method.kimiCode') return cx('method.hintKimiCode')
    if (m.label_key === 'method.codingPlan') return cx('method.hintCodingPlan')
    return cx('method.hintKey', { name })
  }
  if (m.kind === 'oauth') return cx('method.hintAccount', { name })
  if (m.kind === 'siwc') return cx('method.hintChatgpt')
  if (m.agent === 'codex') return cx('method.hintChatgpt')
  if (m.agent === 'claude') return cx('method.hintClaude')
  return cx('method.hintLogin', { name })
}

/** 这家厂商的全部连接（订阅、Key、账号登录各一条 / 多条） */
export const connectionsOf = (caps: AiCapabilities | null | undefined, vendor: AiVendor): AiConnection[] =>
  (caps?.connections ?? []).filter((c) => c.vendor === vendor.id)

/**
 * 这一种连接方式对应哪一条连接（连接状态落在**方式**上，不是整家厂商一份）：
 * login / oauth 方式 = 方式自己带的 `connection_id`（`sub:codex`、`minimax-oauth`）；key 方式 = 连接上的 `method`（方式 id）。
 * 后端没给这些字段（老后端）时才退回推断：登录看 `sub:<登录家族>`，Key 看 `catalog_id`，再不行按 vendor + 类型——
 * 这家厂商只有一种同类型方式才认（有两种 Key 方式又分不出是哪条时宁可都不认，也不把 A 的连接算到 B 头上）。
 * 多条同方式连接时 ready 的优先。
 */
export function connectionForMethod(
  connections: AiConnection[],
  vendor: AiVendor,
  method: AiVendorMethod,
): AiConnection | undefined {
  let own: AiConnection[]
  if (method.kind !== 'key') {
    const id = method.connection_id ?? (method.kind === 'login' ? `sub:${method.agent ?? loginAgentOf(vendor)}` : null)
    // 账号登录类老后端没给 connection_id：连接 id 形如 `<provider>-oauth`
    own = id ? connections.filter((c) => c.id === id) : connections.filter((c) => c.id.endsWith('-oauth'))
  } else {
    const sameKind = methodsOf(vendor).filter((m) => m.kind === 'key')
    own = connections.filter((c) => {
      if (c.id.startsWith('sub:') || c.id.startsWith('siwc:') || c.id.endsWith('-oauth')) return false
      if (c.method) return c.method === method.id
      if (c.catalog_id && method.catalog_id) return c.catalog_id === method.catalog_id
      return sameKind.length === 1
    })
  }
  return own.find((c) => c.state === 'ready') ?? own[0]
}
