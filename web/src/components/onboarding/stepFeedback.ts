import { createContext, useContext } from 'react'

/**
 * 教程每一步对外壳说话的三个口（外壳 `TutorialSteps` 提供，各步消费）。单独成文件：
 * 步骤组件与外壳互相 import 会成环（`importArchitecture.test`）。
 */
export interface StepFeedback {
  /** 一句可见的提示（拖错地方 / 挪得太少）；成功那句由页脚按完成状态自己说 */
  notice: (text: string | null) => void
  /** 只给读屏的播报（拿起 / 移动 / 放下） */
  announce: (text: string) => void
  /** 任务完成后把焦点交给「下一步」 */
  focusNext: () => void
}

export const StepFeedbackContext = createContext<StepFeedback>({
  notice: () => {},
  announce: () => {},
  focusNext: () => {},
})

export const useStepFeedback = () => useContext(StepFeedbackContext)
