import { useCallback, useEffect, useRef, useState } from 'react'
import '@/lib/aiDraftLedger'
import { isDraftTooLarge, loadDraft, saveDraft, sweepDrafts } from '@/lib/aiDraft'
import { currentProjectId } from '@/lib/session'
import { useAiStore } from '@/store/aiStore'
import { useDocumentStore } from '@/store/documentStore'
import type { PanelObject } from '@/types/document'

/** 打字停多久才写盘：每个键都写 localStorage 没必要，丢的也只是最后半秒 */
const WRITE_DEBOUNCE_MS = 400

let swept = false

/**
 * 助手输入框的草稿（ADR 0131）：绑定 **项目 × 面板文件**，切目标面板时存旧的、读新的。
 *
 * 为什么要持久化：右栏切页签 / 收起会把 `AssistantPanel` 整个卸载（`Inspector` 条件挂载），
 * 刷新、关窗更不用说。落盘是缓存（`lib/aiDraft.ts` 头注释：桌面端口变化会丢，已知降级）。
 *
 * 写：防抖 400 ms；`pagehide` / 页面转入后台 / 组件卸载 / 切目标时立刻 flush。
 * 删：**不在这里**——只有后端给出会话 id（请求被接受）之后，store 才删（`aiStore.accept`）。
 * 这里只负责「文本变了就存」，清空输入框（`clear`）只在调用方确认已被接受之后调用。
 */
export function useAiDraft(panel: PanelObject | null) {
  const fileId = panel?.fileId ?? null
  const [prompt, setPromptState] = useState('')
  const [restored, setRestored] = useState(false)
  const [tooLarge, setTooLarge] = useState(false)
  const latest = useRef<{ project: string | null; fileId: string | null; text: string; dirty: boolean }>({
    project: null,
    fileId: null,
    text: '',
    dirty: false,
  })
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  const flush = useCallback(() => {
    clearTimeout(timer.current)
    timer.current = undefined
    const cur = latest.current
    if (!cur.dirty) return
    cur.dirty = false
    saveDraft(cur.project, cur.fileId, cur.text)
  }, [])

  const setPrompt = useCallback(
    (next: string | ((prev: string) => string)) => {
      const text = typeof next === 'function' ? next(latest.current.text) : next
      latest.current.text = text
      latest.current.dirty = true
      setPromptState(text)
      setRestored(false)
      setTooLarge(isDraftTooLarge(text))
      clearTimeout(timer.current)
      timer.current = setTimeout(flush, WRITE_DEBOUNCE_MS)
    },
    [flush],
  )

  // 目标（项目 × 面板文件）变了：旧的在 cleanup 里 flush，新的读进来
  useEffect(() => {
    const project = currentProjectId()
    if (!swept) {
      swept = true
      // 过期 / 超量 / 目标面板已不存在的草稿；有待确认请求的不因面板消失而删（见 sweepDrafts）
      sweepDrafts({
        exists: (pj, f) =>
          pj !== project || useDocumentStore.getState().doc.objects.some((o) => o.type === 'panel' && (o as PanelObject).fileId === f),
      })
    }
    const d = loadDraft(project, fileId)
    latest.current = { project, fileId, text: d?.text ?? '', dirty: false }
    setPromptState(d?.text ?? '')
    setRestored(!!d && d.text !== '')
    setTooLarge(false)
    if (d?.rid && d.sent && panel) {
      // 刷新 / 重挂之前发出去、后端还没确认的那一条：还原成「已发出未确认」，不让它变回一条可以普通重发的草稿
      useAiStore.getState().restoreSubmission({
        rid: d.rid,
        project,
        fileId: d.fileId,
        panelId: panel.id,
        prompt: d.text,
        target: d.sent,
        canvas: useDocumentStore.getState().activeCanvasId,
        overrides: panel.overrides,
      })
    }
    return flush
    // panel 的其余字段（overrides 等）变化不该触发重读草稿
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fileId, flush])

  useEffect(() => {
    const onHide = () => {
      if (document.visibilityState === 'hidden') flush()
    }
    window.addEventListener('pagehide', flush)
    document.addEventListener('visibilitychange', onHide)
    return () => {
      window.removeEventListener('pagehide', flush)
      document.removeEventListener('visibilitychange', onHide)
    }
  }, [flush])

  /** 确认已被后端接受之后清空输入框（草稿文件由 store 删）。文本已被改过就不动——那是新的草稿 */
  const clearIf = useCallback((sentText: string) => {
    if (latest.current.text !== sentText) return
    latest.current = { ...latest.current, text: '', dirty: false }
    clearTimeout(timer.current)
    setPromptState('')
    setTooLarge(false)
  }, [])

  return { prompt, setPrompt, restored, tooLarge, clearIf }
}
