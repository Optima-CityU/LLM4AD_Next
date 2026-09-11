/**
 * 「从模板创建」课题选题器（ARC-Bench 55 题注册表）。
 *
 * 结构：**一行触发器 + 浮层**。触发器只显示当前选中项（或占位文案），下拉面板走
 * Popover 渲染到 body 的 portal，因此不再撑高外层 Dialog——早先是内联折叠面板，
 * 展开后弹框被顶长、底部「创建」按钮掉出视口。
 *
 * 浮层内是「左列表 + 右预览」两栏：
 *   - 左列表每行只放结构化字段（id / title / metric_direction / metric_key），
 *     icon + 单行文字，固定行高，扫视快；
 *   - 题面 topic 往往上百字，塞进行内会把行撑成多行、列表起伏不定，故移到右侧
 *     预览栏：hover 哪行显示哪行，点击不改变预览（仍跟着鼠标），选中即回填表单。
 * 面板高度由列表的 max-h 决定，与内容多少无关，弹框不会被它拖长。
 *
 * 模板只是**创建时的一次性初始化输入**：后端据此把 stage-07/08/09 产物落进
 * run_dir，不落任何 session 字段。故本组件的职责仅止于「挑一个 id 传上去」。
 */

import { ChevronDown, Loader2, Search, Target } from "lucide-react"
import { useMemo, useState } from "react"
import { useTranslation } from "react-i18next"

import type { ResearchTemplateItem } from "@/client"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import {
  useResearchTemplateDetail,
  useResearchTemplates,
} from "@/hooks/useAutoResearch"
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
  /**
   * 浮层的挂载点。默认挂到 body，但在 Dialog 内必须传 DialogContent 节点：
   * Radix 的 modal Dialog 用 react-remove-scroll 锁外层滚动，只把「DialogContent」
   * 本身登记为 shard，锁会把落在 shard 之外的 wheel 事件 preventDefault 掉——
   * 挂到 body 的浮层因此滚不动（滚轮无效、只有拖滚动条能动）。挂进 DialogContent
   * 内部就落在 shard 里，锁不再拦截；同时也不再依赖 popper 定位，浮层跟着弹框走。
   * 由调用方（CreateSessionDialog）传入，本组件不自己去 querySelector。
   */
  portalContainer?: HTMLElement | null
}

/** 域过滤顺序与后端 `_DOMAIN_ORDER` 一致；空串 = 全部。 */
const DOMAIN_FILTERS = [
  "",
  "ml",
  "physics",
  "biology",
  "statistics",
  "quantum",
] as const

/**
 * 前端写死的「推荐起步题目」：这两题的数据/题面最完整、依赖最轻，适合第一次跑
 * 通全流程。纯展示标记，不影响后端任何行为——后端不认识这个集合。
 */
const RECOMMENDED_IDS = new Set(["ML03", "ML23"])

/** 指标方向的箭头：最大化 ↑ / 最小化 ↓，未指定不给符号。 */
function directionArrow(direction: string | undefined): string {
  if (direction === "maximize") return "↑"
  if (direction === "minimize") return "↓"
  return ""
}

