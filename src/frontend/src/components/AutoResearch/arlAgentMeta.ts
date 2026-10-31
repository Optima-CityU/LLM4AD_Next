import type { TFunction } from "i18next"
import type { IconType } from "react-icons"
import { SiClaude, SiOpenai } from "react-icons/si"

import type {
  ARLAgentMode,
  ARLRole,
  ARLRoleState,
  ARLValidationState,
} from "@/hooks/useARLWebSocket"

/**
 * Engine logo per role: the Validator container runs Codex, the Worker runs
 * Claude Code.
 */
export const ARL_ROLE_LOGO: Record<ARLRole, IconType> = {
  validator: SiOpenai,
  worker: SiClaude,
}

/**
 * Class that scopes a role's accent colour to a subtree.
 *
 * The class (defined in `index.css`) publishes `--arl-accent` and
 * `--arl-accent-fg`, so descendants tint themselves with utilities such as
 * `bg-(--arl-accent)/10` instead of each component carrying a per-role map.
 * `--arl-accent-fg` is the text-safe shade, adjusted for dark mode.
 *
 * @param role - Container role.
 * @returns The scoping class name.
 */
export function arlRoleClass(role: ARLRole): string {
  return role === "validator" ? "arl-role-validator" : "arl-role-worker"
}

/** Visual tone of an agent, shared by the pane header and the status panel. */
export type ARLAgentTone =
  | "running"
  | "booting"
  | "ready"
  | "failed"
  | "offline"

/** Status dot per tone. `running` uses the role accent of the enclosing scope. */
export const ARL_TONE_DOT: Record<ARLAgentTone, string> = {
  running: "bg-(--arl-accent) animate-pulse",
  booting: "bg-amber-500 animate-pulse",
  ready: "bg-emerald-500",
  failed: "bg-destructive",
  offline: "bg-muted-foreground/40",
}

/** Status pill (text + tinted background) per tone. */
export const ARL_TONE_PILL: Record<ARLAgentTone, string> = {
  running: "bg-(--arl-accent)/12 text-(--arl-accent-fg)",
  booting: "bg-amber-500/12 text-amber-700 dark:text-amber-400",
  ready: "bg-emerald-500/12 text-emerald-700 dark:text-emerald-400",
  failed: "bg-destructive/12 text-destructive",
  offline: "bg-muted text-muted-foreground",
}

/** Derived, display-ready view of one container's agent. */
export interface ARLAgentView {
  /** Socket up and `agents_ready` received: the composer may send. */
  ready: boolean
  /** A connection exists or is being made, but the agent is not up yet. */
  booting: boolean
  /** A turn is in flight. */
  running: boolean
  tone: ARLAgentTone
  /** i18n key under `autoResearch.arl.` for the status text. */
  statusKey: string
}

/**
 * Collapse a role's raw state into what the UI shows.
 *
 * An open socket is not enough to call an agent ready: the backend only reads
 * frontend frames once it has booted the container and announced
 * `agents_ready`, so `ready` requires both.
 *
 * @param state - The role's reducer state.
 * @param connected - Whether the shared socket is open.
 * @param connecting - Whether a connection attempt is in flight.
 * @returns The derived view.
 */
export function describeAgent(
  state: ARLRoleState,
  connected: boolean,
  connecting: boolean,
): ARLAgentView {
  const ready = connected && state.ready
  const booting = (connected || connecting) && !state.ready
  const running = state.turnStatus === "running"
  // The main turn can end while subagents it spawned keep going (e.g. ones
  // created through MCP). Shown as its own status rather than "idle", but
  // `running` stays tied to the main turn: that is what the stop button
  // interrupts, same as the native client.
  const subagentsRunning = state.subagents.some((s) => s.status === "running")

  const statusKey = booting
    ? "booting"
    : state.canceling
      ? "stopping"
      : running
        ? "statusRunning"
        : state.turnStatus === "failed"
          ? "statusFailed"
          : subagentsRunning
            ? "statusSubagentsRunning"
            : state.turnStatus === "canceled"
              ? "statusCanceled"
              : state.turnStatus === "completed"
                ? "statusCompleted"
                : state.agentStatus === "running"
                  ? "statusRunning"
                  : "statusIdle"

  const tone: ARLAgentTone = booting
    ? "booting"
    : running || subagentsRunning || state.agentStatus === "running"
      ? "running"
      : state.turnStatus === "failed"
        ? "failed"
        : ready
          ? "ready"
          : "offline"

  return { ready, booting, running, tone, statusKey }
}

/**
 * i18n key (under `autoResearch.arl.`) for the session-wide connection state.
 *
 * @param status - Socket status reported by the hook.
 * @param booting - Socket is open but not every container is ready yet.
 * @returns The i18n key.
 */
export function connectionStatusKey(status: string, booting: boolean): string {
  if (status === "reconnecting") return "reconnecting"
  if (status === "connecting") return "connecting"
  if (booting) return "starting"
  if (status === "connected") return "connected"
  if (status === "offline") return "offline"
  return "disconnected"
}

/**
 * Whether the validation handoff can run: it needs both an address to write
 * into the Worker's `CLAUDE.md` and a `task.md` to copy.
 *
 * @param state - Validation handoff state.
 * @returns True when the sync action is meaningful.
 */
export function canSyncValidation(state: ARLValidationState): boolean {
  return state.configured && state.hasTask
}

/**
 * i18n keys (under `autoResearch.arl.`) for the handoff headline and hint.
 *
 * The two halves land at different times, so each missing piece gets its own
 * message.
 *
 * @param state - Validation handoff state.
 * @returns The title and hint keys.
 */
export function validationMessageKeys(state: ARLValidationState): {
  title: string
  hint: string
} {
  if (!state.configured)
    return { title: "validationMissing", hint: "validationMissingHint" }
  if (!state.hasTask)
    return { title: "validationNoTask", hint: "validationNoTaskHint" }
  return { title: "validationReady", hint: "validationReadyHint" }
}

/**
 * i18n key of the hint shown while the validation service is not ready.
 *
 * Before the Validator conversation starts the hint points at its start
 * button; once it has started that button is gone, so the hint says to
 * finish the setup in the conversation instead.
 *
 * @param validatorStarted - Whether the Validator pane has any messages.
 * @returns Full i18n key.
 */
export function workerWaitKey(validatorStarted: boolean): string {
  return validatorStarted
    ? "autoResearch.arl.guide.workerWaitBusy"
    : "autoResearch.arl.guide.workerWait"
}

/**
 * Paseo provider per role. Mode IDs are only unique within a provider (both
 * Claude and Codex have an `auto` mode), so translations are keyed by it.
 */
const ARL_ROLE_PROVIDER: Record<ARLRole, string> = {
  validator: "codex",
  worker: "claude",
}

/**
 * Localized label and description of a permission mode.
 *
 * The daemon only ships English text from Paseo's provider manifest. Known
 * modes are translated by provider + mode ID; unknown ones (e.g. a mode a
 * newer Paseo adds) fall back to the daemon's text.
 *
 * @param t - i18next translate function.
 * @param role - Container role, which determines the provider.
 * @param mode - Mode as reported by the daemon.
 * @returns Display-ready label and optional description.
 */
export function modeText(
  t: TFunction,
  role: ARLRole,
  mode: ARLAgentMode,
): { label: string; description?: string } {
  const base = `autoResearch.arl.modes.${ARL_ROLE_PROVIDER[role]}.${mode.id}`
  return {
    label: t(`${base}.label`, { defaultValue: mode.label }),
    description: mode.description
      ? t(`${base}.description`, { defaultValue: mode.description })
      : undefined,
  }
}
