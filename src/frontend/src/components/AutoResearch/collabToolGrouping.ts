/** A visible run of adjacent calls to the same tool. */
export type CollabToolRenderItem<T> =
  | { kind: "single"; item: T }
  | { kind: "group"; name: string; items: T[] }

/**
 * Group only adjacent calls to the same tool. Any non-tool item breaks a run,
 * preserving the order and meaning of the surrounding conversation.
 */
export function groupConsecutiveCollabTools<T>(
  items: readonly T[],
  getToolName: (item: T) => string | null,
): CollabToolRenderItem<T>[] {
  const result: CollabToolRenderItem<T>[] = []
  let index = 0

  while (index < items.length) {
    const item = items[index]
    const name = getToolName(item)
    if (!name) {
      result.push({ kind: "single", item })
      index += 1
      continue
    }

    let end = index + 1
    while (end < items.length && getToolName(items[end]) === name) {
      end += 1
    }

    if (end - index > 1) {
      result.push({ kind: "group", name, items: items.slice(index, end) })
    } else {
      result.push({ kind: "single", item })
    }
    index = end
  }

  return result
}
