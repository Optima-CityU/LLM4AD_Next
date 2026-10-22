import {
  ArrowDown,
  Check,
  ChevronDown,
  Loader2,
  Send,
  Shield,
  ShieldCheck,
  Square,
  Zap,
} from "lucide-react"
import { useCallback, useEffect, useRef, useState } from "react"
import { useTranslation } from "react-i18next"

import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import type {
  ARLPermissionRequest,
  ARLRole,
  ARLRoleState,
} from "@/hooks/useARLWebSocket"
import { isUnattendedMode } from "@/hooks/useARLWebSocket"
import { useHljsTheme } from "@/hooks/useHljsTheme"
import { cn } from "@/lib/utils"

import ARLMessageItem from "./ARLMessageItem"

/** Distance from the bottom (px) within which new content auto-scrolls. */
const NEAR_BOTTOM_THRESHOLD = 120

interface Props {
  role: ARLRole
  state: ARLRoleState
  connected: boolean
  onSend: (role: ARLRole, content: string) => void
  onCancel: (role: ARLRole) => void
  onSetMode: (role: ARLRole, modeId: string) => void
  onPermission: (
    role: ARLRole,
    requestId: string,
    response: Record<string, unknown>,
  ) => void
  onPermissions: (
    role: ARLRole,
    requestIds: string[],
    response: Record<string, unknown>,
  ) => void
}

/**
 * One ARL container rendered as a complete chat page: scrollable history on
 * top, composer pinned at the bottom. Two of these sit side by side in
 * {@link ARLPanel}.
 *
 * The scroll region relies on `min-h-0` at every flex ancestor; without it the
 * overflowing content stretches the column instead of scrolling inside it.
 */
