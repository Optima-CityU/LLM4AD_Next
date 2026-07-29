import { AlertTriangle, Check, CircleDot, Cog, Loader2, X } from "lucide-react"
import { memo, useMemo } from "react"
import { useTranslation } from "react-i18next"

import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"
import { cn } from "@/lib/utils"

import { stageNameByLang } from "./tech"

/** 一次阶段状态事件（stage_transition 的一帧）。 */
export interface StageStatus {
  status: string
  /** ISO 时间串（created_time）。 */
  time: string
  /** failed 时的错误原因（payload.error 优先，回退 message.error）。 */
  error?: string
}

/**
 * 一个阶段时间轴条目：某阶段一次访问期间叠加的状态序列。
 * 相邻同阶段的多帧（如 running→done）会被 ChatPanel 折叠进 `statuses`。
 */
export interface StageEntry {
  /** 稳定 key（来自首帧 message id）。 */
  id: string
  stage: number
  statuses: StageStatus[]
}

/** 阶段状态 → 图标 + 配色（与顶部进度轨 / 侧栏状态色一致）。 */
function statusVisual(status: string): {
  Icon: typeof Cog
  spin: boolean
  /** 图标/文字前景色 */
  color: string
  /** 节点圆点底色 */
  dot: string
  /** 节点外发光阴影（tailwind shadow-[..]） */
  glow: string
  /** 耗时微条渐变色 */
  bar: string
} {
  switch (status) {
    case "running":
      return {
        Icon: Loader2,
        spin: true,
        color: "text-primary",
        dot: "bg-primary",
        glow: "shadow-[0_0_8px_0] shadow-primary/50",
        bar: "from-primary/70 to-primary/30",
      }
    case "done":
      return {
        Icon: Check,
        spin: false,
        color: "text-emerald-500",
        dot: "bg-emerald-500",
        glow: "shadow-[0_0_8px_0] shadow-emerald-500/40",
        bar: "from-emerald-500/70 to-emerald-400/25",
      }
    case "waiting":
      return {
        Icon: CircleDot,
        spin: false,
        color: "text-amber-500",
        dot: "bg-amber-500",
        glow: "shadow-[0_0_8px_0] shadow-amber-500/40",
        bar: "from-amber-500/70 to-amber-400/25",
      }
    case "failed":
      return {
        Icon: X,
        spin: false,
        color: "text-red-500",
        dot: "bg-red-500",
        glow: "shadow-[0_0_8px_0] shadow-red-500/50",
        bar: "from-red-500/70 to-red-400/25",
      }
    default:
      return {
        Icon: Cog,
        spin: false,
        color: "text-cyan-500",
        dot: "bg-cyan-500",
        glow: "shadow-[0_0_8px_0] shadow-cyan-500/40",
        bar: "from-cyan-500/70 to-cyan-400/25",
      }
  }
}

function hhmmss(iso: string): string {
  return new Date(iso).toLocaleTimeString()
}

/** 把错误串里的字面量 `\n`/`\r\n`（JSON 转义残留）还原成真实换行，配合 pre-wrap 生效。 */
function normalizeNewlines(s: string): string {
  return s.replace(/\\r\\n|\\n/g, "\n")
}

/** 两个时刻的耗时（毫秒）；非法或负值返回 null。 */
function durationMs(fromIso: string, toIso: string): number | null {
  const ms = new Date(toIso).getTime() - new Date(fromIso).getTime()
  return Number.isFinite(ms) && ms >= 0 ? ms : null
}

/** 毫秒 → 人类可读耗时（秒 / 分秒）。 */
function fmtDuration(ms: number): string {
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  return `${m}m${s % 60}s`
}

/**
 * 竖向阶段时间轴：把一段连续的阶段条目画成一条时间轴。
 *
 * 每个阶段一行：左侧节点色随「最新状态」，右侧阶段名后跟随该次访问叠加的各状态
 * 胶囊（图标 + 时刻）。若该阶段既有开始又有结束，行尾补一枚耗时标。被非阶段消息
 * 打断处自然分段（由 ChatPanel 决定成组边界）。
 */
