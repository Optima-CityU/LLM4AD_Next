import {
  AlertTriangle,
  Check,
  ChevronDown,
  CircleDot,
  Cog,
  Download,
  FileText,
  FlaskConical,
  Info,
  Loader2,
  Play,
  Sparkles,
  X,
} from "lucide-react"
import { useId, useState } from "react"
import { useTranslation } from "react-i18next"
import Markdown from "react-markdown"
import { toast } from "sonner"

import type { ResearchMessageItem } from "@/client"
import {
  MARKDOWN_REHYPE_PLUGINS,
  MARKDOWN_REMARK_PLUGINS,
  makeMarkdownComponents,
} from "@/components/markdown/markdownComponents"
import { downloadResearchArtifact } from "@/hooks/useAutoResearch"
import { useHljsTheme } from "@/hooks/useHljsTheme"
import { cn } from "@/lib/utils"

import ArtifactPreviewDialog from "./ArtifactPreviewDialog"

/** 聊天气泡内的 markdown 渲染（GFM + 代码高亮 + mermaid）。 */
function MessageMarkdown({ content }: { content: string }) {
  useHljsTheme()
  const mdId = useId()
  return (
    <div className="prose prose-sm dark:prose-invert max-w-none break-words prose-headings:text-foreground prose-headings:font-semibold prose-h1:text-base prose-h2:text-sm prose-h3:text-sm prose-p:my-1.5 prose-pre:bg-transparent prose-pre:p-0 prose-pre:my-1.5 prose-ul:my-1.5 prose-ol:my-1.5 prose-li:my-0.5">
      <Markdown
        remarkPlugins={MARKDOWN_REMARK_PLUGINS}
        rehypePlugins={MARKDOWN_REHYPE_PLUGINS}
        components={makeMarkdownComponents(mdId)}
      >
        {content}
      </Markdown>
    </div>
  )
}

interface Props {
  message: ResearchMessageItem
  sessionId?: string
  /** 门控 form 在消息流里只作历史只读回显（活跃门控的操作在底部 GatePanel）。 */
  stale?: boolean
}

/**
 * 单条消息渲染。按 role + payload.kind 分派：
 * - USER：右对齐气泡（gate_reply / collab_turn 附带回显）。
 * - ASSISTANT + kind==form/choice：门控历史只读回显。
 * - ASSISTANT 其它：左对齐气泡。
 * - SYSTEM：紧凑事件行。
 */
export default function MessageItem({ message, sessionId, stale }: Props) {
  if (message.role === "system") {
    return <SystemEventRow message={message} />
  }
  if (message.role === "user") {
    return <UserRow message={message} />
  }
  const kind = (message.payload as { kind?: string } | null)?.kind
  if (kind === "form" || kind === "choice") {
    return (
      <AssistantFormRow message={message} sessionId={sessionId} stale={stale} />
    )
  }

  // 如果是 assistant 消息且没有 content，隐藏（避免永久 loading 或空白气泡）
  if (message.role === "assistant" && !message.content) {
    return null
  }

  return (
    <div className="flex justify-start px-4 py-1">
      <div className="w-full rounded-2xl rounded-tl-sm bg-card/60 border border-border/50 backdrop-blur-sm px-4 py-2 text-sm break-words shadow-sm">
        {message.content ? (
          <MessageMarkdown content={message.content} />
        ) : (
          <span className="italic opacity-60 inline-flex items-center gap-1">
            <Loader2 className="size-3 animate-spin" />
            {message.turn_status === "running" ? "..." : "—"}
          </span>
        )}
        {message.error && (
          <p className="mt-2 text-xs text-destructive">
            <AlertTriangle className="size-3 inline mr-1" />
            {message.error}
          </p>
        )}
      </div>
    </div>
  )
}

// ---- User row (incl. gate_reply echo) ----

