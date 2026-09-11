/**
 * 「从模板创建」课题选题器（ARC-Bench 55 题注册表）。
 *
 * 不自带 Dialog：外层 CreateSessionDialog 已是 `max-h-[85vh]` 的单列滚动容器且
 * 带 `preventOutsideClose`，再叠一层 modal 会与之抢焦点与 ESC。这里只渲一个
 * 可展开的内联面板（触发器 + 折叠区），选中即回调 topicId。
 *
 * 模板只是**创建时的一次性初始化输入**：后端据此把 stage-07/08/09 产物落进
 * run_dir，不落任何 session 字段。故本组件的职责仅止于「挑一个 id 传上去」。
 */

import { ChevronDown, Loader2, Search } from "lucide-react"
import { useMemo, useState } from "react"
import { useTranslation } from "react-i18next"

import type { ResearchTemplateItem } from "@/client"
import { useResearchTemplateDetail, useResearchTemplates } from "@/hooks/useAutoResearch"
import { cn } from "@/lib/utils"

import { SectionLabel } from "./tech"

interface Props {
  /** 当前选中的课题 id；空串表示未选。 */
  value: string
  onChange: (
    topicId: string,
    /** 选中项的摘要，供调用方预填 topic / title / metric；清空时为 null。 */
    item: ResearchTemplateItem | null,
  ) => void
}

/** 域过滤顺序与后端 `_DOMAIN_ORDER` 一致；空串 = 全部。 */
const DOMAIN_FILTERS = ["", "ml", "physics", "biology", "statistics", "quantum"] as const

