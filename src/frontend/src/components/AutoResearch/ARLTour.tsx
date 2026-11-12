import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useSyncExternalStore,
} from "react"
import { useTranslation } from "react-i18next"

import OnboardingTour, {
  type TourStep,
} from "@/components/Onboarding/OnboardingTour"

/** Storage id of the ARL first-visit tour (see `tourStorage`). */
export const ARL_TOUR_ID = "autoresearch-arl"

/**
 * Module-level bridge between the header help menu (rendered by the layout)
 * and the tour (rendered by the page). The page publishes whether an ARL
 * session is on screen; the menu bumps `replayToken` to restart the tour.
 */
const store = {
  available: false,
  replayToken: 0,
  listeners: new Set<() => void>(),
}

function emit() {
  for (const listener of store.listeners) listener()
}

function subscribe(listener: () => void) {
  store.listeners.add(listener)
  return () => {
    store.listeners.delete(listener)
  }
}

function setTourAvailable(available: boolean) {
  if (store.available === available) return
  store.available = available
  emit()
}

function setReplayToken(token: number) {
  if (store.replayToken === token) return
  store.replayToken = token
  emit()
}

/** Restart the ARL tour from its first step. */
export function replayARLTour() {
  setReplayToken(store.replayToken + 1)
}

/** Whether an ARL session is on screen, i.e. the tour can be replayed. */
export function useARLTourAvailable(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => store.available,
    () => false,
  )
}

function useReplayToken(): number {
  return useSyncExternalStore(
    subscribe,
    () => store.replayToken,
    () => 0,
  )
}

interface ARLTourProps {
  /** An ARL session is open and its panes are mounted. */
  enabled: boolean
  /** The right column is collapsed, so its steps have nothing to point at. */
  rightCollapsed: boolean
}

/**
 * First-visit walkthrough for ARL (autoresearch loop) sessions.
 *
 * Follows the order a new user actually works in: build the validation
 * service with the Validator, start research with the Worker, then the
 * controls that keep the loop going (permission mode, composer) and the
 * right-hand column (validation status, workspace files). Every target is
 * always mounted while an ARL session is open, so no step can stall waiting
 * for an element that only appears in some states.
 */
export default function ARLTour({ enabled, rightCollapsed }: ARLTourProps) {
  const { t } = useTranslation()
  const replayToken = useReplayToken()

  useEffect(() => {
    setTourAvailable(enabled)
    return () => setTourAvailable(false)
  }, [enabled])

  // Clear a replay request once that run of the tour ends; otherwise a later
  // session switch would re-enable the tour and play it yet again.
  const wasActive = useRef(false)
  const handleStepChange = useCallback((step: number | null) => {
    if (step !== null) {
      wasActive.current = true
    } else if (wasActive.current) {
      wasActive.current = false
      setReplayToken(0)
    }
  }, [])

  const steps = useMemo<TourStep[]>(() => {
    const list: TourStep[] = [
      {
        selector: '[data-tour="arl-pane-validator"]',
        title: t("tour.arl.validatorTitle"),
        content: t("tour.arl.validatorContent"),
        placement: "right",
      },
      {
        selector: '[data-tour="arl-pane-worker"]',
        title: t("tour.arl.workerTitle"),
        content: t("tour.arl.workerContent"),
        placement: "left",
      },
      {
        selector: '[data-tour="arl-mode-validator"]',
        title: t("tour.arl.modeTitle"),
        content: t("tour.arl.modeContent"),
        placement: "bottom",
      },
      {
        selector: '[data-tour="arl-composer-validator"]',
        title: t("tour.arl.composerTitle"),
        content: t("tour.arl.composerContent"),
        placement: "top",
      },
    ]
    if (!rightCollapsed) {
      list.push(
        {
          selector: '[data-tour="arl-agent-status"]',
          title: t("tour.arl.statusTitle"),
          content: t("tour.arl.statusContent"),
          placement: "left",
        },
        {
          selector: '[data-tour="arl-files"]',
          title: t("tour.arl.filesTitle"),
          content: t("tour.arl.filesContent"),
          placement: "left",
        },
      )
    }
    return list
  }, [t, rightCollapsed])

  return (
    <OnboardingTour
      tourId={ARL_TOUR_ID}
      steps={steps}
      enabled={enabled}
      // Give the panes a moment to lay out after the session loads.
      startDelay={500}
      replayToken={replayToken}
      onStepChange={handleStepChange}
    />
  )
}
