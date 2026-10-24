import { currentLocale } from '@/i18n'
import { ag, cx } from './copy'
import { sortVendors, type AiCapabilities, type AiConnection, type AiVendor } from '@/lib/api'

/** 订阅登录类厂商 → 内部登录用的家族 id（订阅连接 id = `sub:<家族>`）；表外的厂商 id 即家族 id */
const LOGIN_AGENT: Record<string, string> = { chatgpt: 'codex', claude: 'claude' }
export const loginAgentOf = (v: AiVendor): string => LOGIN_AGENT[v.id] ?? v.id

/** 中文界面国内在前，其余海外在前（以界面语言为准，不看后端的 `locale_region` 提示） */
export const isCnFirst = (): boolean => currentLocale() === 'zh-CN'

/** 列表里出现的厂商：「自定义」另走手动入口；账号登录类 client_id 没配置（`available=false`）时不出现 */
export function listedVendors(caps: AiCapabilities | null | undefined): AiVendor[] {
  const vendors = (caps?.vendors ?? []).filter((v) => v.region !== 'custom' && !(v.connect === 'oauth' && !v.available))
  return sortVendors(vendors, isCnFirst())
}

/** 这家厂商当前的连接（同厂商多条时取最该显示的：ready 优先） */
export function connectionOf(caps: AiCapabilities | null | undefined, vendor: AiVendor): AiConnection | undefined {
  const own = (caps?.connections ?? []).filter((c) => c.vendor === vendor.id)
  return own.find((c) => c.state === 'ready') ?? own[0]
}

/** 厂商的界面名：有 `label_key` 走已有的目录文案，否则（订阅类 / 老后端）用 `label` */
export const vendorName = (v: AiVendor): string => (v.label_key ? ag(v.label_key) : v.label)

/** 连接异常的一句人话（只认几个常见原因码，其余统一一句） */
export const connectionErrorText = (c: AiConnection): string =>
  c.error && ['unauthorized', 'network', 'timeout'].includes(c.error) ? cx(`err.${c.error}`) : cx('error')
