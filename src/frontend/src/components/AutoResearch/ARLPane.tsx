import {
  ArrowDown,
  ArrowUp,
  Check,
  ChevronDown,
  Loader2,
  Play,
  Shield,
  ShieldAlert,
  ShieldCheck,
  Square,
  TriangleAlert,
  Zap,
} from "lucide-react"
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import { useTranslation } from "react-i18next"

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
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
  ARLStreamItem,
  ARLValidationState,
} from "@/hooks/useARLWebSocket"
import { isUnattendedMode } from "@/hooks/useARLWebSocket"
import { useHljsTheme } from "@/hooks/useHljsTheme"
import { cn } from "@/lib/utils"

import ARLMessageItem from "./ARLMessageItem"
import ARLSubagentStrip from "./ARLSubagentStrip"
import {
  ARL_ROLE_LOGO,
  ARL_TONE_DOT,
  ARL_TONE_PILL,
  arlRoleClass,
  canSyncValidation,
  describeAgent,
  workerWaitKey,
} from "./arlAgentMeta"
import {
  applyCommand,
  filterCommands,
  resolveClientCommand,
  slashQuery,
  withClientCommands,
} from "./arlCommands"

/** Slash command that bootstraps either container for the ARL flow. */
const SETUP_COMMAND = "/aris-setup"

/** Distance from the bottom (px) within which new content auto-scrolls. */
const NEAR_BOTTOM_THRESHOLD = 120

