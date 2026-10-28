import {
  ArrowDown,
  ArrowRight,
  ArrowUp,
  Check,
  ChevronDown,
  CornerDownLeft,
  Loader2,
  Shield,
  ShieldAlert,
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
import {
  ARL_ROLE_LOGO,
  ARL_TONE_DOT,
  ARL_TONE_PILL,
  arlRoleClass,
  describeAgent,
} from "./arlAgentMeta"
import { applyCommand, filterCommands, slashQuery } from "./arlCommands"

/** Slash command that bootstraps either container for the ARL flow. */
const SETUP_COMMAND = "/aris-setup"

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
  /** Validation handoff state; drives the step guide on an empty pane. */
  validation: ARLValidationState
  /** Run the Validator → Worker sync (offered inline by the guide). */
  onSyncValidation: () => void
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
  const agent = describeAgent(state, connected, connecting)
  const { ready, booting, running } = agent
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
  const pickCommand = (command: ARLCommand) =>
    fillComposer(applyCommand(command))

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
    if (!text || !ready) return
    onSend(role, text)
    setInput("")
    stickToBottom.current = true
  }

  const roleLabel = t(`autoResearch.arl.${role}`)
  const RoleLogo = ARL_ROLE_LOGO[role]
  const statusLabel = t(`autoResearch.arl.${agent.statusKey}`)
  // Booting and running are the two "something is happening" phases; both
  // get the breathing halo on the avatar.
  const busy = booting || running || state.agentStatus === "running"

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
                ready={ready}
                validation={validation}
                onUseCommand={() => fillComposer(`${SETUP_COMMAND} `)}
                onSync={connected ? onSyncValidation : undefined}
              />
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
              <div ref={commandList} className="max-h-64 overflow-y-auto p-1">
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
                        "flex cursor-pointer items-baseline gap-2 rounded-lg px-2.5 py-1.5",
                        index === activeCommand && "bg-(--arl-accent)/10",
                      )}
                    >
                      <span
                        className={cn(
                          "shrink-0 font-mono text-[12.5px] font-medium",
                          index === activeCommand && "text-(--arl-accent-fg)",
                        )}
                      >
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
                      <span className="shrink-0 rounded-md bg-muted px-1.5 text-[10px] text-muted-foreground/80">
                        {command.kind === "skill"
                          ? t("autoResearch.arl.commandKindSkill")
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
              !ready && "opacity-80",
            )}
          >
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
              aria-label={t("autoResearch.arl.placeholder", {
                role: roleLabel,
              })}
              placeholder={
                booting
                  ? t("autoResearch.arl.bootingPlaceholder")
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
                  disabled={!ready || !input.trim()}
                  onClick={submit}
                  aria-label={t("autoResearch.arl.send")}
                  title={
                    booting
                      ? t("autoResearch.arl.bootingHint")
                      : t("autoResearch.arl.send")
                  }
                  className="grid size-8 place-items-center rounded-full bg-(--arl-accent) text-white shadow-[0_4px_12px_-4px_var(--arl-accent)] transition-[transform,opacity,box-shadow] hover:brightness-110 active:scale-95 disabled:bg-muted disabled:text-muted-foreground/60 disabled:shadow-none"
                >
                  {booting ? (
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
    </section>
  )
}

/** Progress of one guide step. */
type GuideStepState = "done" | "current" | "todo"

/** One step of the empty-pane guide. */
interface GuideStep {
  key: string
  state: GuideStepState
  /** Offer the setup-command chip under this step while it is current. */
  command?: boolean
}

/**
 * Steps shown on an empty pane, derived from the live handoff state so the
 * highlighted step always matches what the user has to do next.
 *
 * Validator: run the setup command, which produces the validation service and
 * `task.md`, then hand over to the Worker. Worker: wait for that service, sync
 * it across, then run the same setup command here.
 *
 * @param role - Container role.
 * @param validation - Validation handoff state.
 * @returns Ordered steps; the first unfinished one is `current`.
 */
function guideSteps(
  role: ARLRole,
  validation: ARLValidationState,
): GuideStep[] {
  const serviceReady = validation.configured && validation.hasTask
  const synced = !!validation.syncedAt
  const raw: { key: string; done: boolean; command?: boolean }[] =
    role === "validator"
      ? [
          { key: "validatorStep1", done: serviceReady, command: true },
          { key: "validatorStep2", done: synced },
        ]
      : [
          { key: "workerStep1", done: serviceReady },
          { key: "workerStep2", done: synced },
          { key: "workerStep3", done: false, command: true },
        ]
  const current = raw.findIndex((step) => !step.done)
  return raw.map((step, index) => ({
    key: step.key,
    command: step.command,
    state: step.done ? "done" : index === current ? "current" : "todo",
  }))
}

/**
 * Placeholder for an empty history: a step-by-step guide for the ARL flow.
 *
 * A fresh session otherwise opens on two blank chats with no hint that the
 * Validator has to go first, so the guide is deliberately prominent: an
 * accent card, numbered steps with the next one highlighted, and the setup
 * command as a one-click chip that fills the composer.
 *
 * The guide also shows while the container boots, so the user can read it
 * during the wait. Only the history backfill gets a bare spinner, since
 * messages may be about to appear. The hook resolves `historyLoaded` on
 * `agents_ready` even when the backend skipped the backfill, so that spinner
 * cannot hang on a frame that never arrives.
 */
function EmptyState({
  role,
  booting,
  loadingHistory,
  ready,
  validation,
  onUseCommand,
  onSync,
}: {
  role: ARLRole
  booting: boolean
  loadingHistory: boolean
  /** Agent is up: the command chip can fill the composer. */
  ready: boolean
  validation: ARLValidationState
  onUseCommand: () => void
  /** Run the validation sync; absent while the socket is down. */
  onSync?: () => void
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

  const steps = guideSteps(role, validation)
  const allDone = steps.every((step) => step.state === "done")

  return (
    <div className="flex flex-1 flex-col items-center justify-center py-8">
      <div className="w-full max-w-[340px]">
        {/* Title only: the pane header already carries the engine logo */}
        <p className="mb-3.5 text-center text-[15px] font-semibold tracking-tight text-foreground">
          {t(`autoResearch.arl.guide.${role}Title`)}
        </p>

        {/* Steps card: one short line per step, the next one highlighted */}
        <div className="rounded-2xl border border-(--arl-accent)/30 bg-background/85 px-4 py-3.5 shadow-[0_14px_36px_-18px_color-mix(in_srgb,var(--arl-accent)_55%,transparent)] backdrop-blur dark:bg-background/55">
          <ol>
            {steps.map((step, index) => (
              <li
                key={step.key}
                className="relative flex items-start gap-3 pb-3.5 last:pb-0"
                aria-current={step.state === "current" ? "step" : undefined}
              >
                {/* Connector to the next step */}
                {index < steps.length - 1 && (
                  <span
                    aria-hidden
                    className={cn(
                      "absolute top-7 bottom-0.5 left-[11.5px] w-px",
                      step.state === "done" ? "bg-emerald-500/45" : "bg-border",
                    )}
                  />
                )}
                <span
                  className={cn(
                    "relative z-10 grid size-6 shrink-0 place-items-center rounded-full text-[11px] font-semibold tabular-nums",
                    step.state === "done" && "bg-emerald-500 text-white",
                    step.state === "current" &&
                      "bg-(--arl-accent) text-white shadow-[0_0_0_4px_color-mix(in_srgb,var(--arl-accent)_20%,transparent)]",
                    step.state === "todo" &&
                      "bg-muted text-muted-foreground ring-1 ring-border ring-inset",
                  )}
                >
                  {step.state === "done" ? (
                    <Check className="size-3.5" strokeWidth={3} />
                  ) : (
                    index + 1
                  )}
                </span>
                <StepText
                  text={t(`autoResearch.arl.guide.${step.key}`)}
                  state={step.state}
                  // Only this pane's own command step fills the composer; a
                  // command mentioned for the other pane stays plain code.
                  onUseCommand={
                    step.command && step.state === "current"
                      ? onUseCommand
                      : undefined
                  }
                  commandReady={ready}
                  // The sync step is only ever current once the service and
                  // task.md exist, i.e. exactly when syncing is possible.
                  onSync={step.state === "current" ? onSync : undefined}
                  syncBusy={validation.syncing}
                />
              </li>
            ))}
          </ol>
          {/* Footer: boot progress, or the all-set note */}
          {(booting || allDone) && (
            <div className="-mx-4 mt-3.5 -mb-3.5 flex items-center justify-center gap-1.5 border-t border-border/50 px-4 py-2 text-[11.5px] text-muted-foreground">
              {booting ? (
                <>
                  <Loader2 className="size-3 animate-spin" />
                  {t("autoResearch.arl.bootingHint")}
                </>
              ) : (
                <>
                  <Check className="size-3 text-emerald-500" />
                  {t("autoResearch.arl.guide.allDone")}
                </>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

/** Placeholder in guide strings for the inline sync-button replica. */
const SYNC_TOKEN = "{sync}"

/** Splits a guide string around the setup command and the sync token. */
const STEP_TOKEN_PATTERN = new RegExp(
  `(${SETUP_COMMAND}|${SYNC_TOKEN.replace(/[{}]/g, "\\$&")})`,
)

/**
 * One guide step's label, with inline chips for the tokens it mentions.
 *
 * - `/aris-setup` renders as a command chip; with `onUseCommand` set it is a
 *   button that fills the composer.
 * - `{sync}` renders as a small replica of the round sync button that sits
 *   between the two composers, so the text can point at it by appearance
 *   instead of a vague "the button in the middle". With `onSync` set the
 *   replica runs the sync.
 *
 * Keeping these inside the sentence (instead of a hint line plus a button
 * below it) is what lets each step fit on one line.
 */
function StepText({
  text,
  state,
  onUseCommand,
  commandReady,
  onSync,
  syncBusy,
}: {
  text: string
  state: GuideStepState
  onUseCommand?: () => void
  /** Agent is up, so the chip may fill the composer. */
  commandReady: boolean
  onSync?: () => void
  syncBusy: boolean
}) {
  const { t } = useTranslation()

  const chipBase =
    "mx-0.5 inline-flex items-center gap-1 rounded-md px-1.5 py-px align-baseline font-mono text-[12px] font-semibold"
  // Same shape and colours as the sync button between the composers,
  // shrunk to text size.
  const syncBase =
    "mx-0.5 inline-grid size-[18px] place-items-center rounded-full border bg-background align-[-4px] shadow-sm"

  const renderToken = (part: string, index: number) => {
    if (part === SETUP_COMMAND) {
      return onUseCommand ? (
        <button
          key={index}
          type="button"
          onClick={onUseCommand}
          disabled={!commandReady}
          title={
            commandReady
              ? t("autoResearch.arl.guide.useCommand")
              : t("autoResearch.arl.bootingHint")
          }
          className={cn(
            chipBase,
            "border border-(--arl-accent)/40 bg-(--arl-accent)/10 text-(--arl-accent-fg) transition-colors enabled:hover:border-(--arl-accent)/70 enabled:hover:bg-(--arl-accent)/18 disabled:cursor-not-allowed disabled:opacity-60",
          )}
        >
          {SETUP_COMMAND}
          <CornerDownLeft className="size-3 opacity-70" />
        </button>
      ) : (
        <code key={index} className={cn(chipBase, "bg-muted font-medium")}>
          {SETUP_COMMAND}
        </code>
      )
    }
    if (part === SYNC_TOKEN) {
      return onSync ? (
        <button
          key={index}
          type="button"
          onClick={onSync}
          disabled={syncBusy}
          title={t("autoResearch.arl.validationSync")}
          aria-label={t("autoResearch.arl.validationSync")}
          className={cn(
            syncBase,
            "border-amber-500/60 text-amber-600 transition-transform enabled:hover:scale-110 disabled:cursor-wait dark:text-amber-400",
          )}
        >
          {syncBusy ? (
            <Loader2 className="size-2.5 animate-spin" />
          ) : (
            <ArrowRight className="size-2.5" strokeWidth={2.5} />
          )}
        </button>
      ) : (
        <span
          key={index}
          aria-hidden
          className={cn(syncBase, "border-border text-muted-foreground")}
        >
          <ArrowRight className="size-2.5" strokeWidth={2.5} />
        </span>
      )
    }
    return part
  }

  return (
    <p
      className={cn(
        "min-w-0 flex-1 pt-[3px] text-[13px] leading-[20px]",
        state === "current" && "font-medium text-foreground",
        state === "done" &&
          "text-muted-foreground/70 line-through decoration-muted-foreground/40",
        state === "todo" && "text-muted-foreground",
      )}
    >
      {text.split(STEP_TOKEN_PATTERN).map(renderToken)}
    </p>
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
