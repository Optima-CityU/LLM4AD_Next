import hljsLightCss from "highlight.js/styles/github.css?raw"
import hljsDarkCss from "highlight.js/styles/github-dark.css?raw"
import { useEffect } from "react"

import { useTheme } from "@/components/theme-provider"

const STYLE_ID = "hljs-theme-style"

// Reference count of mounted consumers. The highlight.js theme is a single
// global <style> element shared by every markdown surface (guide, chat tune,
// floating AI chat). Reference counting keeps it alive as long as at least one
// consumer is mounted, so unmounting one view never strips highlighting from
// another that is still on screen.
let refCount = 0

/**
 * Inject the highlight.js theme stylesheet for `rehype-highlight` output.
 *
 * Adds a single shared `<style>` to `document.head`, swapping between the light
 * and dark GitHub themes based on the resolved app theme. The element is removed
 * only once the last consuming component unmounts.
 *
 * Call this once per markdown surface (at the container component level), not
 * per rendered message.
 */
export function useHljsTheme(): void {
  const { resolvedTheme } = useTheme()

  // Mount/unmount: create the shared style element and reference-count it.
  useEffect(() => {
    let style = document.getElementById(STYLE_ID) as HTMLStyleElement | null
    if (!style) {
      style = document.createElement("style")
      style.id = STYLE_ID
      document.head.appendChild(style)
    }
    refCount += 1
    return () => {
      refCount -= 1
      if (refCount <= 0) {
        document.getElementById(STYLE_ID)?.remove()
        refCount = 0
      }
    }
  }, [])

  // Keep the stylesheet content in sync with the active theme.
  useEffect(() => {
    const style = document.getElementById(STYLE_ID) as HTMLStyleElement | null
    if (style) {
      style.textContent = resolvedTheme === "dark" ? hljsDarkCss : hljsLightCss
    }
  }, [resolvedTheme])
}