/** Distance from the top (px) within which older history is requested. */
const HISTORY_START_THRESHOLD = 96

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
  /** Validation handoff state; gates the Worker's start button. */
  validation: ARLValidationState
  /** Run the Validator → Worker sync; false when nothing went out. */
  onSyncValidation: () => boolean
  /** The Validator conversation has begun, so its start button is gone. */
  validatorStarted: boolean
  /** Request one page of older history; false when nothing went out. */
  onLoadOlder: (role: ARLRole) => boolean
  /** Fetch one subagent's transcript for the subagent sheet. */
  onFetchSubagentTimeline: (
    role: ARLRole,
    subagentId: string,
  ) => Promise<ARLStreamItem[]>
  /** `/clear`: archive the agent and start a fresh conversation. */
  onReset: (role: ARLRole) => boolean
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
  validatorStarted,
  onLoadOlder,
  onFetchSubagentTimeline,
  onReset,
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
  // `/clear` asks first: it archives the conversation the agent is in.
  const [confirmClear, setConfirmClear] = useState(false)
  // Scroll geometry captured when an older page was requested, so the view
  // stays on the same row once the page is prepended above it.
  const restoreScroll = useRef<{ height: number; top: number } | null>(null)

  const scrollBox = useRef<HTMLDivElement | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)
  const anchor = useRef<HTMLDivElement | null>(null)
  const stickToBottom = useRef(true)

  // An open socket is not the bar for accepting input: the backend only
  // starts reading frontend frames once it has booted the container and
  // created the agent, which is what `agents_ready` announces. Until then the
  // composer stays locked and says so.
  const agent = describeAgent(state, connected, connecting)
  const { ready, booting, running } = agent
  // Locked while `/clear` swaps the agent: frames sent now would reach the
  // archived one.
  const canType = ready && !state.resetting
  const lastItem = state.items[state.items.length - 1]
  const lastItemLength =
    lastItem &&
    (lastItem.kind === "assistant_message" || lastItem.kind === "thought")
      ? lastItem.text.length
      : 0

  /** Ask for the page before the oldest loaded row, remembering the scroll. */
  const requestOlder = useCallback(() => {
    const box = scrollBox.current
    // One page at a time; the hook also guards, but its view of the pane
    // only catches up on the next render.
    if (!box || restoreScroll.current) return
    if (!onLoadOlder(role)) return
    restoreScroll.current = { height: box.scrollHeight, top: box.scrollTop }
  }, [onLoadOlder, role])

  const handleScroll = useCallback(() => {
    const box = scrollBox.current
    if (!box) return
    const distance = box.scrollHeight - box.scrollTop - box.clientHeight
    const near = distance <= NEAR_BOTTOM_THRESHOLD
    stickToBottom.current = near
    setAtBottom(near)
    // Same trigger as the native client: nearing the top loads more.
    if (box.scrollTop <= HISTORY_START_THRESHOLD) requestOlder()
  }, [requestOlder])

  // Once the older page lands (or the request ends), keep the rows the user
  // was reading in place: the prepended content grows the box above them.
  const firstItemId = state.items[0]?.id
  // biome-ignore lint/correctness/useExhaustiveDependencies: the first row id is the change trigger for a prepend
  useLayoutEffect(() => {
    const pending = restoreScroll.current
    const box = scrollBox.current
    if (!pending || state.loadingOlder) return
    restoreScroll.current = null
    if (box) box.scrollTop = box.scrollHeight - pending.height + pending.top
  }, [firstItemId, state.loadingOlder])

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
    () =>
      query === null
        ? []
        : filterCommands(withClientCommands(commands.commands), query),
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

  // Worker start waits for its sync: `syncedAt` seen at click time, or
  // undefined when no start is pending.
  const pendingStart = useRef<string | null | undefined>(undefined)

  const sendSetup = () => {
    onSend(role, SETUP_COMMAND)
    stickToBottom.current = true
  }

  /**
   * Empty-pane start button: send the setup command right away.
   *
   * The Worker needs the validation service synced first, so on an unsynced
   * Worker this runs the sync and sends once it has succeeded.
   */
  const handleStart = () => {
    if (role === "worker" && !validation.syncedAt) {
      if (onSyncValidation()) pendingStart.current = validation.syncedAt
      return
    }
    sendSetup()
  }

  // biome-ignore lint/correctness/useExhaustiveDependencies: fires on sync completion only
  useEffect(() => {
    if (pendingStart.current === undefined || validation.syncing) return
    const succeeded = validation.syncedAt !== pendingStart.current
    pendingStart.current = undefined
    if (succeeded) sendSetup()
  }, [validation.syncing, validation.syncedAt])

  /**
   * Write the chosen command into the box; sending stays on Enter.
   *
   * Client commands take no arguments and run on pick, like the native
   * client (here that means opening the confirmation).
   */
  const pickCommand = (command: ARLCommand) => {
    if (command.kind === "client") {
      setInput("")
      setConfirmClear(true)
      return
    }
    fillComposer(applyCommand(command))
  }

  /** Replace the composer text, focus it and park the caret at the end. */
  const fillComposer = (text: string) => {
    setInput(text)
    setMenuDismissed(false)
    const el = textareaRef.current
    if (!el) return
    el.focus()
    // After React commits the new value, park the caret at the end.
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
    if (!text || !canType) return
    if (resolveClientCommand(text)?.name === "clear") {
      setInput("")
      setConfirmClear(true)
      return
    }
    onSend(role, text)
    setInput("")
    stickToBottom.current = true
  }

  const roleLabel = t(`autoResearch.arl.${role}`)
  const RoleLogo = ARL_ROLE_LOGO[role]
  const statusLabel = t(`autoResearch.arl.${agent.statusKey}`)
  // Booting and running are the two "something is happening" phases; both
  // get the breathing halo on the avatar.
  const subagentsRunning = state.subagents.some((s) => s.status === "running")
  const busy =
    booting || running || state.agentStatus === "running" || subagentsRunning

  // Permission mode: the container's own list, as reported by its provider.
  const autoMode = isUnattendedMode(state.modeId)
  const currentMode = state.modes.find((mode) => mode.id === state.modeId)
  const modeLabel =
    currentMode?.label ?? state.modeId ?? t("autoResearch.arl.modeUnknown")
  const unattended = state.modes.find((mode) => isUnattendedMode(mode.id))

  const pendingIds = state.permissions.map((request) => request.id)

  return (
    <section
      aria-label={roleLabel}
      className={cn(
        arlRoleClass(role),
        "relative isolate flex min-h-0 min-w-0 flex-1 flex-col",
      )}
    >
      {/* Ambient backdrop: a faint accent glow behind the header over a dot
          grid that fades out downward. Replaces hard borders as the thing
          that tells the two halves apart. */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 -z-10 overflow-hidden"
      >
        <div className="absolute -top-40 left-1/2 h-72 w-[70%] -translate-x-1/2 rounded-full bg-(--arl-accent)/10 blur-3xl dark:bg-(--arl-accent)/14" />
        <div className="arl-dot-grid absolute inset-x-0 top-0 h-56 mask-[linear-gradient(to_bottom,black,transparent)]" />
      </div>

      {/* Header: engine logo, role + engine, live status, permission mode */}
      <header className="relative flex h-16 shrink-0 items-center gap-3 px-5">
        <div
          className={cn(
            "grid size-9 shrink-0 place-items-center rounded-xl bg-(--arl-accent)/12 text-(--arl-accent-fg) ring-1 ring-(--arl-accent)/25 ring-inset",
            busy && "arl-halo",
          )}
        >
          <RoleLogo className="size-[18px]" />
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h2 className="text-[14px] font-semibold tracking-tight text-foreground">
              {roleLabel}
            </h2>
            <span
              className={cn(
                "inline-flex h-5 items-center gap-1.5 rounded-full px-2 text-[11px] font-medium",
                ARL_TONE_PILL[agent.tone],
              )}
            >
              <span
                className={cn(
                  "size-1.5 rounded-full",
                  ARL_TONE_DOT[agent.tone],
                )}
              />
              {statusLabel}
            </span>
          </div>
          <p className="mt-0.5 flex items-center gap-1.5 truncate text-[11.5px] text-muted-foreground/70">
            {t(`autoResearch.arl.${role}Hint`)}
            {state.agentId && (
              <span
                className="font-mono text-[10.5px] text-muted-foreground/45"
                title={state.agentId}
              >
                #{state.agentId.slice(0, 6)}
              </span>
            )}
          </p>
        </div>

        {/* Permission mode picker: switching into an auto/bypass mode is what
            stops the container from asking before every single tool call. */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              size="sm"
              variant="ghost"
              disabled={!ready || state.modes.length === 0}
              className={cn(
                "h-7 shrink-0 gap-1 rounded-full border border-border/60 bg-background/60 px-2.5 text-[11.5px] font-normal shadow-xs backdrop-blur hover:bg-background",
                autoMode &&
                  "border-emerald-500/30 text-emerald-700 dark:text-emerald-400",
              )}
              title={currentMode?.description ?? modeLabel}
            >
              {autoMode ? (
                <ShieldCheck className="size-3.5" />
              ) : (
                <Shield className="size-3.5" />
              )}
              <span className="max-w-[8rem] truncate">{modeLabel}</span>
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

        {/* Running indicator: an accent sheen along the header's bottom edge,
            in place of a static divider. */}
        {(running || booting) && (
          <div
            aria-hidden
            className="absolute inset-x-5 bottom-0 h-px overflow-hidden rounded-full bg-(--arl-accent)/10"
          >
            <div className="arl-sheen" />
          </div>
        )}
      </header>

      {/* History. The top edge fades instead of meeting the header at a line. */}
      <div className="relative flex min-h-0 flex-1 flex-col">
        <div
          ref={scrollBox}
          onScroll={handleScroll}
          className="h-full w-full overflow-y-auto overflow-x-hidden overscroll-y-contain mask-[linear-gradient(to_bottom,transparent,black_20px)]"
        >
          <div className="mx-auto flex min-h-full w-full max-w-[820px] flex-col px-5 pb-4 pt-4">
            {state.items.length === 0 ? (
              <EmptyState
                role={role}
                booting={booting}
                loadingHistory={!booting && connected && !state.historyLoaded}
                ready={ready && !state.resetting}
                validation={validation}
                onStart={handleStart}
                validatorStarted={validatorStarted}
              />
            ) : (
              <>
                {(state.hasOlder || state.loadingOlder) && (
                  <div className="flex justify-center pb-3">
                    {state.loadingOlder ? (
                      <span className="inline-flex h-7 items-center gap-1.5 text-[11.5px] text-muted-foreground">
                        <Loader2 className="size-3 animate-spin" />
                        {t("autoResearch.arl.loadingOlder")}
                      </span>
                    ) : (
                      // Fallback for when the history is too short to
                      // scroll, so the top threshold can never be crossed.
                      <button
                        type="button"
                        onClick={requestOlder}
                        disabled={!ready}
                        className="h-7 rounded-full border border-border/60 bg-background/70 px-3 text-[11.5px] text-muted-foreground transition-colors hover:text-foreground disabled:opacity-50"
                      >
                        {t("autoResearch.arl.loadOlder")}
                      </button>
                    )}
                  </div>
                )}
                {state.items.map((item) => (
                  <ARLMessageItem key={item.id} item={item} />
                ))}
              </>
            )}
            <div ref={anchor} />
          </div>
        </div>

        {!atBottom && state.items.length > 0 && (
          <button
            type="button"
            onClick={scrollToBottom}
            className="absolute bottom-3 left-1/2 z-10 flex h-7 -translate-x-1/2 items-center gap-1 rounded-full border border-border/60 bg-background/85 px-3 text-[11.5px] text-muted-foreground shadow-lg backdrop-blur transition-colors hover:border-(--arl-accent)/50 hover:text-(--arl-accent-fg)"
          >
            <ArrowDown className="size-3.5" />
            {t("autoResearch.arl.scrollToLatest")}
          </button>
        )}
      </div>

      {/* Pending permission requests raised by this container. One turn can
          raise several at once (one per tool call), so they are grouped in a
          single card with bulk actions instead of N stacked dialogs. */}
      {state.permissions.length > 0 && (
        <div className="shrink-0 px-5 pb-2">
          <div className="mx-auto w-full max-w-[820px] overflow-hidden rounded-xl border border-amber-500/30 bg-amber-500/[0.06] shadow-sm">
            <div className="flex items-center gap-2 px-3 py-2">
              <ShieldAlert className="size-3.5 shrink-0 text-amber-600 dark:text-amber-400" />
              <span className="text-[12px] font-medium text-amber-800 dark:text-amber-300">
                {t("autoResearch.arl.permissionTitle")}
              </span>
              <span className="grid h-4 min-w-4 place-items-center rounded-full bg-amber-500/20 px-1 text-[10.5px] font-semibold tabular-nums text-amber-800 dark:text-amber-300">
                {state.permissions.length}
              </span>
              {unattended && !autoMode && (
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-6 gap-1 px-1.5 text-[11.5px] text-muted-foreground"
                  onClick={() => {
                    // Only clear the queue if the mode switch actually went
                    // out; otherwise the container would keep asking on the
                    // next tool call and the cards would have vanished anyway.
                    if (!onSetMode(role, unattended.id)) return
                    onPermissions(role, pendingIds, { behavior: "allow" })
                  }}
                  title={unattended.description}
                >
                  <Zap className="size-3" />
                  {t("autoResearch.arl.permissionStopAsking")}
                </Button>
              )}
              <div className="ml-auto flex items-center gap-1">
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

            <ul className="max-h-44 divide-y divide-amber-500/15 overflow-y-auto border-t border-amber-500/15 bg-background/50">
              {state.permissions.map((request: ARLPermissionRequest) => (
                <PermissionRow
                  key={request.id}
                  request={request}
                  onRespond={(response) =>
                    onPermission(role, request.id, response)
                  }
                />
              ))}
            </ul>
          </div>
        </div>
      )}

      {/* Composer: a floating card, no divider above it. */}
      <div className="shrink-0 px-5 pt-1 pb-4">
        <div className="mx-auto w-full max-w-[820px]">
          <ARLSubagentStrip
            role={role}
            subagents={state.subagents}
            onFetchTimeline={onFetchSubagentTimeline}
          />
        </div>
        <div className="relative mx-auto w-full max-w-[820px]">
          {/* Slash-command menu. Rows take `onMouseDown` + preventDefault so
              clicking one does not blur the textarea (blur closes the menu). */}
          {menuOpen && (
            <div
              id={`arl-commands-${role}`}
              role="listbox"
              aria-label={t("autoResearch.arl.commandsTitle")}
              className="absolute inset-x-0 bottom-full z-20 mb-2 overflow-hidden rounded-xl border border-border/60 bg-popover/95 text-popover-foreground shadow-xl backdrop-blur"
            >
              <div className="flex items-center gap-2 px-3 pt-2 pb-1 text-[11px] text-muted-foreground">
                <span className="font-medium uppercase tracking-wider">
                  {t("autoResearch.arl.commandsTitle")}
                </span>
                {commands.loading && (
                  <Loader2 className="size-3 animate-spin text-muted-foreground/70" />
                )}
                <span className="ml-auto text-muted-foreground/60">
                  {t("autoResearch.arl.commandsKeyHint")}
                </span>
              </div>
              <div
                ref={commandList}
                className="max-h-64 overflow-x-hidden overflow-y-auto p-1"
              >
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
                        "flex min-w-0 cursor-pointer items-baseline gap-2 rounded-lg px-2.5 py-1.5",
                        index === activeCommand && "bg-(--arl-accent)/10",
                      )}
                    >
                      {/* Every text part may shrink and ends in an ellipsis;
                          the full text stays available as a tooltip. */}
                      <span
                        className={cn(
                          "max-w-[45%] shrink-0 truncate font-mono text-[12.5px] font-medium",
                          index === activeCommand && "text-(--arl-accent-fg)",
                        )}
                        title={`/${command.name}`}
                      >
                        /{command.name}
                      </span>
                      {command.argumentHint && (
                        <span
                          className="min-w-0 max-w-[30%] truncate font-mono text-[11px] text-muted-foreground/70"
                          title={command.argumentHint}
                        >
                          {command.argumentHint}
                        </span>
                      )}
                      {(() => {
                        const description =
                          command.kind === "client"
                            ? t("autoResearch.arl.clearCommandDesc")
                            : command.description
                        return (
                          <span
                            className="min-w-0 flex-1 truncate text-[11.5px] text-muted-foreground"
                            title={description}
                          >
                            {description}
                          </span>
                        )
                      })()}
                      <span className="shrink-0 rounded-md bg-muted px-1.5 text-[10px] text-muted-foreground/80">
                        {command.kind === "skill"
                          ? t("autoResearch.arl.commandKindSkill")
                          : command.kind === "client"
                            ? t("autoResearch.arl.commandKindClient")
                            : t("autoResearch.arl.commandKindCommand")}
                      </span>
                    </div>
                  ))
                ) : (
                  <div className="px-2.5 py-2 text-[12px] text-muted-foreground">
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
          <div
            className={cn(
              "rounded-2xl border border-border/60 bg-background/80 shadow-[0_1px_2px_rgba(0,0,0,0.04),0_8px_24px_-12px_rgba(0,0,0,0.12)] backdrop-blur transition-[border-color,box-shadow] dark:bg-background/50",
              "focus-within:border-(--arl-accent)/45 focus-within:shadow-[0_0_0_4px_color-mix(in_srgb,var(--arl-accent)_12%,transparent),0_8px_24px_-12px_rgba(0,0,0,0.18)]",
              !canType && "opacity-80",
            )}
          >
            <textarea
              ref={textareaRef}
              rows={1}
              value={input}
              disabled={!canType}
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
              aria-label={t("autoResearch.arl.placeholder", {
                role: roleLabel,
              })}
              placeholder={
                booting
                  ? t("autoResearch.arl.bootingPlaceholder")
                  : state.resetting
                    ? t("autoResearch.arl.resetting")
                    : t("autoResearch.arl.placeholderShort", { role: roleLabel })
              }
              className="block max-h-40 min-h-[48px] w-full resize-none bg-transparent px-4 pt-3 text-[14.5px] leading-[21px] outline-none placeholder:text-muted-foreground/50 disabled:cursor-not-allowed"
            />
            <div className="flex items-center gap-2 px-2.5 pb-2.5">
              <span className="pl-1.5 text-[11px] text-muted-foreground/50">
                {t("autoResearch.arl.composerHint")}
              </span>
              <span className="flex-1" />
              {running ? (
                // Mirrors the Paseo composer: while a turn is in flight the
                // primary button stops it (cancel_agent_request) instead of
                // sending. Typing + Enter still interrupts with new input.
                <Button
                  size="sm"
                  variant="outline"
                  disabled={!ready || state.canceling}
                  onClick={() => onCancel(role)}
                  className="h-8 gap-1.5 rounded-full border-destructive/30 px-3 text-[12px] text-destructive hover:bg-destructive/10 hover:text-destructive"
                >
                  {state.canceling ? (
                    <Loader2 className="size-3 animate-spin" />
                  ) : (
                    <Square className="size-2.5 fill-current" />
                  )}
                  {state.canceling
                    ? t("autoResearch.arl.stopping")
                    : t("autoResearch.arl.stop")}
                </Button>
              ) : (
                <button
                  type="button"
                  disabled={!canType || !input.trim()}
                  onClick={submit}
                  aria-label={t("autoResearch.arl.send")}
                  title={
                    booting
                      ? t("autoResearch.arl.bootingHint")
                      : t("autoResearch.arl.send")
                  }
                  className="grid size-8 place-items-center rounded-full bg-(--arl-accent) text-white shadow-[0_4px_12px_-4px_var(--arl-accent)] transition-[transform,opacity,box-shadow] hover:brightness-110 active:scale-95 disabled:bg-muted disabled:text-muted-foreground/60 disabled:shadow-none"
                >
                  {booting || state.resetting ? (
                    <Loader2 className="size-3.5 animate-spin" />
                  ) : (
                    <ArrowUp className="size-4" strokeWidth={2.25} />
                  )}
                </button>
              )}
            </div>
          </div>
        </div>
      </div>

      <AlertDialog open={confirmClear} onOpenChange={setConfirmClear}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t("autoResearch.arl.clearTitle", { role: roleLabel })}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t("autoResearch.arl.clearDesc")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("autoResearch.arl.clearCancel")}</AlertDialogCancel>
            <AlertDialogAction onClick={() => onReset(role)}>
              {t("autoResearch.arl.clearConfirm")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  )
}