function UserRow({ message }: { message: ResearchMessageItem }) {
  const submission = message.payload_submission as Record<
    string,
    unknown
  > | null
  const isGateReply =
    (message.payload as { kind?: string } | null)?.kind === "gate_reply"

  return (
    <div className="flex justify-end px-4 py-1">
      <div className="max-w-[75%] rounded-2xl rounded-tr-sm bg-primary/15 border border-primary/25 text-primary px-4 py-2 text-sm whitespace-pre-wrap break-words">
        {message.content || (
          <span className="italic opacity-60">{isGateReply ? "" : "—"}</span>
        )}
        {isGateReply && submission && (
          <dl className="mt-1 space-y-0.5 text-[11px] text-primary/80">
            {Object.entries(submission).map(([k, v]) => (
              <div key={k} className="flex gap-1">
                <dt className="opacity-70">{k}:</dt>
                <dd className="font-medium">{formatValue(v)}</dd>
              </div>
            ))}
          </dl>
        )}
      </div>
    </div>
  )
}

// ---- System event row ----

function SystemEventRow({ message }: { message: ResearchMessageItem }) {
  const { t } = useTranslation()
  const eventType = message.event_type ?? "log"
  const payload = (message.payload ?? {}) as Record<string, unknown>
  const stage = message.stage ?? (payload.stage as number | undefined) ?? null
  const stageName =
    (payload.name as string | undefined) ??
    (payload.stage_name as string | undefined) ??
    ""
  const time = new Date(message.created_time).toLocaleTimeString()

  // 阶段转场：阶段名走文本，状态（running/done/…）走带色胶囊 + 状态图标，
  // 让「进行中 / 已完成 / 失败」在长列表里一眼可辨（纯文本时几乎分不出）。
  if (eventType === "stage_transition" && stage != null) {
    const status = String(payload.status ?? "")
    const v = stageTransitionVisual(status)
    const label = t("autoResearch.chat.stagePrefix", {
      stage,
      name: stageName || `stage-${stage}`,
    })
    // 失败时把后端返回的错误原因展示出来（优先 payload.error，回退 message.error），
    // 否则用户只能看到一个红色「失败」胶囊，无法定位到底缺了什么。
    const errorText =
      status === "failed"
        ? (payload.error as string | undefined) || message.error || ""
        : ""
    return (
      <div className="px-6 py-0.5">
        <div className="flex max-w-fit items-center gap-2 rounded-full bg-muted/30 px-3 py-1 text-[11px] text-muted-foreground/80">
          <v.Icon
            className={cn(
              "size-3.5 shrink-0",
              v.iconClass,
              v.spin && "animate-spin",
            )}
          />
          <span className="truncate">{label}</span>
          {status && (
            <span
              className={cn(
                "shrink-0 rounded-full border px-1.5 py-px text-[10px] font-medium uppercase tracking-wide",
                v.pillClass,
              )}
            >
              {t(`autoResearch.stageStatus.${status}`, status)}
            </span>
          )}
          <span className="text-[10px] opacity-50 shrink-0">{time}</span>
        </div>
        {errorText && (
          <div className="mt-1 ml-2 flex max-w-2xl items-start gap-1.5 rounded-md border border-red-500/25 bg-red-500/5 px-2.5 py-1.5 text-[11px] text-red-600 dark:text-red-400">
            <AlertTriangle className="mt-px size-3.5 shrink-0" />
            <div className="min-w-0 whitespace-pre-wrap break-words">
              <span className="mr-1 font-semibold uppercase tracking-wide opacity-80">
                {t("autoResearch.chat.stageError")}
              </span>
              {errorText}
            </div>
          </div>
        )}
      </div>
    )
  }

  const meta = (() => {
    switch (eventType) {
      case "stage_transition":
        return {
          Icon: Cog,
          color: "text-cyan-500",
          text: t("autoResearch.chat.systemEvent"),
        }
      case "evolution_step":
        return {
          Icon: FlaskConical,
          color: "text-emerald-500",
          text: `evolution · score=${payload.score ?? "?"}${
            payload.file_name ? ` · ${payload.file_name as string}` : ""
          }`,
        }
      case "artifact_ready":
        return {
          Icon: FileText,
          color: "text-primary",
          text: `artifact · ${(payload.kind as string) ?? ""} · ${
            (payload.path as string) ?? ""
          }`,
        }
      case "llm4ad_final_state":
        return {
          Icon: Sparkles,
          color: "text-primary",
          text: t("autoResearch.chat.llm4adFinal"),
        }
      default:
        return {
          Icon: Info,
          color: "text-muted-foreground",
          text: message.content || "",
        }
    }
  })()

  if (!meta.text) return null

  return (
    <div className="px-6 py-0.5">
      <div className="flex max-w-fit items-center gap-2 rounded-full bg-muted/30 px-3 py-1 text-[11px] text-muted-foreground/80">
        <meta.Icon className={cn("size-3.5 shrink-0", meta.color)} />
        <span className="truncate">{meta.text}</span>
        <span className="text-[10px] opacity-50 shrink-0">{time}</span>
      </div>
    </div>
  )
}

