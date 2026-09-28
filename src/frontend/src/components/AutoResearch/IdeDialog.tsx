import { AlertTriangle, Code, Loader2, RefreshCw, X } from "lucide-react"
import { useCallback, useEffect, useRef, useState } from "react"
import { useTranslation } from "react-i18next"

import { UtilsCodeServerService } from "@/client"
import { useTheme } from "@/components/theme-provider"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { cn } from "@/lib/utils"

// 重启 IDE 冷却：与 evolution 的 InitializedView 保持一致，防止连点。
const IDE_REFRESH_COOLDOWN_MS = 3000

interface Props {
  sessionId: string
  open: boolean
  onOpenChange: (open: boolean) => void
}

/**
 * 全屏 code-server IDE 弹层：先取 code token 启动/复用容器，再挂 iframe。
 *
 * 调用方按需挂载（`{open && <IdeDialog ... />}`），关闭即卸载——IDE 的加载态、
 * iframe key、在途请求都随组件一起销毁，下次打开天然是干净的 idle 态，不必像
 * 常驻弹层那样用请求代次作废迟到的 setState。
 */
export default function IdeDialog({ sessionId, open, onOpenChange }: Props) {
  const { t } = useTranslation()
  const { resolvedTheme } = useTheme()
  const [state, setState] = useState<"idle" | "loading" | "success" | "error">(
    "idle",
  )
  const [error, setError] = useState("")
  const [iframeKey, setIframeKey] = useState(0)
  const [refreshing, setRefreshing] = useState(false)

  const load = useCallback(() => {
    setState("loading")
    return UtilsCodeServerService.getCodeToken({
      dark: resolvedTheme === "dark",
    })
      .then(() => {
        setState("success")
        setIframeKey((k) => k + 1)
      })
      .catch((err: unknown) => {
        setState("error")
        const e = err as { body?: { detail?: string }; message?: string }
        setError(
          e?.body?.detail || e?.message || t("evolution.getCodeTokenFailed"),
        )
      })
  }, [resolvedTheme, t])

  // 首次挂载即拉 token（组件只在 open 时被挂载，故无需再判断 open）。
  const bootRef = useRef(false)
  useEffect(() => {
    if (bootRef.current) return
    bootRef.current = true
    void load()
  }, [load])

  // 重启 IDE：重新拉 token 并重载 iframe，带冷却防连点（同 evolution）。
  const handleRestart = () => {
    if (refreshing) return
    setRefreshing(true)
    void load().finally(() => {
      setTimeout(() => setRefreshing(false), IDE_REFRESH_COOLDOWN_MS)
    })
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        showCloseButton={false}
        className="max-w-none w-screen h-screen sm:max-w-none translate-x-0 translate-y-0 top-0 left-0 rounded-none border-0 p-0 gap-0 grid-rows-[auto_minmax(0,1fr)] bg-background/95 backdrop-blur"
      >
        <DialogHeader className="flex flex-row items-center justify-between gap-2 h-14 px-5 border-b border-border/60 space-y-0 text-left">
          <DialogTitle className="flex items-center gap-2 text-base">
            <Code className="size-4 text-primary" />
            {t("autoResearch.mainTabs.ide")}
          </DialogTitle>
          <div className="flex items-center gap-1">
            <button
              type="button"
              aria-label={t("evolution.ideRefresh.label")}
              disabled={refreshing}
              onClick={handleRestart}
              title={t("evolution.ideRefresh.tooltip")}
              className="inline-flex items-center justify-center size-5 rounded text-muted-foreground hover:text-primary hover:bg-primary/10 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <RefreshCw
                className={cn("size-3", refreshing && "animate-spin")}
              />
            </button>
            <DialogClose asChild>
              <button
                type="button"
                aria-label={t("common.close")}
                className="grid place-items-center size-8 rounded-md text-muted-foreground hover:text-foreground hover:bg-accent/60 transition-colors"
              >
                <X className="size-4" />
              </button>
            </DialogClose>
          </div>
        </DialogHeader>
        <div className="min-h-0 overflow-hidden">
          <div className="h-full p-4">
            {state === "loading" && (
              <div className="h-full flex items-center justify-center rounded-lg border border-dashed bg-card/50">
                <Loader2 className="mr-2 size-5 animate-spin" />
                <span className="text-muted-foreground">
                  {t("evolution.startingIDE")}
                </span>
              </div>
            )}
            {state === "error" && (
              <div className="h-full flex flex-col items-center justify-center gap-3 rounded-lg border border-destructive/50 bg-card/50">
                <AlertTriangle className="size-8 text-destructive/70" />
                <p className="max-w-md px-4 text-center text-sm text-destructive">
                  {error}
                </p>
                <button
                  type="button"
                  className="inline-flex items-center gap-1.5 rounded-md border border-primary/40 bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary transition-colors hover:bg-primary/20"
                  onClick={() => void load()}
                >
                  <RefreshCw className="size-3.5" />
                  {t("common.retry")}
                </button>
              </div>
            )}
            {state === "success" && (
              <iframe
                key={iframeKey}
                id="autoresearchVscodeFrame"
                className="w-full h-full border rounded-lg"
                src={`${import.meta.env.VITE_CODE_SERVER_URL || "/code_ide"}/?folder=/data/project_home/research/${sessionId}/`}
                title={t("evolution.vsCodeTitle")}
                allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; fullscreen"
                sandbox="allow-same-origin allow-scripts allow-popups allow-forms allow-modals allow-downloads allow-presentation"
                loading="lazy"
              />
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
