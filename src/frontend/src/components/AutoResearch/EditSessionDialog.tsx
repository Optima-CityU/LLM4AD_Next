import { Loader2 } from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"

import type {
  ResearchFolderItem,
  ResearchMode,
  ResearchSessionItem,
  ResearchSessionUpdateRequest,
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
import { useUpdateResearchSession } from "@/hooks/useAutoResearch"
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
import { SectionLabel } from "./tech"

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** 待编辑会话；关闭态为 null。 */
  session: ResearchSessionItem | null
  folders: ResearchFolderItem[]
  /**
   * 跨类 profile 切换的拦截点：命中时父层弹「清空产物」二次确认（带打包下载），
   * 返回 true 表示已在别处处理、本弹框可关闭；false 表示用户放弃，保持打开。
   */
  onProfileSwitchRequired: (
    session: ResearchSessionItem,
    profile: string,
  ) => Promise<boolean>
}

const TOPIC_MAX = 2000
const TITLE_MAX = 255
const METRIC_KEY_MAX = 64

/**
 * 编辑会话对话框：与新建弹框同款表单，但没有模板选择、没有「创建并运行」。
 *
 * 字段与后端 ``ResearchSessionUpdateRequest`` 一一对应。PATCH 语义下「未提供的键
 * = 不改」，故提交前逐字段与打开时的快照比对，只把真正变了的键放进请求体——这样
 * 既避免无谓的 ``updated_time`` 抖动，也避免把 ``provider_id`` / ``model_name``
 * 这类「传了就覆盖」的字段无脑回写。
 *
 * 唯一需要二次确认的是 **跨类 profile 切换**（sandbox ↔ 非 sandbox）：后端会清空
 * 第 9 步之后的产物且不可恢复，故不在本弹框里直接提交，而是交给父层现有确认框。
 */
