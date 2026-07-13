import { AlertTriangle, Check, Loader2, Save } from "lucide-react"
import { useCallback, useEffect, useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import { Llm4AdResearchService } from "@/client"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { fetchResearchArtifact } from "@/hooks/useAutoResearch"
import { cn } from "@/lib/utils"

import { FileKindIcon } from "./ArtifactPreviewDialog"

/** 单个文件的编辑态。 */
interface FileState {
  loaded: boolean
  loading: boolean
  error: string | null
  /** 已保存到盘上的内容（对比判 dirty）。 */
  saved: string
  /** 编辑框草稿。 */
  draft: string
  saving: boolean
}

/**
 * 门控产物编辑弹框：左侧文件名列表，右侧编辑器（每个文件独立保存）。
 *
 * 文件按需懒加载（点选时才拉原文）。每个文件改完点「保存」直接覆写盘上产物
 * （PUT /artifacts/content，后端 user 隔离 + 防穿越 + 备份原文）。底部「确认」
 * 等价于门控 approve —— 文件已在盘上改好，pipeline 从下一 stage 用改后内容续跑。
 */
export default function GateEditDialog({
  open,
  sessionId,
  stageDir,
  files,
  initialFile,
  onConfirm,
  onClose,
}: {
  open: boolean
  sessionId: string
  /** stage 目录名，如 ``stage-05``；产物路径 = ``{stageDir}/{file}``。 */
  stageDir: string
  files: string[]
  /** 打开时默认激活的文件（缺省取第一个）。 */
  initialFile?: string | null
  /** 点「确认」回调（上层据此提交门控 approve）。 */
  onConfirm: () => void
  onClose: () => void
}) {
  const { t } = useTranslation()
  const [selected, setSelected] = useState<string | null>(
    initialFile ?? files[0] ?? null,
  )
  const [states, setStates] = useState<Record<string, FileState>>({})

  // files 数组每次消息刷新都是新引用，直接挂它做依赖会在编辑中途把草稿清空。
  // 用内容 key 稳定判定「文件集是否真的变了」；带上 session/stageDir，避免不同门控
  // 复用同名文件时串到上一门控已加载的内容。
  const filesKey = `${sessionId}|${stageDir}|${files.join("|")}`

  // 弹框每次打开重置选中（取 initialFile，缺省第一个）+ 清空缓存
  // （避免跨轮/跨门控串内容）。
  // biome-ignore lint/correctness/useExhaustiveDependencies: 用 filesKey 代替 files 数组身份
  // 弹框打开时定位到目标文件。跨门控/跨轮次（文件集变化）才清空缓存，避免
  // 串内容；同一门控内反复开关不清，保留已加载的内容。
  //
  // 关键：清空 states 必须与懒加载 effect 联动。若在这里 setStates({}) 但
  // selected 值不变，懒加载 effect（依赖 [open, selected]）不会重跑，且其闭包
  // 里的 states 仍是上一轮的 loaded 值 → guard 提前 return，不发 fetch，而 UI
  // 读到的却是被清空的 states → 永远卡在 loading。故这里不清 states，改由
  // filesKey 变化时的独立 effect 清，两个 effect 各自以内容 key 收敛。
  // biome-ignore lint/correctness/useExhaustiveDependencies: 用 filesKey 代替 files 数组身份
  useEffect(() => {
    if (open) {
      setSelected(initialFile ?? files[0] ?? null)
    }
  }, [open, filesKey, initialFile])

  // 文件集变化（换门控/轮次）时清空缓存，避免串内容。
  // biome-ignore lint/correctness/useExhaustiveDependencies: 只在 filesKey 变化时清
  useEffect(() => {
    setStates({})
  }, [filesKey])

  const patch = useCallback((name: string, p: Partial<FileState>) => {
    setStates((prev) => ({
      ...prev,
      [name]: { ...prev[name], ...p } as FileState,
    }))
  }, [])

  // 选中文件时懒加载原文（同一文件只拉一次）。
  // 依赖仅 open/selected：不挂 states，否则 patch(loading:true) 改了 states →
  // 本 effect 重跑 → cleanup 把在途 fetch 标记 cancelled，结果被丢弃、loading
  // 永远停在 true。states 的清空只在 filesKey 变化时发生（见上），与本 effect
  // 的 selected 变化同源，闭包不会滞后。
  // biome-ignore lint/correctness/useExhaustiveDependencies: 故意不挂 states/patch，见上
  useEffect(() => {
    if (!open || !selected) return
    const cur = states[selected]
    if (cur?.loaded || cur?.loading) return
    let cancelled = false
    patch(selected, {
      loaded: false,
      loading: true,
      error: null,
      saved: "",
      draft: "",
      saving: false,
    })
    ;(async () => {
      try {
        const resp = await fetchResearchArtifact(
          sessionId,
          `${stageDir}/${selected}`,
        )
        const txt = await resp.text()
        if (cancelled) return
        patch(selected, {
          loaded: true,
          loading: false,
          saved: txt,
          draft: txt,
        })
      } catch (e) {
        if (cancelled) return
        patch(selected, {
          loading: false,
          error: (e as Error)?.message ?? "error",
        })
      }
    })()
    return () => {
      cancelled = true
    }
  }, [open, selected, sessionId, stageDir])

  const cur = selected ? states[selected] : undefined
  const dirty = !!cur && cur.loaded && cur.draft !== cur.saved
  const anySaving = useMemo(
    () => Object.values(states).some((s) => s.saving),
    [states],
  )
  // 有未保存草稿时禁止「确认」：确认等于 approve，会用盘上旧内容续跑，静默丢弃编辑。
  const anyDirty = useMemo(
    () => Object.values(states).some((s) => s.loaded && s.draft !== s.saved),
    [states],
  )

  const saveCurrent = async () => {
    if (!selected || !cur || !dirty || cur.saving) return
    patch(selected, { saving: true })
    try {
      await Llm4AdResearchService.writeArtifact({
        sessionId,
        path: `${stageDir}/${selected}`,
        requestBody: { content: cur.draft },
      })
      patch(selected, { saving: false, saved: cur.draft })
      toast.success(t("autoResearch.form.editSaved"))
    } catch (e) {
      patch(selected, { saving: false })
      toast.error(
        (e as Error)?.message ?? t("autoResearch.form.editSaveFailed"),
      )
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-4xl gap-3">
        <DialogHeader>
          <DialogTitle className="text-sm">
            {t("autoResearch.form.editTitle")}
          </DialogTitle>
        </DialogHeader>

        <div className="flex h-[62vh] gap-3">
          {/* 左：文件名列表 */}
          <div className="w-52 shrink-0 overflow-y-auto rounded-md border border-border/50 bg-muted/20 p-1">
            {files.map((f) => {
              const st = states[f]
              const fileDirty = !!st && st.loaded && st.draft !== st.saved
              return (
                <button
                  key={f}
                  type="button"
                  onClick={() => setSelected(f)}
                  className={cn(
                    "flex w-full items-center gap-1.5 rounded-md px-2 py-1.5 text-left text-xs transition-colors",
                    selected === f
                      ? "bg-primary/15 text-foreground"
                      : "text-foreground/80 hover:bg-background/60",
                  )}
                >
                  <FileKindIcon name={f} />
                  <span className="truncate">{f}</span>
                  {fileDirty && (
                    <span
                      className="ml-auto size-1.5 shrink-0 rounded-full bg-amber-500"
                      title={t("autoResearch.form.editUnsaved")}
                    />
                  )}
                </button>
              )
            })}
          </div>

          {/* 右：编辑器 */}
          <div className="flex min-w-0 flex-1 flex-col gap-2">
            <div className="flex items-center gap-2">
              <span className="truncate text-xs font-mono text-muted-foreground">
                {selected}
              </span>
              <button
                type="button"
                onClick={() => void saveCurrent()}
                disabled={!dirty || cur?.saving}
                className="ml-auto inline-flex items-center gap-1 rounded-md border border-primary/40 bg-primary/10 px-2.5 py-1 text-xs font-medium text-primary transition-colors hover:bg-primary/20 disabled:opacity-40"
              >
                {cur?.saving ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <Save className="size-3.5" />
                )}
                {t("autoResearch.form.editSave")}
              </button>
            </div>

            <div className="min-h-0 flex-1 overflow-hidden rounded-md border border-border/50 bg-background/60">
              {!selected ? (
                <div className="flex h-full items-center justify-center text-xs text-muted-foreground">
                  {t("autoResearch.form.editEmpty")}
                </div>
              ) : cur?.loading || !cur?.loaded ? (
                cur?.error ? (
                  <div className="flex h-full flex-col items-center justify-center gap-2 px-4 text-center text-xs text-destructive">
                    <AlertTriangle className="size-5" />
                    {cur.error}
                  </div>
                ) : (
                  <div className="flex h-full items-center justify-center gap-2 text-xs text-muted-foreground">
                    <Loader2 className="size-4 animate-spin" />
                    {t("autoResearch.artifacts.previewLoading")}
                  </div>
                )
              ) : (
                <textarea
                  value={cur.draft}
                  onChange={(e) =>
                    selected && patch(selected, { draft: e.target.value })
                  }
                  spellCheck={false}
                  className="h-full w-full resize-none bg-transparent p-3 text-xs leading-relaxed font-mono text-foreground/90 outline-none"
                />
              )}
            </div>
          </div>
        </div>

        <DialogFooter className="gap-2">
          {anyDirty && (
            <span className="mr-auto inline-flex items-center gap-1 text-[11px] text-amber-600 dark:text-amber-400">
              <AlertTriangle className="size-3" />
              {t("autoResearch.form.editUnsavedHint")}
            </span>
          )}
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-border/60 px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:text-foreground"
          >
            {t("autoResearch.form.editCancel")}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={anySaving || anyDirty}
            title={t("autoResearch.form.editConfirmHint")}
            className="inline-flex items-center gap-1 rounded-md border border-primary/40 bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary transition-colors hover:bg-primary/20 disabled:opacity-40"
          >
            <Check className="size-3.5" />
            {t("autoResearch.form.editConfirm")}
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
