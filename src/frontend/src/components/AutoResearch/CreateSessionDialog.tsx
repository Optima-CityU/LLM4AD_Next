import { ChevronDown, FilePlus2, Loader2, Play } from "lucide-react"
import { useEffect, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"

import type {
  ResearchFolderItem,
  ResearchMode,
  ResearchSessionCreateRequest,
  ResearchSessionItem,
  ResearchTemplateItem,
} from "@/client"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import {
  useCreateResearchSession,
  useStartResearchTurn,
} from "@/hooks/useAutoResearch"
import { cn } from "@/lib/utils"
import ProviderModelPicker from "./ProviderModelPicker"
import {
  METRIC_DIRECTION_OPTIONS,
  type MetricDirection,
  MODE_OPTIONS,
  metricDirectionToApi,
  PROFILE_OPTIONS,
  type ResearchProfile,
} from "./shared"
import TemplatePicker from "./TemplatePicker"
import { SectionLabel } from "./tech"

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  folders: ResearchFolderItem[]
  initialFolderId: string | null
  onCreated: (session: ResearchSessionItem) => void
}

/**
 * 新建会话对话框。字段：topic + title + folder + provider + model + mode。
 */
export default function CreateSessionDialog({
  open,
  onOpenChange,
  folders,
  initialFolderId,
  onCreated,
}: Props) {
  const { t } = useTranslation()
  const [topic, setTopic] = useState("")
  const [title, setTitle] = useState("")
  const [templateId, setTemplateId] = useState("")
  const [folderId, setFolderId] = useState<string | null>(initialFolderId)
  const [providerId, setProviderId] = useState("default")
  const [modelName, setModelName] = useState("")
  const [mode, setMode] = useState<ResearchMode>("co-pilot")
  const [profile, setProfile] = useState<ResearchProfile>("algorithm_evolution")
  const [metricDirection, setMetricDirection] =
    useState<MetricDirection>("auto")
  const [metricKey, setMetricKey] = useState("")
  const [topicError, setTopicError] = useState("")
  const [titleError, setTitleError] = useState("")
  /**
   * 课题模板浮层的挂载节点（DialogContent 内部的绝对定位层）。存 state 而非 ref：
   * 浮层需要「节点出现后」才渲染，ref 变化不会触发重渲染，挂载点会一直是 null。
   */
  const [contentEl, setContentEl] = useState<HTMLDivElement | null>(null)

  const TOPIC_MIN = 1
  const TOPIC_MAX = 1000
  const TITLE_MAX = 255

  const createMut = useCreateResearchSession()
  const startMut = useStartResearchTurn()

  // 从不同文件夹重新打开时同步默认归属，避免沿用上次的陈旧 folderId。
  useEffect(() => {
    if (open) setFolderId(initialFolderId)
  }, [open, initialFolderId])

  // 记住本次已成功创建的会话：若随后 autoStart 失败，重试时跳过 create，
  // 避免「创建成功但启动失败 → 再点一次又建一个」的重复会话。
  const createdRef = useRef<ResearchSessionItem | null>(null)

  const reset = () => {
    setTopic("")
    setTitle("")
    setTemplateId("")
    setFolderId(initialFolderId)
    setProviderId("default")
    setModelName("")
    setMode("co-pilot")
    setProfile("algorithm_evolution")
    setMetricDirection("auto")
    setMetricKey("")
    setTopicError("")
    setTitleError("")
    createdRef.current = null
  }

  /**
   * 选中/清空课题模板。模板只是建会话时的一次性输入，选中即把它的题面 / 标题 /
   * 指标预填进表单（仍可手改）；清空时题面留空，由后端在无 template_id 时报 400。
   */
  const handleTemplateChange = (
    topicId: string,
    item: ResearchTemplateItem | null,
  ) => {
    setTemplateId(topicId)
    if (!item) return
    setTopic(item.topic || "")
    setTopicError("")
    setTitle(item.title || "")
    setTitleError("")
    if (item.metric_key) setMetricKey(item.metric_key)
    if (
      item.metric_direction === "maximize" ||
      item.metric_direction === "minimize"
    ) {
      setMetricDirection(item.metric_direction)
    }
  }

  const handleSubmit = async (startAfter: boolean) => {
    const trimmed = topic.trim()
    // 选了模板时空题面是合法的：后端会用 manifest 派生 topic。
    if (!templateId && trimmed.length < TOPIC_MIN) {
      setTopicError(
        t("autoResearch.chat.topicTooShort", {
          defaultValue: "主题至少需要 {{min}} 个字符",
          min: TOPIC_MIN,
        }),
      )
      return
    }
    if (trimmed.length > TOPIC_MAX) {
      setTopicError(
        t("autoResearch.chat.topicTooLong", {
          defaultValue: "主题最多 {{max}} 个字符",
          max: TOPIC_MAX,
        }),
      )
      return
    }

    const trimmedTitle = title.trim()
    if (trimmedTitle.length > TITLE_MAX) {
      setTitleError(
        t("autoResearch.chat.titleTooLong", {
          defaultValue: "标题最多 {{max}} 个字符",
          max: TITLE_MAX,
        }),
      )
      return
    }

    try {
      // 复用上次已建的会话（autoStart 失败后重试场景），否则新建
      const created =
        createdRef.current ??
        (await createMut.mutateAsync({
          topic: trimmed,
          title: trimmedTitle || undefined,
          folder_id: folderId,
          provider_id: providerId.trim() || null,
          model_name: modelName.trim() || null,
          mode,
          profile,
          metric_direction: metricDirectionToApi(metricDirection),
          metric_key: metricKey.trim() || undefined,
          // 给定即走「从模板创建」：后端把 ARC-Bench stage-07/08/09 产物物化进 run_dir。
          template_id: templateId || undefined,
        } as ResearchSessionCreateRequest))
      createdRef.current = created

      if (startAfter) {
        // 首启一轮。这一步失败时上面的 createdRef 已经记住会话，重试不会再建一个。
        await startMut.mutateAsync({
          sessionId: created.id,
          body: {
            content: null,
            mode,
            provider_id: providerId.trim() || null,
            model_name: modelName.trim() || null,
          } as never,
        })
      }
      onCreated(created)
      onOpenChange(false)
      reset()
    } catch (err: unknown) {
      const detail =
        (err as { body?: { detail?: string } })?.body?.detail ??
        (err as Error)?.message ??
        "error"
      toast.error(detail)
    }
  }

  const submitting = createMut.isPending || startMut.isPending

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        onOpenChange(v)
        if (!v) reset()
      }}
    >
      {/* 弹框自身不滚（grid 的隐式行会被内容撑长，把 footer 顶出去）：改成
          「标题 / 表单 / 按钮」三行定高，只让中间的表单区滚。max-h 落在内容上，
          表单一长也只有中间那一栏出滚动条，标题与「创建」按钮始终可见。 */}
      <DialogContent
        className="sm:max-w-[760px] grid-rows-[auto_minmax(0,1fr)_auto] max-h-[85vh] overflow-hidden"
        preventOutsideClose
      >
        {/* 浮层挂载点：在收口的 DialogContent 里单独开一个 overflow-visible 的
            绝对定位层。Popover 挂在这里仍算落在 DialogContent 这个滚动锁 shard
            内（react-remove-scroll 只把 DialogContent 登记为 shard，挂到 body 上的
            浮层滚轮事件会被 preventDefault），同时又能超出弹框边界显示，不被
            DialogContent 的 overflow-hidden 裁掉。pointer-events-none 只是不让这层
            挡住下面的表单，浮层自身会重新打开指针事件。 */}
        <div
          ref={setContentEl}
          className="pointer-events-none absolute inset-0 overflow-visible"
          style={{ gridArea: "1 / 1 / -1 / -1" }}
        />
        <DialogHeader>
          <DialogTitle>{t("autoResearch.create.title")}</DialogTitle>
          <DialogDescription className="text-xs">
            {t("autoResearch.create.subtitle")}
          </DialogDescription>
        </DialogHeader>

        <div className="-mx-1 space-y-2.5 overflow-y-auto px-1 py-2">
          <TemplatePicker
            value={templateId}
            onChange={handleTemplateChange}
            portalContainer={contentEl}
          />

          <Field
            label={t("autoResearch.create.topicLabel")}
            error={topicError}
            required
          >
            <div className="relative">
              <textarea
                value={topic}
                onChange={(e) => {
                  setTopic(e.target.value)
                  if (topicError) setTopicError("")
                }}
                placeholder={t("autoResearch.create.topicPlaceholder")}
                rows={3}
                className={`w-full resize-none rounded-md border bg-background/60 px-2 py-1.5 text-sm transition-colors focus:outline-none focus:ring-1 ${
                  topicError
                    ? "border-destructive focus:border-destructive focus:ring-destructive/30"
                    : "border-border/60 focus:border-primary/50 focus:ring-primary/30"
                }`}
                autoFocus
              />
              {/* 字数统计 */}
              <div
                className={`absolute bottom-1.5 right-1.5 px-1.5 py-0.5 rounded text-[10px] font-mono tabular-nums backdrop-blur-sm ${
                  topic.length > TOPIC_MAX
                    ? "bg-destructive/90 text-destructive-foreground"
                    : topic.length > TOPIC_MAX * 0.9
                      ? "bg-amber-500/90 text-white"
                      : "bg-muted/80 text-muted-foreground"
                }`}
              >
                {topic.length} / {TOPIC_MAX}
              </div>
            </div>
          </Field>

          <Field label={t("autoResearch.create.titleLabel")} error={titleError}>
            <div className="relative">
              <Input
                value={title}
                onChange={(e) => {
                  setTitle(e.target.value)
                  if (titleError) setTitleError("")
                }}
                placeholder={t("autoResearch.create.titlePlaceholder")}
                maxLength={TITLE_MAX}
                className={
                  titleError
                    ? "border-destructive focus-visible:border-destructive focus-visible:ring-destructive/30"
                    : ""
                }
              />
              {/* 字数统计 */}
              {title.length > 0 && (
                <div
                  className={`absolute top-1/2 -translate-y-1/2 right-2 px-1.5 py-0.5 rounded text-[10px] font-mono tabular-nums backdrop-blur-sm pointer-events-none ${
                    title.length > TITLE_MAX
                      ? "bg-destructive/90 text-destructive-foreground"
                      : title.length > TITLE_MAX * 0.9
                        ? "bg-amber-500/90 text-white"
                        : "bg-muted/80 text-muted-foreground"
                  }`}
                >
                  {title.length} / {TITLE_MAX}
                </div>
              )}
            </div>
          </Field>

          <Field label={t("autoResearch.create.profileLabel")}>
            <div className="grid grid-cols-2 gap-3">
              {PROFILE_OPTIONS.map((p) => {
                return (
                  <button
                    key={p}
                    type="button"
                    onClick={() => setProfile(p as ResearchProfile)}
                    className={cn(
                      "group relative rounded-lg px-3 py-2.5 text-left transition-all",
                      "border-l border-r border-border/60",
                      profile === p
                        ? "border-t border-b border-primary/60 bg-primary/10 shadow-sm"
                        : "border-t border-b border-border/60 hover:border-primary/60 hover:bg-primary/5",
                    )}
                  >
                    <div className="flex items-start gap-2">
                      {/* 选中指示器 */}
                      <div
                        className={cn(
                          "mt-0.5 size-3.5 shrink-0 rounded-full border-2 transition-all",
                          profile === p
                            ? "border-primary bg-primary"
                            : "border-muted-foreground/40 bg-background",
                        )}
                      >
                        {profile === p && (
                          <div className="size-full flex items-center justify-center">
                            <div className="size-1.5 rounded-full bg-primary-foreground" />
                          </div>
                        )}
                      </div>
                      <div className="flex-1 min-w-0 space-y-0.5">
                        <div className="flex items-center gap-1.5">
                          <span
                            className={cn(
                              "text-[13px] font-medium transition-colors",
                              profile === p
                                ? "text-foreground"
                                : "text-foreground/80 group-hover:text-foreground",
                            )}
                          >
                            {t(`autoResearch.profile.${p}`)}
                          </span>
                          {/* llm4ad 是默认推荐画像：与 autoresearch 走原生 ARC 不同，
                              它把 9-13 阶段接到 LLM4AD 演化引擎上，是这里的主推路径。 */}
                          {p === "algorithm_evolution" && (
                            <span className="shrink-0 rounded bg-amber-500/15 px-1 py-px text-[9px] font-medium text-amber-600 dark:text-amber-400">
                              {t("autoResearch.create.recommendedMark")}
                            </span>
                          )}
                        </div>
                        <div className="text-[11px] leading-snug text-muted-foreground">
                          {t(`autoResearch.profileDesc.${p}`)}
                        </div>
                      </div>
                    </div>
                  </button>
                )
              })}
            </div>
          </Field>

          {/* 指标名与优化方向本来就描述同一个指标（叫什么 + 越大越好还是越小越好），
              拆成两块要上下读两遍，合成一行：左边填名字，右边选方向，一眼是一件事。
              方向用「分段控件」（segmented control）：三个互斥选项全摆在面上，比下拉
              少一次点击、也比三张卡片省纵向空间；每项的长说明（越大越好/越小越好）
              做成 tooltip，不占行内宽度。 */}
          <Field label={t("autoResearch.create.metricLabel")}>
            <div className="grid grid-cols-2 gap-3">
              <Input
                value={metricKey}
                onChange={(e) => setMetricKey(e.target.value)}
                maxLength={64}
                placeholder={t("autoResearch.create.metricKeyPlaceholder")}
              />
              {/* 真 radio 而不是 role="radio" 的按钮：键盘方向键切换、表单语义都是
                  原生行为，也不用自己维护 aria-checked。输入框用 sr-only 藏掉，
                  选中态画在外层 label 上。
                  选中态用「主色底 + 主色描边 + 实心圆点」三重信号：只靠 bg-background
                  的白底在浅色主题里跟灰槽几乎同色，等于没有反馈；圆点与实验类型那组
                  卡片用同一套图形语言（外圈 border-2 + 内点），两处选择器看起来是一家的。 */}
              <div className="flex h-9 items-center gap-1 rounded-md border border-border/60 bg-muted/40 p-0.5">
                {METRIC_DIRECTION_OPTIONS.map((d) => (
                  <Tooltip key={d}>
                    <TooltipTrigger asChild>
                      <label
                        className={cn(
                          "flex h-full flex-1 cursor-pointer items-center justify-center gap-1.5 rounded-[5px] px-1.5 text-[11px] transition-colors",
                          metricDirection === d
                            ? "bg-primary/15 font-medium text-foreground ring-1 ring-primary/50 ring-inset"
                            : "text-muted-foreground hover:bg-background/60 hover:text-foreground",
                        )}
                      >
                        <input
                          type="radio"
                          name="metric-direction"
                          value={d}
                          checked={metricDirection === d}
                          onChange={() =>
                            setMetricDirection(d as MetricDirection)
                          }
                          className="sr-only"
                        />
                        <span
                          className={cn(
                            "flex size-2.5 shrink-0 items-center justify-center rounded-full border-2 transition-colors",
                            metricDirection === d
                              ? "border-primary bg-primary"
                              : "border-muted-foreground/40",
                          )}
                        >
                          {metricDirection === d && (
                            <span className="size-1 rounded-full bg-primary-foreground" />
                          )}
                        </span>
                        {t(`autoResearch.metricDirection.${d}`)}
                      </label>
                    </TooltipTrigger>
                    <TooltipContent side="top" className="max-w-56 text-[11px]">
                      {t(`autoResearch.metricDirection.${d}Desc`)}
                    </TooltipContent>
                  </Tooltip>
                ))}
              </div>
            </div>
            {/* 没有指标名时提示这个字段是什么；填了之后切换成当前方向的说明，
                省得每次都去悬停看 tooltip。 */}
            <p className="pt-1 text-[10px] leading-snug text-muted-foreground/70">
              {metricKey
                ? t(`autoResearch.metricDirection.${metricDirection}Desc`)
                : t("autoResearch.create.metricKeyHint")}
            </p>
          </Field>

          <div className="grid grid-cols-2 gap-3">
            <Field label={t("autoResearch.create.folderLabel")}>
              <Select
                value={folderId ?? "__none__"}
                onValueChange={(v) => setFolderId(v === "__none__" ? null : v)}
              >
                <SelectTrigger size="sm" className="w-full text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__none__">
                    {t("autoResearch.create.folderNone")}
                  </SelectItem>
                  {folders.map((f) => (
                    <SelectItem key={f.id} value={f.id}>
                      {f.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>

            <Field label={t("autoResearch.create.modeLabel")}>
              <Select
                value={mode}
                onValueChange={(v) => setMode(v as ResearchMode)}
              >
                <SelectTrigger size="sm" className="w-full text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {MODE_OPTIONS.map((m) => (
                    <SelectItem key={m} value={m}>
                      {t(`autoResearch.mode.${m}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
          </div>

          <Field label={t("autoResearch.create.providerLabel")}>
            <ProviderModelPicker
              provider={providerId}
              model={modelName}
              onChange={(p, m) => {
                setProviderId(p)
                setModelName(m)
              }}
            />
          </Field>

          {/* 「仅创建」这个分支更冷门，藏在主按钮右侧的箭头里；原先那个
              「创建并运行」勾选框因此删掉了——勾选框把「建会话」和「跑首轮」这两件
              事压成一个布尔值，主按钮文案还得跟着变，不如让按钮本身说明它做什么。 */}
        </div>

        <DialogFooter className="items-center sm:justify-between">
          {/* 左侧常驻一行状态说明：会话建好是停着还是立刻跑，不需要用户记。 */}
          <p className="hidden text-[10px] text-muted-foreground/70 sm:block">
            {t("autoResearch.create.footerHint")}
          </p>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={submitting}
            >
              {t("common.cancel")}
            </Button>

            {/* 主按钮 + 右侧下拉细节：视觉上是一个按钮，点左侧「创建并运行」，
                点右侧箭头展开「仅创建」。 */}
            <div className="flex items-stretch">
              <Button
                className="rounded-r-none"
                onClick={() => void handleSubmit(true)}
                disabled={submitting || (!templateId && !topic.trim())}
              >
                {submitting ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <Play className="size-3.5" />
                )}
                {submitting
                  ? t("autoResearch.create.creating")
                  : t("autoResearch.create.createAndRun")}
              </Button>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    className="w-8 rounded-l-none border-l border-primary-foreground/25 px-0"
                    disabled={submitting || (!templateId && !topic.trim())}
                    aria-label={t("autoResearch.create.moreActions")}
                  >
                    <ChevronDown className="size-3.5" />
                  </Button>
                </DropdownMenuTrigger>
                {/* 不传 container：这里没有滚轮滚动需求，而且 DropdownMenuContent
                    本身不认 container（只有 Popover 那个 wrapper 改过）。
                    挂 body 上让 popper 以视口为碰撞边界，面板反而不容易被弹框裁掉。 */}
                <DropdownMenuContent align="end" side="top" className="w-60">
                  <DropdownMenuItem onSelect={() => void handleSubmit(true)}>
                    <Play className="size-3.5 text-muted-foreground" />
                    <span className="flex flex-col gap-0.5">
                      <span>{t("autoResearch.create.createAndRun")}</span>
                      <span className="text-[10px] leading-snug text-muted-foreground">
                        {t("autoResearch.create.createAndRunDesc")}
                      </span>
                    </span>
                  </DropdownMenuItem>
                  <DropdownMenuItem onSelect={() => void handleSubmit(false)}>
                    <FilePlus2 className="size-3.5 text-muted-foreground" />
                    <span className="flex flex-col gap-0.5">
                      <span>{t("autoResearch.create.createOnly")}</span>
                      <span className="text-[10px] leading-snug text-muted-foreground">
                        {t("autoResearch.create.createOnlyDesc")}
                      </span>
                    </span>
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function Field({
  label,
  children,
  error,
  required,
}: {
  label: string
  children: React.ReactNode
  error?: string
  /** 必填项在标签后加一枚小徽章（不用红星，红星在暗色主题下不够显眼也不带语义）。 */
  required?: boolean
}) {
  const { t } = useTranslation()
  return (
    <div className="space-y-1">
      <SectionLabel className="flex items-center gap-1.5">
        <span className="block">{label}</span>
        {required && (
          <span className="rounded bg-destructive/10 px-1 py-px text-[9px] font-medium normal-case tracking-normal text-destructive">
            {t("autoResearch.create.requiredMark")}
          </span>
        )}
      </SectionLabel>
      {children}
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  )
}
