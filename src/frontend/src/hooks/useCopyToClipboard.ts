// source: https://usehooks-ts.com/react-hook/use-copy-to-clipboard
import { useCallback, useState } from "react"

type CopiedValue = string | null

type CopyFn = (text: string) => Promise<boolean>

function fallbackCopyText(text: string): boolean {
  // Append the textarea inside the currently active dialog (if any) instead
  // of document.body, so Radix Dialog's focus trap / inert siblings don't
  // block the selection. Falls back to body when no dialog is active.
  const container =
    (document.activeElement?.closest(
      "[role='dialog']",
    ) as HTMLElement | null) ?? document.body

  const textarea = document.createElement("textarea")
  textarea.value = text
  textarea.setAttribute("readonly", "")
  textarea.style.position = "fixed"
  textarea.style.top = "0"
  textarea.style.left = "0"
  textarea.style.width = "1px"
  textarea.style.height = "1px"
  textarea.style.padding = "0"
  textarea.style.border = "none"
  textarea.style.opacity = "0"
  textarea.style.pointerEvents = "none"

  const previouslyFocused = document.activeElement as HTMLElement | null
  container.appendChild(textarea)

  try {
    textarea.focus({ preventScroll: true })
    textarea.select()
    // iOS Safari ignores .select() on dynamically-created textareas; an
    // explicit setSelectionRange is the only reliable way to ensure the
    // selection is what execCommand actually copies.
    textarea.setSelectionRange(0, text.length)
    return document.execCommand("copy")
  } catch {
    return false
  } finally {
    container.removeChild(textarea)
    previouslyFocused?.focus?.()
  }
}

export function useCopyToClipboard(): [CopiedValue, CopyFn] {
  const [copiedText, setCopiedText] = useState<CopiedValue>(null)

  const copy: CopyFn = useCallback(async (text) => {
    let ok = false
    if (window.isSecureContext && navigator?.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(text)
        ok = true
      } catch {
        ok = fallbackCopyText(text)
      }
    } else {
      ok = fallbackCopyText(text)
    }

    if (ok) {
      setCopiedText(text)
      setTimeout(() => setCopiedText(null), 2000)
    } else {
      console.warn("Copy failed")
      setCopiedText(null)
    }

    return ok
  }, [])

  return [copiedText, copy]
}
