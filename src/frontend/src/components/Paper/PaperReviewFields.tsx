import { useId } from "react"
import { useTranslation } from "react-i18next"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { cn } from "@/lib/utils"

export type PaperReviewDraft = { label: string; title: string; content: string }

export function PaperReviewFields({
  value,
  onChange,
  disabled = false,
  contentClassName,
}: {
  value: PaperReviewDraft
  onChange: (value: PaperReviewDraft) => void
  disabled?: boolean
  contentClassName?: string
}) {
  const { t } = useTranslation()
  const id = useId()
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-2">
        <Label htmlFor={`${id}-reviewer`}>
          {t("paper.rebuttal.reviewerLabel")}
        </Label>
        <Input
          id={`${id}-reviewer`}
          value={value.label}
          disabled={disabled}
          maxLength={128}
          onChange={(event) =>
            onChange({ ...value, label: event.target.value })
          }
          placeholder={t("paper.rebuttal.reviewerPlaceholder")}
        />
      </div>
      <div className="flex flex-col gap-2">
        <Label htmlFor={`${id}-title`}>{t("paper.rebuttal.reviewTitle")}</Label>
        <Input
          id={`${id}-title`}
          value={value.title}
          disabled={disabled}
          maxLength={255}
          onChange={(event) =>
            onChange({ ...value, title: event.target.value })
          }
        />
      </div>
      <div className="flex flex-col gap-2">
        <Label htmlFor={`${id}-content`}>
          {t("paper.rebuttal.reviewContent")}
        </Label>
        <Textarea
          id={`${id}-content`}
          className={cn("min-h-24 resize-y", contentClassName)}
          value={value.content}
          disabled={disabled}
          maxLength={2_000_000}
          onChange={(event) =>
            onChange({ ...value, content: event.target.value })
          }
          placeholder={t("paper.rebuttal.reviewContentPlaceholder")}
        />
      </div>
    </div>
  )
}
