/**
 * vitest 全局前置：**把界面语言钉成 zh-CN**。
 *
 * 不钉的话每个用例的语言取决于跑测试那台机器——`detectLocale()` 在没有
 * 手动选择时读系统语言，而 jsdom 的 `navigator.language` 是 `en-US`。
 * 于是整套按中文文案取节点的既有用例会在别人机器上红，排查时看到的还是
 * 「找不到这个按钮」这种毫无线索的报错。
 *
 * 两件事都要做：
 *   ① 改 `navigator.language(s)`——不少用例用 `vi.resetModules()` 重建模块图，
 *      那时 `@/i18n` 会重新求值并再跑一次 `detectLocale()`，只钉住当前实例
 *      是拦不住的；
 *   ② 钉住已经建好的那个实例，给不重建模块图的用例兜底。
 *
 * 关心语言的用例自己 `setLocale('en-US')` 或改 `navigator.language`，跑完由
 * 这里的 beforeEach 收回。用 `i18n.changeLanguage` 而不是 `setLocale`：后者
 * 会写 localStorage 偏好，那本身是被测行为，前置动作不该替它写。
 */
import { afterEach, beforeEach } from 'vitest'
import { DEFAULT_LOCALE, i18n, initI18n } from '@/i18n'
import { cancelFocusRescues } from '@/lib/focusRescue'
import { forgetProjectFilesInSession } from '@/lib/projectFile'

const pinNavigator = () => {
  Object.defineProperty(navigator, 'language', {
    value: DEFAULT_LOCALE,
    configurable: true,
  })
  Object.defineProperty(navigator, 'languages', {
    value: [DEFAULT_LOCALE],
    configurable: true,
  })
}

pinNavigator()
initI18n(DEFAULT_LOCALE)

beforeEach(async () => {
  // 项目文件绑定的会话层（`lib/projectFile.ts`）是模块级的：不清的话上一条用例的绑定会
  // 盖住这一条用例自己往 localStorage 里摆的（或清掉的）那一份
  forgetProjectFilesInSession()
  pinNavigator()
  if (i18n.language !== DEFAULT_LOCALE) await i18n.changeLanguage(DEFAULT_LOCALE)
})


// 焦点救援（`lib/focusRescue.ts`）不属于任何组件：store 的状态切换发起、最多活 2 秒后自己
// 收掉。用例在这 2 秒里结束的话，文件跑完 jsdom 被拆掉，它的定时器 / MutationObserver
// 再醒来就是 `document is not defined`——用例全绿，vitest 却记一个 Unhandled Error、
// 退出码 1（#679 的 CI run 36660528689）。全量里有十来个文件、上百条用例会留下它，
// 所以这里一刀切：每条用例结束都停掉，谁也活不过它的用例
afterEach(() => {
  cancelFocusRescues()
})
