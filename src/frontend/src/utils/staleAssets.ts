/**
 * Detection of stale frontend assets after a redeploy.
 *
 * With `autoCodeSplitting` enabled, each route is a separate hash-named chunk.
 * When the frontend is rebuilt while a tab stays open, navigating to a
 * not-yet-loaded route makes the browser request an old chunk that no longer
 * exists -> the dynamic import fails. We surface a "page updated, please
 * refresh" prompt instead of letting it crash.
 */

import { notifyRefresh } from "./updateNotify"

/**
 * Whether an error looks like a failed dynamic import of a missing chunk.
 *
 * Used as a fallback in the router `errorComponent`; the primary path is the
 * `vite:preloadError` event registered by {@link registerStaleAssetDetection}.
 *
 * @param error The thrown value to inspect.
 * @returns True when the message matches a dynamic-import / chunk-load failure.
 */
export function isStaleChunkError(error: unknown): boolean {
  const message =
    error instanceof Error
      ? error.message
      : typeof error === "string"
        ? error
        : ""
  return (
    /failed to fetch dynamically imported module/i.test(message) ||
    /error loading dynamically imported module/i.test(message) ||
    /importing a module script failed/i.test(message) ||
    /Failed to load module script/i.test(message)
  )
}

/**
 * Register the global `vite:preloadError` listener.
 *
 * Vite dispatches this event when a dynamically imported chunk fails to load.
 * We prevent the default (which would rethrow) and surface a refresh prompt.
 * Call once at app startup.
 */
export function registerStaleAssetDetection(): void {
  window.addEventListener("vite:preloadError", (event) => {
    // Take over the error so the app shows our prompt instead of crashing.
    event.preventDefault()
    notifyRefresh("stale-assets")
  })
}
