/**
 * Lightweight pub/sub signal for "the app needs a refresh" notifications.
 *
 * Two independent scenarios feed this module:
 * - `deploying`: an API request failed because the backend is being
 *   redeployed/restarted (5xx gateway errors or a refused connection).
 * - `stale-assets`: the browser tried to load a hashed chunk that no longer
 *   exists because the frontend was rebuilt while this tab stayed open.
 *
 * The UI (`UpdateNotifyDialog`) subscribes and shows a centered modal with a
 * refresh button. No version number, polling, or backend cooperation needed.
 */

export type UpdateNotifyKind = "deploying" | "stale-assets"

type Listener = (kind: UpdateNotifyKind) => void

const listeners = new Set<Listener>()

/**
 * The kind currently being shown to the user. While set, repeated signals of
 * the same kind are suppressed so a burst of failing requests does not spam
 * the dialog. Reset via `clearUpdateNotify` when the dialog is dismissed.
 */
let currentKind: UpdateNotifyKind | null = null

/**
 * Subscribe to update notifications.
 *
 * @param listener Callback invoked with the notification kind.
 * @returns An unsubscribe function.
 */
export function subscribeUpdateNotify(listener: Listener): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

/**
 * Emit an update notification.
 *
 * Deduplicates against the currently displayed kind so repeated triggers
 * (e.g. many failing requests during a deploy) only surface once.
 *
 * @param kind The notification kind to surface.
 */
export function notifyRefresh(kind: UpdateNotifyKind): void {
  if (currentKind === kind) {
    return
  }
  currentKind = kind
  for (const listener of listeners) {
    listener(kind)
  }
}

/**
 * Reset the dedup latch so a later signal of the same kind can surface again.
 *
 * Called when the user dismisses the dialog.
 */
export function clearUpdateNotify(): void {
  currentKind = null
}
