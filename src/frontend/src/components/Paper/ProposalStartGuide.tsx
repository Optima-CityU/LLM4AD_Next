import { FileText } from "lucide-react"
import { useTranslation } from "react-i18next"

/** Help an author start from the conversation while the draft is empty. */
export default function ProposalStartGuide() {
  const { t } = useTranslation()
  return (
    <div
      data-testid="proposal-start-guide"
      className="grid h-full place-content-center gap-3 overflow-y-auto px-6 py-8 text-center"
    >
      <FileText
        className="mx-auto size-6 text-muted-foreground"
        aria-hidden="true"
      />
      <h2 className="font-serif text-xl font-semibold">
        {t("paper.proposalStart.previewHeading")}
      </h2>
      <p className="max-w-sm text-sm leading-6 text-muted-foreground">
        {t("paper.proposalStart.previewDescription")}
      </p>
    </div>
  )
}
