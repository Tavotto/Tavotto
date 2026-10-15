/**
 * 改图助手的**未发送草稿**与**待确认提交账本**（ADR 0132）：存**后端**（`data_dir` 里的 ai_history 库，
 * 与会话历史同一边界），不存浏览器。
 *
 * 为什么不是 localStorage：桌面端口被占会换 origin，localStorage 随之整个清空——用户写了一半的指令不该
 * 因此消失（用户裁决 2026-10-15：不得以 localStorage 的便利代替持久化设计）。这个模块**不碰任何浏览器存储**。
 *
 * 语义（后端为准，前端只是它的客户端）：
 * - 主语 (项目, 面板 fileId[, 请求 id])；项目取调用方传入的认领 pj，null 一律不读不写。
 * - 草稿全文覆盖、最后写入为准；**删除必须带条件**（`deleteDraftIf` 的原文 / `removeAccepted` 的账本原文）：
 *   另一个标签页后写的新稿不会被这一边的确认抹掉。
 * - 账本按 rid 一条：两个标签页同时发时各自的 rid 都在；确认 / 拒绝只清自己那条。
 * - 单稿 ≤ 20 KB（后端 413 `ai_draft_too_large`，不截断）、每项目 30 份、30 天过期——都在后端执行。
 * - 所有请求失败都**不抛**：回结果枚举，调用方（`useAiDraft`）在内存里保留并重试。
 *
 * 只在桌面 / 浏览器模式用：Codex 内嵌画布与 playground 没有助手面板，不 import 本模块
 * （`aiDraft.hosts.test.ts`）。**隐私**：草稿全文与请求 id 不进日志、遥测、诊断包 frontend-state、
 * interaction trace（`aiDraft.privacy.test.ts` + 后端 `tests/test_ai_drafts.py`）。
 */
import {
  acceptAiPending,
  ApiError,
  clearAiPending,
  deleteAiDraftIf,
  fetchAiDraft,
  fetchAiPendings,
  putAiDraft,
  putAiPending,
} from '@/lib/api'

/** 单稿上限（UTF-8 字节），与后端 `ai_history.DRAFT_MAX_BYTES` 同值：前端据此先给「过长」的答案，后端是权威 */
export const DRAFT_MAX_BYTES = 20 * 1024

/** 点发送时钉住的目标（与 `AiPanel` 的 `SendTarget` 同形；这里不 import 组件） */
export interface DraftTarget {
  scope: 'element' | 'axes' | 'figure'
  gid: string | null
  label: string | null
  target: string
}

export interface Draft {
  project: string
  fileId: string
  text: string
  /** 最老那条未落定提交的 rid / 目标（文本也取它提交时的原文）；没有未落定提交时缺省 */
  rid?: string
  sent?: DraftTarget
  /** 全部未落定的提交；每条各自还原成「已发出未确认」 */
  pendings?: { rid: string; text: string; sent: DraftTarget }[]
}

/** `saved` 已存；`removed` 空文本 = 已删；`too_large` 后端 413（旧稿也已删）；`unavailable` 没存成（网络 / 后端错），调用方重试 */
export type SaveResult = 'saved' | 'removed' | 'too_large' | 'unavailable'

const byteLength = (text: string): number => {
  try {
    return new TextEncoder().encode(text).length
  } catch {
    return text.length * 3
  }
}

/** 现在就能判：写之前先给界面一个「过长」的答案，不等防抖与往返 */
export const isDraftTooLarge = (text: string): boolean => byteLength(text) > DRAFT_MAX_BYTES

/** 读：有未落定提交时文本是它提交时的原文（界面据此还原「已发出未确认」）；读不到 / 失败回 null */
export async function loadDraft(project: string | null, fileId: string | null): Promise<Draft | null> {
  if (!project || !fileId) return null
  let doc
  try {
    doc = await fetchAiDraft(fileId, project)
  } catch {
    return null
  }
  const pendings = (doc.pendings ?? []).map((p) => ({ rid: p.rid, text: p.text, sent: p.sent as DraftTarget }))
  if (pendings.length > 0) {
    return { project, fileId, text: pendings[0].text, rid: pendings[0].rid, sent: pendings[0].sent, pendings }
  }
  return doc.draft ? { project, fileId, text: doc.draft.text } : null
}

/** 写草稿**文本**（全文覆盖）。`keepalive`：pagehide / 卸载时的冲刷 */
export async function saveDraft(
  project: string | null,
  fileId: string | null,
  text: string,
  keepalive = false,
): Promise<SaveResult> {
  if (!project || !fileId) return 'unavailable'
  try {
    await putAiDraft(fileId, text, project, keepalive && byteLength(text) < 60 * 1024)
    return text === '' ? 'removed' : 'saved'
  } catch (e) {
    if (e instanceof ApiError && e.status === 413) return 'too_large'
    return 'unavailable'
  }
}

/** 条件删除：后端的文本仍等于 `ifText` 才删。回是否删了 */
export async function deleteDraftIf(project: string | null, fileId: string | null, ifText: string): Promise<boolean> {
  if (!project || !fileId) return false
  try {
    return (await deleteAiDraftIf(fileId, ifText, project)).deleted
  } catch {
    return false
  }
}

/**
 * 点发送：在 spawn 之前把这条提交（原文 + 目标 + 脚本）记进后端账本。**回是否写成功**：
 * 没写成（网络 / 后端错 / 超限）就不该发——账本是「响应丢了之后还能查到这条」的唯一线索，
 * 「尽力写、照样发」会让丢响应后的刷新没有 rid 可查、同一条指令换新 id 再执行一遍（Codex #1098）。
 * 没有项目认领时无从记账，回 true 由调用方照旧处理（那时本来也发不出去）。
 */
export async function markSubmitted(
  project: string | null,
  fileId: string | null,
  text: string,
  rid: string,
  sent: DraftTarget,
  script: string | null = null,
): Promise<boolean> {
  if (!project || !fileId) return true
  try {
    await putAiPending(rid, fileId, text, sent, project, script)
    return true
  } catch {
    return false
  }
}

/** 本项目全部未落定的提交（跨面板，带 script）；读失败回空。发起它的面板已被删 / 换图时靠它找回 */
export async function loadProjectPendings(
  project: string | null,
): Promise<{ file: string; rid: string; text: string; sent: DraftTarget; script: string | null }[]> {
  if (!project) return []
  try {
    return (await fetchAiPendings(project)).pendings.map((p) => ({ ...p, sent: p.sent as DraftTarget }))
  } catch {
    return []
  }
}

/** 后端明确拒绝：请求 id 作废，只清自己那条账本；文本留着让用户改 */
export async function clearSubmitted(project: string | null, fileId: string | null, rid: string): Promise<void> {
  if (!project || !fileId) return
  try {
    await clearAiPending(rid, fileId, project)
  } catch {
    /* 同上 */
  }
}

/** **只在后端给出会话 id（请求已被接受）之后**调用：清这条账本；草稿文本仍等于提交时的原文才一并删 */
export async function removeAccepted(project: string | null, fileId: string | null, rid: string): Promise<void> {
  if (!project || !fileId) return
  try {
    await acceptAiPending(rid, fileId, project)
  } catch {
    /* 同上 */
  }
}
