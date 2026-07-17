export function downgradeUnavailableLongTermMemory(
  memory: Record<string, unknown>,
): Record<string, unknown> {
  if (memory.type === "mindmemos_cloud" && memory.enabled !== false) {
    return { enabled: true, type: "local_yaml" }
  }

  return memory
}
