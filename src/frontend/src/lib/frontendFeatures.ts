/** Return whether a build-time frontend feature flag is explicitly enabled. */
export function parseFrontendFeatureFlag(value: string | undefined): boolean {
  return value?.trim().toLowerCase() === "true"
}

/** AutoRebuttal is hidden unless a deployment explicitly enables it. */
export const AUTO_REBUTTAL_ENABLED = parseFrontendFeatureFlag(
  import.meta.env.VITE_AUTOREBUTTAL_ENABLED,
)

/** AutoDiscovery is hidden unless a deployment explicitly enables it. */
export const AUTO_DISCOVERY_ENABLED = parseFrontendFeatureFlag(
  import.meta.env.VITE_AUTODISCOVERY_ENABLED,
)
