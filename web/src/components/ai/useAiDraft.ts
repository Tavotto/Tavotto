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
/** 仅测试：让「首次挂载清扫」能在同一个模块实例里再发生一次 */
export const __resetDraftSweepForTests = () => {
  swept = false
}

/**
 * 助手输入框的草稿（ADR 0132）：绑定 **项目 × 面板文件**，切目标面板时存旧的、读新的。
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
      // 只清过期 / 损坏 / 超量的。**不按「目标面板还在不在」清**：挂载那一刻文档往往还没恢复完（刷新后
      // 面板列表是稍后才到的），那时「面板不在」是假象，按它清会在每次启动时把草稿全删掉
      // （e2e/ai-draft.spec.ts 刷新那一步量到的）。恢复本身只发生在当前目标面板上，所以「目标已删」
      // 的草稿读不到、也不会显示；留下的死稿由 30 天过期与 LRU 收走。
      sweepDrafts()
    }
    const d = loadDraft(project, fileId)
    latest.current = { project, fileId, text: d?.text ?? '', dirty: false }
    setPromptState(d?.text ?? '')
    setRestored(!!d && d.text !== '')
    setTooLarge(false)
    if (panel) {
      // 刷新 / 重挂之前发出去、后端还没确认的每一条（两个标签页同时发时不止一条）：各自还原成「已发出未确认」
      for (const p of d?.pendings ?? []) {
        useAiStore.getState().restoreSubmission({
          rid: p.rid,
          project,
          fileId: panel.fileId,
          panelId: panel.id,
          prompt: p.text,
          target: p.sent,
          canvas: useDocumentStore.getState().activeCanvasId,
          overrides: panel.overrides,
        })
      }
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
