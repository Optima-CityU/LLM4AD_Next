import { useTranslation } from "react-i18next"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import DiverseIslandStrategyPreview from "./DiverseIslandStrategyPreview"
import FieldLabel from "./FieldLabel"
import type { JsonSchema } from "./resolveSchema"
import {
  compareSchemaEntries,
  getDefaultValue,
  isUiHidden,
  resolveSchema,
} from "./resolveSchema"
import SchemaField from "./SchemaField"
import { useSchemaUi } from "./useSchemaUi"

interface SchemaDiscriminatedFieldProps {
  schema: JsonSchema
  root: JsonSchema
  value: Record<string, unknown> | undefined
  onChange: (value: Record<string, unknown>) => void
  label?: string
  memoryEnabled?: boolean
}

const ISLAND_BEHAVIOR_FIELDS = [
  "num_islands",
  "island_population_size",
  "parallel_islands",
  "island_strategy_strength",
  "exploration_restart_ratio",
  "novelty_survivor_ratio",
  "adaptive_migration",
  "migration_stagnation_threshold",
  "migration_interval",
  "migration_rate",
  "migration_strategy",
  "migration_topology",
  "short_task_generation_threshold",
  "short_task_max_migrations",
  "elite_reevaluation_count",
] as const

export default function SchemaDiscriminatedField({
  schema,
  root,
  value,
  onChange,
  label,
  memoryEnabled = true,
}: SchemaDiscriminatedFieldProps) {
  const {
    label: uiLabel,
    description: uiDescription,
    discriminatorInfo,
  } = useSchemaUi()
  const { t } = useTranslation()
  const info = discriminatorInfo(schema, root)
  if (!info) return null

  const currentType = value?.[info.propertyName] as string | undefined
  const selectedOption =
    info.options.find((o) => o.value === currentType) ?? info.options[0]

  const handleTypeChange = (newType: string) => {
    const option = info.options.find((o) => o.value === newType)
    if (!option) return
    const defaults = getDefaultValue(option.schema, root) as
      | Record<string, unknown>
      | undefined
    onChange({ ...defaults, [info.propertyName]: newType })
  }

  const filteredProperties = { ...selectedOption.schema.properties }
  delete filteredProperties[info.propertyName]

  const requiredSet = new Set(
    selectedOption.schema.required?.filter((r) => r !== info.propertyName) ??
      [],
  )

  const sortedEntries = Object.entries(filteredProperties)
    .filter(([, propSchema]) => {
      const { schema: resolved } = resolveSchema(propSchema, root)
      return !isUiHidden(resolved)
    })
    .sort(([keyA, a], [keyB, b]) => {
      const { schema: ra } = resolveSchema(a, root)
      const { schema: rb } = resolveSchema(b, root)
      return compareSchemaEntries(
        { schema: ra, parentRequired: requiredSet.has(keyA) },
        { schema: rb, parentRequired: requiredSet.has(keyB) },
      )
    })

  const filteredSchema: JsonSchema = {
    ...selectedOption.schema,
    properties: filteredProperties,
    required: selectedOption.schema.required?.filter(
      (r) => r !== info.propertyName,
    ),
  }

  const fieldLabel =
    label ??
    uiLabel(schema, t("evolution.schemaForm.discriminatorFallbackLabel"))
  const fieldDescription = uiDescription(schema)
  const isDiverseIsland = selectedOption.value === "diverse_island_ga"
  const islandBehaviorKeys = new Set<string>(ISLAND_BEHAVIOR_FIELDS)
  const islandBehaviorEntries = ISLAND_BEHAVIOR_FIELDS.flatMap((fieldName) => {
    const entry = sortedEntries.find(([key]) => key === fieldName)
    return entry ? [entry] : []
  })
  const commonEntries = sortedEntries.filter(
    ([key]) => !islandBehaviorKeys.has(key),
  )

  const renderField = (
    [key, propSchema]: [string, JsonSchema],
    layout: "stacked" | "inline" = "stacked",
  ) => (
    <SchemaField
      key={key}
      name={key}
      schema={propSchema}
      root={root}
      value={(value as Record<string, unknown>)?.[key]}
      onChange={(v) => onChange({ ...value, [key]: v })}
      required={filteredSchema.required?.includes(key)}
      layout={layout}
    />
  )

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <FieldLabel label={fieldLabel} description={fieldDescription} />
        <Select
          value={currentType ?? selectedOption.value}
          onValueChange={handleTypeChange}
        >
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {info.options.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {sortedEntries.length > 0 && (
        <div className="ml-2 space-y-4 border-l-2 border-border pl-4">
          {isDiverseIsland ? (
            <>
              <section
                data-testid="island-behavior-settings"
                className="rounded-xl border border-border/70 bg-card"
              >
                <div className="border-b border-border/60 px-4 py-3">
                  <h4 className="text-sm font-semibold">
                    {t("evolution.schemaForm.islandSettings.title")}
                  </h4>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {t("evolution.schemaForm.islandSettings.description")}
                  </p>
                </div>
                <div className="grid gap-x-6 gap-y-2 p-4 xl:grid-cols-2">
                  {islandBehaviorEntries.map((entry) =>
                    renderField(entry, "inline"),
                  )}
                </div>
              </section>

              <DiverseIslandStrategyPreview
                value={value}
                memoryEnabled={memoryEnabled}
              />

              {commonEntries.length > 0 && (
                <section className="space-y-4 rounded-xl border border-border/70 bg-card p-4">
                  <div>
                    <h4 className="text-sm font-semibold">
                      {t("evolution.schemaForm.commonEvolutionSettings.title")}
                    </h4>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      {t(
                        "evolution.schemaForm.commonEvolutionSettings.description",
                      )}
                    </p>
                  </div>
                  {commonEntries.map((entry) => renderField(entry))}
                </section>
              )}
            </>
          ) : (
            sortedEntries.map((entry) => renderField(entry))
          )}
        </div>
      )}
    </div>
  )
}