/**
 * Placeholder for an empty history: one start button, nothing else.
 *
 * The Validator always offers it; the Worker only once the Validator has
 * produced the validation service and `task.md`, and shows a one-line
 * warning until then. While the container boots the button stays disabled with a
 * spinner, and the history backfill gets a bare spinner since messages may be
 * about to appear.
 */
function EmptyState({
  role,
  booting,
  loadingHistory,
  ready,
  validation,
  onStart,
  validatorStarted,
}: {
  role: ARLRole
  booting: boolean
  loadingHistory: boolean
  /** Agent is up and can take the setup command. */
  ready: boolean
  validation: ARLValidationState
  onStart: () => void
  validatorStarted: boolean
}) {
  const { t } = useTranslation()

  if (loadingHistory) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 py-16 text-center">
        <Loader2 className="size-5 animate-spin text-(--arl-accent-fg)" />
        <p className="text-[12.5px] text-muted-foreground/80">
          {t("autoResearch.arl.loadingHistory")}
        </p>
      </div>
    )
  }

  if (role === "worker" && !canSyncValidation(validation)) {
    return (
      <div className="flex flex-1 items-center justify-center py-16">
        {/* Warning tone: the validation service is not ready yet */}
        <p
          role="status"
          className="inline-flex items-center gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3.5 py-2 text-[13px] text-amber-700 dark:text-amber-400"
        >
          <TriangleAlert className="size-4 shrink-0" />
          {t(workerWaitKey(validatorStarted))}
        </p>
      </div>
    )
  }

  const busy = booting || validation.syncing
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-3 py-16">
      {/* Same look as the normal session's start-research button (ChatPanel) */}
      <button
        type="button"
        onClick={onStart}
        disabled={!ready || validation.syncing}
        className="group relative inline-flex items-center gap-2.5 rounded-xl border-2 border-primary/40 bg-gradient-to-br from-primary to-primary/80 px-8 py-3.5 text-base font-semibold text-primary-foreground shadow-lg shadow-primary/25 transition-all hover:shadow-xl hover:shadow-primary/30 hover:scale-105 disabled:opacity-60 disabled:hover:scale-100 disabled:cursor-not-allowed"
      >
        {busy ? (
          <Loader2 className="size-5 animate-spin" />
        ) : (
          <Play className="size-5 transition-transform group-hover:scale-110" />
        )}
        {t(`autoResearch.arl.guide.${role}Start`)}
        <div className="absolute inset-0 rounded-xl bg-gradient-to-r from-transparent via-white/20 to-transparent opacity-0 blur-sm transition-opacity group-hover:opacity-100" />
      </button>
      {booting && (
        <p className="text-xs text-muted-foreground/60">
          {t("autoResearch.arl.bootingHint")}
        </p>
      )}
    </div>
  )
}

