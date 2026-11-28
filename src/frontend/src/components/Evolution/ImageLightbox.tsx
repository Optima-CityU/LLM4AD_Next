import {
  ChevronLeft,
  ChevronRight,
  ImageOff,
  Maximize,
  Minus,
  Plus,
  Scan,
  X,
} from "lucide-react"
import { Dialog as DialogPrimitive } from "radix-ui"
import {
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react"
import { useTranslation } from "react-i18next"

import type { EvaluationFile } from "@/client"
import { cn } from "@/lib/utils"

import {
  type ArtifactProps,
  DownloadArtifact,
  useArtifactContent,
} from "./artifactContent"

/** Largest zoom factor relative to the image's natural pixels. */
const MAX_SCALE = 32
/** Zoom factor applied by the toolbar buttons and +/- keys. */
const STEP = 1.25

interface View {
  /** "fit" tracks the stage size; "free" keeps an explicit scale and pan. */
  mode: "fit" | "free"
  scale: number
  x: number
  y: number
}

const FIT_VIEW: View = { mode: "fit", scale: 1, x: 0, y: 0 }

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value))
}

/** Track an element's content box size. */
function useElementSize<T extends HTMLElement>() {
  const ref = useRef<T>(null)
  const [size, setSize] = useState({ width: 0, height: 0 })
  useLayoutEffect(() => {
    const element = ref.current
    if (!element) return
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect
      setSize({ width, height })
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [])
  return [ref, size] as const
}

/**
 * Zoom and pan state for one image inside a stage.
 *
 * Args:
 *   stage: Current stage size in CSS pixels.
 *   natural: Natural image size, or zero before the image has loaded.
 *
 * Returns:
 *   The effective scale and pan plus actions that keep the image in bounds.
 */
function useZoomView(
  stage: { width: number; height: number },
  natural: { width: number; height: number },
) {
  const [view, setView] = useState<View>(FIT_VIEW)
  const ready = natural.width > 0 && stage.width > 0
  const fitScale = ready
    ? Math.min(stage.width / natural.width, stage.height / natural.height)
    : 1
  const minScale = Math.min(fitScale, 1)
  const scale = view.mode === "fit" ? fitScale : view.scale

  const bound = useCallback(
    (nextScale: number, x: number, y: number) => {
      const maxX = Math.max(0, (natural.width * nextScale - stage.width) / 2)
      const maxY = Math.max(0, (natural.height * nextScale - stage.height) / 2)
      return { x: clamp(x, -maxX, maxX), y: clamp(y, -maxY, maxY) }
    },
    [natural, stage],
  )

  /**
   * Zoom to a scale, keeping the stage point (px, py) fixed.
   *
   * A function target receives the latest scale, so bursts of wheel or pinch
   * events within one frame compound instead of reusing a stale render value.
   */
  const zoomTo = useCallback(
    (target: number | ((current: number) => number), px = 0, py = 0) => {
      if (!ready) return
      setView((prev) => {
        const current = prev.mode === "fit" ? fitScale : prev.scale
        const wanted = typeof target === "function" ? target(current) : target
        const nextScale = clamp(wanted, minScale, MAX_SCALE)
        if (Math.abs(nextScale - fitScale) < 1e-3) return FIT_VIEW
        const ratio = nextScale / current
        const base = prev.mode === "fit" ? { x: 0, y: 0 } : prev
        const { x, y } = bound(
          nextScale,
          px + (base.x - px) * ratio,
          py + (base.y - py) * ratio,
        )
        return { mode: "free", scale: nextScale, x, y }
      })
    },
    [bound, fitScale, minScale, ready],
  )

  const zoomBy = useCallback(
    (factor: number, px = 0, py = 0) =>
      zoomTo((current) => current * factor, px, py),
    [zoomTo],
  )

  const panBy = useCallback(
    (dx: number, dy: number) =>
      setView((prev) =>
        prev.mode === "fit"
          ? prev
          : { ...prev, ...bound(prev.scale, prev.x + dx, prev.y + dy) },
      ),
    [bound],
  )

  return {
    ready,
    scale,
    x: view.mode === "fit" ? 0 : view.x,
    y: view.mode === "fit" ? 0 : view.y,
    isFit: view.mode === "fit",
    canZoomIn: scale < MAX_SCALE - 1e-3,
    canZoomOut: scale > minScale + 1e-3,
    zoomTo,
    zoomBy,
    panBy,
    fit: () => setView(FIT_VIEW),
  }
}

const TOOL_BUTTON =
  "inline-flex size-8 shrink-0 items-center justify-center rounded-md text-white/75 transition-colors hover:bg-white/10 hover:text-white focus-visible:outline-2 focus-visible:outline-white disabled:pointer-events-none disabled:opacity-30 [&_svg]:size-4"

interface ViewerProps extends ArtifactProps {
  index: number
  total: number
  /** Called once the original has rendered or definitively failed. */
  onSettled: () => void
}

/** Top bar and zoomable stage for the current image. */
function Viewer(props: ViewerProps) {
  const { t } = useTranslation()
  const { file, saved, index, total } = props
  const original = useArtifactContent(props, true, "original")
  // The gallery has usually cached this already; it bridges the original's load.
  // GIFs have no server thumbnail, so the request would fetch the original twice.
  const thumb = useArtifactContent(
    props,
    file.media_type !== "image/gif",
    "thumb",
  )
  const [stageRef, stage] = useElementSize<HTMLDivElement>()
  const [natural, setNatural] = useState({ width: 0, height: 0 })
  const zoom = useZoomView(stage, natural)
  // Animate discrete actions (buttons, keys, double-click), not wheel or drag.
  const [smooth, setSmooth] = useState(false)
  const [dragging, setDragging] = useState(false)
  const pointers = useRef(new Map<number, { x: number; y: number }>())
  const pinch = useRef(0)
  const name = file.name.split("/").pop() ?? file.name

  /** Convert a client point into stage-center coordinates. */
  const toStage = useCallback(
    (clientX: number, clientY: number) => {
      const rect = stageRef.current?.getBoundingClientRect()
      if (!rect) return { x: 0, y: 0 }
      return {
        x: clientX - rect.left - rect.width / 2,
        y: clientY - rect.top - rect.height / 2,
      }
    },
    [stageRef],
  )

  const toggleActual = useCallback(
    (px = 0, py = 0) => {
      setSmooth(true)
      if (!zoom.isFit) zoom.fit()
      // Images already shown at 1:1 when fitted zoom to 2x instead.
      else zoom.zoomTo(Math.abs(zoom.scale - 1) < 1e-3 ? 2 : 1, px, py)
    },
    [zoom],
  )

  // Wheel zoom needs a non-passive listener so the page never scrolls.
  const zoomByRef = useRef(zoom.zoomBy)
  zoomByRef.current = zoom.zoomBy
  useEffect(() => {
    const element = stageRef.current
    if (!element) return
    const onWheel = (event: WheelEvent) => {
      event.preventDefault()
      const delta = event.deltaY * (event.deltaMode === 1 ? 16 : 1)
      // Trackpad pinch arrives as ctrl+wheel with small deltas.
      const speed = event.ctrlKey ? 0.01 : 0.0015
      const point = toStage(event.clientX, event.clientY)
      setSmooth(false)
      zoomByRef.current(Math.exp(-delta * speed), point.x, point.y)
    }
    element.addEventListener("wheel", onWheel, { passive: false })
    return () => element.removeEventListener("wheel", onWheel)
  }, [stageRef, toStage])

  // Zoom shortcuts; arrow keys and Escape are handled by the lightbox shell.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.ctrlKey || event.metaKey || event.altKey) return
      const actions: Record<string, () => void> = {
        "+": () => zoom.zoomBy(STEP),
        "=": () => zoom.zoomBy(STEP),
        "-": () => zoom.zoomBy(1 / STEP),
        "0": () => zoom.fit(),
        "1": () => zoom.zoomTo(1),
      }
      const action = actions[event.key]
      if (!action) return
      event.preventDefault()
      setSmooth(true)
      action()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [zoom])

  const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return
    event.currentTarget.setPointerCapture(event.pointerId)
    pointers.current.set(event.pointerId, {
      x: event.clientX,
      y: event.clientY,
    })
    if (pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()]
      pinch.current = Math.hypot(a.x - b.x, a.y - b.y)
    }
    setSmooth(false)
    setDragging(true)
  }

  const onPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const previous = pointers.current.get(event.pointerId)
    if (!previous) return
    const current = { x: event.clientX, y: event.clientY }
    pointers.current.set(event.pointerId, current)
    if (pointers.current.size === 1) {
      zoom.panBy(current.x - previous.x, current.y - previous.y)
      return
    }
    const [a, b] = [...pointers.current.values()]
    const distance = Math.hypot(a.x - b.x, a.y - b.y)
    if (pinch.current > 0) {
      const mid = toStage((a.x + b.x) / 2, (a.y + b.y) / 2)
      zoom.zoomBy(distance / pinch.current, mid.x, mid.y)
    }
    pinch.current = distance
  }

  const onPointerUp = (event: ReactPointerEvent<HTMLDivElement>) => {
    pointers.current.delete(event.pointerId)
    if (pointers.current.size < 2) pinch.current = 0
    if (pointers.current.size === 0) setDragging(false)
  }

  const [decodeFailed, setDecodeFailed] = useState(false)
  const failed = !saved || original.isError || decodeFailed
  const { onSettled } = props
  useEffect(() => {
    if (zoom.ready || failed) onSettled()
  }, [zoom.ready, failed, onSettled])
  const percent = `${Math.round(zoom.scale * 100)}%`
  return (
    <>
      <header className="flex h-14 shrink-0 items-center gap-3 border-b border-white/10 px-4">
        <div className="flex min-w-0 flex-1 flex-col">
          <DialogPrimitive.Title
            className="truncate text-sm font-medium text-white"
            title={file.name}
          >
            {name}
          </DialogPrimitive.Title>
          <DialogPrimitive.Description className="truncate text-xs text-white/55">
            {[
              total > 1 ? `${index + 1} / ${total}` : "",
              zoom.ready ? `${natural.width} × ${natural.height}` : "",
              file.description,
            ]
              .filter(Boolean)
              .join(" · ")}
          </DialogPrimitive.Description>
        </div>
        <div className="flex items-center rounded-lg bg-white/5 p-0.5">
          <button
            type="button"
            className={TOOL_BUTTON}
            disabled={!zoom.ready || !zoom.canZoomOut}
            onClick={() => {
              setSmooth(true)
              zoom.zoomBy(1 / STEP)
            }}
            title={`${t("evaluationData.lightbox.zoomOut")} (-)`}
            aria-label={t("evaluationData.lightbox.zoomOut")}
          >
            <Minus />
          </button>
          <button
            type="button"
            className="h-8 min-w-14 rounded-md px-1 text-xs tabular-nums text-white/85 transition-colors hover:bg-white/10 focus-visible:outline-2 focus-visible:outline-white disabled:opacity-30"
            disabled={!zoom.ready}
            onClick={() => {
              setSmooth(true)
              zoom.fit()
            }}
            title={`${t("evaluationData.lightbox.fit")} (0)`}
            aria-label={t("evaluationData.lightbox.zoomLevel", {
              value: percent,
            })}
          >
            {percent}
          </button>
          <button
            type="button"
            className={TOOL_BUTTON}
            disabled={!zoom.ready || !zoom.canZoomIn}
            onClick={() => {
              setSmooth(true)
              zoom.zoomBy(STEP)
            }}
            title={`${t("evaluationData.lightbox.zoomIn")} (+)`}
            aria-label={t("evaluationData.lightbox.zoomIn")}
          >
            <Plus />
          </button>
        </div>
        <button
          type="button"
          className={TOOL_BUTTON}
          disabled={!zoom.ready}
          onClick={() => toggleActual()}
          title={`${
            zoom.isFit
              ? t("evaluationData.lightbox.actualSize")
              : t("evaluationData.lightbox.fit")
          } (${zoom.isFit ? "1" : "0"})`}
          aria-label={
            zoom.isFit
              ? t("evaluationData.lightbox.actualSize")
              : t("evaluationData.lightbox.fit")
          }
        >
          {zoom.isFit ? <Scan /> : <Maximize />}
        </button>
        <DownloadArtifact {...props} className={TOOL_BUTTON} />
        <DialogPrimitive.Close
          className={TOOL_BUTTON}
          title={`${t("evaluationData.lightbox.close")} (Esc)`}
          aria-label={t("evaluationData.lightbox.close")}
        >
          <X />
        </DialogPrimitive.Close>
      </header>
      {/* Pointer-driven canvas; keyboard equivalents live in the toolbar and shortcuts. */}
      <div
        ref={stageRef}
        role="application"
        aria-label={t("evaluationData.lightbox.title")}
        className={cn(
          "relative min-h-0 flex-1 touch-none select-none overflow-hidden",
          zoom.ready &&
            !zoom.isFit &&
            (dragging ? "cursor-grabbing" : "cursor-grab"),
        )}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onDoubleClick={(event) => {
          const point = toStage(event.clientX, event.clientY)
          toggleActual(point.x, point.y)
        }}
      >
        {failed ? (
          <div className="flex size-full flex-col items-center justify-center gap-2 text-sm text-white/55">
            <ImageOff className="size-8" />
            {saved
              ? t("evaluationData.previewUnavailable")
              : t("evaluationData.pendingFile")}
          </div>
        ) : (
          <>
            {!zoom.ready && thumb.imageUrl && (
              <img
                src={thumb.imageUrl}
                alt=""
                aria-hidden
                className="absolute inset-0 size-full object-contain opacity-60 blur-sm"
              />
            )}
            {!zoom.ready && (
              <div className="absolute inset-0 flex items-center justify-center">
                <div className="size-8 animate-spin rounded-full border-2 border-white/20 border-t-white/80" />
              </div>
            )}
            {original.imageUrl && (
              <img
                src={original.imageUrl}
                alt={file.description || name}
                draggable={false}
                onLoad={(event) =>
                  setNatural({
                    width: event.currentTarget.naturalWidth,
                    height: event.currentTarget.naturalHeight,
                  })
                }
                onError={() => setDecodeFailed(true)}
                className={cn(
                  "pointer-events-none absolute top-1/2 left-1/2 max-w-none origin-center",
                  smooth &&
                    "transition-transform duration-200 ease-out motion-reduce:transition-none",
                  !zoom.ready && "opacity-0",
                )}
                style={{
                  width: natural.width || undefined,
                  height: natural.height || undefined,
                  transform: `translate(-50%, -50%) translate(${zoom.x}px, ${zoom.y}px) scale(${zoom.scale})`,
                  // Show real pixels when inspecting rendering detail up close.
                  imageRendering: zoom.scale >= 2 ? "pixelated" : "auto",
                }}
              />
            )}
          </>
        )}
      </div>
    </>
  )
}

