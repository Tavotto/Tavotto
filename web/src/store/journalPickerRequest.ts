/**
 * 「请把期刊选择器打开」的一次性请求：助手回答里的「选期刊」按钮发它，问题页签顶部那一行的 `JournalPicker` 接住并展开。
 * 选择器本身只在左栏「问题」页签里才挂载，所以请求要能等到它出现再兑现（挂载时看一眼 `pending`）。
 */
import { create } from 'zustand'
import { useUiStore } from './uiStore'

interface JournalPickerRequestState {
  pending: boolean
  consume: () => boolean
}

export const useJournalPickerRequest = create<JournalPickerRequestState>((set, get) => ({
  pending: false,
  consume: () => {
    const was = get().pending
    if (was) set({ pending: false })
    return was
  },
}))

/** 切到左栏「问题」页签并展开期刊选择器 */
export function openJournalPicker(): void {
  useUiStore.getState().setLeftTab('problems')
  useJournalPickerRequest.setState({ pending: true })
}
