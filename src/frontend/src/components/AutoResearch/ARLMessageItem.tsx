import {
  AlertTriangle,
  Bot,
  Brain,
  CheckCircle2,
  ChevronRight,
  Circle,
  CircleDot,
  Cog,
  FilePen,
  FileText,
  Globe,
  Hammer,
  Info,
  ListChecks,
  Loader2,
  Search,
  Terminal,
} from "lucide-react"
import { memo, useId, useState } from "react"
import { useTranslation } from "react-i18next"
import Markdown from "react-markdown"

import {
  MARKDOWN_REHYPE_PLUGINS,
  MARKDOWN_REMARK_PLUGINS,
  makeMarkdownComponents,
} from "@/components/markdown/markdownComponents"
import type { ARLStreamItem } from "@/hooks/useARLWebSocket"
import { cn } from "@/lib/utils"

/**
 * Longest detail body rendered inside an expanded tool call. Tool payloads can
 * carry whole file contents; truncating keeps a single call from dominating
 * the pane.
 */
const DETAIL_MAX_CHARS = 6000

/** Longest inline summary shown on a collapsed tool-call row. */
const SUMMARY_MAX_CHARS = 140

/** Chat body markdown, matching the AutoResearch chat surfaces. */
function MessageMarkdown({
  content,
  streaming,
}: {
  content: string
  streaming?: boolean
}) {
  const mdId = useId()
  return (
    <div className="prose prose-sm dark:prose-invert max-w-none break-words prose-headings:text-foreground prose-headings:font-semibold prose-h1:text-base prose-h2:text-sm prose-h3:text-sm prose-p:my-1.5 prose-pre:bg-transparent prose-pre:p-0 prose-pre:my-1.5 prose-ul:my-1.5 prose-ol:my-1.5 prose-li:my-0.5">
      <Markdown
        remarkPlugins={MARKDOWN_REMARK_PLUGINS}
        rehypePlugins={MARKDOWN_REHYPE_PLUGINS}
        components={makeMarkdownComponents(mdId, { streaming })}
      >
        {content}
      </Markdown>
    </div>
  )
}

