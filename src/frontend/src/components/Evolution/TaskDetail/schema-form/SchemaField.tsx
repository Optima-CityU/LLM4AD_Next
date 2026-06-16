import { ChevronDown, ChevronRight } from "lucide-react"
import { useState } from "react"
import { useTranslation } from "react-i18next"

import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import FieldLabel from "./FieldLabel"
import type { JsonSchema } from "./resolveSchema"
import {
  isMultiline,
  isUiHidden,
  isUserRequired,
  resolveRef,
  resolveSchema,
} from "./resolveSchema"
import SchemaArrayField from "./SchemaArrayField"
import SchemaDiscriminatedField from "./SchemaDiscriminatedField"
import SchemaObjectField from "./SchemaObjectField"
import { useSchemaUi } from "./useSchemaUi"

interface SchemaFieldProps {
  name: string
  schema: JsonSchema
  root: JsonSchema
  value: unknown
  onChange: (value: unknown) => void
  required?: boolean
  label?: string
}

export default function SchemaField({
  name,
  schema,
  root,
  value,
  onChange,
  required,
  label,
}: SchemaFieldProps) {
  const {
    label: uiLabel,
    description: uiDescription,
    discriminatorInfo,
  } = useSchemaUi()
  const { schema: resolved, nullable } = resolveSchema(schema, root)
  const fieldLabel = label ?? uiLabel(resolved, name)
  const fieldDescription = uiDescription(resolved)
  const fieldRequired = required || isUserRequired(resolved)

  // Hidden field — not rendered
  if (isUiHidden(resolved)) {
    return null
  }

  // Discriminated union (oneOf + discriminator)
  if (discriminatorInfo(resolved, root)) {
    return (
      <SchemaDiscriminatedField
        schema={resolved}
        root={root}
        value={value as Record<string, unknown> | undefined}
        onChange={onChange as (v: Record<string, unknown>) => void}
        label={fieldLabel}
      />
    )
  }

  // Multi-type anyOf (e.g., string | object) — smart field that tries object first
  if (resolved.anyOf) {
    const nonNull = resolved.anyOf.filter(
      (s) => resolveRef(s, root).type !== "null",
    )
    if (nonNull.length > 1) {
      const types = new Set(nonNull.map((s) => resolveRef(s, root).type))
      if (types.has("object")) {
        return (
          <AnyOfField
            label={fieldLabel}
            description={fieldDescription}
            value={value}
            onChange={onChange}
            nullable={nullable}
          />
        )
      }
    }
  }

  // Enum → Select
  if (resolved.enum) {
    return (
      <EnumField
        label={fieldLabel}
        description={fieldDescription}
        options={resolved.enum as string[]}
        value={value as string | undefined}
        onChange={onChange}
        required={fieldRequired}
        nullable={nullable}
      />
    )
  }

  // Array
  if (resolved.type === "array") {
    return (
      <SchemaArrayField
        schema={resolved}
        root={root}
        value={value as unknown[] | undefined}
        onChange={onChange as (v: unknown[]) => void}
        label={fieldLabel}
      />
    )
  }

  // Object with properties — collapsible
  if (resolved.type === "object" && resolved.properties) {
    return (
      <CollapsibleObjectSection
        label={fieldLabel}
        description={fieldDescription}
        schema={resolved}
        root={root}
        value={value as Record<string, unknown> | undefined}
        onChange={onChange as (v: Record<string, unknown>) => void}
      />
    )
  }

  // Object with additionalProperties (key-value / JSON editor)
  if (resolved.type === "object" && !resolved.properties) {
    return (
      <JsonTextareaField
        label={fieldLabel}
        description={fieldDescription}
        value={value}
        onChange={onChange}
      />
    )
  }

  // Boolean
  if (resolved.type === "boolean") {
    return (
      <BooleanField
        label={fieldLabel}
        description={fieldDescription}
        value={value as boolean | undefined}
        onChange={onChange}
      />
    )
  }

  // Number / Integer
  if (resolved.type === "number" || resolved.type === "integer") {
    return (
      <NumberField
        label={fieldLabel}
        description={fieldDescription}
        value={value as number | undefined}
        onChange={onChange}
        required={fieldRequired}
        nullable={nullable}
        schema={resolved}
      />
    )
  }

  // String (default)
  return (
    <StringField
      label={fieldLabel}
      description={fieldDescription}
      value={value as string | undefined}
      onChange={onChange}
      required={fieldRequired}
      nullable={nullable}
      multiline={isMultiline(resolved)}
    />
  )
}

