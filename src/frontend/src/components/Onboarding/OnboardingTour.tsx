import { useEffect, useLayoutEffect, useRef, useState } from "react"
import { createPortal } from "react-dom"
import { useTranslation } from "react-i18next"
import { getDemoState } from "@/hooks/useDemoMode"
import { isTourDone, markAllToursDone, markTourDone } from "./tourStorage"

export interface TourStep {
  /** CSS selector for the target element to highlight. */
  selector: string
  /** Step title shown in the tooltip header. */
  title: string
  /** Step description. */
  content: string
  /** Optional preferred placement; defaults to auto-detect. */
  placement?: "top" | "bottom" | "left" | "right"
  /**
   * Optional hook fired when this step becomes active. Used by demo mode to
   * advance the simulated task phase as the user clicks through the tour.
   */
  onEnter?: () => void
}

interface OnboardingTourProps {
  tourId: string
  steps: TourStep[]
  /** Whether the tour is allowed to run (e.g. data ready). Defaults to true. */
  enabled?: boolean
  /** Delay (ms) before searching for the first target. */
  startDelay?: number
  /**
   * If true this tour is treated as the demo-mode walkthrough — it runs only
   * while demo mode is active. All other tours are suppressed during demo mode
   * to avoid stacking with the demo walkthrough.
   */
  isDemoTour?: boolean
  onStepChange?: (stepIndex: number | null) => void
  stepIndex?: number
  onStepIndexChange?: (stepIndex: number) => void
}

interface Rect {
  top: number
  left: number
  width: number
  height: number
}

const PADDING = 6
const TOOLTIP_WIDTH = 320
const TOOLTIP_HEIGHT_EST = 220
const TOOLTIP_GAP = 12
const VIEWPORT_MARGIN = 12

function getRect(el: Element): Rect {
  const r = el.getBoundingClientRect()
  return { top: r.top, left: r.left, width: r.width, height: r.height }
}

function computeTooltipPosition(
  rect: Rect,
  preferred: TourStep["placement"] | undefined,
  tooltipHeight: number,
): {
  top: number
  left: number
  placement: "top" | "bottom" | "left" | "right"
} {
  const vw = window.innerWidth
  const vh = window.innerHeight
  const spaceBottom = vh - (rect.top + rect.height)
  const spaceTop = rect.top
  const spaceRight = vw - (rect.left + rect.width)
  const spaceLeft = rect.left

  const needV = tooltipHeight + TOOLTIP_GAP + VIEWPORT_MARGIN
  const needH = TOOLTIP_WIDTH + TOOLTIP_GAP + VIEWPORT_MARGIN

  let placement: "top" | "bottom" | "left" | "right" = preferred ?? "bottom"
  if (!preferred) {
    if (spaceBottom >= needV) placement = "bottom"
    else if (spaceTop >= needV) placement = "top"
    else if (spaceRight >= needH) placement = "right"
    else if (spaceLeft >= needH) placement = "left"
    else {
      // Spotlight nearly fills the viewport — pick the side with most space;
      // the tooltip will overlap the spotlight but stay fully on-screen.
      const max = Math.max(spaceBottom, spaceTop, spaceRight, spaceLeft)
      if (spaceBottom === max) placement = "bottom"
      else if (spaceTop === max) placement = "top"
      else if (spaceRight === max) placement = "right"
      else placement = "left"
    }
  }

  let top = 0
  let left = 0
  switch (placement) {
    case "top":
      top = rect.top - TOOLTIP_GAP - tooltipHeight
      left = rect.left + rect.width / 2 - TOOLTIP_WIDTH / 2
      break
    case "bottom":
      top = rect.top + rect.height + TOOLTIP_GAP
      left = rect.left + rect.width / 2 - TOOLTIP_WIDTH / 2
      break
    case "left":
      top = rect.top + rect.height / 2 - tooltipHeight / 2
      left = rect.left - TOOLTIP_GAP - TOOLTIP_WIDTH
      break
    case "right":
      top = rect.top + rect.height / 2 - tooltipHeight / 2
      left = rect.left + rect.width + TOOLTIP_GAP
      break
  }
  // Clamp using real tooltip dimensions so the buttons stay reachable.
  left = Math.max(
    VIEWPORT_MARGIN,
    Math.min(left, vw - TOOLTIP_WIDTH - VIEWPORT_MARGIN),
  )
  top = Math.max(
    VIEWPORT_MARGIN,
    Math.min(top, vh - tooltipHeight - VIEWPORT_MARGIN),
  )
  return { top, left, placement }
}

