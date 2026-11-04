import { expect, test } from "bun:test"

import type { ProviderResponse } from "../src/client/types.gen"
import {
  researchModelOptions,
  researchModelSelection,
  researchPreferencesSelection,
} from "../src/lib/researchModels"

const providers = [
  {
    id: "anthropic",
    name: "Lab",
    type: "anthropic",
    model: "Sonnet; Opus; Sonnet",
    api_key: "secret",
  },
  {
    id: "openai",
    name: "Gateway",
    type: "openai_compatible",
    model: "GPT",
    auth_token: "secret",
  },
  { id: "embedding", name: "Other", type: "ollama", model: "embed" },
] as ProviderResponse[]
const current = {
  provider_id: "anthropic",
  model_name: "Sonnet",
  context_window_tokens: 128000,
  max_output_tokens: 16000,
}

test("embedded model choices contain only supported model display metadata", () => {
  const options = researchModelOptions(providers)
  expect(options).toHaveLength(3)
  expect(options[2]).toEqual({
    provider_id: "openai",
    provider_name: "Gateway",
    model_name: "GPT",
  })
  expect(JSON.stringify(options)).not.toContain("secret")
})

test("conversation preference messages only carry supported presentation settings", () => {
  expect(
    researchPreferencesSelection({
      reply_language: "zh",
      additional_guidance: " Explain clearly ",
    }),
  ).toEqual({
    reply_language: "zh",
    additional_guidance: "Explain clearly",
  })
  expect(
    researchPreferencesSelection({
      reply_language: ["zh"],
      additional_guidance: "",
    }),
  ).toBeNull()
  expect(
    researchPreferencesSelection({
      reply_language: "en",
      additional_guidance: 12,
    }),
  ).toBeNull()
})

test("switching providers preserves the configured context and output settings", () => {
  expect(
    researchModelSelection(
      { ...current, provider_id: "openai", model_name: "GPT" },
      providers,
      current,
    ),
  ).toEqual({
    ...current,
    provider_id: "openai",
    model_name: "GPT",
  })
})

test("unlisted providers, invented models and invalid token settings are rejected", () => {
  for (const patch of [
    { provider_id: "not-owned" },
    { model_name: "invented" },
    { context_window_tokens: "128000" },
    { context_window_tokens: 4096 },
    { max_output_tokens: 500 },
    { max_output_tokens: 128001 },
  ]) {
    expect(
      researchModelSelection({ ...current, ...patch }, providers, current),
    ).toBeNull()
  }
})

test("context settings can retain an existing custom model without exposing credentials", () => {
  const custom = { ...current, model_name: "custom-model" }
  expect(
    researchModelSelection(
      { ...custom, context_window_tokens: 64000, api_key: "ignored" },
      providers,
      custom,
    ),
  ).toEqual({
    ...custom,
    context_window_tokens: 64000,
  })
})
