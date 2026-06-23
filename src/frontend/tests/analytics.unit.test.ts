import assert from "node:assert/strict"
import { describe, test } from "node:test"

import {
  type AnalyticsRouter,
  buildAnalyticsConfig,
  type PlausibleTracker,
  setupAnalytics,
} from "../src/lib/analytics-core.ts"

const baseEnv = {
  VITE_PLAUSIBLE_DOMAIN: "",
  VITE_PLAUSIBLE_ENDPOINT: "",
  VITE_PLAUSIBLE_ENABLED: "",
  VITE_PLAUSIBLE_CAPTURE_ON_LOCALHOST: "",
  VITE_PLAUSIBLE_AUTO_CAPTURE_PAGEVIEWS: "",
  VITE_PLAUSIBLE_HASH_BASED_ROUTING: "",
  VITE_PLAUSIBLE_OUTBOUND_LINKS: "",
  VITE_PLAUSIBLE_FILE_DOWNLOADS: "",
  VITE_PLAUSIBLE_FORM_SUBMISSIONS: "",
  VITE_PLAUSIBLE_LOGGING: "",
  VITE_PLAUSIBLE_BIND_TO_WINDOW: "",
}

const createRouter = (): AnalyticsRouter & {
  emitRoute: (href: string, pathname: string, routeId?: string) => void
} => {
  let listener:
    | ((event: { toLocation: { href: string; pathname: string } }) => void)
    | undefined

  const router = {
    state: {
      matches: [{ routeId: "/initial" }],
    },
    subscribe: (eventName, callback) => {
      assert.equal(eventName, "onResolved")
      listener = callback
      return () => {
        listener = undefined
      }
    },
    emitRoute: (href, pathname, routeId = pathname) => {
      router.state.matches = [{ routeId }]
      ;(listener as NonNullable<typeof listener>)({
        toLocation: { href, pathname },
      })
    },
  }

  return router
}

describe("buildAnalyticsConfig", () => {
  test("does not enable analytics without a Plausible domain", () => {
    assert.deepEqual(buildAnalyticsConfig(baseEnv), { enabled: false })
  })

  test("reads Plausible options from Vite environment variables", () => {
    assert.deepEqual(
      buildAnalyticsConfig({
        ...baseEnv,
        VITE_PLAUSIBLE_DOMAIN: "my-app.com",
        VITE_PLAUSIBLE_ENDPOINT: "https://plausible.dadastory.com/api/event",
        VITE_PLAUSIBLE_CAPTURE_ON_LOCALHOST: "true",
        VITE_PLAUSIBLE_AUTO_CAPTURE_PAGEVIEWS: "false",
        VITE_PLAUSIBLE_OUTBOUND_LINKS: "true",
        VITE_PLAUSIBLE_LOGGING: "false",
      }),
      {
        enabled: true,
        domain: "my-app.com",
        endpoint: "https://plausible.dadastory.com/api/event",
        captureOnLocalhost: true,
        autoCapturePageviews: false,
        hashBasedRouting: false,
        outboundLinks: true,
        fileDownloads: false,
        formSubmissions: false,
        logging: false,
        bindToWindow: true,
      },
    )
  })
})

describe("setupAnalytics", () => {
  test("initializes Plausible and tracks route changes with route metadata", () => {
    const calls: unknown[][] = []
    const tracker: PlausibleTracker = {
      init: (config) => calls.push(["init", config]),
      track: (eventName, options) => calls.push(["track", eventName, options]),
    }
    const router = createRouter()

    setupAnalytics(router, tracker, {
      env: {
        ...baseEnv,
        VITE_PLAUSIBLE_DOMAIN: "my-app.com",
        VITE_PLAUSIBLE_AUTO_CAPTURE_PAGEVIEWS: "false",
      },
      getTitle: () => "Dashboard",
      getLocationHref: () => "https://my-app.com/",
    })

    assert.equal(calls[0]?.[0], "init")
    const initConfig = calls[0]?.[1] as {
      customProperties: () => Record<string, string>
    }
    assert.deepEqual(initConfig.customProperties(), {
      path: "/",
      route_id: "/initial",
      title: "Dashboard",
    })

    router.emitRoute("https://my-app.com/evolution?task=1", "/evolution")

    assert.deepEqual(calls, [
      [
        "init",
        {
          domain: "my-app.com",
          autoCapturePageviews: false,
          hashBasedRouting: false,
          outboundLinks: false,
          fileDownloads: false,
          formSubmissions: false,
          captureOnLocalhost: false,
          logging: true,
          bindToWindow: true,
          customProperties: initConfig.customProperties,
        },
      ],
      [
        "track",
        "pageview",
        {
          props: {
            path: "/",
            route_id: "/initial",
            title: "Dashboard",
          },
          url: "https://my-app.com/",
        },
      ],
      [
        "track",
        "pageview",
        {
          props: {
            path: "/evolution",
            route_id: "/evolution",
            search: "?task=1",
            title: "Dashboard",
          },
          url: "https://my-app.com/evolution?task=1",
        },
      ],
    ])
  })
})
