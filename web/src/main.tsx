import './lib/storageMigration' // 必须最先执行：store 模块加载时就读 localStorage
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from './App'
import { ErrorBoundary } from './components/ErrorBoundary'
import { bootstrapDesktopSession, setDesktopMenuLocale } from './lib/desktop'
import { currentLocale, i18n, initI18n, t } from './i18n'
import 'generative-loaders/styles.css'
import './index.css'

// i18n 必须在挂载 React **之前**就位：下面那个「桌面会话建立失败」的页面
// 根本走不到 React，它也得有翻译。
initI18n()
document.documentElement.lang = currentLocale()

// 原生菜单的文案在壳里另有一份（Rust 在 webview 起来之前就要建菜单）。
// 这条通知**放在这儿而不是放进 `@/i18n`**：i18n 模块被 store / lib / 单测到处
// import，让它反过来依赖 `lib/desktop` 会绕成环。浏览器模式下这两句都是 no-op。
void setDesktopMenuLocale(currentLocale())
i18n.on('languageChanged', (lng) => void setDesktopMenuLocale(lng))

const rootEl = document.getElementById('root')!

// 桌面模式先换会话（fragment nonce → HttpOnly cookie）再挂载：store 一挂载就会
// 发 API，会话没建立时全是 401。浏览器模式下 bootstrap 立即返回 skipped。
void bootstrapDesktopSession().then((r) => {
  if (r === 'failed') {
    // 极少数情况（nonce 被吃掉/重复使用）：给出可操作的提示而不是白屏 + 一串 401
    const div = document.createElement('div')
    div.setAttribute(
      'style',
      'display:flex;height:100%;align-items:center;justify-content:center;' +
        'font:13px/1.6 -apple-system,sans-serif;color:#3D3D39;background:#F2F2EF',
    )
    div.textContent = t('boot.desktopSessionFailed', { ns: 'workspace' })
    rootEl.replaceChildren(div)
    return
  }
  createRoot(rootEl).render(
    <StrictMode>
      <ErrorBoundary>
        <App />
      </ErrorBoundary>
    </StrictMode>,
  )
})
