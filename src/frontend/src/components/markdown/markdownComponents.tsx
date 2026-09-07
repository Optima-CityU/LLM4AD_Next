import { Check, Copy } from "lucide-react"
import { Children, isValidElement, type ReactNode, useState } from "react"
import { useTranslation } from "react-i18next"
import type { Components } from "react-markdown"
import rehypeHighlight from "rehype-highlight"
import rehypeKatex from "rehype-katex"
import remarkGfm from "remark-gfm"
import remarkMath from "remark-math"

import { useCopyToClipboard } from "@/hooks/useCopyToClipboard"
import useCustomToast from "@/hooks/useCustomToast"
import { cn } from "@/lib/utils"

// KaTeX stylesheet for LaTeX math rendering ($...$ and $$...$$). Bundled so
// formulas render offline without a CDN. Applies to every markdown surface
// that uses the shared plugin arrays below.
import "katex/dist/katex.min.css"

import { MermaidDiagram } from "./MermaidDiagram"

/**
 * GitHub-flavored markdown (tables, task lists, strikethrough, ...) plus math
 * parsing (`remark-math`) so `$inline$` and `$$block$$` LaTeX are recognized.
 */
export const MARKDOWN_REMARK_PLUGINS = [remarkGfm, remarkMath]
/**
 * Syntax highlighting for fenced code blocks (`rehype-highlight`) and LaTeX math
 * rendering (`rehype-katex`). Highlight token colors come from the highlight.js
 * theme stylesheet injected via `useHljsTheme()`; math styling comes from the
 * bundled KaTeX stylesheet imported above.
 */
export const MARKDOWN_REHYPE_PLUGINS = [rehypeHighlight, rehypeKatex]

/**
 * Recursively flatten a React node tree to its plain text, used to recover the
 * raw source of a code block for the copy-to-clipboard action.
 */
export function extractTextContent(children: ReactNode): string {
  if (typeof children === "string") return children
  if (Array.isArray(children)) return children.map(extractTextContent).join("")
  if (children && typeof children === "object" && "props" in children) {
    return extractTextContent(
      (children as React.ReactElement<{ children?: ReactNode }>).props.children,
    )
  }
  return ""
}

/** Hover-revealed copy button anchored to the top-right of a code block. */
export function CodeCopyButton({ text }: { text: string }) {
  const { t } = useTranslation()
  const [copied, setCopied] = useState(false)
  const [, copy] = useCopyToClipboard()
  const { showSuccessToast, showErrorToast } = useCustomToast()

  const handleCopy = async () => {
    const ok = await copy(text)
    if (ok) {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
      showSuccessToast(t("contactUs.copySuccess"))
    } else {
      showErrorToast(t("contactUs.copyFailed"))
    }
  }

  return (
    <button
      type="button"
      onClick={handleCopy}
      className="absolute top-2 right-2 p-1.5 rounded-md bg-background/80 border border-border/50 text-muted-foreground hover:text-foreground opacity-0 group-hover/code:opacity-100 transition-opacity"
      aria-label="Copy code"
    >
      {copied ? (
        <Check className="size-3.5 text-green-500" />
      ) : (
        <Copy className="size-3.5" />
      )}
    </button>
  )
}

/**
 * Build the `react-markdown` component overrides shared by every markdown
 * surface (the user manual / guide and the chat surfaces).
 *
 * Fenced ```mermaid blocks render as diagrams; every other code block gets a
 * hover copy button and keeps its highlight.js token colors. While a message is
 * still streaming, mermaid blocks are left as plain code so partial, unparseable
 * charts don't flash render errors.
 *
 * Callers may spread the result and add surface-specific overrides (e.g. a
 * custom `a` link handler):
 * `components={{ ...makeMarkdownComponents(id), a: myLink }}`.
 *
 * @param idPrefix - Stable prefix (e.g. from `useId()`) for mermaid element ids.
 * @param opts.streaming - Whether the message is still streaming.
 * @returns Component map to pass to `<Markdown components={...} />`.
 */
export function makeMarkdownComponents(
  idPrefix: string,
  opts?: { streaming?: boolean },
): Components {
  return {
    pre: ({ children, className, node: _node, ...props }) => {
      const childArray = Children.toArray(children)
      const isMermaid = childArray.some(
        (child) => isValidElement(child) && child.type === MermaidDiagram,
      )
      // The mermaid diagram replaces the <pre>; render it bare.
      if (isMermaid) return <>{children}</>

      const codeText = extractTextContent(children)
      return (
        <div className="group/code relative">
          <pre
            {...props}
            className={cn(
              "overflow-x-auto [&_code]:bg-transparent [&_code]:p-0 [&_code]:text-inherit",
              className,
            )}
            style={{ ["--tw-prose-pre-code" as string]: "currentColor" }}
          >
            {children}
          </pre>
          <CodeCopyButton text={codeText} />
        </div>
      )
    },
    code: ({ className, children, node, ...props }) => {
      if (/language-mermaid/.test(className || "") && !opts?.streaming) {
        // Derive the mermaid element id from the block's source position rather
        // than a render-order counter. The counter lived in this closure, so a
        // memoized component map (chat surfaces) never reset it across renders,
        // drifting the id every render and forcing MermaidDiagram to re-render.
        // The source position is stable per occurrence and unique per block.
        const pos = node?.position?.start
        const key = pos ? `${pos.line}-${pos.column}` : "0"
        return (
          <MermaidDiagram
            id={`${idPrefix}-${key}`}
            chart={String(children).trimEnd()}
          />
        )
      }
      return (
        <code {...props} className={className}>
          {children}
        </code>
      )
    },
  }
}