export default function TemplatePicker({
  value,
  onChange,
  portalContainer,
}: Props) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [domain, setDomain] = useState<string>("")
  const [keyword, setKeyword] = useState("")
  /** 右栏预览的课题；与选中项解耦，纯跟随 hover。 */
  const [preview, setPreview] = useState<ResearchTemplateItem | null>(null)

  const { data, isLoading, isError } = useResearchTemplates(domain || null)
  const { data: detail, isFetching: detailLoading } = useResearchTemplateDetail(
    value || null,
  )

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

  const selected = (data?.items ?? []).find((it) => it.id === value) ?? null

  // 后端镜像未装 arc-templates extra：整个入口不渲染（调用方也就没有模板可传）。
  if (data && data.available === false) return null

  return (
    <div className="space-y-1.5">
      <SectionLabel className="block">
        {t("autoResearch.create.templateLabel")}
      </SectionLabel>

      <Popover
        open={open}
        onOpenChange={(v) => {
          setOpen(v)
          if (!v) setPreview(null)
        }}
      >
        <PopoverTrigger asChild>
          <button
            type="button"
            className={cn(
              "flex w-full items-center gap-2 rounded-md border px-2.5 py-2 text-left text-xs transition-colors",
              value
                ? "border-primary/60 bg-primary/5"
                : "border-border/60 hover:border-primary/50 hover:bg-primary/5",
            )}
          >
            <span className="min-w-0 flex-1 truncate">
              {selected ? (
                <>
                  <span className="font-mono text-[10px] text-muted-foreground">
                    {selected.id}
                  </span>
                  <span className="ml-1.5 text-[12px] text-foreground">
                    {selected.title}
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
        </PopoverTrigger>

        {/* 宽度跟随触发器：Radix 把触发器的宽度写进 --radix-popover-trigger-width，
            面板跟着它走，才和上面那个下拉框左右齐平（弹框加宽后触发器就宽了，
            面板写在 40rem 上反而对不齐）。仍留一个视口上限，窄屏不会被撑出屏幕。
            高度由左右两栏自身的 18rem 决定，不随内容多少伸缩。 */}
        <PopoverContent
          align="start"
          sideOffset={6}
          collisionPadding={12}
          container={portalContainer ?? undefined}
          className="flex w-(--radix-popover-trigger-width) max-w-[calc(100vw-3rem)] flex-col p-2"
          onOpenAutoFocus={(e) => e.preventDefault()}
        >
          {/* 过滤条：域 + 关键词，常驻浮层顶部 */}
          <div className="flex flex-wrap items-center gap-1 pb-2">
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
                {d
                  ? t(`autoResearch.templateDomain.${d}`)
                  : t("autoResearch.create.templateAllDomains")}
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

          {/* 两栏并排：左侧列表与右侧题面预览各自 18rem 定高、各自滚动，
              浮层高度因此固定，不随内容多少伸缩。 */}
          <div className="flex gap-2">
            {/* 左：结构化列表。行高固定（py-1 + 单行），不因题面长短起伏。
                宽度跟着面板走（可伸缩，22rem 封顶），面板变宽时右栏才拿到余量。 */}
            <div className="max-h-[18rem] min-h-[18rem] w-[min(22rem,50%)] shrink-0 space-y-0.5 overflow-y-auto pr-0.5">
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
                  onMouseEnter={() => setPreview(it)}
                  onFocus={() => setPreview(it)}
                  onClick={() => {
                    onChange(it.id, it)
                    setOpen(false)
                  }}
                  className={cn(
                    "flex w-full items-center gap-2 rounded px-1.5 py-1 text-left transition-colors",
                    value === it.id ? "bg-primary/10" : "hover:bg-muted/60",
                  )}
                >
                  {/* 只留 id + title：metric_key 一列在窄行里挤成第二条「小字列」，
                      扫读时反而干扰，指标信息移到右栏预览里看。 */}
                  <span className="w-11 shrink-0 font-mono text-[10px] text-muted-foreground">
                    {it.id}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-[11px] text-foreground/90">
                    {it.title}
                  </span>
                  {RECOMMENDED_IDS.has(it.id) && (
                    <span className="shrink-0 rounded bg-amber-500/15 px-1 py-px text-[9px] font-medium text-amber-600 dark:text-amber-400">
                      {t("autoResearch.create.templateRecommended")}
                    </span>
                  )}
                </button>
              ))}
            </div>

            {/* 右：题面预览。与左栏同高（min-h/max-h 都是 18rem），未 hover 时给
                提示文案——高度不随内容变化，浮层就不会抖。 */}
            <div className="min-h-[18rem] max-h-[18rem] min-w-0 flex-1 overflow-y-auto rounded border border-border/40 bg-muted/20 p-2">
              {preview ? (
                <div className="space-y-1.5">
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <span className="font-mono text-[10px] text-primary/80">
                      {preview.id}
                    </span>
                    <span className="text-[11px] font-medium text-foreground">
                      {preview.title}
                    </span>
                    {RECOMMENDED_IDS.has(preview.id) && (
                      <span className="rounded bg-amber-500/15 px-1 py-px text-[9px] font-medium text-amber-600 dark:text-amber-400">
                        {t("autoResearch.create.templateRecommended")}
                      </span>
                    )}
                  </div>
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px] text-muted-foreground">
                    {preview.domain_label && (
                      <span>{preview.domain_label}</span>
                    )}
                    {preview.metric_key && (
                      <span className="inline-flex items-center gap-0.5 font-mono">
                        <Target className="size-2.5" />
                        {preview.metric_key}
                        {directionArrow(preview.metric_direction)}
                      </span>
                    )}
                    {preview.metric_direction && (
                      <span>
                        {t(
                          `autoResearch.metricDirection.${preview.metric_direction}`,
                          preview.metric_direction,
                        )}
                      </span>
                    )}
                  </div>
                  <p className="whitespace-pre-wrap text-[11px] leading-relaxed text-muted-foreground">
                    {preview.topic}
                  </p>
                </div>
              ) : (
                <div className="flex h-full items-center justify-center px-3 text-center text-[11px] leading-snug text-muted-foreground/50">
                  {t("autoResearch.create.templatePreviewHint", {
                    defaultValue: "悬停左侧课题查看完整题面",
                  })}
                </div>
              )}
            </div>
          </div>
        </PopoverContent>
      </Popover>

      {/* 选中后的提醒 + 取消入口，合成一块：警告是「选用模板的代价」，取消选择正是
          这句话给出的对策，放在一起用户才不用回头去标题行找那个按钮。
          未选模板时不显示（默认就是从第 1 阶段自跑，没什么可提醒的）。 */}
      {value && (
        <div className="space-y-1.5 rounded-md border border-amber-500/30 bg-amber-500/5 p-2">
          <p className="text-[10px] leading-relaxed text-amber-700 dark:text-amber-400">
            {t("autoResearch.create.templateStageWarning")}
          </p>
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px] text-muted-foreground">
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
            {detailLoading && (
              <Loader2 className="size-3 animate-spin opacity-60" />
            )}
            <button
              type="button"
              onClick={() => onChange("", null)}
              className="ml-auto shrink-0 rounded border border-border/60 px-1.5 py-0.5 text-foreground/80 transition-colors hover:bg-muted hover:text-foreground"
            >
              {t("autoResearch.create.templateClear")}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
