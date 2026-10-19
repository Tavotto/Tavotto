import { useEffect, useRef, useState, type DragEvent, type KeyboardEvent } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { PRODUCT_NAME } from '@/lib/brand'
import { dragHasFiles } from '@/lib/scriptImport'
import { useOnboardingStore } from '@/store/onboardingStore'
import { STATUS_AUTO_DISMISS_MS } from '@/store/uiStore'
import { useAutoOpen } from './autoOpen'
import { TutorialSteps } from './TutorialSteps'
import { TutorialWelcome } from './TutorialWelcome'

/**
 * 三步原生新手教程的根（ADR 0136）：欢迎页 → 拖入示例脚本 → 拖动图例 → 改标题字号 → 前后对比。
 *
 * 它是盖在主页 / 工作台上面的一整层（portal 到 body，`z-overlay`；对话框照常压在它上面），
 * 打开期间把应用根 `#root` 设成 `inert`——底下的工作台点不到、Tab 不进去、读屏读不到。
 * 状态只在 `onboardingStore`（`surface` 决定显示哪一屏，步骤与演示改动是持久化的那一格）。
 *
 * 四条边界（P0）：
 *   * **不执行 Python、不发请求**：整层只读写 onboardingStore，图是 `lib/onboarding/demoFigure`
 *     的受控演示；
 *   * **外部文件进不来**：系统拖进来的文件在这一层被拒收（`dropEffect = 'none'`，不读
 *     `DataTransfer.files`），只说一句「完成后可以导入自己的脚本」。macOS 壳只在页面接受了
 *     拖动时才旁听系统路径（`native_drop.rs`），拒收 = 壳不发 `tavotto:file-drop`；主页的
 *     订阅另有一道闸（教程层开着就不认）；
 *   * **按键不漏到底下**：教程层里的按键不冒泡到 window，工作台的快捷键（Esc / Delete / ⌘Z…）
 *     不会在看不见的画布上生效；
 *   * **收起不留东西**：监听、`inert`、计时器都随卸载清掉。
 */
export function NativeTutorial() {
  const surface = useOnboardingStore((s) => s.surface)
  useAutoOpen()
  useInertApp(surface !== 'none')
  if (surface === 'none') return null
  return createPortal(<TutorialLayer surface={surface} />, document.body)
}

/** 教程层开着时，底下的应用整片 inert（点不到、Tab 不进、读屏不读）；收起时原样还回去 */
function useInertApp(on: boolean): void {
  useEffect(() => {
    const root = document.getElementById('root')
    if (!on || !root || root.hasAttribute('inert')) return
    root.setAttribute('inert', '')
    return () => root.removeAttribute('inert')
  }, [on])
}

/* --------------------------------- 外层 --------------------------------- */

function TutorialLayer({ surface }: { surface: 'welcome' | 'tutorial' }) {
  const { t } = useTranslation('dialogs')
  const [externalNotice, setExternalNotice] = useState(false)
  const layer = useRef<HTMLDivElement>(null)

  // 「外部文件」那句话几秒后自己收起；换屏时也收
  useEffect(() => {
    if (!externalNotice) return
    const timer = window.setTimeout(() => setExternalNotice(false), STATUS_AUTO_DISMISS_MS)
    return () => window.clearTimeout(timer)
  }, [externalNotice])
  useEffect(() => setExternalNotice(false), [surface])

  // 打开时焦点进来：这一屏自己会把焦点放到标题 / 主控件上；没放（步骤页、从菜单打开）就落在
  // 整层上——焦点绝不留在底下那个已经 inert 的界面里。收起时还给打开前的那个元素
  useEffect(() => {
    const raf = requestAnimationFrame(() => {
      if (layer.current && !layer.current.contains(document.activeElement)) {
        layer.current.focus({ preventScroll: true })
      }
    })
    return () => cancelAnimationFrame(raf)
    // 换屏（欢迎页 → 第一步）时点下去的那颗按钮跟着卸载，焦点会掉回 body：每次换屏都再核一次
  }, [surface])
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null
    return () => {
      // 等这一轮提交把 #root 的 inert 摘掉再还焦点（inert 里的元素拿不到焦点）
      queueMicrotask(() => {
        if (before?.isConnected && !before.closest('[inert]')) before.focus({ preventScroll: true })
      })
    }
  }, [])

  const refuseFiles = (e: DragEvent) => {
    if (!dragHasFiles(e.dataTransfer.types)) return
    // 拒收：不 preventDefault 的话浏览器会把文件当页面打开；dropEffect none = 这里放不下
    e.preventDefault()
    e.dataTransfer.dropEffect = 'none'
    setExternalNotice(true)
  }

  const isolateKeys = (e: KeyboardEvent) => {
    // 教程层自己处理完的按键不再冒泡到 window：工作台的快捷键不该在看不见的画布上生效
    e.stopPropagation()
  }

  return (
    <div
      ref={layer}
      role="dialog"
      aria-modal="true"
      aria-label={t('onboarding.shell.label', { product: PRODUCT_NAME })}
      data-native-tutorial=""
      data-surface={surface}
      tabIndex={-1}
      className="fixed inset-0 z-overlay flex flex-col bg-bg text-ink outline-none"
      onDragEnter={refuseFiles}
      onDragOver={refuseFiles}
      onDrop={refuseFiles}
      onKeyDown={isolateKeys}
    >
      {surface === 'welcome' ? <TutorialWelcome /> : <TutorialSteps />}
      <div
        role="status"
        aria-live="polite"
        data-tutorial-external-notice=""
        className="pointer-events-none fixed inset-x-0 bottom-6 flex justify-center px-4"
      >
        {externalNotice && (
          <span className="animate-rise-in rounded-full bg-ink px-4 py-2 text-base text-surface shadow-pop">
            {t('onboarding.shell.externalFile', { product: PRODUCT_NAME })}
          </span>
        )}
      </div>
    </div>
  )
}
