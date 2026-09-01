/**
 * 英文文案不会把界面撑坏。
 *
 * jsdom **没有布局**：offsetWidth 恒为 0，scrollWidth 也一样，所以「真的溢出
 * 了吗」在这里问不出来——那一半由 `e2e/i18n.spec.ts` 在真浏览器里量
 * （scrollWidth > clientWidth）。这一批守的是能在单测层守住的两件事：
 *
 *   ① **紧位置的英文字数有上限**。顶栏按钮、标签页、右栏分区标题这些位置宽度
 *      是排版给死的，中文两个字、英文写成一句话就会挤走别的东西。写清楚每条
 *      的位置和预算，评审时看得见代价。
 *   ② **会长的文本都有截断兜底**。宽度受限的容器里放不带 `truncate` /
 *      `line-clamp` 的长文本，英文下要么撑破容器要么把兄弟节点挤出去。
 *
 * 预算是**回归闸门**，不是审美标准：现值都是当前文案量出来的，留了一点余量。
 * 真需要更长的词就连同截图一起把预算改上去，别偷偷放宽。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { CanvasTabs } from '@/components/CanvasTabs'
import { Inspector } from '@/components/inspector/Inspector'
import { TopBar } from '@/components/TopBar'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { DEFAULT_LOCALE, i18n, resources } from '@/i18n'
import type { Namespace } from '@/i18n'
import { useDocumentStore } from '@/store/documentStore'
import { emptyProject } from '@/types/document'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true
globalThis.fetch = (async () => new Response('{}', { status: 200 })) as typeof fetch

const lookup = (ns: Namespace, key: string): string => {
  let node: unknown = resources['en-US'][ns]
  for (const part of key.split('.')) {
    node = (node as Record<string, unknown>)?.[part]
  }
  return typeof node === 'string' ? node : ''
}

/** [命名空间, key, 字符预算, 这是界面上的哪个位置] */
type Budget = [Namespace, string, number, string]

