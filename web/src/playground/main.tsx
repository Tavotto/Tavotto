import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { initI18n, readStoredLocale, systemLocale, t as translate, urlLocale } from '@/i18n'
import { PRODUCT_NAME, RELEASES_LATEST_URL } from '@/lib/brand'
import { PlaygroundApp } from './PlaygroundApp'
import '@/index.css'

/**
 * 浏览器 playground 的入口（`/try`，产物由 scripts/build_browser_playground.py
 * 构建、同步进网站仓库）。
 *
 * 语言：手动选择 > `?lang=`（网站首页按语言带过来）> 系统语言 > **en-US**。
 * 最后一档与产品默认（zh-CN）不同是有意的：tavotto.com 的直接访客以国际
 * 读者居多，而从中文首页点进来的链接都带着 `?lang=zh`。
 *
 * 能力检测放在挂载之前：不满足就说清楚缺什么，绝不留一个坏掉的编辑器。
 */

const rootEl = document.getElementById('root')!

const missing: string[] = []
if (typeof WebAssembly === 'undefined') missing.push('WebAssembly')
if (typeof Worker === 'undefined') missing.push('Web Worker')
if (typeof TextDecoder === 'undefined' || typeof File === 'undefined') missing.push('File API')

initI18n(readStoredLocale() ?? urlLocale() ?? systemLocale() ?? 'en-US')

if (missing.length) {
  // 还没有可用的 React 环境保证（老浏览器），用最朴素的 DOM 说清楚；
  // 文案仍走 i18n（上面已经 initI18n），产品名来自 brand，不手写第二份
  const sep = translate('playground.bootListSeparator', { ns: 'dialogs' })
  rootEl.innerHTML = ''
  const p = document.createElement('p')
  p.style.cssText = 'max-width:32rem;margin:20vh auto 0;padding:0 1.5rem;font-size:14px;line-height:1.6;color:#5c5c56;text-align:center'
  p.textContent = translate('playground.bootUnsupportedBrowser', {
    ns: 'dialogs',
    product: PRODUCT_NAME,
    missing: missing.join(sep),
  })
  const a = document.createElement('a')
  a.href = RELEASES_LATEST_URL
  a.textContent = translate('playground.bootDownloadDesktop', { ns: 'dialogs', product: PRODUCT_NAME })
  a.style.cssText = 'display:block;margin-top:1rem;color:#2868b7'
  p.appendChild(a)
  rootEl.appendChild(p)
} else {
  createRoot(rootEl).render(
    <StrictMode>
      <ErrorBoundary>
        {/* 画布与属性页里有 Tooltip：Provider 必须在根上（与 App.tsx 同） */}
        <TooltipProvider>
          <PlaygroundApp />
        </TooltipProvider>
      </ErrorBoundary>
    </StrictMode>,
  )
}
