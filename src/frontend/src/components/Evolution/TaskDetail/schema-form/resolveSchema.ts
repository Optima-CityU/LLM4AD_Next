/**
 * JSON Schema resolution utilities.
 * Handles $ref resolution, default extraction, nullable detection,
 * and discriminated union parsing.
 */

export interface SchemaUi {
  required?: boolean
  order?: number
  label?: { zh?: string; en?: string }
  description?: { zh?: string; en?: string }
  hidden?: boolean
  user_required?: boolean
  multiline?: boolean
  widget?: string
  slider?: {
    min: number
    max: number
    step: number
  }
}

export interface JsonSchema {
  type?: string
  title?: string
  description?: string
  default?: unknown
  enum?: unknown[]
  const?: unknown
  properties?: Record<string, JsonSchema>
  required?: string[]
  items?: JsonSchema
  $ref?: string
  $defs?: Record<string, JsonSchema>
  oneOf?: JsonSchema[]
  anyOf?: JsonSchema[]
  allOf?: JsonSchema[]
  discriminator?: { propertyName: string }
  additionalProperties?: boolean | JsonSchema
  minimum?: number
  maximum?: number
  exclusiveMinimum?: number
  exclusiveMaximum?: number
  minItems?: number
  maxItems?: number
  format?: string
  ui?: SchemaUi
}

export type UiLocale = "zh" | "en"

export function getUiLabel(
  schema: JsonSchema,
  fallback?: string,
  locale: UiLocale = "zh",
): string {
  const labels = schema.ui?.label
  if (labels) {
    return (
      labels[locale] ?? labels.zh ?? labels.en ?? schema.title ?? fallback ?? ""
    )
  }
  return schema.title ?? fallback ?? ""
}

export function getUiDescription(
  schema: JsonSchema,
  locale: UiLocale = "zh",
): string | undefined {
  const descs = schema.ui?.description
  if (descs) {
    return descs[locale] ?? descs.zh ?? descs.en ?? schema.description
  }
  return schema.description
}

export function isUiHidden(schema: JsonSchema): boolean {
  return schema.ui?.hidden === true || schema.const !== undefined
}

export function getUiOrder(schema: JsonSchema): number {
  return schema.ui?.order ?? 999
}

export function isUserRequired(schema: JsonSchema): boolean {
  return schema.ui?.user_required === true
}

export function isUiRequired(schema: JsonSchema): boolean {
  return schema.ui?.required === true
}

export function isMultiline(schema: JsonSchema): boolean {
  return schema.ui?.multiline === true
}

export function getUiSlider(
  schema: JsonSchema,
): { min: number; max: number; step: number } | undefined {
  if (schema.ui?.widget !== "slider") return undefined
  const slider = schema.ui.slider
  if (
    !slider ||
    !Number.isFinite(slider.min) ||
    !Number.isFinite(slider.max) ||
    !Number.isFinite(slider.step) ||
    slider.max <= slider.min ||
    slider.step <= 0
  ) {
    return undefined
  }
  return slider
}

export function fitSliderToValue(
  slider: { min: number; max: number; step: number },
  value: number,
): { min: number; max: number; step: number } {
  return {
    ...slider,
    min: Math.min(slider.min, value),
    max: Math.max(slider.max, value),
  }
}

/**
 * Determine if a field should show a required indicator (*).
 * A field is "effectively required" if it's in the parent's `required` list
 * OR has `ui.user_required === true`.
 */
export function isFieldRequired(
  schema: JsonSchema,
  parentRequired?: boolean,
): boolean {
  return parentRequired === true || isUserRequired(schema)
}

/**
 * Sort comparator for sibling schema entries.
 * Required fields (schema-required or user_required) come first,
 * then sort by ui.order ascending.
 */
export function compareSchemaEntries(
  a: { schema: JsonSchema; parentRequired?: boolean },
  b: { schema: JsonSchema; parentRequired?: boolean },
): number {
  const aReq = isFieldRequired(a.schema, a.parentRequired) ? 0 : 1
  const bReq = isFieldRequired(b.schema, b.parentRequired) ? 0 : 1
  if (aReq !== bReq) return aReq - bReq
  return getUiOrder(a.schema) - getUiOrder(b.schema)
}

/**
 * Resolve a $ref in the schema against the root $defs.
 */
