type AnalyticsEnv = Record<string, unknown>

export type AnalyticsConfig =
  | { enabled: false }
  | {
      enabled: true
      domain: string
      endpoint?: string
      autoCapturePageviews: boolean
      hashBasedRouting: boolean
      outboundLinks: boolean
      fileDownloads: boolean
      formSubmissions: boolean
      captureOnLocalhost: boolean
      logging: boolean
      bindToWindow: boolean
    }

export type PlausibleInitConfig = Omit<
  Extract<AnalyticsConfig, { enabled: true }>,
  "enabled"
> & {
  customProperties: () => Record<string, string>
}

export type PlausibleEventOptions = {
  props?: Record<string, string>
  interactive?: boolean
  url?: string
}

export interface AnalyticsRouter {
  state?: {
    location?: {
      href?: string
      pathname?: string
    }
    matches?: Array<{
      id?: string
      routeId?: string
      route?: {
        id?: string
      }
    }>
  }
  subscribe: (
    eventName: "onResolved",
    callback: (event: {
      toLocation?: {
        href?: string
        pathname?: string
      }
    }) => void,
  ) => () => void
}

export interface PlausibleTracker {
  init: (config: PlausibleInitConfig) => void
  track: (eventName: string, options: PlausibleEventOptions) => void
}

export type SetupAnalyticsOptions = {
  env?: AnalyticsEnv
  getTitle?: () => string
  getLocationHref?: () => string
}

const browserEnv = () =>
  (import.meta as unknown as { env?: AnalyticsEnv }).env ?? {}

const readString = (value: unknown): string | undefined => {
  if (typeof value !== "string") return undefined
  const trimmed = value.trim()
  return trimmed.length > 0 ? trimmed : undefined
}

const readBoolean = (value: unknown, defaultValue: boolean): boolean => {
  if (typeof value === "boolean") return value
  if (typeof value !== "string") return defaultValue

  const normalized = value.trim().toLowerCase()
  if (!normalized) return defaultValue
  if (["1", "true", "yes", "on"].includes(normalized)) return true
  if (["0", "false", "no", "off"].includes(normalized)) return false
  return defaultValue
}

export const buildAnalyticsConfig = (
  env: AnalyticsEnv = browserEnv(),
): AnalyticsConfig => {
  const enabled = readBoolean(env.VITE_PLAUSIBLE_ENABLED, true)
  const domain = readString(env.VITE_PLAUSIBLE_DOMAIN)

  if (!enabled || !domain) {
    return { enabled: false }
  }

  const endpoint = readString(env.VITE_PLAUSIBLE_ENDPOINT)

  return {
    enabled: true,
    domain,
    ...(endpoint ? { endpoint } : {}),
    autoCapturePageviews: readBoolean(
      env.VITE_PLAUSIBLE_AUTO_CAPTURE_PAGEVIEWS,
      true,
    ),
    hashBasedRouting: readBoolean(env.VITE_PLAUSIBLE_HASH_BASED_ROUTING, false),
    outboundLinks: readBoolean(env.VITE_PLAUSIBLE_OUTBOUND_LINKS, false),
    fileDownloads: readBoolean(env.VITE_PLAUSIBLE_FILE_DOWNLOADS, false),
    formSubmissions: readBoolean(env.VITE_PLAUSIBLE_FORM_SUBMISSIONS, false),
    captureOnLocalhost: readBoolean(
      env.VITE_PLAUSIBLE_CAPTURE_ON_LOCALHOST,
      false,
    ),
    logging: readBoolean(env.VITE_PLAUSIBLE_LOGGING, true),
    bindToWindow: readBoolean(env.VITE_PLAUSIBLE_BIND_TO_WINDOW, true),
  }
}

const getRouteId = (router: AnalyticsRouter): string | undefined => {
  const matches = router.state?.matches
  const lastMatch = matches?.[matches.length - 1]
  return lastMatch?.routeId ?? lastMatch?.id ?? lastMatch?.route?.id
}

const parseHref = (href: string) => {
  try {
    return new URL(href, "http://localhost")
  } catch {
    return new URL("http://localhost/")
  }
}

const buildRouteProperties = (
  router: AnalyticsRouter,
  href: string,
  title: string,
): Record<string, string> => {
  const url = parseHref(href)
  const props: Record<string, string> = {
    path: url.pathname,
  }

  const routeId = getRouteId(router)
  if (routeId) props.route_id = routeId
  if (url.search) props.search = url.search
  if (url.hash) props.hash = url.hash
  if (title) props.title = title

  return props
}

export const setupAnalytics = (
  router: AnalyticsRouter,
  tracker: PlausibleTracker,
  options: SetupAnalyticsOptions = {},
) => {
  const config = buildAnalyticsConfig(options.env)
  if (!config.enabled) return () => {}

  const getTitle = options.getTitle ?? (() => document.title)
  const getLocationHref =
    options.getLocationHref ?? (() => window.location.href)
  const getProps = (href = getLocationHref()) =>
    buildRouteProperties(router, href, getTitle())

  const { enabled: _enabled, ...plausibleConfig } = config
  tracker.init({
    ...plausibleConfig,
    customProperties: () => getProps(),
  })

  if (config.autoCapturePageviews) {
    return () => {}
  }

  let lastHref = getLocationHref()
  tracker.track("pageview", {
    props: getProps(lastHref),
    url: lastHref,
  })

  return router.subscribe("onResolved", (event) => {
    const nextHref =
      event.toLocation?.href ??
      router.state?.location?.href ??
      getLocationHref()

    if (!nextHref || nextHref === lastHref) return
    lastHref = nextHref

    tracker.track("pageview", {
      props: getProps(nextHref),
      url: nextHref,
    })
  })
}
