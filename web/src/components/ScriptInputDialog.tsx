import { useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { useScriptInputStore } from '@/store/scriptInputStore'
import {
  ScriptInputFields,
  scriptInputFooterSlots,
  useScriptInputAnswer,
} from './ScriptInputForm'
import { Dialog } from './ui/Dialog'

const si = (key: string, values?: Record<string, unknown>) =>
  translate(`scriptInput.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/**
 * 脚本里的 `input()` 在等作答（ADR 0099 §六）。内容在 `ScriptInputForm`，与准备面板共用。
 *
 * - **一次只问一个**：显示队首；答完这一问就关，下一问来了再打开（同一个框换了内容）。
 * - **同一问只有一个展示面**（T08）：别的展示面（准备面板）认领了展示时这里不出现；它放手（面板关掉）时
 *   这里接着显示**同一问**——关面板只是换展示，不取消脚本、不丢掉唯一的答题入口。
 * - **不许随手关**（`blockDismiss`）：关掉而不答，脚本会白等 10 分钟。出口只有三个——提交 / 结束输入（EOF）/
 *   停止脚本（硬杀会话）。
 * - 常驻挂载：答完的那 90ms 里队列已空，正文按最后一问画（Dialog 的常驻写法）；打开时焦点直接在答案框
 *   （`initialFocusRef`，2026-10-07 设计审计 §10.2）。
 */
export function ScriptInputDialog() {
  useTranslation('dialogs')
  const answer = useScriptInputAnswer()
  const elsewhere = useScriptInputStore((s) => s.presenters.length > 0)
  const answerRef = useRef<HTMLInputElement>(null)
  const last = useRef(answer.head)
  if (answer.head) last.current = answer.head
  const head = last.current
  if (!head) return null
  const shown = { ...answer, head }

  return (
    <Dialog
      open={!!answer.head && !elsewhere}
      onOpenChange={() => {}}
      // 闸：Esc / 点外面都不算回答（出口只有提交 / 结束输入 / 停止脚本），所以不给 onEscape
      blockDismiss
      busy={answer.busy}
      anchor="script-input"
      size="lg"
      title={si('title')}
      description={si('question', { index: head.index, script: head.script })}
      initialFocusRef={answerRef}
      footer={scriptInputFooterSlots(shown)}
    >
      <ScriptInputFields answer={shown} answerRef={answerRef} />
    </Dialog>
  )
}
