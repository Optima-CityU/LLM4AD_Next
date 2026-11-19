import { type RefObject, useEffect, useRef } from "react"

import { installEmbeddedAuthBridge } from "@/utils/authLifecycle"

/** Used by the native runtime host to renew tickets without replacing its iframe. */
export function useEmbeddedAuthSession(
  frameRef: RefObject<HTMLIFrameElement | null>,
  renewSession: () => Promise<unknown>,
  enabled = true,
): void {
  const renewRef = useRef(renewSession)
  renewRef.current = renewSession
  useEffect(() => {
    if (!enabled) return
    return installEmbeddedAuthBridge(
      () => frameRef.current?.contentWindow,
      () => renewRef.current(),
    )
  }, [enabled, frameRef])
}