/** Keep a neighbour's original in the query cache so paging feels instant. */
function Preload(props: ArtifactProps) {
  useArtifactContent(props, props.saved, "original")
  return null
}

function StripItem(
  props: ArtifactProps & {
    active: boolean
    /** False until the current original has loaded, so it gets the bandwidth first. */
    enabled: boolean
    onSelect: () => void
  },
) {
  const ref = useRef<HTMLButtonElement>(null)
  // Only fetch thumbnails scrolled into (or near) the strip's visible range.
  const [seen, setSeen] = useState(false)
  useEffect(() => {
    const element = ref.current
    if (seen || !element) return
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setSeen(true)
          observer.disconnect()
        }
      },
      { root: element.closest("nav"), rootMargin: "0px 240px" },
    )
    observer.observe(element)
    return () => observer.disconnect()
  }, [seen])
  const thumb = useArtifactContent(
    props,
    props.saved && props.enabled && seen,
    "thumb",
  )
  useEffect(() => {
    if (props.active)
      ref.current?.scrollIntoView({ block: "nearest", inline: "center" })
  }, [props.active])
  return (
    <button
      ref={ref}
      type="button"
      aria-current={props.active || undefined}
      aria-label={props.file.name}
      title={props.file.name}
      onClick={props.onSelect}
      className={cn(
        "relative h-14 w-20 shrink-0 overflow-hidden rounded-md bg-white/5 ring-offset-2 ring-offset-black transition focus-visible:outline-2 focus-visible:outline-white",
        props.active
          ? "opacity-100 ring-2 ring-white"
          : "opacity-45 hover:opacity-80",
      )}
    >
      {thumb.imageUrl ? (
        <img
          src={thumb.imageUrl}
          alt=""
          draggable={false}
          className="size-full object-cover"
        />
      ) : !props.saved || thumb.isError ? (
        <ImageOff className="m-auto size-4 text-white/40" />
      ) : (
        <div className="size-full animate-pulse bg-white/10" />
      )}
    </button>
  )
}

