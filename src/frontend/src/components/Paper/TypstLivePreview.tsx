import { unzipSync } from "fflate"
import {
  AlertTriangle,
  Check,
  Download,
  FileOutput,
  Loader2,
  RefreshCw,
} from "lucide-react"
import { useEffect, useRef, useState } from "react"
import { useTranslation } from "react-i18next"

import { Llm4AdPapersService } from "@/client"
import { Button } from "@/components/ui/button"
import { authFetch } from "@/utils/auth"

import { TYPST_EXTRA_FONTS, TYPST_FONT_BASE } from "./typstFonts"

type PreviewStatus = "idle" | "loading" | "compiling" | "ready" | "error"

let runtimePromise: Promise<typeof import("@myriaddreamin/typst.ts")> | null =
  null
let compileQueue: Promise<unknown> = Promise.resolve()

function relativeTypstPath(fromPath: string, toPath: string) {
  const from = fromPath.split("/").filter(Boolean).slice(0, -1)
  const to = toPath.split("/").filter(Boolean)
  let shared = 0
  while (
    shared < from.length &&
    shared < to.length &&
    from[shared] === to[shared]
  ) {
    shared += 1
  }
  return [...from.slice(shared).map(() => ".."), ...to.slice(shared)].join("/")
}

function ensureTypstBibliography(
  entryPath: string,
  entrySource: string,
  bundle: Record<string, Uint8Array>,
) {
  const bibliography = bundle["references.bib"]
  if (
    !bibliography?.byteLength ||
    !new TextDecoder().decode(bibliography).trim() ||
    /#bibliography\s*\(/.test(entrySource)
  ) {
    return entrySource
  }
  const bibliographyPath = relativeTypstPath(entryPath, "references.bib")
  return `${entrySource.trimEnd()}\n\n#bibliography("${bibliographyPath}")\n`
}

function loadTypstRuntime() {
  runtimePromise ??= Promise.all([
    import("@myriaddreamin/typst.ts"),
    import("@myriaddreamin/typst.ts/contrib/snippet"),
    import("@myriaddreamin/typst-ts-web-compiler/wasm?url"),
  ]).then(([runtime, snippet, wasm]) => {
    // The two default groups are pinned to a CDN by `typst.ts`, and the browser
    // fetches them directly, so they fail outright wherever outbound internet is
    // unavailable. `assetUrlPrefix` keeps the groups but redirects them to the
    // copies the frontend serves itself.
    runtime.$typst.use(
      snippet.TypstSnippet.preloadFontAssets({
        assets: ["text", "cjk"],
        assetUrlPrefix: TYPST_FONT_BASE,
      }),
    )
    // Added alongside the two default groups, not instead of them: the defaults
    // cover the common case, and these fill the gaps they leave (chiefly bold
    // CJK, which the default CJK group has no face for). Fetched on the first
    // compilation, so the first preview waits for these bytes.
    runtime.$typst.use(snippet.TypstSnippet.preloadFonts(TYPST_EXTRA_FONTS))
    runtime.$typst.setCompilerInitOptions({ getModule: () => wasm.default })
    return runtime
  })
  return runtimePromise
}

function compileErrorMessage(error: unknown) {
  if (error instanceof Error && error.message.trim()) return error.message
  return String(error || "Typst compilation failed")
}

