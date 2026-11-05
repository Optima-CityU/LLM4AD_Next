import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useMemo } from "react"
import { useTranslation } from "react-i18next"

import {
  Llm4AdPapersService,
  type PaperConversationPreferences,
  type PaperModelBindingUpdate,
} from "@/client"
import { paperWorkspaceKeys } from "@/hooks/usePapers"
import { useProviders } from "@/hooks/useProviders"
import { autoDesignKeys } from "@/lib/autoDesign"
import {
  researchModelOptions,
  researchModelSelection,
  researchPreferencesSelection,
} from "@/lib/researchModels"

/** Share platform model choices with native composers while saving through the host. */
export function useEmbeddedModelBinding(
  workspaceId: string,
  binding: PaperModelBindingUpdate,
  preferences: PaperConversationPreferences,
) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const providers = useProviders()
  const options = useMemo(
    () => researchModelOptions(providers.data?.items ?? []),
    [providers.data],
  )
  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: paperWorkspaceKeys.all }),
      queryClient.invalidateQueries({ queryKey: autoDesignKeys.all }),
    ])
  }
  const update = useMutation({
    mutationFn: async (value: unknown) => {
      const selected = researchModelSelection(
        value,
        providers.data?.items ?? [],
        binding,
      )
      if (!selected) throw new Error("Invalid research model selection")
      return Llm4AdPapersService.updateModelBinding({
        workspaceId,
        requestBody: selected,
      })
    },
    onSuccess: refresh,
  })
  const updatePreferences = useMutation({
    mutationFn: async (value: unknown) => {
      const selected = researchPreferencesSelection(value)
      if (!selected) throw new Error("Invalid conversation preferences")
      return Llm4AdPapersService.updateWorkspace({
        workspaceId,
        requestBody: { conversation_preferences: selected },
      })
    },
    onSuccess: refresh,
  })
  const metadata = useMemo(
    () => ({
      modelBinding: binding,
      conversationPreferences: preferences,
      modelOptions: options,
      modelsLoading: providers.isPending,
      modelUpdating: update.isPending,
      modelError: update.isError
        ? t("paper.model.switchFailed")
        : providers.isError
          ? t("paper.model.providersLoadFailed")
          : "",
      preferencesUpdating: updatePreferences.isPending,
      preferencesError: updatePreferences.isError
        ? t("paper.preferences.saveFailed")
        : "",
    }),
    [
      binding,
      options,
      providers.isPending,
      providers.isError,
      update.isPending,
      update.isError,
      t,
      preferences,
      updatePreferences.isPending,
      updatePreferences.isError,
    ],
  )
  return {
    metadata,
    selectModel: update.mutate,
    modelUpdating: update.isPending,
    selectPreferences: updatePreferences.mutate,
    preferencesUpdating: updatePreferences.isPending,
  }
}
