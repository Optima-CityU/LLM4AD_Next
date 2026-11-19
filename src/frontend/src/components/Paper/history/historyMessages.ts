import { useCallback } from "react"
import { useTranslation } from "react-i18next"
import translations from "./historyTranslations.json"

/** Keep new keys usable before the parent merges the accompanying locale JSON. */
export function useHistoryText() {
  const { t } = useTranslation()
  return useCallback(
    (
      key: keyof typeof translations.en.paper.history,
      values?: Record<string, string | number>,
    ) =>
      t(`paper.history.${key}`, {
        defaultValue: translations.en.paper.history[key],
        ...values,
      }),
    [t],
  )
}
