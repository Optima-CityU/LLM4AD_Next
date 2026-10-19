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
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
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
  ARLCommand,
  ARLCommandsState,
  ARLPermissionRequest,
  ARLRole,
  ARLRoleState,
  ARLValidationState,
} from "@/hooks/useARLWebSocket"
import { isUnattendedMode } from "@/hooks/useARLWebSocket"
import { useHljsTheme } from "@/hooks/useHljsTheme"
import { cn } from "@/lib/utils"

import ARLMessageItem from "./ARLMessageItem"
import ARLValidationBar from "./ARLValidationBar"
import { applyCommand, filterCommands, slashQuery } from "./arlCommands"

/** Distance from the bottom (px) within which new content auto-scrolls. */
const NEAR_BOTTOM_THRESHOLD = 120

interface Props {
  role: ARLRole
  state: ARLRoleState
  connected: boolean
  /** A connection attempt is in flight (first try or an automatic retry). */
  connecting: boolean
  onSend: (role: ARLRole, content: string) => void
  /** Slash commands this container's agent offers. */
  commands: ARLCommandsState
  /** Ask the daemon for a fresh command list (fired when `/` is typed). */
  onListCommands: (role: ARLRole) => boolean
  onCancel: (role: ARLRole) => void
  onSetMode: (role: ARLRole, modeId: string) => boolean
  onPermission: (
    role: ARLRole,
    requestId: string,
    response: Record<string, unknown>,
  ) => boolean
  onPermissions: (
    role: ARLRole,
    requestIds: string[],
    response: Record<string, unknown>,
  ) => boolean
  /**
   * Validation handoff state, passed only to the pane that renders it.
   *
   * Omitted for the Validator: it is the side that *provides* the service, so
   * a strip telling it to go set one up would be nonsense.
   */
  validation?: ARLValidationState
  onSyncValidation?: () => void
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
  connecting,
  onSend,
  commands,
  onListCommands,
  onCancel,
  onSetMode,
  onPermission,
  onPermissions,
  validation,
  onSyncValidation,
}: Props) {
  const { t } = useTranslation()
  // Highlight.js theme is ref-counted and shared: register it once per pane
  // rather than per rendered message.
  useHljsTheme()

  const [input, setInput] = useState("")
  const [atBottom, setAtBottom] = useState(true)
  // Slash-command menu: highlighted row, and whether the user dismissed the
  // menu (Esc / blur) for the text currently in the box.
  const [activeCommand, setActiveCommand] = useState(0)
  const [menuDismissed, setMenuDismissed] = useState(false)
  const commandList = useRef<HTMLDivElement | null>(null)

  const scrollBox = useRef<HTMLDivElement | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)
  const anchor = useRef<HTMLDivElement | null>(null)
  const stickToBottom = useRef(true)

  // An open socket is not the bar for accepting input: the backend only
  // starts reading frontend frames once it has booted the container and
  // created the agent, which is what `agents_ready` announces. Until then the
  // composer stays locked and says so.
  const ready = connected && state.ready
  const booting = (connected || connecting) && !state.ready

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
      const box = scrollBox.current
      if (box) box.scrollTop = box.scrollHeight
    })
    return () => cancelAnimationFrame(frame)
  }, [state.items.length, lastItemLength])

  // Jump to the newest message when the pane first fills with history.
  useEffect(() => {
    if (!state.historyLoaded) return
    const timer = setTimeout(() => {
      const box = scrollBox.current
      if (box) box.scrollTop = box.scrollHeight
      stickToBottom.current = true
      setAtBottom(true)
    }, 60)
    return () => clearTimeout(timer)
  }, [state.historyLoaded])

  // Grow the composer with its content, matching the chat composer. Capped by
  // `max-h-40` on the element, past which it scrolls internally.
  // biome-ignore lint/correctness/useExhaustiveDependencies: input drives the recompute, the body does not read it
  useEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = "auto"
    el.style.height = `${el.scrollHeight}px`
  }, [input])

  // Drive the scroll container directly rather than via `scrollIntoView`:
  // with two panes side by side inside the page's own scroll ancestors,
  // `scrollIntoView` can also nudge those ancestors, dragging the other pane
  // and the page frame along with it.
  const scrollToBottom = () => {
    const box = scrollBox.current
    if (box) box.scrollTo({ top: box.scrollHeight, behavior: "smooth" })
    stickToBottom.current = true
    setAtBottom(true)
  }

  // The menu tracks the text: open while the input is `/` plus a partial
  // name, closed once a space follows (the user is on the arguments by then).
  const query = ready ? slashQuery(input) : null
  const menuOpen = query !== null && !menuDismissed
  const matches = useMemo(
    () => (query === null ? [] : filterCommands(commands.commands, query)),
    [commands.commands, query],
  )

  // Fetch on every open rather than once per session: skills can be installed
  // mid-session (e.g. by `skill-installer`), and the cached list keeps the
  // menu populated while the refresh is in flight.
  // biome-ignore lint/correctness/useExhaustiveDependencies: refetch only when the menu opens, not on each keystroke
  useEffect(() => {
    if (menuOpen) onListCommands(role)
  }, [menuOpen, role])

  // Restart from the top whenever the candidate set changes.
  // biome-ignore lint/correctness/useExhaustiveDependencies: query and list are the reset triggers
  useEffect(() => {
    setActiveCommand(0)
  }, [query, commands.commands])

  // Keep the highlighted row visible. Scrolls the list box itself instead of
  // `scrollIntoView`, which would also drag the pane's scroll ancestors.
  useEffect(() => {
    if (!menuOpen) return
    const box = commandList.current
    const row = box?.querySelector<HTMLElement>(
      `[data-command-index="${activeCommand}"]`,
    )
    if (!box || !row) return
    if (row.offsetTop < box.scrollTop) box.scrollTop = row.offsetTop
    else if (
      row.offsetTop + row.offsetHeight >
      box.scrollTop + box.clientHeight
    )
      box.scrollTop = row.offsetTop + row.offsetHeight - box.clientHeight
  }, [activeCommand, menuOpen])

  /** Write the chosen command into the box; sending stays on Enter. */
  const pickCommand = (command: ARLCommand) => {
    const text = applyCommand(command)
    setInput(text)
    setMenuDismissed(false)
    const el = textareaRef.current
    if (!el) return
    el.focus()
    // After React commits the new value, park the caret after the space.
    requestAnimationFrame(() => el.setSelectionRange(text.length, text.length))
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // IME composition (e.g. Chinese input) uses Enter/arrows to pick
    // candidates; never hijack those keys mid-composition.
    if (e.nativeEvent.isComposing) return

    if (menuOpen && matches.length > 0) {
      if (e.key === "ArrowDown") {
        e.preventDefault()
        setActiveCommand((i) => (i + 1) % matches.length)
        return
      }
      if (e.key === "ArrowUp") {
        e.preventDefault()
        setActiveCommand((i) => (i - 1 + matches.length) % matches.length)
        return
      }
      if ((e.key === "Enter" && !e.shiftKey) || e.key === "Tab") {
        e.preventDefault()
        pickCommand(matches[Math.min(activeCommand, matches.length - 1)])
        return
      }
    }
    if (menuOpen && e.key === "Escape") {
      e.preventDefault()
      setMenuDismissed(true)
      return
    }

    if (e.key !== "Enter" || e.shiftKey) return
    e.preventDefault()
    submit()
  }

  const submit = () => {
    const text = input.trim()
    if (!text || !ready) return
    onSend(role, text)
    setInput("")
    stickToBottom.current = true
  }

  const roleLabel = t(`autoResearch.arl.${role}`)
  const statusLabel = booting
    ? t("autoResearch.arl.booting")
    : state.canceling
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
              : booting
                ? "animate-pulse bg-amber-500"
                : ready
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
              disabled={!ready || state.modes.length === 0}
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

      {/* Validation handoff. Held back until the pane is ready so it does not
          compete with the boot spinner — during startup "no validation
          service detected" is true but useless, since the user cannot act on
          it yet anyway. */}
      {validation && onSyncValidation && ready && (
        <ARLValidationBar
          state={validation}
          connected={connected}
          onSync={onSyncValidation}
        />
      )}

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
                {/* Spin only while something is actually in flight. Booting
                    can take a while (credentials, container, agent), so say
                    which phase we are in rather than showing an empty pane
                    that looks ready. The hook resolves `historyLoaded` on
                    `agents_ready` even when the backend skipped the backfill,
                    so this cannot hang on a frame that never arrives. */}
                {booting || (!state.historyLoaded && connected) ? (
                  <>
                    <Loader2 className="size-4 animate-spin text-muted-foreground/60" />
                    <p className="text-[12.5px] text-muted-foreground/70">
                      {booting
                        ? t("autoResearch.arl.bootingHint")
                        : t("autoResearch.arl.loadingHistory")}
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
                  // Only clear the queue if the mode switch actually went out;
                  // otherwise the container would keep asking on the next
                  // tool call and the cards would have vanished regardless.
                  if (!onSetMode(role, unattended.id)) return
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
        <div className="relative mx-auto w-full max-w-[820px]">
          {/* Slash-command menu. Rows take `onMouseDown` + preventDefault so
              clicking one does not blur the textarea (blur closes the menu). */}
          {menuOpen && (
            <div
              id={`arl-commands-${role}`}
              role="listbox"
              aria-label={t("autoResearch.arl.commandsTitle")}
              className="absolute inset-x-0 bottom-full z-20 mb-2 overflow-hidden rounded-xl border border-border/60 bg-popover text-popover-foreground shadow-lg"
            >
              <div className="flex items-center gap-2 border-b border-border/50 px-3 py-1.5 text-[11px] text-muted-foreground">
                <span className="font-medium">
                  {t("autoResearch.arl.commandsTitle")}
                </span>
                {commands.loading && (
                  <Loader2 className="size-3 animate-spin text-muted-foreground/70" />
                )}
                <span className="ml-auto text-muted-foreground/60">
                  {t("autoResearch.arl.commandsKeyHint")}
                </span>
              </div>
              <div ref={commandList} className="max-h-64 overflow-y-auto py-1">
                {matches.length > 0 ? (
                  matches.map((command, index) => (
                    <div
                      key={command.name}
                      id={`arl-command-${role}-${index}`}
                      role="option"
                      aria-selected={index === activeCommand}
                      tabIndex={-1}
                      data-command-index={index}
                      onMouseDown={(e) => {
                        e.preventDefault()
                        pickCommand(command)
                      }}
                      onMouseEnter={() => setActiveCommand(index)}
                      className={cn(
                        "flex cursor-pointer items-baseline gap-2 px-3 py-1.5",
                        index === activeCommand
                          ? "bg-accent text-accent-foreground"
                          : "",
                      )}
                    >
                      <span className="shrink-0 font-mono text-[12.5px] font-medium">
                        /{command.name}
                      </span>
                      {command.argumentHint && (
                        <span className="shrink-0 font-mono text-[11px] text-muted-foreground/70">
                          {command.argumentHint}
                        </span>
                      )}
                      <span
                        className="min-w-0 flex-1 truncate text-[11.5px] text-muted-foreground"
                        title={command.description}
                      >
                        {command.description}
                      </span>
                      <span className="shrink-0 rounded border border-border/60 px-1 text-[10px] text-muted-foreground/70">
                        {command.kind === "skill"
                          ? t("autoResearch.arl.commandKindSkill")
                          : t("autoResearch.arl.commandKindCommand")}
                      </span>
                    </div>
                  ))
                ) : (
                  <div className="px-3 py-2 text-[12px] text-muted-foreground">
                    {commands.error
                      ? commands.error
                      : !commands.loaded
                        ? t("autoResearch.arl.commandsLoading")
                        : t("autoResearch.arl.commandsEmpty")}
                  </div>
                )}
              </div>
            </div>
          )}
          <div className="rounded-2xl border border-border/60 bg-muted/30 px-3 py-2 focus-within:border-primary/40">
            <textarea
              ref={textareaRef}
              rows={1}
              value={input}
              disabled={!ready}
              onChange={(e) => {
                setInput(e.target.value)
                // Typing again re-arms a menu dismissed with Esc.
                setMenuDismissed(false)
              }}
              onKeyDown={handleKeyDown}
              onBlur={() => setMenuDismissed(true)}
              onFocus={() => setMenuDismissed(false)}
              role="combobox"
              aria-expanded={menuOpen}
              aria-autocomplete="list"
              aria-controls={`arl-commands-${role}`}
              aria-activedescendant={
                menuOpen && matches.length > 0
                  ? `arl-command-${role}-${activeCommand}`
                  : undefined
              }
              placeholder={
                booting
                  ? t("autoResearch.arl.bootingPlaceholder")
                  : t("autoResearch.arl.placeholder", { role: roleLabel })
              }
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
                  disabled={!ready || state.canceling}
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
                  disabled={!ready || !input.trim()}
                  onClick={submit}
                  className="h-7 rounded-full px-3 text-[12.5px]"
                  title={
                    booting ? t("autoResearch.arl.bootingHint") : undefined
                  }
                >
                  {booting ? (
                    <Loader2 className="mr-1 size-3 animate-spin" />
                  ) : (
                    <Send className="mr-1 size-3" />
                  )}
                  {booting
                    ? t("autoResearch.arl.booting")
                    : t("autoResearch.arl.send")}
                </Button>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
