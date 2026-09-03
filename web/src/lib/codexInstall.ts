/**
 * 「安装 Codex 集成」按钮的**结果层**（ADR 0012 / issue #170）。
 *
 * ## 这里没有安装逻辑，一行都没有
 *
 * 安装器只有 `src/tavotto/engine/codexinstall.py` 那一份。按钮 spawn
 * `tavotto-cli codex install --json`，这个模块只做三件事：解析那一行 JSON、
 * 把 `step` 名翻成人话、把 `error_code` 翻成人话。marketplace、插件引用、
 * sparse 路径这些**安装步骤的字面量在这里出现一次都算第二权威**
 * （看护 `tests/test_desktop_codex_button.py`）。
 *
 * ## 不透传英文 code
 *
 * 与 `unsupported_props`（#76）同一条纪律：`codex_cli_missing` 这种字符串是
 * **分诊用的稳定身份**，不是给人读的句子。界面上必须是翻译过的原因；code 只在
 * 诊断区（`detail` 原文）旁边作为材料存在。code 认不出来时回退到 `other`——
 * 老界面 + 新引擎不该把一串下划线甩给用户。
 */
import { t } from '@/i18n'

/** 引擎每一步的结论（`engine/codexinstall.py` 的 `_step()` 逐字段对应） */
export interface CodexStep {
  step: string
  ok: boolean
  skipped: boolean
  detail?: string
  error_code?: string
}

/** `tavotto codex <action> --json` 的一行输出 */
export interface CodexResult {
  ok: boolean
  action: string
  steps: CodexStep[]
  error_code?: string
  error?: string
}

const ci = (key: string, values?: Record<string, unknown>) =>
  t(`settings.agents.codexInstall.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/** 壳侧失败（连那行 JSON 都没拿到）——带稳定 code，不带英文句子 */
export class CodexShellError extends Error {
  readonly code: string

  constructor(code: string) {
    super(code)
    this.name = 'CodexShellError'
    this.code = code
  }
}

/**
 * 解析 CLI 那一行。**形状不对就是没拿到**（`ok`/`steps` 必须在）——
 * 「成功地拿到一个空对象」会让界面显示成「装好了，零个步骤」，那比报错更坏。
 */
export function parseCodexResult(raw: string): CodexResult {
  let data: unknown
  try {
    data = JSON.parse(raw)
  } catch {
    throw new CodexShellError('bad_output')
  }
  const o = data as Partial<CodexResult>
  if (typeof o?.ok !== 'boolean' || !Array.isArray(o.steps)) {
    throw new CodexShellError('bad_output')
  }
  return {
    ok: o.ok,
    action: typeof o.action === 'string' ? o.action : '',
    steps: o.steps.map((s) => ({
      step: String((s as CodexStep)?.step ?? ''),
      ok: (s as CodexStep)?.ok === true,
      skipped: (s as CodexStep)?.skipped === true,
      detail: (s as CodexStep)?.detail,
      error_code: (s as CodexStep)?.error_code,
    })),
    error_code: typeof o.error_code === 'string' ? o.error_code : undefined,
    error: typeof o.error === 'string' ? o.error : undefined,
  }
}

/**
 * 步骤名 → 人话。
 *
 * **步骤名的清单只在文案表里**，前端不另存一份数组——那份数组会与引擎的
 * `_step()` 漂移，而漂移的表现是「界面上多出一个永远显示不出名字的步骤」。
 * i18next 查不到时原样回 key，据此回退成引擎给的原名（诊断材料，不翻），
 * 而不是把 `settings.agents.…` 甩到界面上。齐全性由
 * `tests/test_desktop_codex_button.py` 从引擎那边枚举着比。
 */
export function codexStepLabel(step: string): string {
  const key = `settings.agents.codexInstall.step.${step}`
  const text = t(key, { ns: 'dialogs' })
  return text === key ? step : text
}

/** 一步的状态词：跳过 / 完成 / 失败。**「跳过」是独立一档**，不是「完成」的同义词 */
export function codexStepStateText(s: CodexStep): string {
  if (!s.ok) return ci('stepState.failed')
  return s.skipped ? ci('stepState.skipped') : ci('stepState.done')
}

/**
 * 失败原因的**成文**。界面上显示的就是这一句，绝不显示 `code` 本身。
 *
 * `codex_cli_missing` 那句要把「找过哪些位置」带出来——只说「找不到 codex」
 * 对一个把它装在别处的用户什么忙都帮不上（与引擎 `find_codex()` 的注释同一条
 * 理由）。那串位置在引擎的 `detail` 里，是路径，**不翻**。
 */
export function codexErrorText(code: string | undefined, detail?: string): string {
  // **认得的 code 清单也只在文案表里**：另存一个 Set 只会与引擎的 `ERR_*` 漂移，
  // 而漂移的表现正是这条纪律要挡的那个——界面上冒出一串下划线英文。
  // 查不到（老界面 + 新引擎，或壳回了别的字符串）一律落到 `other`。
  const key = code ? `settings.agents.codexInstall.error.${code}` : ''
  const hit = key ? t(key, { ns: 'dialogs' }) : ''
  if (!hit || hit === key) return ci('error.other')
  if (code === 'codex_cli_missing' && detail) return `${hit} ${detail}`
  return hit
}

/** 整个结果的失败成文：优先用顶层 `error_code`，回退到第一条失败的步骤 */
export function codexResultErrorText(r: CodexResult): string {
  const failed = r.steps.find((s) => !s.ok)
  return codexErrorText(r.error_code ?? failed?.error_code, r.error ?? failed?.detail)
}
