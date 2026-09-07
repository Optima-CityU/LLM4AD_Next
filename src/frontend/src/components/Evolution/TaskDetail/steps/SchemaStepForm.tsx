import { useCallback, useState } from "react"
import { useTranslation } from "react-i18next"

import { Button } from "@/components/ui/button"
import type { JsonSchema } from "../schema-form/resolveSchema"
import SchemaField from "../schema-form/SchemaField"
import { useSchemaUi } from "../schema-form/useSchemaUi"

interface SchemaStepFormProps {
  schemaKey: string
  schema: JsonSchema
  root: JsonSchema
  value: unknown
  onChange: (v: unknown) => void
  title?: string
  description?: string
  onBack?: () => void
  onNext?: () => void
  headerContent?: React.ReactNode
  memoryEnabled?: boolean
}

export default function SchemaStepForm({
  schemaKey,
  schema,
  root,
  value,
  onChange,
  onBack,
  onNext,
  headerContent,
  memoryEnabled,
}: SchemaStepFormProps) {
  const { t } = useTranslation()
  const { validate } = useSchemaUi()
  const [errors, setErrors] = useState<string[]>([])
  const [showErrors, setShowErrors] = useState(false)

  const handleNext = useCallback(() => {
    const errs = validate(schema, root, value)
    if (errs.length > 0) {
      setErrors(errs)
      setShowErrors(true)
      return
    }
    setShowErrors(false)
    onNext?.()
  }, [schema, root, value, onNext, validate])

  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 min-h-0 overflow-y-auto space-y-6 pr-1">
        {headerContent}

        <SchemaField
          name={schemaKey}
          schema={schema}
          root={root}
          value={value}
          onChange={onChange}
          memoryEnabled={memoryEnabled}
        />

        {showErrors && errors.length > 0 && (
          <div className="rounded-md border border-destructive/50 bg-destructive/10 p-3">
            <p className="text-sm font-medium text-destructive mb-1">
              {t("evolution.schemaForm.fixIssues")}
            </p>
            <ul className="text-xs text-destructive space-y-0.5">
              {errors.map((err, i) => (
                <li key={i}>• {err}</li>
              ))}
            </ul>
          </div>
        )}
      </div>

      <div className="shrink-0 py-2">
        <div className="flex justify-center gap-6">
          {onBack && (
            <Button type="button" variant="outline" onClick={onBack}>
              {t("common.previousStep")}
            </Button>
          )}
          {onNext && (
            <Button type="button" onClick={handleNext}>
              {t("common.nextStep")}
            </Button>
          )}
        </div>
      </div>
    </div>
  )
}