export default function OnboardingTour({
  tourId,
  steps,
  enabled = true,
  startDelay = 200,
  isDemoTour = false,
  onStepChange,
  stepIndex: controlledStepIndex,
  onStepIndexChange,
}: OnboardingTourProps) {
  const { t } = useTranslation()
  const [active, setActive] = useState(false)
  const [uncontrolledStepIndex, setUncontrolledStepIndex] = useState(0)
  const [rect, setRect] = useState<Rect | null>(null)
  const [tooltipHeight, setTooltipHeight] = useState(TOOLTIP_HEIGHT_EST)
  const [tooltipPos, setTooltipPos] = useState<{
    top: number
    left: number
    placement: "top" | "bottom" | "left" | "right"
  } | null>(null)
  const rafRef = useRef<number | null>(null)
  const tooltipRef = useRef<HTMLDivElement | null>(null)

  // Decide whether to start the tour.
  useEffect(() => {
    if (!enabled) return
    if (isTourDone(tourId)) return
    // Demo-mode coordination: while demo is active, only the demo tour itself
    // may run; while demo is inactive, demo-only tours stay dormant.
    const demoActive = getDemoState().active
    if (demoActive && !isDemoTour) return
    if (!demoActive && isDemoTour) return
    const t = window.setTimeout(() => setActive(true), startDelay)
    return () => window.clearTimeout(t)
  }, [enabled, tourId, startDelay, isDemoTour])

  // A tour can lose its target while a parent dialog closes, a task changes,
  // or the prerequisite data becomes unavailable. Explicitly end it so a
  // portal overlay can never outlive the UI it is explaining.
  useEffect(() => {
    if (enabled) return
    setActive(false)
    setRect(null)
    setTooltipPos(null)
  }, [enabled])

  const stepIndex = controlledStepIndex ?? uncontrolledStepIndex
  const setStepIndex = (nextStepIndex: number) => {
    if (controlledStepIndex === undefined) setUncontrolledStepIndex(nextStepIndex)
    onStepIndexChange?.(nextStepIndex)
  }
  const currentStep = steps[stepIndex]

  // Notify the active step that it has become current. Wrapped in an effect
  // (not inline in render) so demo-mode side effects don't fire during render.
  // biome-ignore lint/correctness/useExhaustiveDependencies: only re-fire on step change
  useEffect(() => {
    if (!active || !currentStep) return
    currentStep.onEnter?.()
  }, [active, stepIndex])

  useEffect(() => {
    onStepChange?.(active ? stepIndex : null)
  }, [active, onStepChange, stepIndex])

  // Locate the current target element and recompute geometry.
  useLayoutEffect(() => {
    if (!active || !currentStep) return

    let cancelled = false
    let frame: number | null = null

    const updateTarget = () => {
      if (cancelled) return
      const el = document.querySelector(currentStep.selector)
      if (el) {
        const r = getRect(el)
        setRect(r)
        setTooltipPos(
          computeTooltipPosition(r, currentStep.placement, tooltipHeight),
        )
        if (r.top < 0 || r.top + r.height > window.innerHeight) {
          ;(el as HTMLElement).scrollIntoView({
            block: "center",
            behavior: "smooth",
          })
        }
      } else {
        setRect(null)
        setTooltipPos(null)
      }
      frame = requestAnimationFrame(updateTarget)
    }
    updateTarget()

    return () => {
      cancelled = true
      if (frame !== null) cancelAnimationFrame(frame)
      if (rafRef.current) cancelAnimationFrame(rafRef.current)
    }
  }, [active, currentStep, tooltipHeight])

  // Measure the tooltip's actual height after render so the clamp keeps it on-screen.
  useLayoutEffect(() => {
    const el = tooltipRef.current
    if (!el) return
    const measure = () => {
      const h = el.getBoundingClientRect().height
      if (h > 0 && Math.abs(h - tooltipHeight) > 1) setTooltipHeight(h)
    }
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [tooltipHeight])

  useEffect(() => {
    if (!active || !tooltipPos) return
    const frame = window.requestAnimationFrame(() => tooltipRef.current?.focus())
    return () => window.cancelAnimationFrame(frame)
  }, [active, tooltipPos])

  if (!active || !currentStep || !rect || !tooltipPos) return null

  const isLast = stepIndex === steps.length - 1
  const finish = () => {
    markTourDone(tourId)
    setActive(false)
  }
  const skipAll = () => {
    markAllToursDone()
    setActive(false)
  }
  const next = () => {
    if (isLast) finish()
    else setStepIndex(stepIndex + 1)
  }
  const prev = () => setStepIndex(Math.max(0, stepIndex - 1))

  // Spotlight cutout via four overlay rectangles around the target.
  const overlayBg = "rgba(0, 8, 24, 0.55)"
  const isDark =
    typeof document !== "undefined" &&
    document.documentElement.classList.contains("dark")
  const ringColor = isDark
    ? "rgba(0, 212, 255, 0.85)"
    : "rgba(37, 99, 235, 0.9)"
  const ringGlow = isDark
    ? "rgba(0, 212, 255, 0.45)"
    : "rgba(37, 99, 235, 0.35)"
  const tooltipBorder = isDark
    ? "rgba(0, 212, 255, 0.35)"
    : "rgba(37, 99, 235, 0.25)"
  const cutout = {
    top: rect.top - PADDING,
    left: rect.left - PADDING,
    width: rect.width + PADDING * 2,
    height: rect.height + PADDING * 2,
  }

  return createPortal(
    <div
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 9999,
        pointerEvents: "none",
      }}
    >
      {/* Top */}
      <div
        style={{
          position: "fixed",
          top: 0,
          left: 0,
          right: 0,
          height: Math.max(0, cutout.top),
          background: overlayBg,
          pointerEvents: "auto",
        }}
      />
      {/* Bottom */}
      <div
        style={{
          position: "fixed",
          top: cutout.top + cutout.height,
          left: 0,
          right: 0,
          bottom: 0,
          background: overlayBg,
          pointerEvents: "auto",
        }}
      />
      {/* Left */}
      <div
        style={{
          position: "fixed",
          top: cutout.top,
          left: 0,
          width: Math.max(0, cutout.left),
          height: cutout.height,
          background: overlayBg,
          pointerEvents: "auto",
        }}
      />
      {/* Right */}
      <div
        style={{
          position: "fixed",
          top: cutout.top,
          left: cutout.left + cutout.width,
          right: 0,
          height: cutout.height,
          background: overlayBg,
          pointerEvents: "auto",
        }}
      />

      {/* Highlight ring */}
      <div
        style={{
          position: "fixed",
          top: cutout.top,
          left: cutout.left,
          width: cutout.width,
          height: cutout.height,
          borderRadius: 8,
          boxShadow: `0 0 0 2px ${ringColor}, 0 0 24px 4px ${ringGlow}`,
          pointerEvents: "none",
          transition: "all 0.2s ease",
        }}
      />

      {/* Tooltip */}
      <div
        ref={tooltipRef}
        role="dialog"
        aria-modal="true"
        tabIndex={-1}
        style={{
          position: "fixed",
          top: tooltipPos.top,
          left: tooltipPos.left,
          width: TOOLTIP_WIDTH,
          background: "var(--popover)",
          color: "var(--popover-foreground)",
          border: `1px solid ${tooltipBorder}`,
          borderRadius: 10,
          padding: 16,
          boxShadow: isDark
            ? "0 12px 32px rgba(0, 0, 0, 0.45), 0 0 16px rgba(0, 212, 255, 0.15)"
            : "0 12px 32px rgba(15, 23, 42, 0.18), 0 0 16px rgba(37, 99, 235, 0.08)",
          pointerEvents: "auto",
          fontSize: 13,
          lineHeight: 1.55,
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginBottom: 6,
          }}
        >
          <div style={{ fontSize: 14, fontWeight: 600 }}>
            {currentStep.title}
          </div>
          <div style={{ fontSize: 11, opacity: 0.6 }}>
            {stepIndex + 1} / {steps.length}
          </div>
        </div>
        <div style={{ marginBottom: 14, opacity: 0.92 }}>
          {currentStep.content}
        </div>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 8,
          }}
        >
          <div style={{ display: "flex", gap: 8 }}>
            <button
              type="button"
              onClick={finish}
              style={{
                fontSize: 11,
                color: "var(--muted-foreground)",
                background: "transparent",
                border: "none",
                cursor: "pointer",
                padding: 0,
                textDecoration: "underline",
              }}
            >
              {t("tour.skipPage")}
            </button>
            <button
              type="button"
              onClick={skipAll}
              style={{
                fontSize: 11,
                color: "var(--muted-foreground)",
                opacity: 0.75,
                background: "transparent",
                border: "none",
                cursor: "pointer",
                padding: 0,
                textDecoration: "underline",
              }}
            >
              {t("tour.skipAll")}
            </button>
          </div>
          <div style={{ display: "flex", gap: 6 }}>
            {stepIndex > 0 && (
              <button
                type="button"
                onClick={prev}
                style={{
                  fontSize: 12,
                  padding: "5px 12px",
                  borderRadius: 6,
                  border: "1px solid var(--border)",
                  background: "transparent",
                  color: "var(--foreground)",
                  cursor: "pointer",
                }}
              >
                {t("tour.prev")}
              </button>
            )}
            <button
              type="button"
              onClick={next}
              style={{
                fontSize: 12,
                padding: "5px 14px",
                borderRadius: 6,
                border: "1px solid var(--primary)",
                background: "var(--primary)",
                color: "var(--primary-foreground)",
                cursor: "pointer",
                fontWeight: 500,
              }}
            >
              {isLast ? t("tour.finish") : t("tour.next")}
            </button>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  )
}
