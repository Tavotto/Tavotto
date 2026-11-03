import { describe, expect, it } from 'vitest'
import { modelBrandKey } from './modelVendor'

describe('modelBrandKey', () => {
  it.each([
    ['deepseek-v4-pro', 'deepseek'],
    ['DeepSeek-Chat', 'deepseek'],
    ['claude-sonnet-4-5', 'claude'],
    ['opus', 'claude'],
    ['gpt-5.1-codex', 'openai'],
    ['o3', 'openai'],
    ['o4-mini', 'openai'],
    ['gemini-2.5-pro', 'gemini'],
    ['kimi-k2', 'kimi'],
    ['moonshot-v1-8k', 'kimi'],
    ['glm-4.6', 'zhipu'],
    ['qwen3-coder', 'qwen'],
    ['MiniMax-M2', 'minimax'],
    ['abab6.5s', 'minimax'],
    ['deepseek/deepseek-chat', 'deepseek'],
    ['openrouter/anthropic/claude-3-opus', 'claude'],
  ])('%s -> %s', (model, key) => expect(modelBrandKey(model)).toBe(key))

  it.each([null, undefined, '', '  ', 'custom-llm-7', 'opusx', 'o3x', 'organizer-1'])('%s -> null', (model) =>
    expect(modelBrandKey(model)).toBeNull(),
  )
})
