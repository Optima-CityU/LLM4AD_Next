import { Loader2 } from "lucide-react"
import { useEffect, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"

import type {
  ResearchFolderItem,
  ResearchMode,
  ResearchSessionCreateRequest,
  ResearchSessionItem,
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
  useCreateResearchSession,
  useStartResearchTurn,
} from "@/hooks/useAutoResearch"
import { cn } from "@/lib/utils"

import ProviderModelPicker from "./ProviderModelPicker"
import { MODE_OPTIONS, PROFILE_OPTIONS, type ResearchProfile } from "./shared"
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
  const [folderId, setFolderId] = useState<string | null>(initialFolderId)
  const [providerId, setProviderId] = useState("default")
  const [modelName, setModelName] = useState("")
  const [mode, setMode] = useState<ResearchMode>("co-pilot")
  const [profile, setProfile] = useState<ResearchProfile>("algorithm_evolution")
  const [autoStart, setAutoStart] = useState(false)
  const [topicError, setTopicError] = useState("")
  const [titleError, setTitleError] = useState("")

  const TOPIC_MIN = 1
  const TOPIC_MAX = 500
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
    setFolderId(initialFolderId)
    setProviderId("default")
    setModelName("")
    setMode("co-pilot")
    setProfile("algorithm_evolution")
    setAutoStart(false)
    setTopicError("")
    setTitleError("")
    createdRef.current = null
  }

  const handleSubmit = async () => {
    const trimmed = topic.trim()
    if (trimmed.length < TOPIC_MIN) {
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
          topic: topic.trim(),
          title: title.trim() || undefined,
          folder_id: folderId,
          provider_id: providerId.trim() || null,
          model_name: modelName.trim() || null,
          mode,
          profile,
        } as ResearchSessionCreateRequest))
      createdRef.current = created

      if (autoStart) {
        // 首启一轮
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
      <DialogContent
        className="sm:max-w-[540px] max-h-[85vh] overflow-y-auto"
        preventOutsideClose
      >
        <DialogHeader>
          <DialogTitle>{t("autoResearch.create.title")}</DialogTitle>
          <DialogDescription className="text-xs">
            {t("autoResearch.create.subtitle")}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3 py-2">
          <Field label={t("autoResearch.create.topicLabel")} error={topicError}>
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
                      "group relative rounded-lg px-4 py-3 text-left transition-all",
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
                          "mt-0.5 size-4 shrink-0 rounded-full border-2 transition-all",
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
                      <div className="flex-1 min-w-0 space-y-1">
                        <div
                          className={cn(
                            "text-sm font-medium transition-colors",
                            profile === p
                              ? "text-foreground"
                              : "text-foreground/80 group-hover:text-foreground",
                          )}
                        >
                          {t(`autoResearch.profile.${p}`)}
                        </div>
                        <div className="text-[11px] leading-relaxed text-muted-foreground">
                          {t(`autoResearch.profileDesc.${p}`)}
                        </div>
                      </div>
                    </div>
                  </button>
                )
              })}
            </div>
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

          <label className="flex items-center gap-2 text-xs text-muted-foreground pt-1 select-none">
            <input
              type="checkbox"
              checked={autoStart}
              onChange={(e) => setAutoStart(e.target.checked)}
              className="size-3.5 accent-primary"
            />
            {t("autoResearch.create.createAndStart")}
          </label>
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={submitting}
          >
            {t("common.cancel")}
          </Button>
          <Button
            onClick={() => void handleSubmit()}
            disabled={submitting || !topic.trim()}
          >
            {submitting && <Loader2 className="size-4 animate-spin" />}
            {submitting
              ? t("autoResearch.create.creating")
              : autoStart
                ? t("autoResearch.create.createAndStart")
                : t("autoResearch.create.create")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function Field({
  label,
  children,
  error,
}: {
  label: string
  children: React.ReactNode
  error?: string
}) {
  return (
    <div className="space-y-1">
      <SectionLabel className="block">{label}</SectionLabel>
      {children}
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  )
}
