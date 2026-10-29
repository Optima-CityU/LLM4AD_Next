/**
 * Slash-command completion for the ARL composer.
 *
 * Each container's daemon reports the commands its agent understands (built-in
 * CLI commands plus installed skills) via `list_commands_request`. This module
 * holds the pure pieces — normalizing that list, deciding when the menu is
 * open, filtering, and writing a choice back into the input — so they can be
 * tested without a DOM.
 */

/** One entry of the daemon's `list_commands_response`. */
export interface ARLCommand {
  name: string
  description: string
  /** Usage hint for arguments, e.g. `[<objective>|pause|resume|clear]`. */
  argumentHint: string
  /** `command` for built-ins, `skill` for installed skills. */
  kind: string
}

/** Per-role command list as fetched from the daemon. */
export interface ARLCommandsState {
  commands: ARLCommand[]
  /** A `list_commands` request is in flight. */
  loading: boolean
  /** True once at least one response (success or failure) has arrived. */
  loaded: boolean
  error: string | null
}

export function emptyCommandsState(): ARLCommandsState {
  return { commands: [], loading: false, loaded: false, error: null }
}

/**
 * Coerce the daemon's raw command list into well-formed entries.
 *
 * Drops nameless entries, strips a leading `/` some providers include, and
 * de-duplicates by name (first occurrence wins) so React keys stay unique.
 */
export function normalizeCommands(raw: unknown): ARLCommand[] {
  if (!Array.isArray(raw)) return []
  const seen = new Set<string>()
  const commands: ARLCommand[] = []
  for (const entry of raw) {
    if (!entry || typeof entry !== "object") continue
    const record = entry as Record<string, unknown>
    const name =
      typeof record.name === "string"
        ? record.name.trim().replace(/^\//, "")
        : ""
    if (!name || seen.has(name)) continue
    seen.add(name)
    commands.push({
      name,
      description:
        typeof record.description === "string" ? record.description : "",
      argumentHint:
        typeof record.argumentHint === "string" ? record.argumentHint : "",
      kind: typeof record.kind === "string" ? record.kind : "command",
    })
  }
  return commands
}

/**
 * The command name being typed, or `null` when the menu should be closed.
 *
 * The menu is open only while the whole input is `/` followed by a partial
 * name: once a space (or newline) follows, the user has moved on to the
 * arguments and the menu would only get in the way.
 */
export function slashQuery(input: string): string | null {
  const match = /^\/(\S*)$/.exec(input)
  return match ? match[1] : null
}

/**
 * Commands matching a partial name, prefix matches first.
 *
 * Within each group the daemon's order is kept; it already lists commands
 * deliberately. Matching is case-insensitive.
 */
export function filterCommands(
  commands: ARLCommand[],
  query: string,
): ARLCommand[] {
  const q = query.toLowerCase()
  if (!q) return commands
  const prefix: ARLCommand[] = []
  const contains: ARLCommand[] = []
  for (const command of commands) {
    const name = command.name.toLowerCase()
    if (name.startsWith(q)) prefix.push(command)
    else if (name.includes(q)) contains.push(command)
  }
  return [...prefix, ...contains]
}

/**
 * Composer text after picking a command: `/name ` with a trailing space, so
 * the user can type arguments straight away and the menu closes.
 */
export function applyCommand(command: ARLCommand): string {
  return `/${command.name} `
}