function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max)}…` : text
}

function asText(value: unknown): string {
  if (value === null || value === undefined) return ""
  if (typeof value === "string") return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

/** Icon matching the tool's `detail.type`, mirroring the Paseo renderer. */
function toolIcon(detailType: string | undefined) {
  switch (detailType) {
    case "shell":
      return Terminal
    case "read":
      return FileText
    case "edit":
    case "write":
      return FilePen
    case "search":
      return Search
    case "fetch":
      return Globe
    case "sub_agent":
      return Bot
    case "plan":
      return ListChecks
    case "plain_text":
      return CircleDot
    case "worktree_setup":
      return Hammer
    default:
      return Cog
  }
}

/** One-line summary of a tool call, used on the collapsed row. */
function toolSummary(detail: any): string {
  if (!detail || typeof detail !== "object") return ""
  switch (detail.type) {
    case "shell":
      return truncate(String(detail.command ?? ""), SUMMARY_MAX_CHARS)
    case "read":
    case "edit":
    case "write":
      return truncate(String(detail.filePath ?? ""), SUMMARY_MAX_CHARS)
    case "search":
      return truncate(String(detail.query ?? ""), SUMMARY_MAX_CHARS)
    case "fetch":
      return truncate(String(detail.url ?? ""), SUMMARY_MAX_CHARS)
    case "sub_agent":
      return truncate(
        String(detail.description ?? detail.subAgentType ?? ""),
        SUMMARY_MAX_CHARS,
      )
    case "plan":
      return truncate(String(detail.text ?? ""), SUMMARY_MAX_CHARS)
    case "plain_text":
      return truncate(
        String(detail.label ?? detail.text ?? ""),
        SUMMARY_MAX_CHARS,
      )
    default:
      return truncate(
        asText(detail.input ?? detail).replace(/\s+/g, " "),
        SUMMARY_MAX_CHARS,
      )
  }
}

/** Expanded body of a tool call: the most informative field per detail type. */
function toolBody(detail: any): string {
  if (!detail || typeof detail !== "object") return asText(detail)
  switch (detail.type) {
    case "shell":
      return [detail.command, detail.output].filter(Boolean).join("\n\n")
    case "read":
    case "write":
      return asText(detail.content)
    case "edit":
      return asText(detail.unifiedDiff ?? detail.newString)
    case "search":
      return asText(detail.content ?? detail.filePaths)
    case "fetch":
      return asText(detail.result)
    case "sub_agent":
      return asText(detail.log)
    case "plan":
      return asText(detail.text)
    case "plain_text":
      return asText(detail.text ?? detail.label)
    default:
      return asText(detail.input ?? detail)
  }
}

/** A collapsible tool-call row: one badge line, expandable detail panel. */
function ToolCallRow({
  item,
}: {
  item: Extract<ARLStreamItem, { kind: "tool_call" }>
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const detail = item.detail as any
  const Icon = toolIcon(detail?.type)
  const summary = toolSummary(detail)
  const body = toolBody(detail)

  const failed = item.status === "failed"
  const running = item.status === "running"
  const statusLabel = failed
    ? t("autoResearch.arl.toolFailed")
    : item.status === "canceled"
      ? t("autoResearch.arl.toolCanceled")
      : running
        ? t("autoResearch.arl.toolRunning")
        : t("autoResearch.arl.toolCompleted")

  const hasDetail = !!body || !!item.error

  return (
    <div className="py-0.5">
      <button
        type="button"
        onClick={() => hasDetail && setOpen((v) => !v)}
        className={cn(
          "-mx-2 flex w-full items-center gap-2 rounded-lg border border-transparent px-2 py-1 text-left text-[12.5px] leading-[18px] transition-colors",
          hasDetail ? "cursor-pointer hover:bg-muted/50" : "cursor-default",
        )}
      >
        <ChevronRight
          className={cn(
            "size-3 shrink-0 text-muted-foreground/60 transition-transform",
            open && "rotate-90",
            !hasDetail && "opacity-0",
          )}
        />
        {running ? (
          <Loader2 className="size-3.5 shrink-0 animate-spin text-primary" />
        ) : failed ? (
          <AlertTriangle className="size-3.5 shrink-0 text-destructive" />
        ) : (
          <Icon className="size-3.5 shrink-0 text-muted-foreground" />
        )}
        <span className="shrink-0 font-medium text-foreground/90">
          {item.name}
        </span>
        {summary && (
          <span className="min-w-0 flex-1 truncate font-mono text-[11.5px] text-muted-foreground/70">
            {summary}
          </span>
        )}
        <span
          className={cn(
            "shrink-0 text-[10.5px]",
            failed ? "text-destructive" : "text-muted-foreground/50",
          )}
        >
          {statusLabel}
        </span>
      </button>

      {open && hasDetail && (
        <div className="mt-1 mb-1.5 overflow-hidden rounded-lg border border-border/50">
          {body && (
            <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-all bg-muted/30 px-2.5 py-2 font-mono text-[11.5px] leading-[17px] text-foreground/80">
              {truncate(body, DETAIL_MAX_CHARS)}
            </pre>
          )}
          {item.error && (
            <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all border-t border-border/40 bg-destructive/10 px-2.5 py-2 font-mono text-[11.5px] text-destructive">
              {asText(item.error)}
            </pre>
          )}
        </div>
      )}
    </div>
  )
}

/** Reasoning stream, collapsed to a single row until expanded. */
function ThoughtRow({
  item,
}: {
  item: Extract<ARLStreamItem, { kind: "thought" }>
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  return (
    <div className="py-0.5">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="-mx-2 flex w-full items-center gap-2 rounded-lg border border-transparent px-2 py-1 text-left text-[12.5px] leading-[18px] transition-colors hover:bg-muted/50"
      >
        <ChevronRight
          className={cn(
            "size-3 shrink-0 text-muted-foreground/60 transition-transform",
            open && "rotate-90",
          )}
        />
        {item.streaming ? (
          <Loader2 className="size-3.5 shrink-0 animate-spin text-muted-foreground" />
        ) : (
          <Brain className="size-3.5 shrink-0 text-muted-foreground" />
        )}
        <span className="shrink-0 text-muted-foreground">
          {t("autoResearch.arl.thinking")}
        </span>
        <span className="min-w-0 flex-1 truncate font-mono text-[11.5px] text-muted-foreground/60">
          {truncate(item.text.replace(/\s+/g, " "), SUMMARY_MAX_CHARS)}
        </span>
      </button>
      {open && (
        <div className="mt-1 mb-1.5 overflow-hidden rounded-lg border border-border/50">
          <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words px-2.5 py-2 text-[12.5px] leading-[19px] text-muted-foreground">
            {truncate(item.text, DETAIL_MAX_CHARS)}
          </pre>
        </div>
      )}
    </div>
  )
}

/** Format a token count as `168.2k` / `1.2M`. */
function formatTokens(tokens: number): string {
  if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(1)}M`
  if (tokens >= 1_000) return `${(tokens / 1_000).toFixed(1)}k`
  return String(tokens)
}

