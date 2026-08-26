/** 两个测试文件共用的 capabilities 造型器（真实形状，字段一个不少）。 */
import type { AiAgentCaps, AiCapabilities } from '@/lib/api'

export const agentCaps = (over: Partial<AiAgentCaps> = {}): AiAgentCaps => ({
  id: 'codex',
  display_name: 'Codex',
  icon_key: 'codex',
  state: 'ready',
  installed: true,
  enabled: true,
  usable: true,
  version: 'codex-cli 1.2.3',
  executable_path: '/opt/homebrew/bin/codex',
  path_override: null,
  detection_source: 'path',
  models: ['gpt-5.6-luna'],
  default_model: 'gpt-5.6-luna',
  efforts: ['low', 'medium', 'high'],
  default_effort: 'medium',
  endpoint: null,
  active_endpoint_id: null,
  features: {
    third_party_endpoints: true,
    model_selection: true,
    effort_selection: true,
    wire_api_selection: true,
    readiness_probe: true,
  },
  diagnostics: {
    searched: ['/opt/homebrew/bin', '/usr/local/bin'],
    broken_path: null,
    readiness: 'ready',
    readiness_detail: 'cli_reports_signed_in',
  },
  install: { method: 'npm', package: '@openai/codex', available: true, status: 'idle' },
  ...over,
})

export const claudeCaps = (over: Partial<AiAgentCaps> = {}): AiAgentCaps =>
  agentCaps({
    id: 'claude',
    display_name: 'Claude Code',
    icon_key: 'claude',
    version: 'claude 2.0.0',
    executable_path: '/usr/local/bin/claude',
    models: ['sonnet', 'opus'],
    default_model: 'sonnet',
    efforts: [],
    default_effort: null,
    features: {
      third_party_endpoints: true,
      model_selection: true,
      effort_selection: false,
      wire_api_selection: false,
      readiness_probe: true,
    },
    install: {
      method: 'npm',
      package: '@anthropic-ai/claude-code',
      available: true,
      status: 'idle',
    },
    ...over,
  })

export const capsOf = (
  agents: AiAgentCaps[],
  over: Partial<AiCapabilities> = {},
): AiCapabilities => ({
  agents,
  endpoints: [],
  presets: [],
  checked_at_ms: 1_756_000_000_000,
  ...over,
})
