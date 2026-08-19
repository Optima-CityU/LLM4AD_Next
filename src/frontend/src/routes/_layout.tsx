import {
  createFileRoute,
  Outlet,
  redirect,
  useNavigate,
  useRouterState,
} from "@tanstack/react-router"
import { LogOut, RotateCcw, Settings } from "lucide-react"
import { useTranslation } from "react-i18next"

import { Footer } from "@/components/Common/Footer"
import { GithubFeedbackLink } from "@/components/Common/GithubFeedbackLink"
import LanguageToggle from "@/components/Common/LanguageToggle"
import ThemeToggle from "@/components/Common/ThemeToggle"
import { resetAllTours } from "@/components/Onboarding/tourStorage"
import AppSidebar from "@/components/Sidebar/AppSidebar"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  SidebarInset,
  SidebarProvider,
  SidebarTrigger,
} from "@/components/ui/sidebar"
import useAuth, { isLoggedIn } from "@/hooks/useAuth"
import useCustomToast from "@/hooks/useCustomToast"
import { getInitials } from "@/utils"

export const Route = createFileRoute("/_layout")({
  component: Layout,
  beforeLoad: async ({ location }) => {
    if (!isLoggedIn()) {
      throw redirect({
        to: "/login",
        search: { redirect: location.pathname + (location.searchStr || "") },
      })
    }
  },
})

function usePageTitle() {
  const { t } = useTranslation()
  const router = useRouterState()
  const path = router.location.pathname
  const PAGE_TITLES: Record<string, string> = {
    "/projects": t("layout.projectManagement"),
    "/config_template": t("layout.configTemplate"),
    "/llm_rovider": t("layout.llmProvider"),
    "/live-codes": t("layout.liveCode"),
    "/admin": t("layout.userManagement"),
    "/settings": t("layout.accountSettings"),
    "/changelog": t("layout.changelog"),
    "/feedback": t("layout.feedback"),
    "/guide": t("layout.userManual"),
    "/items": "Items",
  }
  return PAGE_TITLES[path] || t("layout.overview")
}

function HeaderUserMenu() {
  const { t } = useTranslation()
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const { showSuccessToast } = useCustomToast()

  if (!user) return null

  const handleReplayTour = () => {
    // Reset every tour back to the freshly-registered state — projects /
    // demo / evolution-create / evolution-task-config / evolution-ai-build /
    // evolution-results all become "unseen" again. Each one will re-trigger
    // the next time the user lands on its host page.
    resetAllTours()
    showSuccessToast(
      t("layout.replayTourReset", {
        defaultValue:
          "Walkthroughs reset — they'll show up again as you visit each page.",
      }),
    )
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className="flex items-center gap-2 px-2 py-1 rounded-md
            hover:bg-accent/60 transition-colors border border-transparent
            hover:border-border/40 focus:outline-none"
        >
          <Avatar className="size-7">
            <AvatarFallback className="text-xs font-medium bg-primary/10 text-primary border border-primary/30">
              {getInitials(user.full_name || "U")}
            </AvatarFallback>
          </Avatar>
          <span className="text-xs text-muted-foreground max-w-[80px] truncate hidden sm:inline">
            {user.full_name}
          </span>
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" sideOffset={8} className="w-48">
        <DropdownMenuLabel className="font-normal px-3 py-2">
          <p className="text-sm font-medium">{user.full_name}</p>
          <p className="text-xs text-muted-foreground">{user.email}</p>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          className="cursor-pointer"
          onClick={() => navigate({ to: "/settings" })}
        >
          <Settings className="size-4 mr-2" />
          {t("layout.accountSettings")}
        </DropdownMenuItem>
        <DropdownMenuItem className="cursor-pointer" onClick={handleReplayTour}>
          <RotateCcw className="size-4 mr-2" />
          {t("layout.replayTour", {
            defaultValue: "Replay guided tour",
          })}
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          className="text-destructive focus:bg-destructive/15 focus:text-destructive cursor-pointer"
          onClick={logout}
        >
          <LogOut className="size-4 mr-2" />
          {t("layout.logout")}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

function Layout() {
  const { t } = useTranslation()
  const pageTitle = usePageTitle()
  const { user } = useAuth()

  return (
    <SidebarProvider>
      <AppSidebar />
      <SidebarInset className="flex flex-col h-screen overflow-hidden">
        <header className="shrink-0 z-10 flex h-14 items-center justify-between px-4 bg-background/95 backdrop-blur border-b border-border shadow-sm">
          {/* Left: sidebar trigger + page title */}
          <div className="flex min-w-0 items-center gap-3">
            <SidebarTrigger className="-ml-1 text-muted-foreground hover:text-primary" />
            <div className="w-px h-5 bg-border" />
            <h1 className="truncate text-sm font-semibold tracking-wide text-foreground">
              {pageTitle}
            </h1>
          </div>

          {/* Right: toggles + role + avatar */}
          <div className="flex shrink-0 items-center gap-1 text-xs">
            <GithubFeedbackLink labelClassName="hidden xl:inline" />
            <LanguageToggle />
            <ThemeToggle />
            <div className="w-px h-5 bg-border mx-1.5" />
            <span
              className={`px-1.5 py-0.5 rounded text-xs leading-none border ${
                user?.is_superuser
                  ? "bg-primary/10 text-primary border-primary/30"
                  : "bg-muted text-muted-foreground border-border"
              }`}
            >
              {user?.is_superuser
                ? t("common.superAdmin")
                : t("common.normalUser")}
            </span>
            <div className="w-px h-5 bg-border mx-1.5" />
            <HeaderUserMenu />
          </div>
        </header>
        <main className="flex-1 min-h-0 overflow-y-auto px-6 pt-6 pb-2 md:px-8 xl:px-6">
          <div className="mx-auto max-w-7xl 2xl:max-w-[1600px] h-full">
            <Outlet />
          </div>
        </main>
        <Footer />
      </SidebarInset>
    </SidebarProvider>
  )
}
