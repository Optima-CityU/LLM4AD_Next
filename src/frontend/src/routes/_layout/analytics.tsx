import { createFileRoute, redirect } from "@tanstack/react-router"
import { ExternalLink, LineChart, Settings2 } from "lucide-react"
import { useTranslation } from "react-i18next"

import { UsersService } from "@/client"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"

const readAnalyticsUrl = () => {
  const rawUrl = import.meta.env.VITE_ADMIN_ANALYTICS_URL?.trim()
  if (!rawUrl) return null

  try {
    const url = new URL(rawUrl)
    if (url.protocol !== "https:" && url.protocol !== "http:") {
      return null
    }
    return url.toString()
  } catch {
    return null
  }
}

export const Route = createFileRoute("/_layout/analytics")({
  component: AnalyticsAdmin,
  beforeLoad: async () => {
    const user = await UsersService.readUserMe()
    if (!user.is_superuser) {
      throw redirect({
        to: "/projects",
      })
    }
  },
  head: () => ({
    meta: [
      {
        title: "Visitor Analytics - LLM4AD_Next",
      },
    ],
  }),
})

function AnalyticsAdmin() {
  const { t } = useTranslation()
  const analyticsUrl = readAnalyticsUrl()

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <div className="shrink-0 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex size-10 shrink-0 items-center justify-center rounded-md border bg-muted">
            <LineChart className="size-5 text-muted-foreground" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h2 className="text-base font-semibold">
                {t("adminAnalytics.title")}
              </h2>
              <Badge variant={analyticsUrl ? "secondary" : "outline"}>
                {analyticsUrl
                  ? t("adminAnalytics.statusReady")
                  : t("adminAnalytics.statusMissing")}
              </Badge>
            </div>
            <p className="mt-1 text-sm text-muted-foreground">
              {t("adminAnalytics.subtitle")}
            </p>
          </div>
        </div>

        {analyticsUrl && (
          <Button variant="outline" size="sm" asChild>
            <a href={analyticsUrl} target="_blank" rel="noreferrer">
              <ExternalLink data-icon="inline-start" />
              {t("adminAnalytics.openExternal")}
            </a>
          </Button>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-hidden rounded-md border bg-background">
        {analyticsUrl ? (
          <iframe
            title={t("adminAnalytics.iframeTitle")}
            src={analyticsUrl}
            className="h-full min-h-[520px] w-full border-0"
            referrerPolicy="strict-origin-when-cross-origin"
            allow="clipboard-write"
          />
        ) : (
          <div className="flex h-full min-h-[520px] items-center justify-center p-6">
            <Alert className="max-w-xl">
              <Settings2 />
              <AlertTitle>{t("adminAnalytics.emptyTitle")}</AlertTitle>
              <AlertDescription>
                {t("adminAnalytics.emptyDescription")}
              </AlertDescription>
            </Alert>
          </div>
        )}
      </div>
    </div>
  )
}
