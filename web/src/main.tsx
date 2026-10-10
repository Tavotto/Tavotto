import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from './App'
import { ErrorBoundary } from './components/ErrorBoundary'
import { IconProvider } from './components/ui/Icon'
import { bootstrapDesktopSession, setDesktopMenuLocale } from './lib/desktop'
import { currentLocale, i18n, initI18n, t } from './i18n'
import { applyTheme } from './lib/theme'
import { useUiStore } from './store/uiStore'
import './index.css'

// i18n 必须在挂载 React **之前**就位：下面那个「桌面会话建立失败」的页面
// 根本走不到 React，它也得有翻译。
initI18n()
document.documentElement.lang = currentLocale()

// 外观（设置 › 通用 › 外观）：首帧之前的那一次落点在 index.html <head> 的同步脚本里（这个模块是延迟执行的，
// 跑到这里时浏览器可能已经画过一帧）；这里是对账——以 uiStore 读出的偏好为准再落一次。
// 下面那个不经 React 的「会话建立失败」页也读同一套 CSS 变量（宪法第二十八节）
applyTheme(useUiStore.getState().theme)

// 原生菜单的文案在壳里另有一份（Rust 在 webview 起来之前就要建菜单）。
// 这条通知**放在这儿而不是放进 `@/i18n`**：i18n 模块被 store / lib / 单测到处
// import，让它反过来依赖 `lib/desktop` 会绕成环。浏览器模式下这两句都是 no-op。
// 头一次是**汇报**当前生效的语言（可能只是跟随系统），后面每一次
// languageChanged 都来自用户在设置里换语言（`setLocale` 是唯一入口）——
// 只有后者算「亲手选的」，壳据此决定要不要把它记成跨重启的偏好。
void setDesktopMenuLocale(currentLocale())
i18n.on('languageChanged', (lng) => void setDesktopMenuLocale(lng, true))

const rootEl = document.getElementById('root')!

// 桌面模式先换会话（fragment nonce → HttpOnly cookie）再挂载：store 一挂载就会
// 发 API，会话没建立时全是 401。浏览器模式下 bootstrap 立即返回 skipped。
void bootstrapDesktopSession().then((r) => {
  if (r === 'failed' || r === 'unauthenticated') {
    // 极少数情况（nonce 被吃掉/重复使用，或没有会话的手敲地址）：
    // 给出可操作的提示而不是白屏 + 一串 401
    // 不经 React（会话都没有，React 树里的 store 一挂上就是一串 401），但外观照样走 token：
    // 与崩溃页同一副——panel 圆角、对话框投影、17 / 600 标题、13 正文、一颗主按钮「重新加载」
    const div = document.createElement('div')
    div.setAttribute('data-boot-failure', '')
    div.setAttribute(
      'style',
      'display:flex;height:100%;align-items:center;justify-content:center;padding:16px;' +
        'background:var(--color-bg);font-family:var(--font-sans)',
    )
    const card = document.createElement('div')
    card.setAttribute('role', 'alert')
    card.setAttribute(
      'style',
      'display:flex;flex-direction:column;gap:16px;width:420px;max-width:100%;padding:20px;' +
        'border-radius:var(--radius-panel);background:var(--color-surface);box-shadow:var(--shadow-dialog)',
    )
    const body = document.createElement('p')
    body.setAttribute('style', 'margin:0;font-size:13px;line-height:1.6;color:var(--color-ink-2)')
    body.textContent =
      r === 'unauthenticated'
        ? t('boot.sessionUnauthenticated', { ns: 'workspace' })
        : t('boot.desktopSessionFailed', { ns: 'workspace' })
    const reload = document.createElement('button')
    reload.type = 'button'
    reload.setAttribute('data-boot-reload', '')
    reload.textContent = t('actions.reload')
    reload.setAttribute(
      'style',
      'align-self:flex-end;height:32px;padding:0 14px;border:0;border-radius:9999px;' +
        'font:13px var(--font-sans);background:var(--color-ink);color:var(--color-surface)',
    )
    reload.addEventListener('click', () => location.reload())
    card.append(body, reload)
    div.append(card)
    rootEl.replaceChildren(div)
    return
  }
  createRoot(rootEl).render(
    <StrictMode>
      <ErrorBoundary>
        {/* 图标默认档在根上给（Icon.tsx）：不写 size 的图标拿到 14px / 1.75 描边 */}
        <IconProvider>
          <App />
        </IconProvider>
      </ErrorBoundary>
    </StrictMode>,
  )
})
