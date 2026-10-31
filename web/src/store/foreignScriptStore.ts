/**
 * 「这是 R / MATLAB 脚本，要不要另写一份 Matplotlib 版」的入口卡状态（ADR 0143 修订）。
 *
 * 主页拖入 / 选择的是 `.R` / `.r` / `.m` 时，项目（脚本所在的文件夹）照常打开，然后在这里记一条“待提议”：
 * 画布右下的一张卡（`PreparationCard` 里的 `ForeignScriptCard`）说一句话 + 一个主按钮「生成可编辑版本」。
 * 这里只持有**提议**；生成流水（运行中 / 对照 / 采用 / 失败）归 `convertStore`，原脚本从不被改写。
 * 换项目、点 × 都清掉提议（它绑定的是某个项目里的某个文件）。
 */
import { create } from 'zustand'
import type { ForeignLanguage } from '@/lib/foreignScript'
import { onCurrentProjectChange } from '@/lib/session'

export interface ForeignOffer {
  /** 脚本（项目相对路径，即文件名） */
  script: string
  language: ForeignLanguage
}

interface ForeignScriptState {
  offer: ForeignOffer | null
  setOffer: (offer: ForeignOffer) => void
  clear: () => void
}

export const useForeignScriptStore = create<ForeignScriptState>((set) => ({
  offer: null,
  setOffer: (offer) => set({ offer }),
  clear: () => set({ offer: null }),
}))

onCurrentProjectChange(() => useForeignScriptStore.setState({ offer: null }))