export default function TypstLivePreview({
  sourceVersionId,
  sourceHash,
  activePath,
  entryPath,
  source,
  sourceLoading = false,
  downloadName = "research-proposal.pdf",
  downloadEnabled = false,
}: {
  sourceVersionId: string
  sourceHash: string
  activePath: string
  entryPath: string
  source: string
  sourceLoading?: boolean
  downloadName?: string
  downloadEnabled?: boolean
}) {
  const { t } = useTranslation()
  const [status, setStatus] = useState<PreviewStatus>("idle")
  const [pdfUrl, setPdfUrl] = useState<string | null>(null)
  const [error, setError] = useState("")
  const [bundle, setBundle] = useState<
    Record<string, Uint8Array> | null | undefined
  >(undefined)
  const [revision, setRevision] = useState(0)
  const compileSequence = useRef(0)

  useEffect(() => {
    let cancelled = false
    setBundle(undefined)
    setStatus("loading")
    setError("")
    const loadBundle = async () => {
      try {
        const exported = await Llm4AdPapersService.exportSourceVersion({
          sourceVersionId,
        })
        const response = await authFetch(exported.url)
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        const archive = unzipSync(new Uint8Array(await response.arrayBuffer()))
        if (!cancelled) setBundle(archive)
      } catch {
        if (!cancelled) {
          setBundle(null)
          setStatus("error")
          setError(t("paper.typst.bundleUnavailable"))
        }
      }
    }
    void loadBundle()
    return () => {
      cancelled = true
    }
  }, [sourceHash, sourceVersionId, t])

  useEffect(() => {
    if (sourceLoading) return
    if (!source.trim()) {
      setStatus("idle")
      setError("")
      return
    }
    if (bundle === undefined || bundle === null) return
    const bundledSource = bundle[activePath]
    if (
      pdfUrl &&
      bundledSource &&
      new TextDecoder().decode(bundledSource) === source
    ) {
      setStatus("ready")
      setError("")
      return
    }
    const sequence = ++compileSequence.current
    const timer = window.setTimeout(async () => {
      setStatus(runtimePromise ? "compiling" : "loading")
      try {
        const runtime = await loadTypstRuntime()
        if (sequence !== compileSequence.current) return
        setStatus("compiling")
        const compile = async () => {
          const { $typst } = runtime
          await $typst.resetShadow()
          for (const [path, content] of Object.entries(bundle)) {
            if (path.endsWith("/")) continue
            await $typst.mapShadow(`/${path}`, content)
          }
          await $typst.mapShadow(
            `/${activePath}`,
            new TextEncoder().encode(source),
          )
          const bundledEntry = bundle[entryPath]
          const entrySource =
            activePath === entryPath
              ? source
              : bundledEntry
                ? new TextDecoder().decode(bundledEntry)
                : ""
          if (entrySource) {
            await $typst.mapShadow(
              `/${entryPath}`,
              new TextEncoder().encode(
                ensureTypstBibliography(entryPath, entrySource, bundle),
              ),
            )
          }
          return $typst.pdf({ mainFilePath: `/${entryPath}`, root: "/" })
        }
        const pendingCompile = compileQueue.then(compile, compile)
        compileQueue = pendingCompile.then(
          () => undefined,
          () => undefined,
        )
        const pdf = await pendingCompile
        if (sequence !== compileSequence.current) return
        if (!pdf?.byteLength) throw new Error("Typst returned an empty PDF")
        const bytes = Uint8Array.from(pdf)
        const nextUrl = URL.createObjectURL(
          new Blob([bytes.buffer], { type: "application/pdf" }),
        )
        setPdfUrl((current) => {
          if (current) URL.revokeObjectURL(current)
          return nextUrl
        })
        setError("")
        setStatus("ready")
      } catch (compileError) {
        if (sequence !== compileSequence.current) return
        setError(compileErrorMessage(compileError))
        setStatus("error")
      }
    }, 650)
    return () => window.clearTimeout(timer)
  }, [activePath, bundle, entryPath, pdfUrl, revision, source, sourceLoading])

  useEffect(
    () => () => {
      if (pdfUrl) URL.revokeObjectURL(pdfUrl)
    },
    [pdfUrl],
  )

  const busy = status === "loading" || status === "compiling"

  return (
    <section className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden bg-muted/20">
      <header className="flex h-12 shrink-0 items-center gap-2 border-b bg-background px-3">
        <span className="grid size-7 shrink-0 place-items-center rounded-md bg-primary/10 text-primary">
          <FileOutput className="size-3.5" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-xs font-semibold">
            {t("paper.typst.previewTitle")}
          </p>
          <p className="truncate text-[10px] text-muted-foreground">
            {status === "loading"
              ? t("paper.typst.loadingCompiler")
              : status === "compiling"
                ? t("paper.typst.compiling")
                : status === "ready"
                  ? t("paper.typst.upToDate")
                  : status === "error"
                    ? t("paper.typst.compileFailed")
                    : t("paper.typst.waitingForSource")}
          </p>
        </div>
        {downloadEnabled && pdfUrl && status === "ready" && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-8 gap-1.5 px-2.5 text-xs"
            title={t("paper.typst.downloadPdf")}
            onClick={() => {
              const anchor = document.createElement("a")
              anchor.href = pdfUrl
              anchor.download = downloadName
              anchor.click()
            }}
          >
            <Download className="size-3.5" />
            {t("paper.typst.downloadPdf")}
          </Button>
        )}
        {busy ? (
          <Loader2 className="size-4 animate-spin text-primary" />
        ) : status === "ready" ? (
          <Check className="size-4 text-emerald-600 dark:text-emerald-400" />
        ) : (
          <Button
            type="button"
            size="icon"
            variant="ghost"
            className="size-8"
            title={t("paper.typst.recompile")}
            disabled={!source.trim()}
            onClick={() => setRevision((value) => value + 1)}
          >
            <RefreshCw className="size-3.5" />
          </Button>
        )}
      </header>
      <div className="relative min-h-0 flex-1 overflow-hidden">
        {pdfUrl ? (
          <iframe
            title={t("paper.typst.previewTitle")}
            src={`${pdfUrl}#view=FitH&toolbar=1`}
            className="h-full w-full border-0 bg-white"
          />
        ) : (
          <div className="grid h-full place-items-center p-8">
            <div className="max-w-sm text-center text-sm text-muted-foreground">
              {busy ? (
                <Loader2 className="mx-auto mb-3 size-7 animate-spin text-primary" />
              ) : (
                <FileOutput className="mx-auto mb-3 size-8 opacity-50" />
              )}
              {busy
                ? t("paper.typst.preparingPreview")
                : t("paper.typst.emptyPreview")}
            </div>
          </div>
        )}
        {error && (
          <div className="absolute inset-x-3 bottom-3 flex max-h-28 items-start gap-2 overflow-auto rounded-lg border border-destructive/25 bg-background/95 px-3 py-2 text-xs text-destructive shadow-lg backdrop-blur">
            <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
            <pre className="min-w-0 whitespace-pre-wrap break-words font-mono text-[10px] leading-4">
              {error}
            </pre>
          </div>
        )}
      </div>
    </section>
  )
}
