/**
 * React 侧的小工具。组件里翻译一律走 `useTranslation()`（react-i18next 会
 * 订阅 languageChanged，切语言时组件自然重渲染，不需要刷新页面）。
 */
import { useTranslation } from 'react-i18next'
import { formatMessage } from './index'
import type { UiMessage } from './message'

/**
 * 把描述符翻成当前语言。**必须在组件里用这个而不是直接 formatMessage**：
 * 前者顺带订阅了语言变化，后者不订阅——切语言后 toast 会卡在旧语言上。
 */
export function useFormatMessage(): (m: UiMessage | null | undefined) => string {
  useTranslation()
  return (m) => formatMessage(m)
}

/** 当前语言（组件内使用，切换时会触发重渲染）。 */
export function useLocale(): string {
  const { i18n } = useTranslation()
  return i18n.language
}