// --- Collapsible object section (VS Code-style fold) ---

function CollapsibleObjectSection({
  label,
  description,
  schema,
  root,
  value,
  onChange,
}: {
  label: string
  description?: string
  schema: JsonSchema
  root: JsonSchema
  value: Record<string, unknown> | undefined
  onChange: (v: Record<string, unknown>) => void
}) {
  const [collapsed, setCollapsed] = useState(false)

  return (
    <div className="space-y-1">
      <button
        type="button"
        onClick={() => setCollapsed(!collapsed)}
        className="flex items-center gap-1 group/fold -ml-1 py-0.5 rounded hover:bg-accent/40 transition-colors pr-2"
      >
        {collapsed ? (
          <ChevronRight className="size-4 text-muted-foreground shrink-0" />
        ) : (
          <ChevronDown className="size-4 text-muted-foreground shrink-0" />
        )}
        <span className="text-sm font-medium">{label}</span>
        {description && (
          <span className="text-xs text-muted-foreground ml-1.5 truncate max-w-[280px]">
            {description}
          </span>
        )}
      </button>
      {!collapsed && (
        <div className="ml-[7px] border-l-2 border-border pl-4">
          <SchemaObjectField
            schema={schema}
            root={root}
            value={value}
            onChange={onChange}
          />
        </div>
      )}
    </div>
  )
}

// --- Leaf field components ---

function StringField({
  label,
  description,
  value,
  onChange,
  required,
  nullable,
  multiline,
}: {
  label: string
  description?: string
  value: string | undefined
  onChange: (v: unknown) => void
  required?: boolean
  nullable?: boolean
  multiline?: boolean
}) {
  const { t } = useTranslation()
  const placeholder = nullable
    ? t("evolution.schemaForm.nullablePlaceholder")
    : undefined

  return (
    <div className="space-y-2">
      <FieldLabel label={label} description={description} required={required} />
      {multiline ? (
        <Textarea
          className="min-h-[80px] resize-y"
          value={value ?? ""}
          onChange={(e) => {
            const v = e.target.value
            onChange(nullable && v === "" ? null : v)
          }}
          placeholder={placeholder}
        />
      ) : (
        <Input
          value={value ?? ""}
          onChange={(e) => {
            const v = e.target.value
            onChange(nullable && v === "" ? null : v)
          }}
          placeholder={placeholder}
        />
      )}
    </div>
  )
}

function NumberField({
  label,
  description,
  value,
  onChange,
  required,
  nullable,
  schema,
}: {
  label: string
  description?: string
  value: number | undefined
  onChange: (v: unknown) => void
  required?: boolean
  nullable?: boolean
  schema: JsonSchema
}) {
  const { t } = useTranslation()
  const getError = (): string | null => {
    if (value === undefined || value === null) return null
    if (schema.minimum !== undefined && (value as number) < schema.minimum)
      return t("evolution.schemaForm.minError", { min: schema.minimum })
    if (schema.maximum !== undefined && (value as number) > schema.maximum)
      return t("evolution.schemaForm.maxError", { max: schema.maximum })
    if (
      schema.exclusiveMinimum !== undefined &&
      (value as number) <= schema.exclusiveMinimum
    )
      return t("evolution.schemaForm.exclusiveMinError", {
        min: schema.exclusiveMinimum,
      })
    if (
      schema.exclusiveMaximum !== undefined &&
      (value as number) >= schema.exclusiveMaximum
    )
      return t("evolution.schemaForm.exclusiveMaxError", {
        max: schema.exclusiveMaximum,
      })
    return null
  }
  const error = getError()

  const hints: string[] = []
  if (schema.minimum !== undefined) hints.push(`>= ${schema.minimum}`)
  else if (schema.exclusiveMinimum !== undefined)
    hints.push(`> ${schema.exclusiveMinimum}`)
  if (schema.maximum !== undefined) hints.push(`<= ${schema.maximum}`)
  else if (schema.exclusiveMaximum !== undefined)
    hints.push(`< ${schema.exclusiveMaximum}`)
  const placeholder = nullable
    ? t("evolution.schemaForm.nullablePlaceholder")
    : hints.length > 0
      ? hints.join(", ")
      : undefined

  return (
    <div className="space-y-2">
      <FieldLabel label={label} description={description} required={required} />
      <Input
        type="number"
        value={value ?? ""}
        min={schema.minimum ?? schema.exclusiveMinimum}
        max={schema.maximum ?? schema.exclusiveMaximum}
        step={schema.type === "integer" ? 1 : "any"}
        onChange={(e) => {
          const v = e.target.value
          if (v === "") {
            onChange(nullable ? null : undefined)
          } else {
            onChange(
              schema.type === "integer" ? parseInt(v, 10) : parseFloat(v),
            )
          }
        }}
        placeholder={placeholder}
        className={error ? "border-destructive" : undefined}
      />
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  )
}