export function resolveRef(schema: JsonSchema, root: JsonSchema): JsonSchema {
  if (!schema.$ref) return schema
  // Format: "#/$defs/SomeName"
  const refPath = schema.$ref.replace(/^#\//, "").split("/")
  let resolved: unknown = root
  for (const segment of refPath) {
    resolved = (resolved as Record<string, unknown>)?.[segment]
  }
  if (!resolved) {
    console.warn(`Could not resolve $ref: ${schema.$ref}`)
    return schema
  }
  return resolved as JsonSchema
}

/**
 * Fully resolve a schema, handling $ref and nullable anyOf.
 * Returns the resolved schema and whether it's nullable.
 */
export function resolveSchema(
  schema: JsonSchema,
  root: JsonSchema,
): { schema: JsonSchema; nullable: boolean } {
  let resolved = schema.$ref ? resolveRef(schema, root) : schema
  if (schema.$ref) {
    resolved = {
      ...resolved,
      title: schema.title ?? resolved.title,
      description: schema.description ?? resolved.description,
      default: schema.default ?? resolved.default,
      ui: schema.ui ?? resolved.ui,
    }
  }

  // Handle anyOf with null type (nullable pattern)
  if (resolved.anyOf) {
    const nonNull = resolved.anyOf.filter(
      (s) =>
        !(s.type === "null" || (s as Record<string, unknown>).type === "null"),
    )
    const hasNull = resolved.anyOf.length > nonNull.length
    if (nonNull.length === 1) {
      const inner = resolveRef(nonNull[0], root)
      return {
        schema: {
          ...inner,
          title: resolved.title ?? inner.title,
          description: resolved.description ?? inner.description,
          default: resolved.default ?? inner.default,
          ui: resolved.ui ?? inner.ui,
        },
        nullable: hasNull,
      }
    }
  }

  // Handle allOf with single item (merge pattern)
  if (resolved.allOf && resolved.allOf.length === 1) {
    const inner = resolveRef(resolved.allOf[0], root)
    resolved = { ...inner, ...resolved, allOf: undefined }
  }

  return { schema: resolved, nullable: false }
}

/**
 * Detect discriminated union: oneOf + discriminator.
 */
export function getDiscriminatorInfo(
  schema: JsonSchema,
  root: JsonSchema,
  locale: UiLocale = "zh",
): {
  propertyName: string
  options: Array<{ label: string; value: string; schema: JsonSchema }>
} | null {
  if (!schema.oneOf || !schema.discriminator?.propertyName) return null
  const propName = schema.discriminator.propertyName

  const options = schema.oneOf.map((variant) => {
    const resolved = resolveRef(variant, root)
    const constValue = resolved.properties?.[propName]?.const
    const value =
      typeof constValue === "string" ? constValue : String(constValue ?? "")
    const label = getUiLabel(resolved, value, locale)
    return { label, value, schema: resolved }
  })

  return { propertyName: propName, options }
}

/**
 * Extract default value for a schema node.
 */
export function getDefaultValue(schema: JsonSchema, root: JsonSchema): unknown {
  const { schema: resolved } = resolveSchema(schema, root)

  if (resolved.default !== undefined) return resolved.default
  if (resolved.const !== undefined) return resolved.const

  // Discriminated union: default to first option
  const discInfo = getDiscriminatorInfo(resolved, root)
  if (discInfo && discInfo.options.length > 0) {
    return getDefaultValue(discInfo.options[0].schema, root)
  }

  if (resolved.type === "object" && resolved.properties) {
    const obj: Record<string, unknown> = {}
    for (const [key, propSchema] of Object.entries(resolved.properties)) {
      const val = getDefaultValue(propSchema, root)
      if (val !== undefined) obj[key] = val
    }
    return Object.keys(obj).length > 0 ? obj : undefined
  }

  if (resolved.type === "array") {
    return resolved.default ?? []
  }

  if (resolved.type === "string") return resolved.default ?? ""
  if (resolved.type === "number" || resolved.type === "integer")
    return resolved.default
  if (resolved.type === "boolean") return resolved.default ?? false

  return undefined
}

/**
 * Extract only defaults explicitly declared by the schema.
 * Unlike getDefaultValue(), this never manufactures UI placeholders such as
 * empty strings, false, or empty arrays for fields that have no real default.
 */
function getDeclaredDefaultValue(
  schema: JsonSchema,
  root: JsonSchema,
): unknown {
  if (schema.default !== undefined) return schema.default
  if (schema.const !== undefined) return schema.const

  const { schema: resolved } = resolveSchema(schema, root)
  if (resolved.default !== undefined) return resolved.default
  if (resolved.const !== undefined) return resolved.const

  if (resolved.type === "object" && resolved.properties) {
    const value: Record<string, unknown> = {}
    for (const [key, propertySchema] of Object.entries(resolved.properties)) {
      const propertyDefault = getDeclaredDefaultValue(propertySchema, root)
      if (propertyDefault !== undefined) value[key] = propertyDefault
    }
    return Object.keys(value).length > 0 ? value : undefined
  }

  return undefined
}

/**
 * Recursively fill schema defaults into an existing value.
 * Existing values always win, including explicit false, zero, empty strings,
 * arrays, and null. For discriminated unions, defaults come from the variant
 * selected by the existing discriminator value.
 */
export function mergeSchemaDefaults(
  schema: JsonSchema,
  root: JsonSchema,
  current: unknown,
): unknown {
  const { schema: resolved } = resolveSchema(schema, root)

  if (current === null) return null
  if (
    resolved.enum &&
    current !== undefined &&
    !resolved.enum.some((option) => Object.is(option, current))
  ) {
    const declaredDefault = getDeclaredDefaultValue(schema, root)
    if (declaredDefault !== undefined) return declaredDefault
  }

  const discriminator = getDiscriminatorInfo(resolved, root)
  if (discriminator) {
    const currentObject =
      current && typeof current === "object" && !Array.isArray(current)
        ? (current as Record<string, unknown>)
        : {}
    const currentType = currentObject[discriminator.propertyName]
    const selected =
      discriminator.options.find((option) => option.value === currentType) ??
      discriminator.options[0]
    if (!selected) return current
    const merged = mergeSchemaDefaults(selected.schema, root, currentObject)
    return {
      ...(merged as Record<string, unknown>),
      [discriminator.propertyName]: selected.value,
    }
  }

  if (current === undefined) return getDeclaredDefaultValue(schema, root)

  if (
    resolved.type === "object" &&
    resolved.properties &&
    typeof current === "object" &&
    !Array.isArray(current)
  ) {
    const currentObject = current as Record<string, unknown>
    const merged: Record<string, unknown> = { ...currentObject }
    for (const [key, propertySchema] of Object.entries(resolved.properties)) {
      const value = mergeSchemaDefaults(
        propertySchema,
        root,
        currentObject[key],
      )
      if (value !== undefined) merged[key] = value
    }
    return merged
  }

  return current
}

/**
 * Get a default value for a discriminated union by selecting the first option.
 */
export function getDiscriminatedDefault(
  schema: JsonSchema,
  root: JsonSchema,
): unknown {
  const info = getDiscriminatorInfo(schema, root)
  if (!info || info.options.length === 0) return undefined
  return getDefaultValue(info.options[0].schema, root)
}

/**
 * Check if a schema represents a simple type.
 */
export function isPrimitive(schema: JsonSchema): boolean {
  return (
    schema.type === "string" ||
    schema.type === "number" ||
    schema.type === "integer" ||
    schema.type === "boolean"
  )
}

/**
 * Validate a value against a schema. Returns an array of error messages.
 */
export function validateSchema(
  schema: JsonSchema,
  root: JsonSchema,
  value: unknown,
  locale: UiLocale = "zh",
): string[] {
  const { schema: resolved } = resolveSchema(schema, root)
  const errors: string[] = []

  const msg =
    locale === "zh"
      ? {
          min: (v: number) => `值不能小于 ${v}`,
          max: (v: number) => `值不能大于 ${v}`,
          exclusiveMin: (v: number) => `值必须大于 ${v}`,
          minItems: (v: number) => `至少需要 ${v} 项`,
          required: (f: string) => `${f} 为必填项`,
        }
      : {
          min: (v: number) => `Must be at least ${v}`,
          max: (v: number) => `Must be at most ${v}`,
          exclusiveMin: (v: number) => `Must be greater than ${v}`,
          minItems: (v: number) => `At least ${v} items required`,
          required: (f: string) => `${f} is required`,
        }

  // Number constraints
  if (typeof value === "number") {
    if (resolved.minimum !== undefined && value < resolved.minimum)
      errors.push(msg.min(resolved.minimum))
    if (resolved.maximum !== undefined && value > resolved.maximum)
      errors.push(msg.max(resolved.maximum))
    if (
      resolved.exclusiveMinimum !== undefined &&
      value <= resolved.exclusiveMinimum
    )
      errors.push(msg.exclusiveMin(resolved.exclusiveMinimum))
  }

  // Array constraints
  if (resolved.type === "array") {
    const arr = Array.isArray(value) ? value : []
    if (resolved.minItems !== undefined && arr.length < resolved.minItems)
      errors.push(msg.minItems(resolved.minItems))
    if (resolved.items) {
      arr.forEach((item, i) => {
        const itemErrors = validateSchema(resolved.items!, root, item, locale)
        errors.push(...itemErrors.map((e) => `#${i + 1}: ${e}`))
      })
    }
    return errors
  }

  // Discriminated union: validate selected variant
  const discInfo = getDiscriminatorInfo(resolved, root)
  if (discInfo) {
    if (typeof value === "object" && value !== null) {
      const obj = value as Record<string, unknown>
      const selectedType = obj[discInfo.propertyName] as string
      const selectedOption = discInfo.options.find(
        (o) => o.value === selectedType,
      )
      if (selectedOption) {
        errors.push(
          ...validateSchema(selectedOption.schema, root, value, locale),
        )
      }
    }
    return errors
  }

  if (value === undefined || value === null) return errors

  // Object: check required fields and user_required fields
  if (
    resolved.type === "object" &&
    resolved.properties &&
    typeof value === "object"
  ) {
    const obj = value as Record<string, unknown>
    const schemaRequired = new Set(resolved.required ?? [])

    for (const [key, propSchema] of Object.entries(resolved.properties)) {
      const { schema: propResolved } = resolveSchema(propSchema, root)
      const needsValue = schemaRequired.has(key) || isUserRequired(propResolved)
      if (
        needsValue &&
        (obj[key] === undefined || obj[key] === null || obj[key] === "")
      ) {
        errors.push(msg.required(getUiLabel(propResolved, key, locale)))
      }
    }
  }

  return errors
}
