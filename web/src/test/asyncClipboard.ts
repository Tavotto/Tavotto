/**
 * 测试用：把 `navigator` 摆成某个引擎的样子，给 `lib/clipboard.canPasteFromMenu` 看。
 * jsdom 自报 `AppleWebKit/…`、又没有 `navigator.clipboard`——不摆的话它就是「WebKit、读不了」。
 */
export const CHROMIUM_UA =
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36'
export const SAFARI_UA =
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15'
/** macOS 桌面壳（Tauri / WKWebView）：没有 Version / Safari 记号 */
export const WKWEBVIEW_UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko)'

/** 摆好 UA 与（可选的）`clipboard.readText`；返回还原函数 */
export function stubClipboardEngine(ua: string, readText: boolean): () => void {
  const realUa = Object.getOwnPropertyDescriptor(navigator, 'userAgent')
  const realClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard')
  Object.defineProperty(navigator, 'userAgent', { value: ua, configurable: true })
  Object.defineProperty(navigator, 'clipboard', {
    value: readText ? { readText: async () => '', writeText: async () => {} } : { writeText: async () => {} },
    configurable: true,
  })
  return () => {
    if (realUa) Object.defineProperty(navigator, 'userAgent', realUa)
    else Reflect.deleteProperty(navigator as object, 'userAgent')
    if (realClipboard) Object.defineProperty(navigator, 'clipboard', realClipboard)
    else Reflect.deleteProperty(navigator as object, 'clipboard')
  }
}