function BooleanField({
  label,
  description,
  value,
  onChange,
}: {
  label: string
  description?: string
  value: boolean | undefined
  onChange: (v: unknown) => void
}) {
  return (
    <div className="flex items-start gap-3 py-1">
      <Checkbox
        checked={value ?? false}
        onCheckedChange={(checked) => onChange(!!checked)}
        className="mt-0.5"
      />
      <div className="space-y-0.5">
        <FieldLabel label={label} description={description} />
      </div>
    </div>
  )
}

const NULLABLE_SENTINEL = "__unset__"

function EnumField({
  label,
  description,
  options,
  value,
  onChange,
  required,
  nullable,
}: {
  label: string
  description?: string
  options: string[]
  value: string | undefined
  onChange: (v: unknown) => void
  required?: boolean
  nullable?: boolean
}) {
  const { t } = useTranslation()
  return (
    <div className="space-y-2">
      <FieldLabel label={label} description={description} required={required} />
      <Select
        value={value ?? (nullable ? NULLABLE_SENTINEL : "")}
        onValueChange={(v) => onChange(v === NULLABLE_SENTINEL ? null : v)}
      >
        <SelectTrigger>
          <SelectValue
            placeholder={t("evolution.schemaForm.selectPlaceholder")}
          />
        </SelectTrigger>
        <SelectContent>
          {nullable && (
            <SelectItem value={NULLABLE_SENTINEL}>
              {t("evolution.schemaForm.nullableOption")}
            </SelectItem>
          )}
          {options.map((opt) => (
            <SelectItem key={String(opt)} value={String(opt)}>
              {String(opt)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}

function JsonTextareaField({
  label,
  description,
  value,
  onChange,
}: {
  label: string
  description?: string
  value: unknown
  onChange: (v: unknown) => void
}) {
  const { t } = useTranslation()
  const [text, setText] = useState(() =>
    value ? JSON.stringify(value, null, 2) : "{}",
  )
  const [error, setError] = useState<string>()

  const handleBlur = () => {
    try {
      const parsed = JSON.parse(text)
      onChange(parsed)
      setError(undefined)
    } catch {
      setError(t("evolution.schemaForm.invalidJson"))
    }
  }

  return (
    <div className="space-y-2">
      <FieldLabel label={label} description={description} />
      <Textarea
        className="min-h-[80px] resize-y font-mono"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onBlur={handleBlur}
      />
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  )
}

function AnyOfField({
  label,
  description,
  value,
  onChange,
  nullable,
}: {
  label: string
  description?: string
  value: unknown
  onChange: (v: unknown) => void
  nullable?: boolean
}) {
  const { t } = useTranslation()
  const [text, setText] = useState(() => {
    if (value === undefined || value === null) return ""
    if (typeof value === "string") return value
    return JSON.stringify(value, null, 2)
  })
  const isObject = typeof value === "object" && value !== null

  const handleBlur = () => {
    const trimmed = text.trim()
    if (!trimmed) {
      onChange(nullable ? null : undefined)
      return
    }
    // Try to parse as JSON object first
    try {
      const parsed = JSON.parse(trimmed)
      if (typeof parsed === "object" && parsed !== null) {
        onChange(parsed)
        return
      }
    } catch {
      // Not valid JSON — fall through to string
    }
    onChange(trimmed)
  }

  return (
    <div className="space-y-2">
      <FieldLabel label={label} description={description} />
      <Textarea
        className="min-h-[80px] resize-y font-mono"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onBlur={handleBlur}
        placeholder={t("evolution.schemaForm.anyOfPlaceholder")}
      />
      <p className="text-xs text-muted-foreground">
        {t("evolution.schemaForm.currentType")}:{" "}
        {isObject
          ? t("evolution.schemaForm.typeObject")
          : t("evolution.schemaForm.typeString")}
      </p>
    </div>
  )
}
