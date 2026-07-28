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

  const TOPIC_MIN = 1
  const TOPIC_MAX = 500

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

          <Field label={t("autoResearch.create.titleLabel")}>
            <Input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder={t("autoResearch.create.titlePlaceholder")}
            />
          </Field>

          <Field label={t("autoResearch.create.profileLabel")}>
            <Select
              value={profile}
              onValueChange={(v) => setProfile(v as ResearchProfile)}
            >
              <SelectTrigger size="sm" className="w-full text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {PROFILE_OPTIONS.map((p) => (
                  <SelectItem key={p} value={p}>
                    {t(`autoResearch.profile.${p}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
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
