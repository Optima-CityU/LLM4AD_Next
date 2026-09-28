/**
 * 协作 agent（AutoResearch ChatPanel 里的 AI 助手）工具调用卡片。
 *
 * 后端把一次工具调用拆成两帧 SSE 事件（`collab_tool`）：
 * - `phase="start"`：只有 `tool` / `tool_call_id`，用来先把「正在执行」的意图渲染出来；
 * - `phase="end"`：带上 `input`（入参，能解析成 JSON 时是对象，否则为 null）、
 *   `input_raw`（原始片段）、`output`（结果文本，超长已被后端截断）、`state`。
 *
 * 入参和结果都是**流式增量**拼出来的（`TOOL_CALL_DELTA` / `TOOL_RESULT_TEXT_DELTA`），
 * 所以 end 帧才代表这条调用完整。渲染时按 `tool_call_id` 归并：同 id 的 end 覆盖 start。
 */

import { AlertTriangle, ChevronRight, Loader2, SquareTerminal } from "lucide-react"
import { memo, useState } from "react"
import { useTranslation } from "react-i18next"

import { cn } from "@/lib/utils"

/** 一次工具调用的累积状态（由 ChatPanel 按 tool_call_id 归并 start / end 两帧）。 */
export interface CollabToolCall {
  /** 后端给的调用 id；缺失时退化为 `name#index`。 */
  id: string
  name: string
  /** 已收到 end 帧（无论成功失败）。 */
  done: boolean
  /** 入参：能解析成 JSON 时为对象，否则为 null，此时看 `inputRaw`。 */
  input: unknown
  inputRaw: string
  output: string
  state: string
}

// 折叠态摘要的最大字符数：工具入参里常带整段正文（Write 的 content、Edit 的 new_str），
// 不裁剪会把整条摘要撑成十几行，折叠就失去意义了。
const SUMMARY_MAX_CHARS = 120
// 展开后入参 / 结果各自的展示上限（后端已在 2000 字符处截断并追加 "... (truncated)"，
// 这里只是防止一次性渲染过多 DOM 节点）。
const IO_MAX_CHARS = 4000

/** 把任意入参压成单行短摘要，供折叠态显示。 */
function summarize(input: unknown, inputRaw: string): string {
  if (input && typeof input === "object") {
    // 优先展示路径类字段——问「改了哪个文件」是最常见的问题。
    const record = input as Record<string, unknown>
    const preferred = ["path", "file_path", "dir", "pattern"]
    const parts: string[] = []
    for (const key of preferred) {
      const value = record[key]
      if (typeof value === "string" && value) parts.push(value)
    }
    if (parts.length === 0) {
      for (const [key, value] of Object.entries(record)) {
        if (typeof value === "string" || typeof value === "number") {
          parts.push(`${key}=${String(value)}`)
        }
        if (parts.length >= 2) break
      }
    }
    if (parts.length > 0) {
      const joined = parts.join(" · ")
      return joined.length > SUMMARY_MAX_CHARS
        ? `${joined.slice(0, SUMMARY_MAX_CHARS)}…`
        : joined
    }
  }
  const fallback = (inputRaw || "").replace(/\s+/g, " ").trim()
  return fallback.length > SUMMARY_MAX_CHARS
    ? `${fallback.slice(0, SUMMARY_MAX_CHARS)}…`
    : fallback
}

/** 展开态展示入参：JSON 解析成功就 pretty-print，否则原样显示半截片段。 */
function formatInput(call: CollabToolCall): string {
  const text =
    call.input != null
      ? JSON.stringify(call.input, null, 2)
      : call.inputRaw || ""
  return text.length > IO_MAX_CHARS ? `${text.slice(0, IO_MAX_CHARS)}…` : text
}

/** 单条工具调用的可折叠卡片。 */
function CollabToolCardInner({ call }: { call: CollabToolCall }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)

  // state 为空代表还在跑（后端在结果到达前报的是 ""）。denied / interrupted / error
  // 都按失败上色，方便一眼看出这轮里有没有工具调用被拒绝或被打断。
  const failed = call.done && !!call.state && call.state !== "success"
  const summary = summarize(call.input, call.inputRaw)
  const inputText = formatInput(call)
  const hasDetail = !!inputText || !!call.output

  return (
    <div
      className={cn(
        "rounded-lg border text-xs overflow-hidden transition-colors",
        failed
          ? "border-destructive/40 bg-destructive/5"
          : "border-border/50 bg-background/50",
      )}
    >
      <button
        type="button"
        onClick={() => hasDetail && setOpen((o) => !o)}
        className={cn(
          "flex w-full items-center gap-1.5 px-2 py-1 text-left",
          hasDetail ? "cursor-pointer hover:bg-muted/40" : "cursor-default",
        )}
      >
        <ChevronRight
          className={cn(
            "size-3 shrink-0 text-muted-foreground/60 transition-transform",
            open && "rotate-90",
            !hasDetail && "opacity-0",
          )}
        />
        {call.done ? (
          failed ? (
            <AlertTriangle className="size-3 shrink-0 text-destructive" />
          ) : (
            <SquareTerminal className="size-3 shrink-0 text-primary" />
          )
        ) : (
          <Loader2 className="size-3 shrink-0 animate-spin text-primary" />
        )}
        <span className="shrink-0 font-mono text-foreground/90">
          {call.name}
        </span>
        <span className="min-w-0 flex-1 truncate font-mono text-muted-foreground/70">
          {summary}
        </span>
        {failed && (
          <span className="shrink-0 text-[10px] text-destructive">
            {call.state}
          </span>
        )}
      </button>

      {open && hasDetail && (
        <div className="space-y-1 border-t border-border/40 px-2 py-1.5">
          {inputText && (
            <div>
              <div className="text-[10px] uppercase tracking-wider text-muted-foreground/60">
                {t("autoResearch.collab.toolInput")}
              </div>
              <pre className="mt-0.5 max-h-48 overflow-auto whitespace-pre-wrap break-all rounded bg-muted/30 px-1.5 py-1 font-mono text-[11px] text-foreground/80">
                {inputText}
              </pre>
            </div>
          )}
          {call.output && (
            <div>
              <div className="text-[10px] uppercase tracking-wider text-muted-foreground/60">
                {t("autoResearch.collab.toolOutput")}
              </div>
              <pre className="mt-0.5 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded bg-muted/30 px-1.5 py-1 font-mono text-[11px] text-foreground/80">
                {call.output}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export const CollabToolCard = memo(CollabToolCardInner)
