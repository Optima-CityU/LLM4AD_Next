import type {
  PaperConversationPreferences,
  PaperModelBindingUpdate,
  ProviderResponse,
} from "@/client"

export const CLAUDE_RUNTIME_PROVIDER_TYPES = new Set([
  "anthropic",
  "openai",
  "openai_compatible",
])

/** Accept presentation preferences without changing the language of deliverables. */
export function researchPreferencesSelection(
  value: unknown,
): PaperConversationPreferences | null {
  if (!value || typeof value !== "object") return null
  const preferences = value as Record<string, unknown>
  if (
    typeof preferences.reply_language !== "string" ||
    !["auto", "zh", "en"].includes(preferences.reply_language) ||
    typeof preferences.additional_guidance !== "string" ||
    preferences.additional_guidance.length > 4000
  )
    return null
  return {
    reply_language: preferences.reply_language as "auto" | "zh" | "en",
    additional_guidance: preferences.additional_guidance.trim(),
  }
}

/** Expose display metadata only; provider credentials stay in the host page. */
export function researchModelOptions(providers: ProviderResponse[]) {
  return providers
    .filter((provider) => CLAUDE_RUNTIME_PROVIDER_TYPES.has(provider.type))
    .flatMap((provider) =>
      [...new Set(provider.model.split(";").map((model) => model.trim()))]
        .filter(Boolean)
        .map((model) => ({
          provider_id: provider.id,
          provider_name: provider.name,
          model_name: model,
        })),
    )
}

/** Validate an iframe selection against the authenticated host's model catalog. */
export function researchModelSelection(
  value: unknown,
  providers: ProviderResponse[],
  current: PaperModelBindingUpdate,
): PaperModelBindingUpdate | null {
  if (!value || typeof value !== "object") return null
  const binding = value as Record<string, unknown>
  const provider = providers.find(
    (item) =>
      item.id === binding.provider_id &&
      CLAUDE_RUNTIME_PROVIDER_TYPES.has(item.type),
  )
  if (!provider || typeof binding.model_name !== "string") return null
  const configured = provider.model.split(";").map((model) => model.trim())
  const existing =
    binding.provider_id === current.provider_id &&
    binding.model_name === current.model_name
  if (!configured.includes(binding.model_name) && !existing) return null
  const context = binding.context_window_tokens
  const output = binding.max_output_tokens
  if (
    typeof context !== "number" ||
    typeof output !== "number" ||
    !Number.isInteger(context) ||
    !Number.isInteger(output) ||
    context < 8192 ||
    output < 1024 ||
    output > context
  )
    return null
  return {
    provider_id: provider.id,
    model_name: binding.model_name,
    context_window_tokens: context,
    max_output_tokens: output,
  }
}