export default function EditSessionDialog({
  open,
  onOpenChange,
  session,
  folders,
  onProfileSwitchRequired,
}: Props) {
  const { t } = useTranslation()

  const [title, setTitle] = useState("")
  const [topic, setTopic] = useState("")
  const [profile, setProfile] = useState<ResearchProfile>("algorithm_evolution")
  const [folderId, setFolderId] = useState<string | null>(null)
  const [mode, setMode] = useState<ResearchMode>("co-pilot")
  const [metricDirection, setMetricDirection] =
    useState<MetricDirection>("auto")
  const [metricKey, setMetricKey] = useState("")
  const [providerId, setProviderId] = useState("default")
  const [modelName, setModelName] = useState("")
  /**
   * 模型候选浮层的挂载节点（DialogContent 内部的绝对定位层）。存 state 而非 ref：
   * 浮层需要「节点出现后」才渲染，ref 变化不会触发重渲染，挂载点会一直是 null。
   */
  const [contentEl, setContentEl] = useState<HTMLDivElement | null>(null)

  const updateMut = useUpdateResearchSession()

  // 每次打开都从当前会话重灌表单：弹框是「无状态」的一次性编辑器，避免上次
  // 编辑残留的值被当成本次意图（尤其是被取消掉的那一次）。
  useEffect(() => {
    if (!open || !session) return
    setTitle(session.title ?? "")
    setTopic(session.topic ?? "")
    setProfile(session.profile as ResearchProfile)
    setFolderId(session.folder_id ?? null)
    setMode(session.mode as ResearchMode)
    setMetricDirection(
      session.metric_direction === "maximize" ||
        session.metric_direction === "minimize"
        ? session.metric_direction
        : "auto",
    )
    setMetricKey(session.metric_key ?? "")
    setProviderId(session.provider_id || "default")
    setModelName(session.model_name ?? "")
  }, [open, session])

  /** 与打开时快照比对，产出只含变更键的 PATCH 请求体。 */
  const patch = useMemo<ResearchSessionUpdateRequest>(() => {
    if (!session) return {}
    const body: ResearchSessionUpdateRequest = {}

    const nextTitle = title.trim()
    // 后端对 title 的 strip-空值策略是「保留原值」，所以这里也只在非空时提交。
    if (nextTitle && nextTitle !== session.title) body.title = nextTitle

    const nextTopic = topic.trim()
    if (nextTopic !== session.topic) body.topic = nextTopic

    if (profile !== session.profile) body.profile = profile
    // 显式带 folder_id 键（含 null）才表示「移动」；未变则完全不出现该键。
    if (folderId !== (session.folder_id ?? null)) body.folder_id = folderId
    if (mode !== session.mode) body.mode = mode

    const nextDirection = metricDirectionToApi(metricDirection)
    if (nextDirection !== session.metric_direction) {
      body.metric_direction = nextDirection
    }
    const nextMetricKey = metricKey.trim()
    if (nextMetricKey !== session.metric_key) body.metric_key = nextMetricKey

    if (providerId !== (session.provider_id || "default")) {
      body.provider_id = providerId
    }
    if (modelName !== (session.model_name ?? "")) body.model_name = modelName

    return body
  }, [
    session,
    title,
    topic,
    profile,
    folderId,
    mode,
    metricDirection,
    metricKey,
    providerId,
    modelName,
  ])

  const dirty = Object.keys(patch).length > 0
  const submitting = updateMut.isPending

  const handleSubmit = async () => {
    if (!session || !dirty || submitting) return

    // profile 单独一类：跨类切换由父层走「清空产物」二次确认（带打包下载），
    // 且必须发生在其它字段落库之前——一旦清盘提交失败，用户不该看到一个
    // 「标题改了、产物没了」的半成品状态。
    if (profile !== session.profile) {
      const proceed = await onProfileSwitchRequired(session, profile)
      if (!proceed) return
    }

    // 其余字段：把 profile 从 PATCH 里剔除（父层已单独提交），只发真正变更的键。
    const rest: ResearchSessionUpdateRequest = { ...patch }
    delete rest.profile
    try {
      if (Object.keys(rest).length > 0) {
        await updateMut.mutateAsync({ sessionId: session.id, body: rest })
      }
      toast.success(t("autoResearch.edit.saved"))
      onOpenChange(false)
    } catch (err: unknown) {
      const detail =
        (err as { body?: { detail?: string } })?.body?.detail ??
        (err as Error)?.message ??
        "error"
      toast.error(detail)
    }
  }

  // 标题框有 maxLength 兜底、超不了；主题没有，故只对主题做超限禁用。
  const topicOver = topic.length > TOPIC_MAX

  return (
    <Dialog open={open} onOpenChange={(v) => !submitting && onOpenChange(v)}>
      {/* 与新建弹框同款三段式：标题 / 可滚表单 / 按钮。原先整框 overflow-y-auto
          会让 grid 的隐式行被撑长，footer 跟着内容滚出视口。 */}
      <DialogContent
        className="sm:max-w-[760px] grid-rows-[auto_minmax(0,1fr)_auto] max-h-[85vh] overflow-hidden"
        preventOutsideClose
      >
        {/* 浮层挂载点：与新建弹框同一套做法——在收口的 DialogContent 里开一个
            overflow-visible 的绝对定位层。挂在这里的 Popover 仍算落在 DialogContent
            这个滚动锁 shard 内（react-remove-scroll 只把 DialogContent 登记为 shard，
            挂到 body 上的浮层滚轮会被 preventDefault），同时又能超出弹框边界显示，
            不被 DialogContent 的 overflow-hidden 裁掉。pointer-events-none 只是不让
            这层挡住下面的表单，浮层自身会重新打开指针事件。 */}
        <div
          ref={setContentEl}
          className="pointer-events-none absolute inset-0 overflow-visible"
          style={{ gridArea: "1 / 1 / -1 / -1" }}
        />
        <DialogHeader>
          <DialogTitle>{t("autoResearch.edit.title")}</DialogTitle>
          <DialogDescription className="text-xs">
            {t("autoResearch.edit.subtitle")}
          </DialogDescription>
        </DialogHeader>

        <div className="-mx-1 space-y-2.5 overflow-y-auto px-1 py-2">
          <Field label={t("autoResearch.create.titleLabel")}>
            <div className="relative">
              <Input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder={t("autoResearch.create.titlePlaceholder")}
                maxLength={TITLE_MAX}
                autoFocus
              />
              {title.length > 0 && (
                <Counter current={title.length} max={TITLE_MAX} />
              )}
            </div>
          </Field>

          <Field label={t("autoResearch.create.topicLabel")} required>
            <div className="relative">
              <textarea
                value={topic}
                onChange={(e) => setTopic(e.target.value)}
                placeholder={t("autoResearch.create.topicPlaceholder")}
                rows={3}
                className={cn(
                  "w-full resize-none rounded-md border bg-background/60 px-2 py-1.5 text-sm transition-colors focus:outline-none focus:ring-1",
                  topicOver
                    ? "border-destructive focus:border-destructive focus:ring-destructive/30"
                    : "border-border/60 focus:border-primary/50 focus:ring-primary/30",
                )}
              />
              <Counter current={topic.length} max={TOPIC_MAX} />
            </div>
          </Field>

          <Field label={t("autoResearch.create.profileLabel")}>
            <div className="grid grid-cols-2 gap-3">
              {PROFILE_OPTIONS.map((p) => (
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
                        {/* 与新建弹框一致：llm4ad（sandbox）是主推路径，标一枚推荐。 */}
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
              ))}
            </div>
            {/* 跨类切换会清空产物，提前在表单里示警，别让用户保存后才发现。
                用与模板提示同款的琥珀告警盒（带边框），比裸文字更像「警告」。 */}
            {session &&
              profile !== session.profile &&
              isCrossTypeProfile(session.profile, profile) && (
                <div className="mt-1.5 rounded-md border border-amber-500/30 bg-amber-500/5 px-2 py-1.5">
                  <p className="text-[10px] leading-relaxed text-amber-700 dark:text-amber-400">
                    {t("autoResearch.edit.profilePurgeHint")}
                  </p>
                </div>
              )}
          </Field>

          {/* 指标名与优化方向合成一行，与新建弹框同一套版式：左边填名字，右边用
              分段控件选方向（每项的长说明做成 tooltip，不占行内宽度）。 */}
          <Field label={t("autoResearch.create.metricLabel")}>
            <div className="grid grid-cols-2 gap-3">
              <Input
                value={metricKey}
                onChange={(e) => setMetricKey(e.target.value)}
                maxLength={METRIC_KEY_MAX}
                placeholder={t("autoResearch.create.metricKeyPlaceholder")}
              />
              {/* 原生 radio + sr-only 藏输入框，选中态画在外层 label 上：键盘方向键
                  切换、aria-checked 都是原生行为。 */}
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
                          name="edit-metric-direction"
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
              portalContainer={contentEl}
            />
          </Field>
        </div>

        <DialogFooter className="items-center sm:justify-between">
          {/* 左侧常驻一行说明：只提交改动过的字段，与新建弹框的 footer 同款版式。 */}
          <p className="hidden text-[10px] text-muted-foreground/70 sm:block">
            {t("autoResearch.edit.footerHint")}
          </p>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={submitting}
            >
              {t("common.cancel")}
            </Button>
            <Button
              onClick={() => void handleSubmit()}
              disabled={!dirty || submitting || topicOver}
            >
              {submitting && <Loader2 className="size-4 animate-spin" />}
              {submitting ? t("autoResearch.edit.saving") : t("common.save")}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/**
 * 参考的 profile 是否属于 sandbox 类型（走 LLM4AD 演化引擎）。
 *
 * 与后端 ``profile_switch._is_sandbox_profile`` 同一判定口径的两个已知值：只有
 * ``algorithm_evolution`` 是 sandbox。这里不复用 PROFILE_OPTIONS 之外的值——若
 * profile 列表扩到 3 个以上，此函数须与后端同步更新。
 */
function isCrossTypeProfile(from: string, to: string): boolean {
  const isSandbox = (p: string) => p === "algorithm_evolution"
  return isSandbox(from) !== isSandbox(to)
}

/** 右下角字数统计：接近上限转琥珀、超限转红。 */
function Counter({ current, max }: { current: number; max: number }) {
  return (
    <div
      className={cn(
        "absolute bottom-1.5 right-1.5 rounded px-1.5 py-0.5 text-[10px] font-mono tabular-nums backdrop-blur-sm pointer-events-none",
        current > max
          ? "bg-destructive/90 text-destructive-foreground"
          : current > max * 0.9
            ? "bg-amber-500/90 text-white"
            : "bg-muted/80 text-muted-foreground",
      )}
    >
      {current} / {max}
    </div>
  )
}

function Field({
  label,
  children,
  required,
}: {
  label: string
  children: React.ReactNode
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
    </div>
  )
}
