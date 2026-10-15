import { useCallback, useEffect, useRef, useState } from 'react'
import '@/lib/aiDraftLedger'
import { isDraftTooLarge, loadDraft, saveDraft } from '@/lib/aiDraft'
import { currentProjectId, onCurrentProjectChange } from '@/lib/session'
import { useAiStore } from '@/store/aiStore'
import { useDocumentStore } from '@/store/documentStore'
import type { PanelObject } from '@/types/document'

const scriptBase = (p: string) => p.split(/[\\/]/).pop() ?? p

/** 打字停多久才写后端：每个键都发请求没必要，丢的也只是最后半秒（pagehide 会冲刷） */
export const WRITE_DEBOUNCE_MS = 600
/** 写失败（离线 / 后端暂时不可用）后多久重试：文本一直留在内存里，不丢 */
export const RETRY_MS = 3000

/**
 * 助手输入框的草稿（ADR 0132）：绑定 **项目 × 面板文件**，**存后端**（不碰 localStorage）。
 *
 * 为什么要持久化：右栏切页签 / 收起会把 `AssistantPanel` 整个卸载（`Inspector` 条件挂载），刷新、关窗、
 * 桌面端口变化（换 origin）更不用说——这些都不该让写了一半的指令消失。
 *
 * 写：防抖 600 ms；`pagehide` / 页面转入后台 / 组件卸载 / 切目标时用 `keepalive` 立刻冲刷；失败（`unavailable`）
 * 时文本留在内存、3 秒后重试，直到写成或用户继续改。
 * 读：挂载 / 换目标时读一次；**窗口重新获得焦点 / 页面回到前台时再读一次**（多标签页以后端为准）——
 * 但本地有没写出去的改动（dirty）时不覆盖，最后写入为准。
 * 删：**不在这里**——只有后端给出会话 id（请求被接受）之后，store 经账本 `accept` 条件删除。
 * 这里只负责「文本变了就存」，清空输入框（`clearIf`）只在调用方确认已被接受之后调用。
 */
