export function resolveCurrentReport<T>(
  taskReport: T | null | undefined,
  queriedReport: T | null | undefined,
): T | null {
  return queriedReport !== undefined ? queriedReport : (taskReport ?? null)
}
