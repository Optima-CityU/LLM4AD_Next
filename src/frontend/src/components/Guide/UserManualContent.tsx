import {
  ChevronRight,
  ChevronsDownUp,
  ChevronsUpDown,
  Loader2,
} from "lucide-react"
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import Markdown from "react-markdown"
import { toast } from "sonner"
import {
  MARKDOWN_REHYPE_PLUGINS,
  MARKDOWN_REMARK_PLUGINS,
  makeMarkdownComponents,
} from "@/components/markdown/markdownComponents"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { useHljsTheme } from "@/hooks/useHljsTheme"
import { cn } from "@/lib/utils"

import { type GuideNavItem, guideNav } from "./guide.config"

const docLoaders = import.meta.glob("@docs/**/*.md", {
  query: "?raw",
  import: "default",
}) as Record<string, () => Promise<string>>

function findDocLoader(
  item: GuideNavItem,
  lang: string,
): (() => Promise<string>) | undefined {
  const suffix = `/${lang}/${item.key}.md`
  const entry = Object.entries(docLoaders).find(([k]) => k.endsWith(suffix))
  return entry?.[1]
}

function findFirstLeaf(items: GuideNavItem[]): GuideNavItem | undefined {
  for (const item of items) {
    if (!item.children) return item
    const found = findFirstLeaf(item.children)
    if (found) return found
  }
}

function findNavItemByKey(
  key: string,
  items: GuideNavItem[],
): GuideNavItem | null {
  for (const item of items) {
    if (item.key === key) return item
    if (item.children) {
      const found = findNavItemByKey(key, item.children)
      if (found) return found
    }
  }
  return null
}

function findAncestorKeys(
  targetKey: string,
  items: GuideNavItem[],
  path: string[] = [],
): string[] | null {
  for (const item of items) {
    if (item.key === targetKey) return path
    if (item.children) {
      const found = findAncestorKeys(targetKey, item.children, [
        ...path,
        item.key,
      ])
      if (found) return found
    }
  }
  return null
}

function isExternalLink(href: string): boolean {
  return (
    href.startsWith("http://") ||
    href.startsWith("https://") ||
    href.startsWith("mailto:") ||
    href.startsWith("#")
  )
}

function resolveDocLink(href: string, currentKey: string): string | null {
  if (isExternalLink(href)) return null

  const lastSlash = currentKey.lastIndexOf("/")
  const currentDir =
    lastSlash >= 0 ? currentKey.substring(0, lastSlash + 1) : ""
  const parts = (currentDir + href).split("/")
  const resolved: string[] = []
  for (const part of parts) {
    if (part === "..") {
      resolved.pop()
    } else if (part !== ".") {
      resolved.push(part)
    }
  }
  let result = resolved.join("/")
  if (result.endsWith(".md")) {
    result = result.slice(0, -3)
  }
  return result
}

function getAllExpandableKeys(): string[] {
  const keys: string[] = []
  function walk(items: GuideNavItem[]) {
    for (const item of items) {
      if (item.children && item.children.length > 0) {
        keys.push(item.key)
        walk(item.children)
      }
    }
  }
  walk(guideNav)
  return keys
}

