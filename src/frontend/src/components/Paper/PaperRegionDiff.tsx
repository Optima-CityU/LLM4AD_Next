import { useTranslation } from "react-i18next"
import { paperRegionDiff } from "@/lib/paperRegions"

/** Render a literal, focused diff without interpreting suggested Markdown as markup. */
export default function PaperRegionDiff({
  original,
  replacement,
}: {
  original: string
  replacement: string
}) {
  const { t } = useTranslation()
  const diff = paperRegionDiff(original, replacement)
  return (
    <div
      className="min-w-0 overflow-hidden rounded-md border text-[15px] leading-7"
      data-testid="paper-region-diff"
    >
      {diff.prefix && (
        <pre className="max-h-28 overflow-auto whitespace-pre-wrap break-words bg-muted/20 px-4 py-2 font-sans text-muted-foreground">
          {diff.prefix}
        </pre>
      )}
      <div className="border-l-2 border-rose-500/60 bg-rose-500/[0.06] px-4 py-3">
        <p className="mb-1 text-sm font-medium text-rose-800 dark:text-rose-200">
          {t("paper.regions.before")}
        </p>
        <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words font-sans">
          {diff.removed || t("paper.regions.empty")}
        </pre>
      </div>
      <div className="border-t border-l-2 border-l-emerald-500/60 bg-emerald-500/[0.06] px-4 py-3">
        <p className="mb-1 text-sm font-medium text-emerald-800 dark:text-emerald-200">
          {t("paper.regions.after")}
        </p>
        <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words font-sans">
          {diff.added || t("paper.regions.empty")}
        </pre>
      </div>
      {diff.suffix && (
        <pre className="max-h-28 overflow-auto whitespace-pre-wrap break-words bg-muted/20 px-4 py-2 font-sans text-muted-foreground">
          {diff.suffix}
        </pre>
      )}
    </div>
  )
}
