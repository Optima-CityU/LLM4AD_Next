import { Link as RouterLink, useRouterState } from "@tanstack/react-router"
import type { LucideIcon } from "lucide-react"
import {
  BookOpen,
  Coins,
  Database,
  FolderKanban,
  LineChart,
  MessageSquareText,
  QrCode,
  ScrollText,
  ServerCog,
  Shield,
  Sparkles,
  Users,
} from "lucide-react"
import { useTranslation } from "react-i18next"

import { Logo } from "@/components/Common/Logo"
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarSeparator,
  useSidebar,
} from "@/components/ui/sidebar"
import useAuth from "@/hooks/useAuth"
import { GITHUB_ISSUES_URL } from "@/lib/siteMetadata"

type Item = {
  icon: LucideIcon
  title: string
  path?: string
  href?: string
  /** Optional badge label shown inline (e.g. "Beta"). */
  badge?: string
}

function NavItems({ items }: { items: Item[] }) {
  const { isMobile, setOpenMobile } = useSidebar()
  const router = useRouterState()
  const currentPath = router.location.pathname

  const handleMenuClick = () => {
    if (isMobile) {
      setOpenMobile(false)
    }
  }

  return (
    <SidebarMenu className="gap-2">
      {items.map((item) => (
        <SidebarMenuItem
          key={item.title}
          data-tour={
            item.path === "/llm_rovider" ? "llm-provider-link" : undefined
          }
        >
          <SidebarMenuButton
            className="h-10"
            tooltip={item.title}
            isActive={item.path ? currentPath === item.path : false}
            asChild
          >
            {item.href ? (
              <a
                href={item.href}
                target="_blank"
                rel="noopener noreferrer"
                onClick={handleMenuClick}
              >
                <item.icon />
                <span>{item.title}</span>
              </a>
            ) : (
              <RouterLink to={item.path!} onClick={handleMenuClick}>
                <item.icon />
                <span>{item.title}</span>
                {item.badge && (
                  <span className="ml-auto text-[9px] font-semibold px-1 py-0.5 rounded-full bg-amber-500/15 text-amber-600 dark:text-amber-400 border border-amber-500/30 leading-none shrink-0">
                    {item.badge}
                  </span>
                )}
              </RouterLink>
            )}
          </SidebarMenuButton>
        </SidebarMenuItem>
      ))}
    </SidebarMenu>
  )
}

export function AppSidebar() {
  const { t } = useTranslation()
  const { user: currentUser } = useAuth()

  const projectItems: Item[] = [
    {
      icon: FolderKanban,
      title: t("sidebar.projectManagement"),
      path: "/projects",
    },
    {
      icon: Sparkles,
      title: t("sidebar.autoResearch"),
      path: "/autoresearch",
      badge: "Beta",
    },
  ]

  const configItems: Item[] = [
    { icon: ServerCog, title: t("sidebar.llmProvider"), path: "/llm_rovider" },
    { icon: Database, title: t("sidebar.memory"), path: "/memory" },
  ]

  const helpItems: Item[] = [
    {
      icon: BookOpen,
      title: t("sidebar.userManual"),
      path: "/guide",
    },
    {
      icon: MessageSquareText,
      title: t("sidebar.feedback"),
      href: GITHUB_ISSUES_URL,
    },
    {
      icon: ScrollText,
      title: t("sidebar.changelog"),
      path: "/changelog",
    },
  ]

  const adminItems: Item[] = [
    { icon: Users, title: t("sidebar.userManagement"), path: "/admin" },
    { icon: Coins, title: t("sidebar.litellmQuotas"), path: "/litellm-quotas" },
    { icon: LineChart, title: t("sidebar.analytics"), path: "/analytics" },
    { icon: QrCode, title: t("sidebar.liveCode"), path: "/live-codes" },
  ]

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader className="px-4 py-6 group-data-[collapsible=icon]:px-0 group-data-[collapsible=icon]:items-center">
        <Logo variant="responsive" to="/" />
      </SidebarHeader>
      <SidebarContent className="gap-4 pt-2">
        {/* Projects */}
        <SidebarGroup>
          <SidebarGroupContent>
            <NavItems items={projectItems} />
          </SidebarGroupContent>
        </SidebarGroup>

        {/* Global config */}
        <SidebarGroup>
          <SidebarGroupLabel>{t("sidebar.globalConfig")}</SidebarGroupLabel>
          <SidebarGroupContent>
            <NavItems items={configItems} />
          </SidebarGroupContent>
        </SidebarGroup>

        {/* Help & Info */}
        <SidebarGroup>
          <SidebarGroupLabel>{t("sidebar.helpAndInfo")}</SidebarGroupLabel>
          <SidebarGroupContent>
            <NavItems items={helpItems} />
          </SidebarGroupContent>
        </SidebarGroup>

        {/* Admin area */}
        {currentUser?.is_superuser && (
          <>
            <div className="relative mx-2 my-1">
              <SidebarSeparator className="mx-0" />
              <span className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 flex items-center gap-1 bg-sidebar px-2 text-[10px] text-sidebar-foreground/40 whitespace-nowrap group-data-[collapsible=icon]:hidden">
                <Shield className="size-3" />
                {t("sidebar.admin")}
              </span>
            </div>
            <SidebarGroup>
              <SidebarGroupContent>
                <NavItems items={adminItems} />
              </SidebarGroupContent>
            </SidebarGroup>
          </>
        )}
      </SidebarContent>
    </Sidebar>
  )
}

export default AppSidebar
