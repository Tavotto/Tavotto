/**
 * 关窗询问闸的前端一半（issue #223，配 `src-tauri/src/main.rs` 的 `CloseGate`）。
 *
 * **为什么壳要专门问一句**：`beforeunload` 是浏览器的机制，WKWebView / WebView2
 * 在**窗口**被关掉时派不派它取决于壳，从来没有在真机上验证过。所以桌面上
 * 「有未保存的工作」这件事必须由壳来问，`beforeunload` 只保留浏览器模式那条路。
 *
 * **判据的主语**：这里问的是**保存状态**（`saveState`），不是「文档改过没有」。
 * 与 ADR 0024 的关闭保护同一条规则、同一个函数（`hasUnsavedWork`）——两处各写
 * 一份判据的结果是「刷新会拦、关窗不拦」这种只有真机才看得见的分叉。
 */

import { hasUnsavedWork, saveNow, useDocumentStore } from '@/store/documentStore'
import { resolveDesktopCloseRequest } from './desktop'

/** 三选一里用户点了哪个 */
export type CloseAnswer = 'save' | 'discard' | 'cancel'

/** 答完之后窗口的去向 */
export type CloseOutcome = 'closing' | 'stay'

/**
 * 壳问「能关吗」。
 *
 * 返回 `true` = 需要弹三选一；`false` = 已经替用户答了「关」，别弹。
 *
 * 没有未落盘的工作时**直接放行**：一个「确定要关闭吗」的空提示会让用户学会
 * 无脑点确定，那时候真该拦的那一次也拦不住了（与 `startAutosave` 里
 * `beforeunload` 先读状态再冲刷是同一条理由）。
 */
export async function beginCloseRequest(): Promise<boolean> {
  if (!hasUnsavedWork(useDocumentStore.getState().saveState)) {
    await resolveDesktopCloseRequest('close')
    return false
  }
  // 先接手再问用户：壳的看门狗只等「有没有人接手」，不等用户想多久。
  await resolveDesktopCloseRequest('hold')
  return true
}

/**
 * 用户在三选一里点了什么。
 *
 * 「保存」**存不成就不关**（`save_error` 是写盘失败，`conflict` 是磁盘上那份
 * 已经不是我以为的那份，后者连写都不会写——见 `flushAutosave`）。存不成还照关，
 * 用户按下的那个「保存」就成了一句谎话：他以为存好了，实际磁盘上是旧的。
 * 这种时候窗口留着，界面上原有的保存状态/冲突提示接着说话。
 */
export async function answerCloseRequest(answer: CloseAnswer): Promise<CloseOutcome> {
  if (answer === 'cancel') {
    await resolveDesktopCloseRequest('cancel')
    return 'stay'
  }
  if (answer === 'save') {
    const after = await saveNow()
    if (hasUnsavedWork(after)) {
      await resolveDesktopCloseRequest('cancel')
      return 'stay'
    }
  }
  await resolveDesktopCloseRequest('close')
  return 'closing'
}
