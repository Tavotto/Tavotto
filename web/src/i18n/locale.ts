/**
 * 语言标签的规范化与持久化。
 *
 * 只有两档界面语言：简体中文与英文。**默认永远是 zh-CN**——老用户升级上来
 * 不该被系统语言悄悄换掉界面。优先级：用户手动选择 > 系统语言 > zh-CN。
 *
 * 语言偏好存在独立的 `magplot.locale` 里，**不进 .magplot 文档、不进项目数据**：
 * 它是这台机器上这个人的偏好，跟着文档走会让同一份排版在别人机器上换语言。
 */
export const SUPPORTED_LOCALES = ['zh-CN', 'en-US'] as const
export type Locale = (typeof SUPPORTED_LOCALES)[number]

export const DEFAULT_LOCALE: Locale = 'zh-CN'

/** 独立于文档与项目数据的偏好键 */
export const LOCALE_STORAGE_KEY = 'magplot.locale'

/**
 * BCP-47 标签 → 支持的语言。
 *
 * `zh` / `zh-CN` / `zh-Hans` / `zh-Hans-CN` / `zh-SG` 全部归到 zh-CN；
 * 所有 `en-*` 归到 en-US。认不出来回 null，由调用方决定退到哪儿——
 * 「认不出来」和「明确选了中文」是两件事，混在一起就没法表达「跟随系统」。
 *
 * 繁体（zh-Hant / zh-TW / zh-HK）目前没有单独的翻译文件，暂时也落到 zh-CN：
 * 中文界面比英文界面离它更近。
 */
export function normalizeLocale(tag: string | null | undefined): Locale | null {
  if (!tag) return null
  const lower = String(tag).trim().toLowerCase().replace(/_/g, '-')
  if (!lower) return null
  const primary = lower.split('-')[0]
  if (primary === 'zh') return 'zh-CN'
  if (primary === 'en') return 'en-US'
  return null
}

/** 已保存的手动选择；没选过（或存储不可用）回 null。 */
export function readStoredLocale(): Locale | null {
  try {
    return normalizeLocale(window.localStorage.getItem(LOCALE_STORAGE_KEY))
  } catch {
    return null // 隐私模式：本次会话仍能切，只是记不住
  }
}

export function writeStoredLocale(locale: Locale | null): void {
  try {
    if (locale) window.localStorage.setItem(LOCALE_STORAGE_KEY, locale)
    else window.localStorage.removeItem(LOCALE_STORAGE_KEY)
  } catch {
    /* 存不下不影响本次会话 */
  }
}

/**
 * 桌面壳带过来的语言（落地 URL 的 `?lang=`）。
 *
 * 桌面模式下 sidecar 绑 `127.0.0.1:0`，**端口每次启动都不一样**，而端口是
 * Web Storage origin 的一部分——存进 `localStorage` 的偏好活不过一次重启。
 * 于是下次启动 `detectLocale()` 退回系统语言，`main.tsx` 再把这个退回值报给
 * 壳，把用户真正选过的那门语言连同原生菜单一起**覆盖掉**：选了跟系统不同
 * 语言的桌面用户，每次重启都被打回去。
 *
 * 唯一活得下来的存储在壳那边（应用配置目录里的 `menu-locale`），所以由它在
 * 落地 URL 上把**用户亲手选过**的那门语言带过来。壳不带这个参数就说明用户
 * 从没选过，照旧走系统语言。浏览器模式下永远没有它，行为一个字节不变。
 */
export function urlLocale(): Locale | null {
  if (typeof window === 'undefined') return null
  try {
    return normalizeLocale(new URLSearchParams(window.location.search).get('lang'))
  } catch {
    return null
  }
}

/** 系统语言（浏览器/桌面壳都走 navigator）。 */
export function systemLocale(): Locale | null {
  if (typeof navigator === 'undefined') return null
  const tags = navigator.languages?.length ? navigator.languages : [navigator.language]
  for (const tag of tags) {
    const hit = normalizeLocale(tag)
    if (hit) return hit
  }
  return null
}

/**
 * 用户手动选择 > 系统语言 > zh-CN。
 *
 * 「手动选择」有两个来源：本 origin 的 localStorage（浏览器模式、以及桌面
 * 本次会话内），和桌面壳经 `?lang=` 带过来的那份（跨重启唯一活得下来的，
 * 见 `urlLocale`）。localStorage 在前：它是本次会话里刚发生的选择。
 */
export function detectLocale(): Locale {
  return readStoredLocale() ?? urlLocale() ?? systemLocale() ?? DEFAULT_LOCALE
}

/** 语言自己的名字（切换菜单里永远用目标语言自称，不翻译）。 */
export const LOCALE_LABELS: Record<Locale, string> = {
  'zh-CN': '简体中文',
  'en-US': 'English',
}