function NavItemButton({
  item,
  lang,
  activeItem,
  onSelect,
  depth,
  expandedKeys,
  onToggleExpand,
}: {
  item: GuideNavItem
  lang: string
  activeItem: GuideNavItem
  onSelect: (item: GuideNavItem) => void
  depth: number
  expandedKeys: Set<string>
  onToggleExpand: (key: string) => void
}) {
  const hasChildren = item.children && item.children.length > 0
  const Icon = item.icon
  const label = lang === "zh" ? item.labelZh : item.labelEn

  if (hasChildren) {
    const expanded = expandedKeys.has(item.key)
    return (
      <div>
        <button
          type="button"
          onClick={() => onToggleExpand(item.key)}
          className={cn(
            "w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm transition-colors text-left",
            "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
          )}
          style={{ paddingLeft: `${12 + depth * 12}px` }}
        >
          <ChevronRight
            className={cn(
              "size-3.5 shrink-0 transition-transform",
              expanded && "rotate-90",
            )}
          />
          {Icon && <Icon className="size-4 shrink-0" />}
          <span className="truncate">{label}</span>
        </button>
        {expanded && (
          <div className="space-y-0.5">
            {item.children!.map((child) => (
              <NavItemButton
                key={child.key}
                item={child}
                lang={lang}
                activeItem={activeItem}
                onSelect={onSelect}
                depth={depth + 1}
                expandedKeys={expandedKeys}
                onToggleExpand={onToggleExpand}
              />
            ))}
          </div>
        )}
      </div>
    )
  }

  const isActive = item.key === activeItem.key
  return (
    <button
      type="button"
      onClick={() => onSelect(item)}
      className={cn(
        "w-full flex items-center gap-2.5 px-3 py-2.5 rounded-lg text-sm transition-colors text-left",
        isActive
          ? "bg-primary/10 text-primary font-medium"
          : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
      )}
      style={{ paddingLeft: `${12 + depth * 12}px` }}
    >
      {Icon && <Icon className="size-4 shrink-0" />}
      <span className="truncate">{label}</span>
    </button>
  )
}

