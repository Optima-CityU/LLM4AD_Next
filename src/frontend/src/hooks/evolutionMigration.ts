export interface MigrationRecord {
  id?: unknown
  source_id?: unknown
}

export interface RawMigrationEvent {
  type?: unknown
  migrants?: unknown
}

/** Build clone-id → source-id aliases from one structured migration event. */
export function collectMigrationAliases(
  event: RawMigrationEvent,
): Map<string, string> {
  const aliases = new Map<string, string>()
  if (!Array.isArray(event.migrants)) return aliases

  for (const raw of event.migrants) {
    if (!raw || typeof raw !== "object") continue
    const record = raw as MigrationRecord
    if (
      typeof record.id !== "string" ||
      record.id.length === 0 ||
      typeof record.source_id !== "string" ||
      record.source_id.length === 0
    ) {
      continue
    }
    aliases.set(record.id, record.source_id)
  }
  return aliases
}

/** Resolve invisible migrated clones to visible source algorithms. */
export function resolveMigrationParentIds(
  parentIds: string[],
  aliases: ReadonlyMap<string, string>,
): string[] {
  return [
    ...new Set(parentIds.map((parentId) => aliases.get(parentId) ?? parentId)),
  ]
}
