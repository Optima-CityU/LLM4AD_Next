import {
  createFileRoute,
  Link,
  Outlet,
  redirect,
  useNavigate,
} from "@tanstack/react-router"
import { ArrowLeft, LogOut, Settings } from "lucide-react"
import { type ReactNode, useMemo, useState } from "react"
import { useTranslation } from "react-i18next"

import { ScanlineOverlay, TechBackground } from "@/components/AutoResearch/tech"
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
import { AutoResearchHeaderContext } from "@/hooks/useAutoResearchHeader"
import { getInitials } from "@/utils"

import icon from "/assets/images/logo.svg"

export const Route = createFileRoute("/_layout_autoresearch")({
  component: AutoResearchLayout,
  beforeLoad: async ({ location }) => {
    if (!isLoggedIn()) {
      throw redirect({
        to: "/login",
        search: { redirect: location.pathname + (location.searchStr || "") },
      })
    }
  },
})

/** 独立布局：极简 header + Outlet；主体三栏在子路由渲染。 */
function AutoResearchLayout() {
  const { t } = useTranslation()
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  // 顶栏中间列：由子路由（页面）注入「当前会话标题条」。
  const [headerCenter, setHeaderCenter] = useState<ReactNode>(null)
  // 顶栏左侧列（logo 右侧）：侧栏收起时注入「会话/分组切换器」。
  const [headerLeft, setHeaderLeft] = useState<ReactNode>(null)
  const headerCtx = useMemo(
    () => ({ setHeaderCenter, setHeaderLeft, bandEl: null }),
    [],
  )

  return (
    <AutoResearchHeaderContext.Provider value={headerCtx}>
      <div className="arc-shell flex flex-col h-screen overflow-hidden bg-background text-foreground">
        {/* 粒子网络背景 + 扫描线叠层：与演化页同款科技风 */}
        <TechBackground />
        <ScanlineOverlay />

        <header className="relative z-10 grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-2 sm:gap-4 px-3 sm:px-6 h-14 shrink-0 bg-muted/50 dark:bg-background/95 backdrop-blur border-b border-border shadow-[0_2px_8px_-4px] shadow-black/10 dark:shadow-sm">
          <div className="flex items-center gap-3 min-w-0">
            <Link
              to="/"
              className="flex items-center gap-2 text-muted-foreground hover:text-primary transition-colors"
              title={t("autoResearch.header.backToHome")}
            >
              <ArrowLeft className="size-4" />
            </Link>
            <Link
              to="/projects"
              className="flex items-center gap-2 group hover:opacity-80 transition-opacity min-w-0"
            >
              <div className="relative shrink-0">
                <img
                  src={icon}
                  alt="LLM4AD_Next"
                  className="h-7 w-auto landing-spin-periodic"
                />
                <div className="absolute inset-0 rounded-full bg-primary/20 blur-md opacity-0 group-hover:opacity-100 transition-opacity duration-500" />
              </div>
              <span className="text-sm font-bold tracking-wider hidden sm:inline landing-gradient-animated">
                LLM4AD_Next
              </span>
            </Link>
            {/* 结合标志：LLM4AD_Next × AutoResearchClaw（可点击跳转到源项目） */}
            <span
              className="shrink-0 text-muted-foreground/50 text-xs font-medium select-none"
              aria-hidden
            >
              ×
            </span>
            <a
              href="https://github.com/aiming-lab/AutoResearchClaw"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-[11px] font-medium px-1.5 py-0.5 rounded bg-primary/10 text-primary border border-primary/20 shrink-0 hover:bg-primary/20 hover:border-primary/30 transition-colors cursor-pointer"
              title={t("autoResearch.header.brandTooltip")}
            >
              {t("autoResearch.header.brand")}
            </a>
            {/* Beta 标志：提示此功能仍在早期测试阶段 */}
            <span className="inline-flex items-center text-[10px] font-semibold px-1.5 py-0.5 rounded-full bg-amber-500/15 text-amber-600 dark:text-amber-400 border border-amber-500/30 leading-none shrink-0 select-none">
              Beta
            </span>
            {/* 侧栏收起时：logo 右侧注入会话/分组切换器 */}
            {headerLeft}
          </div>
          {/* 中间标题：占位保留三列网格，实际标题用绝对定位相对整页居中，
              不受左右两列宽度差异影响（左右列宽不同会让 grid 的中列不在页面正中）。 */}
          <div aria-hidden />
          <div className="pointer-events-none absolute inset-x-0 flex justify-center">
            <div className="pointer-events-auto flex items-center justify-center max-w-[min(38vw,440px)] min-w-0">
              {headerCenter}
            </div>
          </div>
          <div className="flex items-center justify-end gap-2">
            <LanguageToggle />
            <ThemeToggle />
            <div className="w-px h-5 bg-border/40" />
            {user && (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <button
                    type="button"
                    className="flex items-center gap-2 px-2 py-1 rounded-md hover:bg-accent/60 transition-colors border border-transparent hover:border-border/40 focus:outline-none"
                  >
                    <Avatar className="size-7">
                      <AvatarFallback className="text-xs font-medium bg-primary/10 text-primary border border-primary/30">
                        {getInitials(user.full_name || "U")}
                      </AvatarFallback>
                    </Avatar>
                    <span className="text-xs text-muted-foreground max-w-[100px] truncate hidden sm:inline">
                      {user.full_name}
                    </span>
                  </button>
                </DropdownMenuTrigger>
                <DropdownMenuContent
                  align="end"
                  sideOffset={8}
                  className="w-48"
                >
                  <DropdownMenuLabel className="font-normal px-3 py-2">
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
                    <Settings className="size-4 mr-2" />
                    {t("layout.accountSettings")}
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
            )}
          </div>
        </header>

        {/* header 下方三栏主体：左右边栏与中间区一起顶到 header 下（进度轨已并入中间区顶部） */}
        <div className="flex-1 min-h-0 relative z-10">
          <Outlet />
        </div>
      </div>
    </AutoResearchHeaderContext.Provider>
  )
}