const BUDGETS: Budget[] = [
  // 顶栏：44px 高的一行里塞了品牌 / 项目 / 文档 / 工具 / 缩放 / 导出
  ['workspace', 'topbar.export', 12, '顶栏唯一的填色主动作'],
  ['workspace', 'topbar.undo', 12, '顶栏图标按钮的无障碍名'],
  ['workspace', 'topbar.redo', 12, '顶栏图标按钮的无障碍名'],
  ['workspace', 'topbar.more', 12, '顶栏「更多」菜单触发器'],
  ['workspace', 'topbar.fitCanvas', 20, '缩放区的按钮'],
  ['workspace', 'topbar.saveSaving', 16, '文档名旁的保存状态'],
  ['workspace', 'topbar.saveDirty', 20, '文档名旁的保存状态'],
  ['workspace', 'topbar.saveError', 16, '文档名旁的保存状态'],
  ['workspace', 'topbar.saveConflict', 20, '文档名旁的保存状态'],
  // 右栏三个标签页并排，宽度 296–320px
  ['inspector', 'tab.properties', 14, '右栏标签页'],
  ['inspector', 'tab.canvas', 14, '右栏标签页'],
  // 通用按钮：对话框页脚一行放得下两三个
  ['common', 'actions.cancel', 12, '对话框次要动作'],
  ['common', 'actions.close', 12, '对话框次要动作'],
  ['common', 'actions.save', 12, '对话框主动作'],
  ['common', 'actions.delete', 12, '对话框主动作'],
  ['common', 'actions.continue', 14, '确认框主动作'],
  // 左侧 44px 图标轨道的抽屉名（图标下方 / tooltip 里）
  ['workspace', 'rail.assets', 16, '左侧轨道按钮'],
  ['workspace', 'rail.layers', 16, '左侧轨道按钮'],
  ['workspace', 'rail.elements', 18, '左侧轨道按钮'],
  ['workspace', 'rail.canvases', 16, '左侧轨道按钮'],
  // 画布标签条：一排标签，每个都可关闭
  ['workspace', 'tabs.newCanvas', 18, '画布标签条上的新建'],
  ['workspace', 'tabs.allCanvases', 18, '画布标签条上的总览'],
  // 缺依赖修复卡片的动作按钮（ADR 0019）。它们落在右栏里，容器约 272px；
  // Button 是 whitespace-nowrap + shrink-0 的，超出预算就直接把整栏撑破
  // ——所以这几条的预算是硬边界，不是审美偏好。
  ['errors', 'engine.repairUseProjectEnv', 32, '缺依赖卡片：装进项目环境'],
  ['errors', 'engine.repairCreateManaged', 32, '缺依赖卡片：建一个 Tavotto 环境'],
  ['errors', 'engine.repairUseManaged', 32, '缺依赖卡片：装进 Tavotto 环境'],
  ['errors', 'engine.repairInstallToProject', 32, '缺依赖卡片：确认装进项目环境'],
  ['errors', 'engine.repairPrepareAndContinue', 32, '缺依赖卡片：确认建环境并继续'],
  ['errors', 'engine.repairCancel', 12, '安装进度里的取消'],
  ['errors', 'engine.repairClose', 12, '安装结束后的关闭'],
  // 接入状态：六个状态名同时出现在素材卡角标（卡片宽 ~150px，`truncate` 兜底）、
  // 接入中心每一行的 badge 与素材说明条的标题里。角标是卡片上唯一的文字覆盖层，
  // 长一点就会盖到图上——预算是硬边界。
  ['workspace', 'readiness.status.editable', 18, '素材卡角标 / 行内状态'],
  ['workspace', 'readiness.status.auto_linkable', 18, '素材卡角标 / 行内状态'],
  ['workspace', 'readiness.status.needs_probe', 18, '素材卡角标 / 行内状态'],
  ['workspace', 'readiness.status.conflict', 18, '素材卡角标 / 行内状态'],
  ['workspace', 'readiness.status.source_missing', 18, '素材卡角标 / 行内状态'],
  ['workspace', 'readiness.status.layout_only', 18, '素材卡角标 / 行内状态'],
  // 横幅右端与说明条里的按钮，与「关闭」并排
  ['workspace', 'readiness.openCenter', 20, '摘要横幅上的按钮'],
  ['workspace', 'readiness.whyNotEditable', 26, '画布工具条上的解释入口'],
  // 快速编辑（Prompt 09）：模式标签在顶栏那一行里，两个出口在画布上方那条
  // 浮动条上——它必须能整条塞进窄画布，所以按钮文案压得比一般按钮还紧
  ['workspace', 'fastEdit.mode', 14, '顶栏模式标签 / 浮动条上的模式角标'],
  ['workspace', 'fastEdit.layoutMode', 16, '顶栏模式标签（画布排版）'],
  ['workspace', 'fastEdit.addToCanvas', 16, '快速编辑浮动条上的主动作'],
  ['workspace', 'fastEdit.toLayout', 16, '快速编辑浮动条上的返回出口'],
  ['workspace', 'fastEdit.connectSource', 24, '降级说明条上的下一步'],
  ['workspace', 'assets.openFigure', 8, '素材卡悬停时右下角那个就近入口'],
  ['workspace', 'fastEdit.sizeStale', 14, '原图尺寸旁的来源标记'],
  ['workspace', 'fastEdit.sizeAssumed', 16, '原图尺寸旁的来源标记'],
  ['workspace', 'fastEdit.sizeUnknown', 16, '原图尺寸旁的来源标记'],
  ['workspace', 'rail.readiness', 20, '左侧轨道按钮'],
  ['workspace', 'rail.problems', 16, '左侧轨道按钮'],
  // 问题面板（Prompt 11）：等级筛选是并排的四个小 chip，抽屉最窄 280px；
  // 行内的两颗按钮跟在标题右边，长一点就把主语挤没了——硬边界
  ['errors', 'problems.severity.error', 12, '等级 chip / 行内 badge'],
  ['errors', 'problems.severity.warn', 12, '等级 chip / 行内 badge'],
  ['errors', 'problems.severity.notVerifiable', 16, '等级 chip / 行内 badge'],
  ['errors', 'problems.severity.suggestion', 12, '等级 chip / 行内 badge'],
  ['errors', 'problems.fix', 8, '问题行上的修复按钮'],
  ['errors', 'problems.fixChoose', 10, '问题行上的修复按钮（要先选）'],
  ['errors', 'problems.retry', 14, '检查失败后的重试'],
  ['errors', 'problems.clearFilter', 12, '筛选为空时的出口'],
]

