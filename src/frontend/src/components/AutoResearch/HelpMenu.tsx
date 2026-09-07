import { BookOpen, HelpCircle, MessageSquarePlus, Users } from "lucide-react"
import { useState } from "react"
import { useTranslation } from "react-i18next"

import { ContactUsDialog } from "@/components/Feedback/ContactUsDialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { UserManualDialog } from "@/components/Guide/UserManualDialog"
import { GITHUB_ISSUES_URL } from "@/lib/siteMetadata"

/**
 * AutoResearch 顶栏右上角的「帮助」入口。全局悬浮问号球（FeedbackFAB）在本页
 * 隐藏，改由此下拉承接同样的三项：提交反馈 / 使用手册 / 联系我们，复用同一批
 * 弹窗组件与 i18n 键，仅换成贴合顶栏的图标按钮 + 下拉形态。
 */
export function AutoResearchHelpMenu() {
  const { t } = useTranslation()
  const [manualOpen, setManualOpen] = useState(false)
  const [contactOpen, setContactOpen] = useState(false)

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            aria-label={t("feedback.fab.userManual")}
            title={t("feedback.fab.userManual")}
            className="grid place-items-center size-8 rounded-md text-muted-foreground hover:text-primary transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <HelpCircle className="size-4" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" sideOffset={8} className="w-44">
          <DropdownMenuItem asChild>
            <a
              href={GITHUB_ISSUES_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="cursor-pointer"
            >
              <MessageSquarePlus className="size-4 mr-2 text-primary" />
              {t("feedback.fab.submitFeedback")}
            </a>
          </DropdownMenuItem>
          <DropdownMenuItem
            className="cursor-pointer"
            onClick={() => setManualOpen(true)}
          >
            <BookOpen className="size-4 mr-2 text-primary" />
            {t("feedback.fab.userManual")}
          </DropdownMenuItem>
          <DropdownMenuItem
            className="cursor-pointer"
            onClick={() => setContactOpen(true)}
          >
            <Users className="size-4 mr-2 text-primary" />
            {t("feedback.fab.contactUs")}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <UserManualDialog open={manualOpen} onOpenChange={setManualOpen} />
      <ContactUsDialog open={contactOpen} onOpenChange={setContactOpen} />
    </>
  )
}