function StageTimeline({ entries }: { entries: StageEntry[] }) {
  const { t, i18n } = useTranslation()

  // 组内最长耗时，用于耗时微条按比例缩放（让「哪一步拖时间」一眼可见）。
  const maxMs = useMemo(() => {
    let max = 0
    for (const e of entries) {
      const last = e.statuses[e.statuses.length - 1]
      const term = last.status === "done" || last.status === "failed"
      if (e.statuses.length > 1 && term) {
        const d = durationMs(e.statuses[0].time, last.time)
        if (d != null && d > max) max = d
      }
    }
    return max
  }, [entries])

  if (entries.length === 0) return null

  return (
    <div className="px-5 py-1.5">
      <ol className="relative flex flex-col gap-0.5">
        {/* 贯穿竖线：加粗到 2px，更明显 */}
        <span
          aria-hidden
          className="absolute left-2 top-3 bottom-3 w-0.5 rounded-full bg-gradient-to-b from-border/40 via-border to-border/40"
        />
        {entries.map((e) => {
          const latest = e.statuses[e.statuses.length - 1]
          const v = statusVisual(latest.status)
          const name =
            stageNameByLang(e.stage, i18n.language) || `stage-${e.stage}`
          const first = e.statuses[0]
          const term = latest.status === "done" || latest.status === "failed"
          const ms =
            e.statuses.length > 1 && term
              ? durationMs(first.time, latest.time)
              : null
          const barPct = ms != null && maxMs > 0 ? Math.max(6, (ms / maxMs) * 100) : 0
          const errText = e.statuses.find((s) => s.status === "failed")?.error

          const row = (
            <div
              className={cn(
                "group/stage flex min-w-0 flex-1 items-center gap-2.5 rounded-lg border border-transparent px-2 py-1.5 transition-colors",
                errText
                  ? "cursor-help border-red-500/20 bg-red-500/5 hover:bg-red-500/10"
                  : "hover:border-border/50 hover:bg-muted/25",
              )}
            >
              {/* 阶段名 + 状态图标 */}
              <span className="inline-flex min-w-0 items-center gap-1.5">
                <v.Icon
                  className={cn(
                    "size-4 shrink-0",
                    v.color,
                    v.spin && "animate-spin",
                  )}
                />
                <span className="truncate text-[12px] font-medium text-foreground/90">
                  {t("autoResearch.chat.stagePrefix", { stage: e.stage, name })}
                </span>
                {errText && (
                  <AlertTriangle className="size-3.5 shrink-0 text-red-500" />
                )}
              </span>

              {/* 时间流：开始 → 结束，中间嵌耗时微条 */}
              <span className="ml-auto flex shrink-0 items-center gap-1.5 text-[10px] tabular-nums text-muted-foreground/70">
                <span>{hhmmss(first.time)}</span>
                {ms != null ? (
                  <span className="flex items-center gap-1.5">
                    {/* 耗时微条：宽度 ∝ 组内相对耗时 */}
                    <span className="relative hidden h-1 w-16 overflow-hidden rounded-full bg-muted/50 sm:block">
                      <span
                        className={cn(
                          "absolute inset-y-0 left-0 rounded-full bg-gradient-to-r",
                          v.bar,
                        )}
                        style={{ width: `${barPct}%` }}
                      />
                    </span>
                    <span
                      className={cn(
                        "rounded-full px-1.5 py-px font-medium",
                        v.color,
                        "bg-current/10",
                      )}
                    >
                      {fmtDuration(ms)}
                    </span>
                    <span>{hhmmss(latest.time)}</span>
                  </span>
                ) : (
                  <span
                    className={cn(
                      "rounded-full px-1.5 py-px font-medium",
                      v.color,
                      "bg-current/10",
                    )}
                  >
                    {t(`autoResearch.stageStatus.${latest.status}`, latest.status)}
                  </span>
                )}
              </span>
            </div>
          )
          return (
            <li key={e.id} className="relative flex items-center gap-2 py-0.5 pl-5">
              {/* 时间轴连接点：带状态色的小圆点 */}
              <span
                className={cn(
                  "absolute left-[5px] top-1/2 size-2 -translate-y-1/2 rounded-full border-2 border-background",
                  v.dot,
                )}
                aria-hidden
              />
              {errText ? (
                <HoverCard openDelay={120} closeDelay={60}>
                  <HoverCardTrigger asChild>{row}</HoverCardTrigger>
                  <HoverCardContent
                    side="top"
                    align="start"
                    className="max-h-[60vh] w-[min(32rem,80vw)] overflow-y-auto border-red-500/40 p-3 text-[11px]"
                  >
                    <div className="mb-1.5 flex items-center gap-1.5 font-semibold uppercase tracking-wide text-red-600 dark:text-red-400">
                      <AlertTriangle className="size-3.5 shrink-0" />
                      {t("autoResearch.chat.stageError")}
                    </div>
                    <div className="whitespace-pre-wrap break-words text-foreground/90">
                      {normalizeNewlines(errText)}
                    </div>
                  </HoverCardContent>
                </HoverCard>
              ) : (
                row
              )}
            </li>
          )
        })}
      </ol>
    </div>
  )
}

export default memo(StageTimeline)
