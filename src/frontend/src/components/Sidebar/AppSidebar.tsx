import { Link as RouterLink, useRouterState } from "@tanstack/react-router"
import type { LucideIcon } from "lucide-react"
import {
  BookOpen,
  ChevronRight,
  Database,
  FileText,
  FolderKanban,
  GitBranch,
  MessageSquareReply,
  MessageSquareText,
  Microscope,
  QrCode,
  ScrollText,
  ServerCog,
  Shield,
  Sparkles,
  Users,
} from "lucide-react"
import { Collapsible } from "radix-ui"
import { useEffect, useState } from "react"
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
  SidebarMenuSub,
  SidebarMenuSubButton,
  SidebarMenuSubItem,
  SidebarSeparator,
  useSidebar,
} from "@/components/ui/sidebar"
import useAuth from "@/hooks/useAuth"
import {
  AUTO_DISCOVERY_ENABLED,
  AUTO_REBUTTAL_ENABLED,
} from "@/lib/frontendFeatures"
import {
  parseResearchWorkspaceMode,
  type ResearchWorkspaceMode,
} from "@/lib/researchWorkspace"
import { GITHUB_ISSUES_URL } from "@/lib/siteMetadata"

type Item = {
  icon: LucideIcon
  title: string
  path?: string
  href?: string
  /** Optional badge label shown inline (e.g. "Beta"). */
  badge?: string
}

/** Entry of the research workspace submenu. `mode` targets a papers tab. */
type ResearchItem = {
  icon: LucideIcon
  title: string
  path: "/autoresearch" | "/papers"
  mode?: ResearchWorkspaceMode
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

function ResearchWorkspaceNav() {
  const { t } = useTranslation()
  const router = useRouterState()
  const currentPath = router.location.pathname
  const currentMode = parseResearchWorkspaceMode(
    new URLSearchParams(router.location.searchStr).get("mode"),
  )
  const researchActive =
    currentPath.startsWith("/autoresearch") || currentPath === "/papers"
  const [open, setOpen] = useState(researchActive)

  useEffect(() => {
    if (researchActive) {
      setOpen(true)
    }
  }, [researchActive])

  const researchItems: ResearchItem[] = [
    {
      icon: Sparkles,
      title: t("sidebar.autoResearch"),
      path: "/autoresearch",
    },
    {
      icon: FileText,
      title: t("sidebar.autoProposal"),
      path: "/papers",
      mode: "proposal",
    },
    ...(AUTO_REBUTTAL_ENABLED
      ? [
          {
            icon: MessageSquareReply,
            title: t("sidebar.autoRebuttal"),
            path: "/papers" as const,
            mode: "manuscript" as const,
          },
        ]
      : []),
    ...(AUTO_DISCOVERY_ENABLED
      ? [
          {
            icon: GitBranch,
            title: t("sidebar.autoDiscovery"),
            path: "/papers" as const,
            mode: "algorithm" as const,
          },
        ]
      : []),
  ]

  return (
    <SidebarGroup>
      <SidebarGroupContent>
        <SidebarMenu className="gap-1">
          <Collapsible.Root
            open={open}
            onOpenChange={setOpen}
            className="group/research"
          >
            <SidebarMenuItem>
              <Collapsible.Trigger asChild>
                <SidebarMenuButton
                  className="h-10"
                  tooltip={t("sidebar.paperWorkspace")}
                  isActive={researchActive}
                >
                  <Microscope />
                  <span className="font-medium">
                    {t("sidebar.paperWorkspace")}
                  </span>
                  <span className="ml-auto rounded-full border border-amber-500/30 bg-amber-500/15 px-1 py-0.5 text-[9px] font-semibold leading-none text-amber-600 dark:text-amber-400">
                    Beta
                  </span>
                  <ChevronRight className="ml-1 transition-transform duration-200 group-data-[state=open]/research:rotate-90" />
                </SidebarMenuButton>
              </Collapsible.Trigger>
              <Collapsible.Content>
                <SidebarMenuSub>
                  {researchItems.map((item) => {
                    const isActive =
                      item.path === "/autoresearch"
                        ? currentPath.startsWith("/autoresearch")
                        : currentPath === "/papers" && currentMode === item.mode
                    return (
                      <SidebarMenuSubItem key={item.title}>
                        <SidebarMenuSubButton isActive={isActive} asChild>
                          {item.mode ? (
                            <RouterLink
                              to="/papers"
                              search={{
                                mode: item.mode,
                                workspaceId: undefined,
                              }}
                            >
                              <item.icon />
                              <span>{item.title}</span>
                            </RouterLink>
                          ) : (
                            <RouterLink to="/autoresearch">
                              <item.icon />
                              <span>{item.title}</span>
                            </RouterLink>
                          )}
                        </SidebarMenuSubButton>
                      </SidebarMenuSubItem>
                    )
                  })}
                </SidebarMenuSub>
              </Collapsible.Content>
            </SidebarMenuItem>
          </Collapsible.Root>
        </SidebarMenu>
      </SidebarGroupContent>
    </SidebarGroup>
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

        <ResearchWorkspaceNav />

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
