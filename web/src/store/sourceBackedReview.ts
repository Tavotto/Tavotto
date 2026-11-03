import { create } from 'zustand'
import type { AdoptionGuard, SourceBackedVerdict } from '@/lib/artifactValidation'

/**
 * 来源驱动采用的「对照确认」（ADR 0139 §二 / §六）：同一屏左原 PNG（磁盘原像素）、右重画，
 * 用户看过之后才确认「以重画为基础编辑」。**这里只管对话框的开合与答案**：
 * 提交 guard 是 `actions.ts` 里那一次事务（取消 = 什么都不写）。
 *
 * - `adopt`：首次采用，重画与原图有差异；
 * - `changed`：已采用面板重画摘要变了（脚本 / 数据 / 环境变了）→ 确认作废，重新对照。
 *
 * 瞬态：只在内存，不进文档；切项目（`clear()`）按「取消」收尾。
 */
export interface ReviewRequest {
  mode: 'adopt' | 'changed'
  panelId: string
  fileId: string
  verdict: SourceBackedVerdict
  /** 磁盘原图的显示地址（`/api/file`，不经引擎） */
  originalSrc: string
  /** 重画的 PNG（data URL，引擎对同一份零编辑重画出的图） */
  reconstructionSrc: string
}

interface ReviewState {
  request: ReviewRequest | null
  ask: (request: ReviewRequest) => Promise<boolean>
  answer: (confirmed: boolean) => void
  clear: () => void
}

let resolver: ((ok: boolean) => void) | null = null

export const useSourceBackedReview = create<ReviewState>((set, get) => ({
  request: null,
  ask: (request) => {
    // 同一时刻只有一个对照：新的来了，旧的按取消收掉（它的发起者随后会因 `current()` 失败而放弃）
    resolver?.(false)
    return new Promise<boolean>((resolve) => {
      resolver = resolve
      set({ request })
    })
  },
  answer: (confirmed) => {
    const done = resolver
    resolver = null
    if (get().request) set({ request: null })
    done?.(confirmed)
  },
  clear: () => get().answer(false),
}))

/** 「重新对照确认」的执行者由 `actions.ts` 登记（直接 import 会让 renderStore → actions 成环） */
type ReviewRunner = (panelId: string) => unknown
let reviewRunner: ReviewRunner | null = null
export const registerSourceBackedReviewRunner = (fn: ReviewRunner): void => {
  reviewRunner = fn
}
export const runSourceBackedReview = (panelId: string): void => {
  void reviewRunner?.(panelId)
}

/** 提示的主人：对照完成 / 面板删除 / 切项目时撤掉 */
export const sourceReviewOwner = (panelId: string): string => `source.review:${panelId}`

/**
 * 渲染被后端以 `reconstruction_changed` / `confirmation_required` 拒绝（`renderStore.rejectArtifactRenders` 调）：
 * 编辑保留、不自动换基，给一句话 + 一个主按钮「重新对照确认」。
 * 提示本身（要读文档找面板、要发 toast）由 `actions.ts` 登记——本模块保持叶子，否则 renderStore → 这里 → documentStore
 * 会把 import 环扩大（`importArchitecture` 门禁）。
 */
type ReviewNotifier = (fileId: string, guard: AdoptionGuard, reason: unknown) => void
let notifier: ReviewNotifier | null = null
export const registerReconstructionNotifier = (fn: ReviewNotifier): void => {
  notifier = fn
}
export const notifyReconstructionReview: ReviewNotifier = (fileId, guard, reason) => notifier?.(fileId, guard, reason)
