import { msg } from '@/i18n'
import { askConfirm } from '@/store/uiStore'

/**
 * 「没存的草稿要被丢掉了」那一问（设计审计 2026-10-07 §9.1 / §10.2）。
 *
 * 设置 › 样式 / 规范页切换库里的另一份、切分区、关设置，以及论文样式对话框切样式、
 * 关对话框，都会整份换掉编辑草稿——此前一声不响。这里只问一句、走全局确认框
 * （`askConfirm`）：取消 = 继续编辑（什么都不动），确认 = 放弃修改（danger）。
 * 保存的出口就在各自页面上（主按钮），不在这一问里另开第三条路。
 */
export function askDiscardDraft(): Promise<boolean> {
  return askConfirm({
    title: msg('draftGuard.title', undefined, 'dialogs'),
    body: msg('draftGuard.body', undefined, 'dialogs'),
    cancelLabel: msg('draftGuard.keepEditing', undefined, 'dialogs'),
    confirmLabel: msg('draftGuard.discard', undefined, 'dialogs'),
    danger: true,
  })
}
