import { Code, FileBarChart } from "lucide-react"
import { useTranslation } from "react-i18next"

/**
 * 报告分析 / IDE 占位页（功能待接入）。
 *
 * 由右侧栏顶部的「报告分析」「打开 IDE」图标按钮弹层复用。kind 决定图标与标题。
 */
export function PlaceholderTab({ kind }: { kind: "report" | "ide" }) {
  const { t } = useTranslation()
  const Icon = kind === "ide" ? Code : FileBarChart
  return (
    <div className="flex flex-col items-center justify-center gap-4 py-16 text-muted-foreground">
      <div
        className="relative p-6 rounded-full"
        style={{
          background:
            "radial-gradient(circle, color-mix(in srgb, var(--primary) 8%, transparent) 0%, transparent 70%)",
        }}
      >
        <Icon
          className="size-12 text-primary/40"
          style={{
            filter:
              "drop-shadow(0 0 12px color-mix(in srgb, var(--primary) 25%, transparent))",
          }}
        />
      </div>
      <div className="text-center space-y-1">
        <p className="text-sm font-semibold text-foreground/70">
          {t(`autoResearch.mainTabs.${kind}`)}
        </p>
        <p className="text-xs text-muted-foreground/60">
          {t("autoResearch.mainTabs.comingSoon")}
        </p>
      </div>
    </div>
  )
}
