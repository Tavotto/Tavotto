/**
 * 可延迟翻译的用户可见文本描述符。
 *
 * 为什么不直接存翻译好的字符串：撤销栈、状态 toast、确认框这些东西**活得比
 * 一次渲染长**。存成 "删除 3 个对象" 之后用户切到英文，历史面板里还是中文，
 * 而且再也换不回来了（原始参数已经被拼进字符串）。存 key + 参数，显示时
 * 现翻，切语言整条历史跟着变。
 *
 * 这是**运行时状态**，绝不写进 .tavotto 文档——文档 schema 一个字节不动。
 */
import type { Namespace } from './index'

export interface UiMessage {
  key: string
  ns?: Namespace
  values?: Record<string, unknown>
}

/** 简写：`msg('history.deleteObjects', { count: 3 })`（默认 common 命名空间）。 */
export function msg(
  key: string,
  values?: Record<string, unknown>,
  ns?: Namespace,
): UiMessage {
  return values || ns ? { key, ...(ns ? { ns } : {}), ...(values ? { values } : {}) } : { key }
}

/** 命名空间版简写：`nsMsg('workspace', 'topbar.export')`。 */
export function nsMsg(
  ns: Namespace,
  key: string,
  values?: Record<string, unknown>,
): UiMessage {
  return msg(key, values, ns)
}

/**
 * 用户自己的内容（项目名、文件名、画布名……）——**不翻译**，原样透出。
 * 包成描述符只是为了让「历史条目一律是描述符」这条不变式成立。
 */
export function literal(text: string): UiMessage {
  return { key: 'literal', ns: 'common', values: { text } }
}

/** 判断一个值是不是描述符（读老的运行时状态时用）。 */
export function isUiMessage(v: unknown): v is UiMessage {
  return typeof v === 'object' && v !== null && typeof (v as UiMessage).key === 'string'
}
