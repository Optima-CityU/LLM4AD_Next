import i18n from "@/i18n"

export function formatProviderModel(model: string): string {
  return model === "codex-default"
    ? i18n.t("llmProvider.codex.followDefault")
    : model
}