/** 阶段转场状态的图标 + 胶囊配色（与顶部进度轨 / 侧栏状态色一致）。 */
function stageTransitionVisual(status: string): {
  Icon: typeof Cog
  iconClass: string
  spin: boolean
  pillClass: string
} {
  switch (status) {
    case "running":
      return {
        Icon: Play,
        iconClass: "text-primary",
        spin: false,
        pillClass: "border-primary/30 bg-primary/10 text-primary",
      }
    case "done":
      return {
        Icon: Check,
        iconClass: "text-emerald-500",
        spin: false,
        pillClass: "border-emerald-500/30 bg-emerald-500/10 text-emerald-500",
      }
    case "waiting":
      return {
        Icon: CircleDot,
        iconClass: "text-amber-500",
        spin: false,
        pillClass: "border-amber-500/30 bg-amber-500/10 text-amber-500",
      }
    case "failed":
      return {
        Icon: X,
        iconClass: "text-red-500",
        spin: false,
        pillClass: "border-red-500/30 bg-red-500/10 text-red-500",
      }
    default:
      return {
        Icon: Cog,
        iconClass: "text-cyan-500",
        spin: false,
        pillClass: "border-border/50 bg-muted/40 text-muted-foreground",
      }
  }
}

// ---- Assistant form / choice (HITL gate) —— 只读回显 ----
//
// 活跃门控的操作入口在底部 GatePanel；消息流里的 form/choice 仅作历史回显
// （已提交内容 / 已失效表单），不再承载交互。

interface FormField {
  name: string
  label?: string
  type?: "text" | "textarea" | "number" | "select" | "boolean"
  options?: Array<string | { label: string; value: string }>
  required?: boolean
  default?: unknown
  placeholder?: string
}

interface FormPayload {
  kind: "form" | "choice"
  prompt?: string
  reason?: string
  context_summary?: string
  output_files?: string[]
  stage?: number
  fields?: FormField[]
  options?: Array<{ label: string; value: string; description?: string }>
  available_actions?: Array<string | { value: string; label?: string }>
}

function actionLabel(t: (k: string) => string, value: string): string {
  const key = `autoResearch.form.actions.${value}`
  const label = t(key)
  return label === key ? value : label
}