/** One pending permission request with its allow / deny actions. */
function PermissionRow({
  request,
  onRespond,
}: {
  request: ARLPermissionRequest
  onRespond: (response: Record<string, unknown>) => boolean
}) {
  const { t } = useTranslation()
  return (
    <li className="flex items-start gap-2 px-3 py-2">
      <div className="min-w-0 flex-1">
        <div className="truncate font-mono text-[11.5px] font-medium text-foreground">
          {request.name}
          {request.title && (
            <span className="text-muted-foreground"> · {request.title}</span>
          )}
        </div>
        {request.description && (
          <div className="mt-0.5 line-clamp-2 text-[11px] leading-snug text-muted-foreground">
            {request.description}
          </div>
        )}
      </div>
      {/* Providers can ship their own buttons (plan mode, questions); fall
          back to plain allow/deny when they do not. */}
      {request.actions?.length ? (
        request.actions.map((action) => (
          <Button
            key={action.id}
            size="sm"
            variant={action.behavior === "allow" ? "default" : "outline"}
            className="h-6 shrink-0 px-2 text-[11.5px]"
            onClick={() =>
              onRespond({
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
            onClick={() => onRespond({ behavior: "deny" })}
          >
            {t("autoResearch.arl.permissionDeny")}
          </Button>
          <Button
            size="sm"
            className="h-6 shrink-0 px-2 text-[11.5px]"
            onClick={() => onRespond({ behavior: "allow" })}
          >
            {t("autoResearch.arl.permissionAllow")}
          </Button>
        </>
      )}
    </li>
  )
}
