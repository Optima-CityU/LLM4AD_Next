import {
  createFileRoute,
  Link,
  Outlet,
  redirect,
  useNavigate,
  useRouterState,
} from "@tanstack/react-router"
import { ArrowLeft, LogOut, Settings } from "lucide-react"
import { type ReactNode, useMemo, useState } from "react"
import { useTranslation } from "react-i18next"

import LanguageToggle from "@/components/Common/LanguageToggle"
import ThemeToggle from "@/components/Common/ThemeToggle"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import useAuth, { isLoggedIn } from "@/hooks/useAuth"
import { PaperHeaderContext } from "@/hooks/usePaperHeader"
import { parseResearchWorkspaceMode } from "@/lib/researchWorkspace"
import { getInitials } from "@/utils"

import icon from "/assets/images/logo.svg"

export const Route = createFileRoute("/_layout_paper")({
  component: PaperLayout,
  beforeLoad: async ({ location }) => {
    if (!isLoggedIn()) {
      throw redirect({
        to: "/login",
        search: { redirect: location.pathname + (location.searchStr || "") },
      })
    }
  },
})

/** Provide an isolated full-viewport shell for paper authoring. */
function PaperLayout() {
  const { t } = useTranslation()
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const router = useRouterState()
  const researchWorkspaceMode = parseResearchWorkspaceMode(
    new URLSearchParams(router.location.searchStr).get("mode"),
  )
  const [headerCenter, setHeaderCenter] = useState<ReactNode>(null)
  const [headerRight, setHeaderRight] = useState<ReactNode>(null)
  const headerContext = useMemo(() => ({ setHeaderCenter, setHeaderRight }), [])

  return (
    <PaperHeaderContext.Provider value={headerContext}>
      <div className="flex h-screen flex-col overflow-hidden bg-background text-foreground">
        <header className="relative z-20 grid h-14 shrink-0 grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-2 border-b border-border bg-muted/50 px-3 shadow-[0_2px_8px_-4px] shadow-black/10 backdrop-blur dark:bg-background/95 dark:shadow-sm sm:gap-4 sm:px-6">
          <div className="flex min-w-0 items-center gap-3">
            <Link
              to="/papers"
              search={{
                mode: researchWorkspaceMode,
                workspaceId: undefined,
              }}
              className="flex items-center text-muted-foreground transition-colors hover:text-primary"
              title={t("paper.workspace.back")}
            >
              <ArrowLeft className="size-4" />
            </Link>
            <Link
              to="/projects"
              className="group flex min-w-0 items-center gap-2 transition-opacity hover:opacity-80"
            >
              <div className="relative shrink-0">
                <img
                  src={icon}
                  alt="OpenLoopX"
                  className="h-7 w-auto landing-spin-periodic"
                />
                <div className="absolute inset-0 rounded-full bg-primary/20 opacity-0 blur-md transition-opacity duration-500 group-hover:opacity-100" />
              </div>
              <span className="hidden text-sm font-bold tracking-wider landing-gradient-animated sm:inline">
                OpenLoopX
              </span>
            </Link>
            <span
              className="hidden shrink-0 select-none text-xs font-medium text-muted-foreground/50 md:inline"
              aria-hidden
            >
              ×
            </span>
            <span className="hidden shrink-0 items-center rounded border border-primary/20 bg-primary/10 px-1.5 py-0.5 text-[11px] font-medium text-primary md:inline-flex">
              {t(
                `paper.workspace.navigation.${researchWorkspaceMode}` as const,
              )}
            </span>
            <span className="hidden shrink-0 select-none items-center rounded-full border border-amber-500/30 bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-semibold leading-none text-amber-600 dark:text-amber-400 lg:inline-flex">
              Beta
            </span>
          </div>

          <div className="pointer-events-none absolute inset-x-0 flex justify-center">
            <div className="pointer-events-auto flex min-w-0 max-w-[min(38vw,440px)] items-center justify-center">
              {headerCenter}
            </div>
          </div>
          <div aria-hidden />

          <div className="flex min-w-0 items-center justify-end gap-2">
            {headerRight}
            {headerRight && <div className="h-5 w-px bg-border/40" />}
            <LanguageToggle />
            <ThemeToggle />
            <div className="h-5 w-px bg-border/40" />
            {user && (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <button
                    type="button"
                    className="flex items-center gap-2 rounded-md border border-transparent px-2 py-1 transition-colors hover:border-border/40 hover:bg-accent/60 focus:outline-none"
                  >
                    <Avatar className="size-7">
                      <AvatarFallback className="border border-primary/30 bg-primary/10 text-xs font-medium text-primary">
                        {getInitials(user.full_name || "U")}
                      </AvatarFallback>
                    </Avatar>
                    <span className="hidden max-w-[100px] truncate text-xs text-muted-foreground sm:inline">
                      {user.full_name}
                    </span>
                  </button>
                </DropdownMenuTrigger>
                <DropdownMenuContent
                  align="end"
                  sideOffset={8}
                  className="w-48"
                >
                  <DropdownMenuLabel className="px-3 py-2 font-normal">
                    <p className="text-sm font-medium">{user.full_name}</p>
                    <p className="text-xs text-muted-foreground">
                      {user.email}
                    </p>
                  </DropdownMenuLabel>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem
                    className="cursor-pointer"
                    onClick={() => navigate({ to: "/settings" })}
                  >
                    <Settings className="mr-2 size-4" />
                    {t("layout.accountSettings")}
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem
                    className="cursor-pointer text-destructive focus:bg-destructive/15 focus:text-destructive"
                    onClick={logout}
                  >
                    <LogOut className="mr-2 size-4" />
                    {t("layout.logout")}
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            )}
          </div>
        </header>

        <div className="relative z-10 min-h-0 flex-1">
          <Outlet />
        </div>
      </div>
    </PaperHeaderContext.Provider>
  )
}
