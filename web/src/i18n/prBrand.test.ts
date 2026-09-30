/**
 * 界面文案不得手写产品名（仓库不变量：品牌唯一出处 `web/src/lib/brand.ts`，文案里写 `{{product}}`，调用处传
 * `PRODUCT_NAME`）。仓库里没有扫 errors.json / workspace.json 的品牌门禁——存量文案里还有几十处手写的「Tavotto」
 * （如 `backend.native_*` 的「Tavotto Run」），一刀切的全量判据今天就是红的。所以这里先钉住**一键修复（#742）
 * 新增或改过的键**：渲染之前的原始字符串里一个「Tavotto」都不许有（Codex #742 P1）。
 *
 * 键的清单按 #742 对 0ceca0b74 的 diff 列出；`repairErrorShort.*` 整组按前缀取（码随 `repairError.*` 增减）。
 */
import { describe, expect, it } from 'vitest'
import { resources } from '@/i18n'

const PR_KEYS: Record<'errors' | 'workspace', string[]> = {
  errors: [
    'engine.dependencyPreparePrivatePythonBundled',
    'engine.dependencyTargetHint_project_venv',
    'engine.dependencyTargetHint_tavotto_managed',
    'engine.dependencyTarget_tavotto_managed',
    'engine.oneClickChecking',
    'engine.oneClickRepair',
    'engine.oneClickSentence',
    'engine.oneClickSentenceDownload',
    'engine.oneClickSentenceEnv',
    'engine.oneClickSentenceEnvDownload',
    'engine.oneClickSentenceSystem',
    'engine.repairAdvanced',
    'engine.repairCancelledManaged',
    'engine.repairCancelledProjectEnv',
    'engine.repairDetails',
    'engine.repairDownloadAria',
    'engine.repairDownloadBytes',
    'engine.repairDownloadUnpacking',
    'engine.repairErrorCode',
    'engine.repairErrorShortGeneric',
    'engine.repairFactBundled',
    'engine.repairFactCached',
    'engine.repairFactDownload',
    'engine.repairFactEnvBundled',
    'engine.repairFactEnvCached',
    'engine.repairFactEnvDownload',
    'engine.repairFactNetwork',
    'engine.repairFactUntouched',
    'engine.repairManagedUnavailable',
    'engine.repairManagedUnavailableHint',
    'engine.repairPypiMirror',
    'engine.repairPythonLaunching',
    'engine.repairPythonPreparing',
    'engine.repairPythonVerifying',
    'engine.repairStage_env',
    'engine.repairStage_packages',
    'engine.repairStage_python',
    'engine.repairStage_rerun',
    'engine.repairStep',
    'engine.userEnvBody',
  ],
  workspace: ['scripts.group_needsFix', 'scripts.recoveryDetails'],
}
const PR_PREFIXES: Record<'errors' | 'workspace', string[]> = { errors: ['engine.repairErrorShort.'], workspace: [] }

type Tree = { [k: string]: string | Tree }
function leaves(tree: Tree, prefix = ''): [string, string][] {
  return Object.entries(tree).flatMap(([k, v]) => {
    const path = prefix ? `${prefix}.${k}` : k
    return typeof v === 'string' ? [[path, v] as [string, string]] : leaves(v, path)
  })
}

describe.each(Object.keys(resources))('#742 碰过的文案不手写产品名（%s）', (lang) => {
  const bundle = (resources as unknown as Record<string, Record<string, Tree>>)[lang]
  it.each(['errors', 'workspace'] as const)('%s', (ns) => {
    const all = new Map(leaves(bundle[ns]))
    const picked = [
      ...PR_KEYS[ns],
      ...[...all.keys()].filter((k) => PR_PREFIXES[ns].some((p) => k.startsWith(p))),
    ]
    // 清单本身不能是空的 / 指向不存在的键（否则这条判据恒绿）
    expect(PR_PREFIXES[ns].every((p) => [...all.keys()].some((k) => k.startsWith(p)))).toBe(true)
    for (const key of picked) expect(all.has(key), `${lang} ${ns}:${key} 不存在`).toBe(true)
    const offenders = picked.filter((k) => /tavotto/i.test(all.get(k) ?? ''))
    expect(offenders, '这些键手写了产品名，改成 {{product}}').toEqual([])
  })
})