export default function TemplatePicker({ value, onChange }: Props) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [domain, setDomain] = useState<string>("")
  const [keyword, setKeyword] = useState("")

  const { data, isLoading, isError } = useResearchTemplates(domain || null)
  const { data: detail, isFetching: detailLoading } =
    useResearchTemplateDetail(value || null)

  const items = useMemo(() => {
    const all = data?.items ?? []
    const kw = keyword.trim().toLowerCase()
    if (!kw) return all
    // 题面可能很长，关键词只做包含匹配即可——55 条数据不值得上模糊搜索。
    return all.filter(
      (it) =>
        it.title.toLowerCase().includes(kw) ||
        it.id.toLowerCase().includes(kw) ||
        it.topic.toLowerCase().includes(kw),
    )
  }, [data, keyword])

  // 后端镜像未装 arc-templates extra：整个入口不渲染（调用方也就没有模板可传）。
  if (data && data.available === false) return null

  const selected = (data?.items ?? []).find((it) => it.id === value) ?? null

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <SectionLabel className="block">
          {t("autoResearch.create.templateLabel")}
        </SectionLabel>
        {value && (
          <button
            type="button"
            onClick={() => onChange("", null)}
            className="text-[11px] text-muted-foreground transition-colors hover:text-foreground"
          >
            {t("autoResearch.create.templateClear")}
          </button>
        )}
      </div>

      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "flex w-full items-center gap-2 rounded-md border px-2.5 py-2 text-left text-xs transition-colors",
          value
            ? "border-primary/60 bg-primary/5"
            : "border-border/60 hover:border-primary/50 hover:bg-primary/5",
        )}
      >
        <span className="min-w-0 flex-1">
          {selected ? (
            <>
              <span className="block truncate text-[12px] font-medium text-foreground">
                {selected.title}
              </span>
              <span className="block truncate font-mono text-[10px] text-muted-foreground">
                {selected.id} · {selected.domain_label}
              </span>
            </>
          ) : (
            <span className="text-muted-foreground">
              {t("autoResearch.create.templatePlaceholder")}
            </span>
          )}
        </span>
        <ChevronDown
          className={cn(
            "size-3.5 shrink-0 text-muted-foreground transition-transform",
            open && "rotate-180",
          )}
        />
      </button>

      {open && (
        <div className="space-y-2 rounded-md border border-border/60 bg-background/60 p-2">
          <div className="flex flex-wrap items-center gap-1">
            {DOMAIN_FILTERS.map((d) => (
              <button
                key={d || "__all__"}
                type="button"
                onClick={() => setDomain(d)}
                className={cn(
                  "rounded px-1.5 py-0.5 text-[10px] transition-colors",
                  domain === d
                    ? "bg-primary/15 text-primary"
                    : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                )}
              >
                {d ? t(`autoResearch.templateDomain.${d}`) : t("autoResearch.create.templateAllDomains")}
              </button>
            ))}
            <div className="relative ml-auto w-32">
              <Search className="pointer-events-none absolute left-1.5 top-1/2 size-3 -translate-y-1/2 text-muted-foreground" />
              <input
                value={keyword}
                onChange={(e) => setKeyword(e.target.value)}
                placeholder={t("autoResearch.create.templateSearch")}
                className="w-full rounded border border-border/60 bg-background/80 py-0.5 pl-5 pr-1 text-[11px] focus:border-primary/50 focus:outline-none"
              />
            </div>
          </div>

          <div className="max-h-52 space-y-0.5 overflow-y-auto pr-0.5">
            {isLoading && (
              <div className="flex items-center justify-center gap-2 py-4 text-[11px] text-muted-foreground">
                <Loader2 className="size-3 animate-spin" />
                {t("common.loading")}
              </div>
            )}
            {isError && (
              <div className="py-4 text-center text-[11px] text-destructive">
                {t("autoResearch.create.templateLoadFailed")}
              </div>
            )}
            {!isLoading && !isError && items.length === 0 && (
              <div className="py-4 text-center text-[11px] text-muted-foreground">
                {t("autoResearch.create.templateEmpty")}
              </div>
            )}
            {items.map((it) => (
              <button
                key={it.id}
                type="button"
                onClick={() => {
                  onChange(it.id, it)
                  setOpen(false)
                }}
                className={cn(
                  "block w-full rounded px-2 py-1.5 text-left transition-colors",
                  value === it.id
                    ? "bg-primary/10"
                    : "hover:bg-muted/50",
                )}
              >
                <span className="flex items-baseline gap-1.5">
                  <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
                    {it.id}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-[12px] text-foreground/90">
                    {it.title}
                  </span>
                  {it.metric_key && (
                    <span className="shrink-0 font-mono text-[10px] text-muted-foreground/70">
                      {it.metric_key}
                      {it.metric_direction === "maximize"
                        ? "↑"
                        : it.metric_direction === "minimize"
                          ? "↓"
                          : ""}
                    </span>
                  )}
                </span>
                <span className="mt-0.5 block truncate text-[10px] leading-snug text-muted-foreground">
                  {it.topic}
                </span>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* 预览：确认这个模板会预置出什么（briefing / 假设 / 实验设计三件套）。 */}
      {value && (
        <div className="rounded-md border border-border/40 bg-muted/20 p-2">
          {detailLoading ? (
            <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
              <Loader2 className="size-3 animate-spin" />
              {t("common.loading")}
            </div>
          ) : (
            <div className="space-y-1.5">
              <div className="flex flex-wrap items-center gap-2 text-[10px] text-muted-foreground">
                <span className="rounded bg-emerald-500/10 px-1.5 py-0.5 text-emerald-600 dark:text-emerald-400">
                  {t("autoResearch.create.templatePreset", {
                    defaultValue: "预置 stage 7-9",
                  })}
                </span>
                {typeof detail?.hypotheses?.length === "number" && (
                  <span>
                    {t("autoResearch.create.templateHypotheses", {
                      defaultValue: "{{n}} 条假设",
                      n: detail.hypotheses.length,
                    })}
                  </span>
                )}
                {(detail?.domains ?? []).map((d) => (
                  <span key={d} className="font-mono">
                    {d}
                  </span>
                ))}
              </div>
              {detail?.synthesis && (
                <p className="line-clamp-4 text-[11px] leading-snug text-muted-foreground">
                  {detail.synthesis}
                </p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
