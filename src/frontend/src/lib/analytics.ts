import { init, track } from "@plausible-analytics/tracker"
import {
  type AnalyticsRouter,
  type SetupAnalyticsOptions,
  setupAnalytics as setupAnalyticsCore,
} from "./analytics-core"

export type { AnalyticsConfig, AnalyticsRouter } from "./analytics-core"
export { buildAnalyticsConfig } from "./analytics-core"

export const setupAnalytics = (
  router: AnalyticsRouter,
  options?: SetupAnalyticsOptions,
) =>
  setupAnalyticsCore(
    router,
    {
      init,
      track,
    },
    options,
  )
