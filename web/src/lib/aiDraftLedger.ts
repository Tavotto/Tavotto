import { setAiSubmissionLedger } from '@/store/aiStore'
import { clearSubmitted, markSubmitted, removeAccepted } from './aiDraft'

/**
 * 把草稿存储注册成 aiStore 的提交账本（ADR 0131）。**只被助手面板 import**——有助手面板的宿主
 * （桌面 / 浏览器模式）才会带上它；Codex 内嵌画布与 playground 的包里没有这一行，
 * aiStore 在那里的账本是空的（也没人会调 `start`）。
 */
setAiSubmissionLedger({ markSubmitted, clearSubmitted, removeAccepted })