const NAV_BUTTON =
  "absolute top-1/2 z-10 flex size-11 -translate-y-1/2 items-center justify-center rounded-full bg-black/45 text-white/85 backdrop-blur transition hover:bg-black/70 hover:text-white focus-visible:outline-2 focus-visible:outline-white disabled:pointer-events-none disabled:opacity-0 [&_svg]:size-6"

interface ImageLightboxProps {
  /** Images in gallery order; the lightbox pages through all of them. */
  files: EvaluationFile[]
  openedId: string
  taskId: string
  evaluationId: string
  availableIds: string[]
  onClose: () => void
}

/** Full-screen image viewer with paging, zoom, pan and a filmstrip. */
export default function ImageLightbox(props: ImageLightboxProps) {
  const { t } = useTranslation()
  const { files, taskId, evaluationId, availableIds, onClose } = props
  const [currentId, setCurrentId] = useState(props.openedId)
  const index = Math.max(
    0,
    files.findIndex((file) => file.id === currentId),
  )
  const current = files[index]
  // Id of the latest original that finished loading. Until the first one does,
  // the strip waits; neighbour preloads wait for whichever image is current.
  const [settledId, setSettledId] = useState<string | null>(null)
  const shownId = current?.id
  const markSettled = useCallback(() => {
    if (shownId) setSettledId(shownId)
  }, [shownId])
  const propsFor = (file: EvaluationFile): ArtifactProps => ({
    taskId,
    evaluationId,
    file,
    saved: availableIds.includes(file.id),
  })
  const go = (offset: number) => {
    const next = files[index + offset]
    if (next) setCurrentId(next.id)
  }
  const onKeyDown = (event: ReactKeyboardEvent) => {
    if (event.ctrlKey || event.metaKey || event.altKey) return
    if (event.key === "ArrowLeft") go(-1)
    else if (event.key === "ArrowRight") go(1)
    else if (event.key === "Home") setCurrentId(files[0].id)
    else if (event.key === "End") setCurrentId(files[files.length - 1].id)
    else return
    event.preventDefault()
  }
  if (!current) return null
  const neighbours =
    settledId === current.id
      ? [files[index - 1], files[index + 1]].filter(
          (file): file is EvaluationFile => Boolean(file),
        )
      : []

  return (
    <DialogPrimitive.Root open onOpenChange={(open) => !open && onClose()}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/90 backdrop-blur-sm data-[state=open]:animate-in data-[state=open]:fade-in-0" />
        <DialogPrimitive.Content
          className="fixed inset-0 z-50 flex flex-col text-white outline-none data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-98"
          onKeyDown={onKeyDown}
          // Focus the viewer itself so no toolbar button shows a stray focus ring.
          onOpenAutoFocus={(event) => {
            event.preventDefault()
            ;(event.currentTarget as HTMLElement | null)?.focus()
          }}
        >
          <div className="relative flex min-h-0 flex-1 flex-col">
            {/* Remount per image so zoom, pan and load state start fresh. */}
            <Viewer
              key={current.id}
              {...propsFor(current)}
              index={index}
              total={files.length}
              onSettled={markSettled}
            />
            {files.length > 1 && (
              <>
                <button
                  type="button"
                  className={cn(NAV_BUTTON, "left-4 mt-7")}
                  disabled={index === 0}
                  onClick={() => go(-1)}
                  title={`${t("evaluationData.lightbox.previous")} (←)`}
                  aria-label={t("evaluationData.lightbox.previous")}
                >
                  <ChevronLeft />
                </button>
                <button
                  type="button"
                  className={cn(NAV_BUTTON, "right-4 mt-7")}
                  disabled={index === files.length - 1}
                  onClick={() => go(1)}
                  title={`${t("evaluationData.lightbox.next")} (→)`}
                  aria-label={t("evaluationData.lightbox.next")}
                >
                  <ChevronRight />
                </button>
              </>
            )}
          </div>
          {files.length > 1 && (
            <nav
              className="flex shrink-0 justify-center-safe gap-2 overflow-x-auto border-t border-white/10 px-4 py-3 [scrollbar-width:none]"
              aria-label={t("evaluationData.lightbox.strip")}
            >
              {files.map((file) => (
                <StripItem
                  key={file.id}
                  {...propsFor(file)}
                  active={file.id === current.id}
                  enabled={settledId !== null}
                  onSelect={() => setCurrentId(file.id)}
                />
              ))}
            </nav>
          )}
          <p
            className={cn(
              "pointer-events-none absolute left-1/2 hidden -translate-x-1/2 rounded-full bg-black/55 px-3 py-1 text-xs text-white/60 md:block",
              files.length > 1 ? "bottom-24" : "bottom-6",
            )}
          >
            {t("evaluationData.lightbox.hint")}
          </p>
          {neighbours.map((file) => (
            <Preload key={file.id} {...propsFor(file)} />
          ))}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