/** Divider marking a context compaction, live while it is in progress. */
function CompactionRow({
  item,
}: {
  item: Extract<ARLStreamItem, { kind: "compaction" }>
}) {
  const { t } = useTranslation()
  const loading = item.status === "loading"
  const details: string[] = []
  if (item.trigger === "auto" || item.trigger === "manual") {
    details.push(t(`autoResearch.arl.compactionTrigger.${item.trigger}`))
  }
  if (item.preTokens !== undefined) {
    details.push(
      t("autoResearch.arl.compactionPreTokens", {
        tokens: formatTokens(item.preTokens),
      }),
    )
  }
  return (
    <div
      className="my-2 flex items-center gap-2 text-[11.5px] text-muted-foreground/60"
      role="status"
    >
      <span className="h-px flex-1 bg-border/60" />
      {loading ? (
        <Loader2 className="size-3 shrink-0 animate-spin text-primary" />
      ) : (
        <span aria-hidden="true">✂</span>
      )}
      <span>
        {loading
          ? t("autoResearch.arl.compacting")
          : t("autoResearch.arl.compacted")}
        {!loading && details.length > 0 && ` · ${details.join(" · ")}`}
      </span>
      <span className="h-px flex-1 bg-border/60" />
    </div>
  )
}

/** Compact todo checklist. */
function TodoRow({
  item,
}: {
  item: Extract<ARLStreamItem, { kind: "todo_list" }>
}) {
  const { t } = useTranslation()
  const todos = (item.items ?? []) as any[]
  if (todos.length === 0) return null
  return (
    <div className="my-1.5 overflow-hidden rounded-lg border border-border/50 bg-background/40 px-3 py-2">
      <div className="mb-1 text-[10.5px] uppercase tracking-wider text-muted-foreground/60">
        {t("autoResearch.arl.todoTitle")}
      </div>
      <ul className="space-y-0.5">
        {todos.map((todo, index) => {
          const done =
            todo?.status === "completed" ||
            todo?.status === "done" ||
            todo?.completed === true
          const active =
            todo?.status === "in_progress" || todo?.status === "active"
          return (
            <li
              key={todo?.id ?? index}
              className="flex items-start gap-1.5 text-[12.5px] leading-[18px]"
            >
              {done ? (
                <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-primary" />
              ) : active ? (
                <Loader2 className="mt-0.5 size-3.5 shrink-0 animate-spin text-primary" />
              ) : (
                <Circle className="mt-0.5 size-3.5 shrink-0 text-muted-foreground/50" />
              )}
              <span
                className={cn(
                  "min-w-0 break-words",
                  done
                    ? "text-muted-foreground line-through"
                    : "text-foreground/90",
                )}
              >
                {String(todo?.text ?? todo?.content ?? todo?.title ?? "")}
              </span>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

/** Inline notification / error card with a translucent level tint. */
function NotificationRow({
  item,
}: {
  item: Extract<ARLStreamItem, { kind: "notification" }>
}) {
  const level = item.level || "info"
  const tint =
    level === "error"
      ? "border-destructive/40 bg-destructive/10 text-destructive"
      : level === "warning"
        ? "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400"
        : "border-border/50 bg-muted/40 text-muted-foreground"
  const Icon = level === "error" || level === "warning" ? AlertTriangle : Info
  return (
    <div
      className={cn(
        "my-1.5 flex items-start gap-2 overflow-hidden rounded-lg border px-3 py-2.5",
        tint,
      )}
    >
      <Icon className="mt-0.5 size-4 shrink-0" />
      <span className="min-w-0 whitespace-pre-wrap break-words text-sm leading-5">
        {item.message}
      </span>
    </div>
  )
}

/**
 * Render one timeline item inside a role pane.
 *
 * Rows follow the Paseo chat anatomy: user messages are right-aligned bubbles,
 * assistant output is full-width markdown, and everything the agent *did*
 * (tools, reasoning, todos) renders as a compact collapsible row so long
 * transcripts stay scannable.
 */
function ARLMessageItemInner({ item }: { item: ARLStreamItem }) {
  switch (item.kind) {
    case "user_message":
      return (
        <div className="flex justify-end py-1.5">
          <div className="max-w-[85%] rounded-2xl rounded-tr-[4px] bg-muted px-4 py-2.5 text-sm leading-[21px] break-words whitespace-pre-wrap text-foreground">
            {item.text}
          </div>
        </div>
      )

    case "assistant_message":
      if (!item.text) return null
      return (
        <div className="py-3">
          <MessageMarkdown content={item.text} streaming={item.streaming} />
        </div>
      )

    case "thought":
      return <ThoughtRow item={item} />

    case "tool_call":
      return <ToolCallRow item={item} />

    case "todo_list":
      return <TodoRow item={item} />

    case "notification":
      return <NotificationRow item={item} />

    case "compaction":
      return <CompactionRow item={item} />

    case "system":
      return (
        <div className="py-1 text-center text-[11.5px] text-muted-foreground/70">
          {item.message}
        </div>
      )

    default:
      return null
  }
}

export default memo(ARLMessageItemInner)