export default function ARLPane({
  role,
  state,
  connected,
  onSend,
  onCancel,
  onSetMode,
  onPermission,
  onPermissions,
}: Props) {
  const { t } = useTranslation()
  // Highlight.js theme is ref-counted and shared: register it once per pane
  // rather than per rendered message.
  useHljsTheme()

  const [input, setInput] = useState("")
  const [atBottom, setAtBottom] = useState(true)

  const scrollBox = useRef<HTMLDivElement | null>(null)
  const anchor = useRef<HTMLDivElement | null>(null)
  const stickToBottom = useRef(true)

  const running = state.turnStatus === "running"
  const lastItem = state.items[state.items.length - 1]
  const lastItemLength =
    lastItem &&
    (lastItem.kind === "assistant_message" || lastItem.kind === "thought")
      ? lastItem.text.length
      : 0

  const handleScroll = useCallback(() => {
    const box = scrollBox.current
    if (!box) return
    const distance = box.scrollHeight - box.scrollTop - box.clientHeight
    const near = distance <= NEAR_BOTTOM_THRESHOLD
    stickToBottom.current = near
    setAtBottom(near)
  }, [])

  // Follow new content only while the user is already near the bottom, so
  // scrolling back through history is not yanked away. Scheduled on a rAF so
  // the scroll happens after the new content has been committed to the DOM.
  // `state.items.length` / `lastItemLength` are change triggers the body does
  // not read directly (streaming chunks grow the last item in place).
  // biome-ignore lint/correctness/useExhaustiveDependencies: item count and tail text length are the change triggers
  useEffect(() => {
    if (!stickToBottom.current) return
    const frame = requestAnimationFrame(() => {
      anchor.current?.scrollIntoView({ block: "end" })
    })
    return () => cancelAnimationFrame(frame)
  }, [state.items.length, lastItemLength])

  // Jump to the newest message when the pane first fills with history.
  useEffect(() => {
    if (!state.historyLoaded) return
    const timer = setTimeout(() => {
      anchor.current?.scrollIntoView({ block: "end" })
      stickToBottom.current = true
      setAtBottom(true)
    }, 60)
    return () => clearTimeout(timer)
  }, [state.historyLoaded])

  const scrollToBottom = () => {
    anchor.current?.scrollIntoView({ behavior: "smooth", block: "end" })
    stickToBottom.current = true
    setAtBottom(true)
  }

  const submit = () => {
    const text = input.trim()
    if (!text || !connected) return
    onSend(role, text)
    setInput("")
    stickToBottom.current = true
  }

  const roleLabel = t(`autoResearch.arl.${role}`)
  const statusLabel = state.canceling
    ? t("autoResearch.arl.stopping")
    : running
      ? t("autoResearch.arl.statusRunning")
      : state.turnStatus === "failed"
        ? t("autoResearch.arl.statusFailed")
        : state.turnStatus === "canceled"
          ? t("autoResearch.arl.statusCanceled")
          : state.turnStatus === "completed"
            ? t("autoResearch.arl.statusCompleted")
            : state.agentStatus === "running"
              ? t("autoResearch.arl.statusRunning")
              : t("autoResearch.arl.statusIdle")

  // Permission mode: the container's own list, as reported by its provider.
  const autoMode = isUnattendedMode(state.modeId)
  const currentMode = state.modes.find((mode) => mode.id === state.modeId)
  const modeLabel =
    currentMode?.label ?? state.modeId ?? t("autoResearch.arl.modeUnknown")
  const unattended = state.modes.find((mode) => isUnattendedMode(mode.id))

  const pendingIds = state.permissions.map((request) => request.id)

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col border-border/60 bg-background first:border-r">
      {/* Header: role, live status, permission mode, agent id */}
      <div className="flex shrink-0 items-center gap-2 border-b border-border/60 px-4 py-2.5">
        <span
          className={cn(
            "size-2 shrink-0 rounded-full",
            running
              ? "animate-pulse bg-primary"
              : connected
                ? "bg-emerald-500"
                : "bg-muted-foreground/40",
          )}
        />
        <span className="text-sm font-semibold text-foreground">
          {roleLabel}
        </span>
        <span className="text-[11.5px] text-muted-foreground/70">
          {statusLabel}
        </span>

        {/* Permission mode picker: switching into an auto/bypass mode is what
            stops the container from asking before every single tool call. */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              size="sm"
              variant="ghost"
              disabled={!connected || state.modes.length === 0}
              className={cn(
                "ml-auto h-6 gap-1 px-1.5 text-[11.5px] font-normal",
                autoMode ? "text-emerald-600 dark:text-emerald-400" : "",
              )}
              title={currentMode?.description ?? modeLabel}
            >
              {autoMode ? (
                <ShieldCheck className="size-3.5" />
              ) : (
                <Shield className="size-3.5" />
              )}
              <span className="max-w-[9rem] truncate">{modeLabel}</span>
              <ChevronDown className="size-3 opacity-60" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-72">
            <DropdownMenuLabel className="text-[11.5px] text-muted-foreground">
              {t("autoResearch.arl.modeTitle")}
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            {state.modes.map((mode) => (
              <DropdownMenuItem
                key={mode.id}
                onSelect={() => onSetMode(role, mode.id)}
                className="flex flex-col items-start gap-0.5"
              >
                <span className="flex w-full items-center gap-1.5 text-[12.5px] font-medium">
                  {mode.id === state.modeId ? (
                    <Check className="size-3.5 text-primary" />
                  ) : (
                    <span className="size-3.5" />
                  )}
                  {mode.label}
                  {isUnattendedMode(mode.id) && (
                    <Zap className="size-3 text-amber-500" />
                  )}
                </span>
                {mode.description && (
                  <span className="pl-5 text-[11px] leading-snug text-muted-foreground">
                    {mode.description}
                  </span>
                )}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>

        <span className="truncate font-mono text-[10.5px] text-muted-foreground/40">
          {state.agentId ? state.agentId.slice(0, 8) : ""}
        </span>
      </div>

      {/* History */}
      <div className="relative flex min-h-0 flex-1 flex-col">
        <div
          ref={scrollBox}
          onScroll={handleScroll}
          className="h-full w-full overflow-y-auto overflow-x-hidden overscroll-y-contain"
        >
          <div className="mx-auto flex min-h-full w-full max-w-[820px] flex-col justify-end px-4 pb-4 pt-2">
            {state.items.length === 0 ? (
              <div className="flex flex-1 flex-col items-center justify-center gap-2 py-16 text-center">
                {!state.historyLoaded && connected ? (
                  <>
                    <Loader2 className="size-4 animate-spin text-muted-foreground/60" />
                    <p className="text-[12.5px] text-muted-foreground/70">
                      {t("autoResearch.arl.loadingHistory")}
                    </p>
                  </>
                ) : (
                  <>
                    <p className="text-sm font-medium text-muted-foreground">
                      {t("autoResearch.arl.emptyTitle")}
                    </p>
                    <p className="max-w-xs text-[12.5px] text-muted-foreground/60">
                      {t("autoResearch.arl.emptyHint")}
                    </p>
                  </>
                )}
              </div>
            ) : (
              state.items.map((item) => (
                <ARLMessageItem key={item.id} item={item} />
              ))
            )}
            <div ref={anchor} />
          </div>
        </div>

        {!atBottom && state.items.length > 0 && (
          <button
            type="button"
            onClick={scrollToBottom}
            className="absolute bottom-3 right-3 z-10 grid size-7 place-items-center rounded-full border border-border/50 bg-background/70 shadow-md backdrop-blur transition-colors hover:border-primary/50 hover:bg-background"
          >
            <ArrowDown className="size-3.5" />
          </button>
        )}
      </div>

      {/* Pending permission requests raised by this container. One turn can
          raise several at once (one per tool call), so they are grouped under
          a single header with bulk actions instead of N stacked dialogs. */}
      {state.permissions.length > 0 && (
        <div className="shrink-0 border-t border-border/60 bg-muted/30 px-4 py-2">
          <div className="mb-1.5 flex items-center gap-2">
            <span className="text-[12px] font-medium text-amber-700 dark:text-amber-400">
              {t("autoResearch.arl.permissionTitle")} ·{" "}
              {state.permissions.length}
            </span>
            {unattended && !autoMode && (
              <Button
                size="sm"
                variant="ghost"
                className="h-6 px-1.5 text-[11.5px] text-muted-foreground"
                onClick={() => {
                  onSetMode(role, unattended.id)
                  onPermissions(role, pendingIds, { behavior: "allow" })
                }}
                title={unattended.description}
              >
                <Zap className="mr-1 size-3" />
                {t("autoResearch.arl.permissionStopAsking")}
              </Button>
            )}
            <div className="ml-auto flex items-center gap-1.5">
              <Button
                size="sm"
                variant="ghost"
                className="h-6 px-2 text-[11.5px]"
                onClick={() =>
                  onPermissions(role, pendingIds, { behavior: "deny" })
                }
              >
                {t("autoResearch.arl.permissionDenyAll")}
              </Button>
              <Button
                size="sm"
                className="h-6 px-2 text-[11.5px]"
                onClick={() =>
                  onPermissions(role, pendingIds, { behavior: "allow" })
                }
              >
                {t("autoResearch.arl.permissionAllowAll")}
              </Button>
            </div>
          </div>

          <div className="max-h-44 space-y-1.5 overflow-y-auto">
            {state.permissions.map((request: ARLPermissionRequest) => (
              <div
                key={request.id}
                className="flex items-start gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 px-2.5 py-2"
              >
                <div className="min-w-0 flex-1">
                  <div className="truncate font-mono text-[11.5px] font-medium text-foreground">
                    {request.name}
                    {request.title ? ` · ${request.title}` : ""}
                  </div>
                  {request.description && (
                    <div className="mt-0.5 line-clamp-2 text-[11px] leading-snug text-muted-foreground">
                      {request.description}
                    </div>
                  )}
                </div>
                {/* Providers can ship their own buttons (plan mode, questions);
                    fall back to plain allow/deny when they do not. */}
                {request.actions?.length ? (
                  request.actions.map((action) => (
                    <Button
                      key={action.id}
                      size="sm"
                      variant={
                        action.behavior === "allow" ? "default" : "outline"
                      }
                      className="h-6 shrink-0 px-2 text-[11.5px]"
                      onClick={() =>
                        onPermission(role, request.id, {
                          behavior: action.behavior,
                          selectedActionId: action.id,
                          ...(action.behavior === "deny"
                            ? { message: "Denied by user" }
                            : {}),
                        })
                      }
                    >
                      {action.label}
                    </Button>
                  ))
                ) : (
                  <>
                    <Button
                      size="sm"
                      variant="outline"
                      className="h-6 shrink-0 px-2 text-[11.5px]"
                      onClick={() =>
                        onPermission(role, request.id, { behavior: "deny" })
                      }
                    >
                      {t("autoResearch.arl.permissionDeny")}
                    </Button>
                    <Button
                      size="sm"
                      className="h-6 shrink-0 px-2 text-[11.5px]"
                      onClick={() =>
                        onPermission(role, request.id, { behavior: "allow" })
                      }
                    >
                      {t("autoResearch.arl.permissionAllow")}
                    </Button>
                  </>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Composer */}
      <div className="shrink-0 border-t border-border/60 px-4 pt-3 pb-4">
        <div className="mx-auto w-full max-w-[820px]">
          <div className="rounded-2xl border border-border/60 bg-muted/30 px-3 py-2 focus-within:border-primary/40">
            <textarea
              rows={1}
              value={input}
              disabled={!connected}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key !== "Enter" || e.shiftKey) return
                e.preventDefault()
                submit()
              }}
              placeholder={t("autoResearch.arl.placeholder", {
                role: roleLabel,
              })}
              className="max-h-40 min-h-[46px] w-full resize-none bg-transparent text-[15px] leading-[21px] outline-none placeholder:text-muted-foreground/50 disabled:opacity-60"
            />
            <div className="flex items-center justify-end gap-2">
              {running ? (
                // Mirrors the Paseo composer: while a turn is in flight the
                // primary button stops it (cancel_agent_request) instead of
                // sending. Typing + Enter still interrupts with new input.
                <Button
                  size="sm"
                  variant="destructive"
                  disabled={!connected || state.canceling}
                  onClick={() => onCancel(role)}
                  className="h-7 rounded-full px-3 text-[12.5px]"
                >
                  {state.canceling ? (
                    <Loader2 className="mr-1 size-3 animate-spin" />
                  ) : (
                    <Square className="mr-1 size-3" />
                  )}
                  {state.canceling
                    ? t("autoResearch.arl.stopping")
                    : t("autoResearch.arl.stop")}
                </Button>
              ) : (
                <Button
                  size="sm"
                  disabled={!connected || !input.trim()}
                  onClick={submit}
                  className="h-7 rounded-full px-3 text-[12.5px]"
                >
                  <Send className="mr-1 size-3" />
                  {t("autoResearch.arl.send")}
                </Button>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
