import { BookOpen, HelpCircle, MessageSquarePlus, Users } from "lucide-react"
import { useCallback, useEffect, useRef, useState } from "react"
import { useTranslation } from "react-i18next"

import { ContactUsDialog } from "@/components/Feedback/ContactUsDialog"
import { GITHUB_ISSUES_URL } from "@/lib/siteMetadata"
import { UserManualDialog } from "@/components/Guide/UserManualDialog"
import { cn } from "@/lib/utils"

function useDraggable(initial: { right: number; bottom: number }) {
  const [pos, setPos] = useState(initial)
  const dragging = useRef(false)
  const offset = useRef({ x: 0, y: 0 })
  const hasMoved = useRef(false)
  const SIZE = 36

  const onPointerDown = useCallback(
    (e: React.PointerEvent) => {
      dragging.current = true
      hasMoved.current = false
      const elLeft = window.innerWidth - pos.right - SIZE
      const elTop = window.innerHeight - pos.bottom - SIZE
      offset.current = { x: e.clientX - elLeft, y: e.clientY - elTop }
    },
    [pos],
  )

  const onDocumentPointerMove = useCallback((e: PointerEvent) => {
    if (!dragging.current) return
    hasMoved.current = true
    const newRight = window.innerWidth - e.clientX - (SIZE - offset.current.x)
    const newBottom = window.innerHeight - e.clientY - (SIZE - offset.current.y)
    setPos({
      right: Math.max(0, Math.min(newRight, window.innerWidth - SIZE)),
      bottom: Math.max(0, Math.min(newBottom, window.innerHeight - SIZE)),
    })
  }, [])

  const onDocumentPointerUp = useCallback(() => {
    dragging.current = false
  }, [])

  useEffect(() => {
    document.addEventListener("pointermove", onDocumentPointerMove)
    document.addEventListener("pointerup", onDocumentPointerUp)
    document.addEventListener("pointercancel", onDocumentPointerUp)
    return () => {
      document.removeEventListener("pointermove", onDocumentPointerMove)
      document.removeEventListener("pointerup", onDocumentPointerUp)
      document.removeEventListener("pointercancel", onDocumentPointerUp)
    }
  }, [onDocumentPointerMove, onDocumentPointerUp])

  return { pos, hasMoved, onPointerDown }
}

export function FeedbackFAB() {
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState(false)
  const [manualOpen, setManualOpen] = useState(false)
  const [contactOpen, setContactOpen] = useState(false)
  const collapseTimer = useRef<ReturnType<typeof setTimeout>>(undefined)

  const { pos, hasMoved, onPointerDown } = useDraggable({
    right: 14,
    bottom: 118,
  })

  const handleMouseEnter = () => {
    clearTimeout(collapseTimer.current)
    setExpanded(true)
  }

  const handleMouseLeave = () => {
    collapseTimer.current = setTimeout(() => setExpanded(false), 300)
  }

  const handleMainClick = () => {
    if (hasMoved.current) return
    setExpanded(!expanded)
  }

  const isOnRight = pos.right < window.innerWidth / 2

  return (
    <>
      <div
        className="fixed z-50"
        style={{ right: pos.right, bottom: pos.bottom }}
        onMouseEnter={handleMouseEnter}
        onMouseLeave={handleMouseLeave}
      >
        {/* Expanded panel */}
        <div
          className={cn(
            "absolute bottom-full mb-2 transition-all duration-200 origin-bottom",
            isOnRight ? "right-0" : "left-0",
            expanded
              ? "opacity-100 scale-100 pointer-events-auto"
              : "opacity-0 scale-95 pointer-events-none",
          )}
        >
          <div className="flex flex-col gap-1.5 rounded-xl border border-border/60 bg-background/95 backdrop-blur-lg shadow-lg p-2 whitespace-nowrap">
            <a
              href={GITHUB_ISSUES_URL}
              target="_blank"
              rel="noopener noreferrer"
              className={cn(
                "flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm",
                "text-foreground/80 hover:text-foreground hover:bg-accent",
                "transition-colors duration-150",
              )}
              onClick={() => setExpanded(false)}
            >
              <MessageSquarePlus className="size-4 text-primary" />
              <span>{t("feedback.fab.submitFeedback")}</span>
            </a>
            <button
              type="button"
              className={cn(
                "flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm",
                "text-foreground/80 hover:text-foreground hover:bg-accent",
                "transition-colors duration-150",
              )}
              onClick={() => {
                setManualOpen(true)
                setExpanded(false)
              }}
            >
              <BookOpen className="size-4 text-primary" />
              <span>{t("feedback.fab.userManual")}</span>
            </button>
            <button
              type="button"
              className={cn(
                "flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm",
                "text-foreground/80 hover:text-foreground hover:bg-accent",
                "transition-colors duration-150",
              )}
              onClick={() => {
                setContactOpen(true)
                setExpanded(false)
              }}
            >
              <Users className="size-4 text-primary" />
              <span>{t("feedback.fab.contactUs")}</span>
            </button>
          </div>
        </div>

        {/* Main FAB button */}
        <button
          type="button"
          className={cn(
            "size-9 rounded-full shadow-md flex items-center justify-center",
            "bg-primary/90 text-primary-foreground",
            "hover:bg-primary hover:shadow-lg",
            "transition-all duration-150 cursor-grab active:cursor-grabbing select-none touch-none",
            "focus:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1",
          )}
          onClick={handleMainClick}
          onPointerDown={onPointerDown}
          aria-label="Help"
        >
          <HelpCircle className="size-4" />
        </button>
      </div>

      <UserManualDialog open={manualOpen} onOpenChange={setManualOpen} />
      <ContactUsDialog open={contactOpen} onOpenChange={setContactOpen} />
    </>
  )
}