describe('紧位置的英文文案有字数上限', () => {
  it.each(BUDGETS)('%s:%s ≤ %d 字符（%s）', (ns, key, max, where) => {
    const text = lookup(ns, key)
    expect(text, `${ns}:${key} 在 en-US 里不存在（${where}）`).not.toBe('')
    expect(text.length, `${ns}:${key}「${text}」超出 ${where} 的预算`).toBeLessThanOrEqual(max)
  })

  it('中文侧同样不该突然变长（同一位置两种语言都得放得下）', () => {
    const over: string[] = []
    for (const [ns, key, max, where] of BUDGETS) {
      let node: unknown = resources['zh-CN'][ns]
      for (const part of key.split('.')) node = (node as Record<string, unknown>)?.[part]
      const text = typeof node === 'string' ? node : ''
      // 汉字比拉丁字母宽，预算折半
      if (text.length > Math.ceil(max / 2)) over.push(`${ns}:${key}「${text}」（${where}）`)
    }
    expect(over).toEqual([])
  })
})

/* -------------------------------------------------------------------------- */

// 只有下半部分的用例会挂组件；上面查字数的那批不碰 DOM
let container: HTMLDivElement | null = null
let root: Root | null = null

const mount = (node: React.ReactNode) => {
  container = document.createElement('div')
  document.body.appendChild(container)
  const r = createRoot(container)
  root = r
  act(() => r.render(<TooltipProvider>{node}</TooltipProvider>))
}

/**
 * 宽度被排版限死的容器：定宽或者给了上限。
 *
 * 不含 `shrink-0` —— 那是「别缩我」，它自己不会溢出，只会把兄弟挤走，而挤谁
 * 由兄弟的 `min-w-0 truncate` 兜着；也不含裸 `flex-1`，它按剩余空间伸缩。
 */
const WIDTH_CONSTRAINED = /(^|\s)(w-\d|w-\[|max-w-)/
/** 兜底手段：截断、限行、或者干脆允许滚动 */
const OVERFLOW_GUARD = /(^|\s)(truncate|line-clamp-|overflow-(hidden|x-auto|y-auto|auto))/

/**
 * 会换行的块级文本（说明段、提示语）不在看护范围内：它们本来就该换行，
 * 变高不会挤走别人。只查**不换行**的那些——`whitespace-nowrap` 一加，
 * 内容超宽就只能往外顶。
 */
const isNowrapText = (el: Element) =>
  /(^|\s)whitespace-nowrap(\s|$)/.test(el.className) && (el.textContent ?? '').trim().length > 0

beforeEach(async () => {
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_i18n_overflow')
  await act(async () => {
    await i18n.changeLanguage('en-US')
  })
})

afterEach(async () => {
  if (root) await act(async () => root!.unmount())
  container?.remove()
  root = null
  container = null
  await act(async () => {
    await i18n.changeLanguage(DEFAULT_LOCALE)
  })
})

describe('英文界面下的截断兜底', () => {
  /** 兜底可以挂在自己身上，也可以挂在里层负责显示文本的那个节点上 */
  const guarded = (el: Element): boolean =>
    OVERFLOW_GUARD.test(el.className as string) ||
    [...el.querySelectorAll('*')].some(
      (kid) => typeof kid.className === 'string' && OVERFLOW_GUARD.test(kid.className),
    )

  const offenders = () =>
    [...container!.querySelectorAll('*')]
      .filter((el) => typeof el.className === 'string')
      .filter((el) => isNowrapText(el))
      .filter((el) => WIDTH_CONSTRAINED.test(el.className))
      .filter((el) => !guarded(el))
      .map((el) => `<${el.tagName.toLowerCase()} class="${el.className}">`)

  it('顶栏：宽度受限且不换行的文本都带截断', () => {
    mount(<TopBar />)
    expect(offenders()).toEqual([])
  })

  it('右栏：同上', () => {
    mount(<Inspector />)
    expect(offenders()).toEqual([])
  })

  it('画布标签条：标签名是用户自己起的，英文界面下更容易超长', () => {
    mount(<CanvasTabs />)
    expect(offenders()).toEqual([])
  })
})