export function useAiDraft(panel: PanelObject | null) {
  const fileId = panel?.fileId ?? null
  // 项目认领是异步到的（刷新后挂载那一刻可能还是 null）：跟着它走，到了就读
  const [project, setProject] = useState(currentProjectId())
  useEffect(() => onCurrentProjectChange(setProject), [])

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
  const retry = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const loadToken = useRef(0)

  /** 把当前文本写到后端。`keepalive`：页面正在关 */
  const persist = useCallback((keepalive: boolean) => {
    clearTimeout(timer.current)
    timer.current = undefined
    const cur = latest.current
    if (!cur.dirty) return
    cur.dirty = false
    const { project: pj, fileId: f, text } = cur
    void saveDraft(pj, f, text, keepalive).then((r) => {
      if (r === 'too_large') {
        setTooLarge(true)
        return
      }
      if (r !== 'unavailable') return
      // 没写成：文本还在内存里。只有这一版仍是最新、且目标没换才重试（用户又改了就走新的防抖）
      const now = latest.current
      if (now.project !== pj || now.fileId !== f || now.text !== text) return
      now.dirty = true
      clearTimeout(retry.current)
      retry.current = setTimeout(() => persist(false), RETRY_MS)
    })
  }, [])

  const setPrompt = useCallback(
    (next: string | ((prev: string) => string)) => {
      const text = typeof next === 'function' ? next(latest.current.text) : next
      latest.current.text = text
      latest.current.dirty = true
      setPromptState(text)
      setRestored(false)
      setTooLarge(isDraftTooLarge(text))
      clearTimeout(retry.current)
      clearTimeout(timer.current)
      timer.current = setTimeout(() => persist(false), WRITE_DEBOUNCE_MS)
    },
    [persist],
  )

  // 目标（项目 × 面板文件）变了：旧的在 cleanup 里冲刷，新的从后端读
  useEffect(() => {
    latest.current = { project, fileId, text: '', dirty: false }
    setPromptState('')
    setRestored(false)
    setTooLarge(false)
    if (!project || !fileId) return
    const token = ++loadToken.current
    void loadDraft(project, fileId).then((d) => {
      if (token !== loadToken.current || !d) return
      // 读的途中用户已经开始打字：以用户这一版为准（最后写入为准），不把读回来的盖上去
      if (!latest.current.dirty) {
        latest.current.text = d.text
        setPromptState(d.text)
        setRestored(d.text !== '')
      }
      if (panel) {
        // 刷新 / 换端口 / 重挂之前发出去、后端还没确认的每一条（两个标签页同时发时不止一条）：
        // 各自还原成「已发出未确认」
        for (const p of d.pendings ?? []) {
          useAiStore.getState().restoreSubmission({
            rid: p.rid,
            project,
            fileId: panel.fileId,
            panelId: panel.id,
            script: panel.script ?? null,
            prompt: p.text,
            target: p.sent,
            canvas: useDocumentStore.getState().activeCanvasId,
            overrides: panel.overrides,
          })
        }
      }
    })
    if (panel?.script) {
      // 同一份脚本的**别的面板**上没落定的提交同样要还原：发送锁按脚本判（不然刷新后切到兄弟面板就绕过了）。
      // 只取账本，不动那边的草稿文本
      const mine = scriptBase(panel.script)
      const siblings = useDocumentStore
        .getState()
        .doc.objects.filter(
          (o): o is PanelObject => o.type === 'panel' && !!(o as PanelObject).script && (o as PanelObject).fileId !== panel.fileId && scriptBase((o as PanelObject).script as string) === mine,
        )
      const seen = new Set<string>()
      for (const sib of siblings) {
        if (seen.has(sib.fileId)) continue
        seen.add(sib.fileId)
        void loadDraft(project, sib.fileId).then((d) => {
          if (token !== loadToken.current) return
          for (const p of d?.pendings ?? []) {
            useAiStore.getState().restoreSubmission({
              rid: p.rid,
              project,
              fileId: sib.fileId,
              panelId: sib.id,
              script: sib.script ?? null,
              prompt: p.text,
              target: p.sent,
              canvas: useDocumentStore.getState().activeCanvasId,
              overrides: sib.overrides,
            })
          }
        })
      }
    }
    return () => {
      loadToken.current++
      persist(true)
      clearTimeout(retry.current)
    }
    // panel 的其余字段（overrides 等）变化不该触发重读草稿
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project, fileId, persist])

  // 页面离开 / 转入后台：keepalive 冲刷；回到前台 / 重新获得焦点：从后端再读一次（多标签页以后端为准）
  useEffect(() => {
    const onHide = () => {
      if (document.visibilityState === 'hidden') persist(true)
    }
    const pull = () => {
      if (document.visibilityState === 'hidden') return
      const cur = latest.current
      if (cur.dirty || !cur.project || !cur.fileId) return
      const token = ++loadToken.current
      void loadDraft(cur.project, cur.fileId).then((d) => {
        if (token !== loadToken.current || latest.current.dirty) return
        const text = d?.text ?? ''
        // 已有未落定的提交时文本是冻结的，不被另一个标签页的新稿换掉
        if (d?.pendings?.length || text === latest.current.text) return
        latest.current.text = text
        setPromptState(text)
        setRestored(false)
      })
    }
    const flush = () => persist(true)
    window.addEventListener('pagehide', flush)
    window.addEventListener('focus', pull)
    document.addEventListener('visibilitychange', onHide)
    document.addEventListener('visibilitychange', pull)
    return () => {
      window.removeEventListener('pagehide', flush)
      window.removeEventListener('focus', pull)
      document.removeEventListener('visibilitychange', onHide)
      document.removeEventListener('visibilitychange', pull)
    }
  }, [persist])

  /** 确认已被后端接受之后清空输入框（草稿由 store 经账本条件删除）。文本已被改过就不动——那是新的草稿 */
  const clearIf = useCallback((sentText: string) => {
    if (latest.current.text !== sentText) return
    latest.current = { ...latest.current, text: '', dirty: false }
    clearTimeout(timer.current)
    clearTimeout(retry.current)
    setPromptState('')
    setTooLarge(false)
  }, [])

  return { prompt, setPrompt, restored, tooLarge, clearIf, project }
}
