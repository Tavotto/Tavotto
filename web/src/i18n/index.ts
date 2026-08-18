/**
 * i18n 单一实例。
 *
 * 两条纪律：
 *   ① **翻译资源静态 import 进 bundle**，不走 http-backend、不连 CDN——
 *      桌面版离线可用是硬要求，动态拉取会让第一屏在断网时是空白 key。
 *   ② **在挂载 React 之前初始化**（main.tsx）：桌面会话建立失败那种
 *      「还没进 React 就要说话」的页面也得有翻译。
 *
 * 非 React 模块（store / lib / 工具函数）直接 `import { t } from '@/i18n'`；
 * React 组件用 `useTranslation()`，这样切换语言时组件会自然重渲染。
 */
import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'

import { DEFAULT_LOCALE, detectLocale, normalizeLocale, writeStoredLocale, type Locale } from './locale'
import type { UiMessage } from './message'

import zhCommon from './locales/zh-CN/common.json'
import zhWorkspace from './locales/zh-CN/workspace.json'
import zhProject from './locales/zh-CN/project.json'
import zhInspector from './locales/zh-CN/inspector.json'
import zhDialogs from './locales/zh-CN/dialogs.json'
import zhErrors from './locales/zh-CN/errors.json'
import zhAi from './locales/zh-CN/ai.json'
import zhShortcuts from './locales/zh-CN/shortcuts.json'

import enCommon from './locales/en-US/common.json'
import enWorkspace from './locales/en-US/workspace.json'
import enProject from './locales/en-US/project.json'
import enInspector from './locales/en-US/inspector.json'
import enDialogs from './locales/en-US/dialogs.json'
import enErrors from './locales/en-US/errors.json'
import enAi from './locales/en-US/ai.json'
import enShortcuts from './locales/en-US/shortcuts.json'

export const NAMESPACES = [
  'common',
  'workspace',
  'project',
  'inspector',
  'dialogs',
  'errors',
  'ai',
  'shortcuts',
] as const
export type Namespace = (typeof NAMESPACES)[number]

export const DEFAULT_NS: Namespace = 'common'

export const resources = {
  'zh-CN': {
    common: zhCommon,
    workspace: zhWorkspace,
    project: zhProject,
    inspector: zhInspector,
    dialogs: zhDialogs,
    errors: zhErrors,
    ai: zhAi,
    shortcuts: zhShortcuts,
  },
  'en-US': {
    common: enCommon,
    workspace: enWorkspace,
    project: enProject,
    inspector: enInspector,
    dialogs: enDialogs,
    errors: enErrors,
    ai: enAi,
    shortcuts: enShortcuts,
  },
} as const

let started = false

/**
 * 幂等初始化。测试里可以反复调用（每个 test 文件都会 import 到某个用 t() 的模块）。
 */
export function initI18n(locale?: Locale): typeof i18n {
  if (started) {
    if (locale && i18n.language !== locale) void i18n.changeLanguage(locale)
    return i18n
  }
  started = true
  void i18n.use(initReactI18next).init({
    resources,
    lng: locale ?? detectLocale(),
    fallbackLng: DEFAULT_LOCALE,
    ns: NAMESPACES as unknown as string[],
    defaultNS: DEFAULT_NS,
    // 中文没有 XSS 意义上的转义需求，且 React 本身已经转义；开着只会把
    // 用户的文件名里的引号显示成 &#39;
    interpolation: { escapeValue: false },
    returnNull: false,
    // 缺 key 时回退到 key 本身而不是空串：漏翻至少还看得见是哪一条
    parseMissingKeyHandler: (key) => key,
  })
  return i18n
}

/**
 * 切换界面语言。写偏好 + 换实例语言，**不碰任何文档或项目数据**。
 * react-i18next 订阅了 languageChanged，界面/toast/tooltip 立即跟着变，
 * 不需要刷新页面。
 */
export async function setLocale(next: Locale): Promise<void> {
  writeStoredLocale(next)
  document.documentElement.lang = next
  await i18n.changeLanguage(next)
}

/** 当前语言（永远是受支持的两档之一）。 */
export function currentLocale(): Locale {
  return normalizeLocale(i18n.language) ?? DEFAULT_LOCALE
}

/**
 * 非 React 代码的翻译入口。
 *
 * 直接把 i18next 实例的 t 转出去——它是**动态绑定**的，切换语言后同一个
 * 引用返回的就是新语言，所以 store / lib 里在调用点取值即可。
 */
export const t = i18n.t.bind(i18n)

/**
 * 描述符 → 当前语言的文本。撤销栈、toast、确认框都在**显示那一刻**才调它。
 */
export function formatMessage(m: UiMessage): string {
  return i18n.t(m.key, { ns: m.ns ?? DEFAULT_NS, ...(m.values ?? {}) })
}

export { i18n }
export * from './locale'
export * from './message'
export type { UiMessage } from './message'

// 模块被 import 到就把实例拉起来。main.tsx 里那次显式调用仍然保留（顺序在那儿
// 说话更清楚），这里是**兜底**：store / lib / 单测都可能先于 main.tsx 求值，
// 没有实例时 t() 会原样吐出 key，界面上就是一串 `project.actions.create`。
initI18n()