export function UserManualContent({ className }: { className?: string }) {
  const { t, i18n } = useTranslation()
  const lang = i18n.language?.startsWith("zh") ? "zh" : "en"
  const initialItem = useMemo(() => findFirstLeaf(guideNav) ?? guideNav[0], [])
  const [activeItem, setActiveItem] = useState<GuideNavItem>(initialItem)
  const [expandedKeys, setExpandedKeys] = useState<Set<string>>(() => {
    const ancestors = findAncestorKeys(initialItem.key, guideNav) ?? []
    return new Set(ancestors)
  })

  const [content, setContent] = useState("")
  const [loading, setLoading] = useState(true)
  const cacheRef = useRef<Map<string, string>>(new Map())
  const mermaidIdPrefix = useId()

  useEffect(() => {
    const cacheKey = `${lang}/${activeItem.key}`
    const cached = cacheRef.current.get(cacheKey)
    if (cached !== undefined) {
      setContent(cached)
      setLoading(false)
      return
    }

    setLoading(true)
    let cancelled = false
    const loader = findDocLoader(activeItem, lang)
    if (!loader) {
      setContent("")
      setLoading(false)
      return
    }
    loader().then((text) => {
      if (cancelled) return
      cacheRef.current.set(cacheKey, text)
      setContent(text)
      setLoading(false)
    })
    return () => {
      cancelled = true
    }
  }, [activeItem, lang])

  useHljsTheme()

  const toggleExpanded = useCallback((key: string) => {
    setExpandedKeys((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }, [])

  const expandAll = useCallback(() => {
    setExpandedKeys(new Set(getAllExpandableKeys()))
  }, [])

  const collapseAll = useCallback(() => {
    setExpandedKeys(new Set())
  }, [])

  const navigateToDoc = useCallback((key: string) => {
    const item = findNavItemByKey(key, guideNav)
    if (!item) return false

    if (item.children) {
      setExpandedKeys((prev) => {
        const next = new Set(prev)
        next.add(key)
        const ancestors = findAncestorKeys(key, guideNav)
        if (ancestors) {
          for (const k of ancestors) next.add(k)
        }
        return next
      })
      const leaf = findFirstLeaf([item])
      if (leaf) setActiveItem(leaf)
    } else {
      const ancestors = findAncestorKeys(key, guideNav)
      if (ancestors) {
        setExpandedKeys((prev) => {
          const next = new Set(prev)
          for (const k of ancestors) next.add(k)
          return next
        })
      }
      setActiveItem(item)
    }
    return true
  }, [])

  return (
    <div className={cn("flex h-full min-h-0", className)}>
      <div className="shrink-0 w-56 border-r border-border/60 flex flex-col min-h-0">
        <div className="shrink-0 flex items-center justify-between gap-1 px-2 py-2 border-b border-border/60">
          <span className="text-xs font-medium text-muted-foreground px-1.5">
            {t("userManual.tocLabel")}
          </span>
          <div className="flex items-center gap-0.5">
            <Button
              variant="ghost"
              size="icon"
              className="size-7"
              onClick={expandAll}
              title={t("userManual.expandAll")}
              aria-label={t("userManual.expandAll")}
            >
              <ChevronsUpDown className="size-3.5" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="size-7"
              onClick={collapseAll}
              title={t("userManual.collapseAll")}
              aria-label={t("userManual.collapseAll")}
            >
              <ChevronsDownUp className="size-3.5" />
            </Button>
          </div>
        </div>
        <ScrollArea className="flex-1 min-h-0">
          <nav className="py-4 px-2 space-y-0.5">
            {guideNav.map((item) => (
              <NavItemButton
                key={item.key}
                item={item}
                lang={lang}
                activeItem={activeItem}
                onSelect={setActiveItem}
                depth={0}
                expandedKeys={expandedKeys}
                onToggleExpand={toggleExpanded}
              />
            ))}
          </nav>
        </ScrollArea>
      </div>

      <ScrollArea className="flex-1 min-h-0">
        {loading ? (
          <div className="flex items-center justify-center h-full py-20">
            <Loader2 className="size-5 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <div
            className={`px-8 py-6 prose prose-sm dark:prose-invert max-w-none
            prose-headings:text-foreground prose-headings:font-semibold
            prose-h1:text-xl prose-h1:border-b prose-h1:border-border/50 prose-h1:pb-2
            prose-h2:text-lg prose-h2:mt-6
            prose-h3:text-base
            prose-p:text-foreground/80 prose-p:leading-relaxed
            prose-strong:text-foreground
            prose-a:text-primary prose-a:no-underline hover:prose-a:underline
            prose-code:text-primary prose-code:bg-muted prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded prose-code:text-xs prose-code:before:content-none prose-code:after:content-none
            prose-pre:bg-code-block-bg prose-pre:text-foreground prose-pre:rounded-xl prose-pre:border prose-pre:border-border/50 prose-pre:p-5 prose-pre:text-[13px] prose-pre:leading-relaxed
            prose-table:text-sm
            prose-th:bg-muted/50 prose-th:px-3 prose-th:py-2 prose-th:text-left prose-th:font-semibold prose-th:border-border
            prose-td:px-3 prose-td:py-2 prose-td:border-border
            prose-li:text-foreground/80
            prose-blockquote:border-primary/30 prose-blockquote:text-foreground/70
            prose-hr:border-border/50`}
          >
            <Markdown
              remarkPlugins={MARKDOWN_REMARK_PLUGINS}
              rehypePlugins={MARKDOWN_REHYPE_PLUGINS}
              components={{
                ...makeMarkdownComponents(mermaidIdPrefix),
                a: ({ children, href, node: _, ...props }) => {
                  if (href && !isExternalLink(href)) {
                    const resolvedKey = resolveDocLink(href, activeItem.key)
                    if (resolvedKey) {
                      const navItem = findNavItemByKey(resolvedKey, guideNav)
                      if (navItem) {
                        return (
                          <a
                            {...props}
                            className="cursor-pointer"
                            onClick={(e) => {
                              e.preventDefault()
                              navigateToDoc(resolvedKey)
                            }}
                          >
                            {children}
                          </a>
                        )
                      }
                      return (
                        <a
                          {...props}
                          className="cursor-pointer"
                          onClick={(e) => {
                            e.preventDefault()
                            toast.warning(
                              t("userManual.docNotAvailable", {
                                key: resolvedKey,
                              }),
                            )
                          }}
                        >
                          {children}
                        </a>
                      )
                    }
                  }
                  return (
                    <a
                      {...props}
                      href={href}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      {children}
                    </a>
                  )
                },
              }}
            >
              {content}
            </Markdown>
          </div>
        )}
      </ScrollArea>
    </div>
  )
}