function AssistantFormRow({
  message,
  sessionId,
  stale,
}: {
  message: ResearchMessageItem
  sessionId?: string
  stale?: boolean
}) {
  const { t } = useTranslation()
  const payload = (message.payload ?? {
    kind: "form",
  }) as unknown as FormPayload
  const locked = !!message.payload_locked
  const submission = message.payload_submission as Record<
    string,
    unknown
  > | null

  const [summaryOpen, setSummaryOpen] = useState(false)
  const [preview, setPreview] = useState<string | null>(null)

  const stageDir =
    payload.stage != null
      ? `stage-${String(payload.stage).padStart(2, "0")}`
      : null

  const downloadFile = async (name: string) => {
    if (!sessionId || !stageDir) return
    try {
      await downloadResearchArtifact(sessionId, `${stageDir}/${name}`)
    } catch {
      toast.error(t("autoResearch.form.downloadFailed"))
    }
  }

  return (
    <div className="flex justify-start px-4 py-1.5">
      <div className="w-full rounded-2xl rounded-tl-sm border border-border/50 bg-card/50 px-4 py-3 text-sm backdrop-blur-sm">
        {payload.stage != null && (
          <div className="mb-1.5 inline-flex items-center gap-1 text-[10px] uppercase tracking-wider text-muted-foreground font-semibold">
            <FlaskConical className="size-3" />
            {t("autoResearch.chat.stagePrefix", {
              stage: payload.stage,
              name: "",
            })}
          </div>
        )}

        {(payload.prompt || payload.reason || message.content) && (
          <p className="text-foreground/80 mb-2 whitespace-pre-wrap">
            {payload.prompt || payload.reason || message.content}
          </p>
        )}

        {payload.output_files && payload.output_files.length > 0 && (
          <div className="mb-2 flex flex-wrap items-center gap-1.5">
            <span className="text-[10px] uppercase tracking-wider text-muted-foreground/70">
              {t("autoResearch.form.outputFiles")}
            </span>
            {payload.output_files.map((f) => {
              const clickable = !!sessionId && !!stageDir
              return (
                <div
                  key={f}
                  className={cn(
                    "inline-flex items-center gap-1 px-2 py-0.5 rounded-md border text-[11px] transition-colors",
                    clickable
                      ? "border-border/60 bg-background/50 text-foreground/80"
                      : "border-border/40 bg-background/40 text-muted-foreground",
                  )}
                >
                  <button
                    type="button"
                    disabled={!clickable}
                    onClick={() =>
                      clickable && setPreview(`${stageDir}/${f}`)
                    }
                    className={cn(
                      "inline-flex items-center gap-1 min-w-0",
                      clickable
                        ? "hover:text-primary transition-colors"
                        : "cursor-default",
                    )}
                  >
                    <FileText className="size-3 shrink-0" />
                    <span className="truncate">{f}</span>
                  </button>
                  {clickable && (
                    <button
                      type="button"
                      onClick={() => downloadFile(f)}
                      className="shrink-0 text-muted-foreground/60 hover:text-primary transition-colors"
                    >
                      <Download className="size-2.5" />
                    </button>
                  )}
                </div>
              )
            })}
          </div>
        )}

        {payload.context_summary && (
          <div className="mb-2">
            <button
              type="button"
              onClick={() => setSummaryOpen((o) => !o)}
              className="inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground transition-colors"
            >
              <ChevronDown
                className={cn(
                  "size-3 transition-transform",
                  summaryOpen && "rotate-180",
                )}
              />
              {t("autoResearch.form.stageOutputPreview")}
            </button>
            {summaryOpen && (
              <p className="mt-1 rounded-md bg-background/60 border border-border/50 px-2.5 py-1.5 text-[11px] font-mono text-muted-foreground whitespace-pre-wrap max-h-40 overflow-y-auto">
                {payload.context_summary}
              </p>
            )}
          </div>
        )}

        {locked && submission?.action ? (
          <div className="mt-2 text-[11px] text-muted-foreground inline-flex items-center gap-1">
            <CircleDot className="size-3" />
            {t("autoResearch.form.submitted")}
            {` · ${actionLabel(t, String(submission.action))}`}
          </div>
        ) : stale ? (
          <div className="mt-2 text-[11px] text-muted-foreground/70 inline-flex items-center gap-1">
            <CircleDot className="size-3" />
            {t("autoResearch.form.expired")}
          </div>
        ) : null}
      </div>

      {sessionId && (
        <ArtifactPreviewDialog
          sessionId={sessionId}
          path={preview}
          onClose={() => setPreview(null)}
        />
      )}
    </div>
  )
}

function formatValue(v: unknown): string {
  if (v == null) return "—"
  if (typeof v === "object") return JSON.stringify(v)
  return String(v)
}
