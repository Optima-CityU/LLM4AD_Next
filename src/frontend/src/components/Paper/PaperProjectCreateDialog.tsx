import { useQueryClient } from "@tanstack/react-query"
import {
  AlertCircle,
  ArrowRight,
  Check,
  FileText,
  FolderPlus,
  Loader2,
  Plus,
  Trash2,
} from "lucide-react"
import { RadioGroup } from "radix-ui"
import { type ComponentType, useCallback, useId, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import { Llm4AdPapersService } from "@/client"
import WorkflowStepper, {
  type WorkflowStep,
} from "@/components/Common/WorkflowStepper"
import {
  type PaperReviewDraft,
  PaperReviewFields,
} from "@/components/Paper/PaperReviewFields"
import { PaperSourceFilePicker } from "@/components/Paper/PaperSourceFilePicker"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { ButtonGroup } from "@/components/ui/button-group"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Separator } from "@/components/ui/separator"
import { Textarea } from "@/components/ui/textarea"
import { paperWorkspaceKeys, useCreatePaperWorkspace } from "@/hooks/usePapers"
import {
  paperSourceUploadFormData,
  paperSourceUploadTooLarge,
} from "@/lib/paperSourceUpload"
import { cn } from "@/lib/utils"

export type PaperModelPickerProps = {
  value: { providerId: string; modelName: string }
  onChange: (value: PaperModelPickerProps["value"]) => void
  disabled?: boolean
}

type ReviewDraft = PaperReviewDraft & { id: number }
type SaveStep = "workspace" | "model" | "materials" | "reviews"
const GUIDED_STEPS = ["details", "materials", "model"] as const
type GuidedStep = (typeof GUIDED_STEPS)[number]
type CreatePage = "choose" | "empty" | GuidedStep
const STEP_LABELS: Record<GuidedStep, string> = {
  details: "paper.workspace.intake.basicInformation",
  materials: "paper.workspace.intake.researchMaterials",
  model: "paper.workspace.intake.modelAndStart",
}

function ProjectOptionGroup({
  labelId,
  labels,
  selected,
  onChange,
  disabled,
}: {
  labelId: string
  labels: [string, string]
  selected: boolean
  onChange: (selected: boolean) => void
  disabled: boolean
}) {
  return (
    <RadioGroup.Root
      asChild
      aria-labelledby={labelId}
      value={String(selected)}
      onValueChange={(value) => onChange(value === "true")}
      disabled={disabled}
    >
      <ButtonGroup className="w-full sm:w-fit">
        {labels.map((label, index) => (
          <RadioGroup.Item asChild key={label} value={String(index === 1)}>
            <Button
              variant="outline"
              size="sm"
              className="flex-1 data-[state=checked]:bg-primary/10 data-[state=checked]:text-foreground"
            >
              {selected === (index === 1) && (
                <Check data-icon="inline-start" aria-hidden="true" />
              )}
              {label}
            </Button>
          </RadioGroup.Item>
        ))}
      </ButtonGroup>
    </RadioGroup.Root>
  )
}

export default function PaperProjectCreateDialog({
  open,
  onOpenChange,
  mode,
  modelPicker: ModelPicker,
  onCreated,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  mode: "proposal" | "manuscript"
  modelPicker: ComponentType<PaperModelPickerProps>
  onCreated: (workspaceId: string, initialPrompt?: string) => void
}) {
  const { t } = useTranslation()
  const fieldId = useId()
  const focusHeading = useCallback((element: HTMLHeadingElement | null) => {
    element?.focus()
  }, [])
  const queryClient = useQueryClient()
  const createWorkspace = useCreatePaperWorkspace()
  const proposal = mode === "proposal"
  const [page, setPage] = useState<CreatePage>("choose")
  const [hasMaterials, setHasMaterials] = useState(false)
  const [autoStart, setAutoStart] = useState(false)
  const [title, setTitle] = useState("")
  const [direction, setDirection] = useState("")
  const [requirements, setRequirements] = useState("")
  const [binding, setBinding] = useState<PaperModelPickerProps["value"]>({
    providerId: "",
    modelName: "",
  })
  const [files, setFiles] = useState<File[]>([])
  const [reviews, setReviews] = useState<ReviewDraft[]>([])
  const nextReviewId = useRef(1)
  const saving = useRef(false)
  const [busy, setBusy] = useState(false)
  const [failedStep, setFailedStep] = useState<SaveStep | null>(null)
  const [workspaceId, setWorkspaceId] = useState<string>()
  const [modelSaved, setModelSaved] = useState(false)
  const [sourceVersionId, setSourceVersionId] = useState<string>()
  const [savedReviews, setSavedReviews] = useState<number[]>([])

  const uploadTooLarge = hasMaterials && paperSourceUploadTooLarge(files)
  const readyReviews =
    !proposal && hasMaterials
      ? reviews.filter(
          (review) =>
            review.label.trim() || review.title.trim() || review.content.trim(),
        )
      : []
  const invalidReview = readyReviews.some(
    (review) => !review.label.trim() || !review.content.trim(),
  )
  const reviewsNeedSource = readyReviews.length > 0 && files.length === 0
  const validModel = Boolean(binding.providerId && binding.modelName)
  const titleValid = Boolean(title.trim())
  const materialsValid = !uploadTooLarge && !invalidReview && !reviewsNeedSource
  const canSave =
    titleValid && (page === "empty" || (validModel && materialsValid))
  const rebuttalReady =
    hasMaterials && files.length > 0 && readyReviews.length > 0

  const submit = async () => {
    if (saving.current || !canSave) return
    saving.current = true
    setBusy(true)
    setFailedStep(null)
    let step: SaveStep = "workspace"
    let id = workspaceId
    let versionId = sourceVersionId
    try {
      if (!id) {
        const context = [
          direction.trim() &&
            (proposal
              ? `${t("paper.workspace.intake.direction")}\n${direction.trim()}`
              : direction.trim()),
          proposal &&
            requirements.trim() &&
            `${t("paper.workspace.intake.requirements")}\n${requirements.trim()}`,
        ].filter(Boolean)
        const workspace = await createWorkspace.mutateAsync({
          title: title.trim(),
          mode,
          ...(page !== "empty" && {
            description: context.join("\n\n") || null,
          }),
        })
        id = workspace.id
        setWorkspaceId(id)
      }
      if (page === "empty") {
        onCreated(id)
        return
      }
      step = "model"
      if (!modelSaved) {
        await Llm4AdPapersService.updateModelBinding({
          workspaceId: id,
          requestBody: {
            provider_id: binding.providerId,
            model_name: binding.modelName,
          },
        })
        setModelSaved(true)
      }
      step = "materials"
      if (hasMaterials && files.length && !versionId) {
        const source = await Llm4AdPapersService.uploadSource({
          workspaceId: id,
          formData: paperSourceUploadFormData({
            files,
            purpose: proposal ? "project_context" : "source",
          }),
        })
        versionId = source.id
        setSourceVersionId(versionId)
      }
      step = "reviews"
      if (versionId) {
        for (const review of readyReviews) {
          if (savedReviews.includes(review.id)) continue
          await Llm4AdPapersService.attachReviewerFeedback({
            sourceVersionId: versionId,
            requestBody: {
              reviewer_label: review.label.trim(),
              title: review.title.trim() || null,
              content: review.content.trim(),
            },
          })
          setSavedReviews((ids) => [...ids, review.id])
        }
      }
      if (autoStart && !proposal && !rebuttalReady) {
        toast.info(t("paper.workspace.intake.rebuttalNeedsMaterials"))
      }
      let initialPrompt: string | undefined
      if (autoStart && (proposal || rebuttalReady)) {
        initialPrompt = t(`paper.workspace.intake.initialPrompt.${mode}`)
        if (!proposal && direction.trim()) {
          initialPrompt += `\n\n${t("paper.workspace.intake.rebuttalGoal")}\n${direction.trim()}`
        }
      }
      onCreated(id, initialPrompt)
    } catch {
      setFailedStep(step)
      if (step === "materials" || step === "reviews") setPage("materials")
    } finally {
      if (id)
        void queryClient.invalidateQueries({ queryKey: paperWorkspaceKeys.all })
      setBusy(false)
      saving.current = false
    }
  }

  const projectOptions = [
    {
      page: "empty",
      icon: FolderPlus,
      title: t("paper.workspace.intake.emptyProject"),
      description: t("paper.workspace.intake.emptyProjectHint"),
    },
    {
      page: "details",
      icon: FileText,
      title: t("paper.workspace.intake.guidedProject"),
      description: t("paper.workspace.intake.guidedProjectHint"),
    },
  ] as const
  const materialsLocked = busy || Boolean(sourceVersionId)
  const projectLocked = busy || Boolean(workspaceId)
  const guidedPage = page !== "choose" && page !== "empty" ? page : null
  const stepIndex = guidedPage === null ? -1 : GUIDED_STEPS.indexOf(guidedPage)
  const nextStep = GUIDED_STEPS[stepIndex + 1]
  const retryMaterials = failedStep === "materials" || failedStep === "reviews"
  const isRetryPage =
    failedStep !== null &&
    (retryMaterials
      ? page === "materials"
      : page === "empty" || page === "model")
  const shouldSubmit =
    page === "empty" ||
    page === "model" ||
    (page === "materials" && retryMaterials)
  const willStart = page !== "empty" && autoStart && (proposal || rebuttalReady)
  const canAdvance = shouldSubmit
    ? canSave
    : page === "details"
      ? titleValid
      : materialsValid
  const lastSelectableStep = stepIndex + Number(!shouldSubmit && canAdvance)
  const steps: WorkflowStep<GuidedStep>[] = GUIDED_STEPS.map((id, index) => ({
    id,
    label: t(STEP_LABELS[id]),
    status: index < stepIndex ? "complete" : "pending",
    disabled: busy || index > lastSelectableStep,
  }))
  const canGoBack = page !== "choose" && (!workspaceId || stepIndex > 0)
  const previousPage = stepIndex > 0 ? GUIDED_STEPS[stepIndex - 1] : "choose"

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => !saving.current && onOpenChange(next)}
    >
      <DialogContent
        className="flex max-h-[90dvh] min-w-0 flex-col gap-0 overflow-hidden p-0 sm:max-w-2xl"
        showCloseButton={!busy}
        preventOutsideClose
      >
        <DialogHeader className="shrink-0 border-b bg-muted/25 px-5 py-4 pr-11 text-left sm:px-6 sm:pr-12">
          <DialogTitle
            key={page}
            ref={focusHeading}
            tabIndex={-1}
            className="outline-none"
          >
            {t(
              guidedPage
                ? STEP_LABELS[guidedPage]
                : page === "empty"
                  ? "paper.workspace.intake.emptyProject"
                  : "paper.workspace.createTitle",
            )}
          </DialogTitle>
          <DialogDescription>
            {t(
              guidedPage
                ? `paper.workspace.intake.stepDescription.${guidedPage}`
                : page === "empty"
                  ? "paper.workspace.intake.emptyProjectHint"
                  : "paper.workspace.intake.description",
            )}
          </DialogDescription>
        </DialogHeader>

        {guidedPage && (
          <WorkflowStepper
            steps={steps}
            value={guidedPage}
            onValueChange={setPage}
            label={t("paper.workspace.intake.progress")}
            previousLabel={t("paper.workspace.intake.previous")}
            nextLabel={t("paper.workspace.intake.next")}
          />
        )}
        <ScrollArea
          key={page}
          className="min-h-0 min-w-0 flex-1 [&>[data-radix-scroll-area-viewport]>div]:block! [&>[data-radix-scroll-area-viewport]>div]:w-full!"
        >
          <div className="flex min-w-0 flex-col gap-4 px-5 py-4 sm:px-6">
            {page === "choose" && (
              <div className="grid gap-3 sm:grid-cols-2">
                {projectOptions.map(
                  ({
                    page: optionPage,
                    icon: Icon,
                    title: optionTitle,
                    description,
                  }) => (
                    <Card
                      key={optionPage}
                      className="relative gap-0 py-4 shadow-none transition-colors hover:border-primary/60 hover:bg-primary/5 has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring"
                    >
                      <CardHeader className="grid grid-cols-[auto_1fr_auto] items-start gap-x-2 gap-y-1 px-4">
                        <Icon
                          className="row-span-2 size-4 text-muted-foreground"
                          aria-hidden="true"
                        />
                        <CardTitle
                          id={`${fieldId}-option-${optionPage}`}
                          className="text-sm leading-5"
                        >
                          {optionTitle}
                        </CardTitle>
                        <ArrowRight
                          className="size-4 text-muted-foreground"
                          aria-hidden="true"
                        />
                        <CardDescription
                          id={`${fieldId}-option-hint-${optionPage}`}
                          className="col-start-2 col-span-2 text-xs leading-5"
                        >
                          {description}
                        </CardDescription>
                      </CardHeader>
                      <Button
                        type="button"
                        variant="ghost"
                        aria-labelledby={`${fieldId}-option-${optionPage}`}
                        aria-describedby={`${fieldId}-option-hint-${optionPage}`}
                        className="absolute inset-0 size-full opacity-0"
                        onClick={() => {
                          setFailedStep(null)
                          setPage(optionPage)
                        }}
                      />
                    </Card>
                  ),
                )}
              </div>
            )}

            {(page === "empty" || page === "details") && (
              <div className="flex flex-col gap-4">
                <div className="flex flex-col gap-2">
                  <Label htmlFor={`${fieldId}-title`}>
                    {t("paper.workspace.name")}
                  </Label>
                  <Input
                    id={`${fieldId}-title`}
                    value={title}
                    onChange={(event) => setTitle(event.target.value)}
                    placeholder={t("paper.workspace.namePlaceholder")}
                    maxLength={255}
                    disabled={projectLocked}
                    required
                  />
                </div>
                {page === "details" && (
                  <div
                    className={cn("grid gap-3", proposal && "sm:grid-cols-2")}
                  >
                    <div className="flex flex-col gap-2">
                      <Label htmlFor={`${fieldId}-direction`}>
                        {t(
                          proposal
                            ? "paper.workspace.intake.direction"
                            : "paper.workspace.intake.rebuttalGoal",
                        )}
                      </Label>
                      <Textarea
                        id={`${fieldId}-direction`}
                        value={direction}
                        onChange={(event) => setDirection(event.target.value)}
                        maxLength={proposal ? 1900 : 4000}
                        disabled={projectLocked}
                      />
                    </div>
                    {proposal && (
                      <div className="flex flex-col gap-2">
                        <Label htmlFor={`${fieldId}-requirements`}>
                          {t("paper.workspace.intake.requirements")}
                        </Label>
                        <Textarea
                          id={`${fieldId}-requirements`}
                          value={requirements}
                          onChange={(event) =>
                            setRequirements(event.target.value)
                          }
                          maxLength={1900}
                          disabled={projectLocked}
                        />
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}

            {page === "materials" && (
              <Card className="gap-4 py-4">
                <CardHeader className="px-4">
                  <CardTitle id={`${fieldId}-materials`}>
                    {t("paper.workspace.intake.materials")}
                  </CardTitle>
                </CardHeader>
                <CardContent className="flex min-w-0 flex-col gap-4 px-4">
                  <ProjectOptionGroup
                    labelId={`${fieldId}-materials`}
                    labels={[
                      t("paper.workspace.intake.noMaterials"),
                      t("paper.workspace.intake.hasMaterials"),
                    ]}
                    selected={hasMaterials}
                    onChange={setHasMaterials}
                    disabled={materialsLocked}
                  />
                  {hasMaterials && (
                    <section
                      aria-labelledby={`${fieldId}-upload`}
                      className="flex min-w-0 flex-col gap-2"
                    >
                      <h3
                        id={`${fieldId}-upload`}
                        className="text-sm font-medium"
                      >
                        {t(
                          proposal
                            ? "paper.workspace.intake.referenceFiles"
                            : "paper.workspace.intake.manuscriptFiles",
                        )}
                      </h3>
                      <PaperSourceFilePicker
                        purpose={proposal ? "project_context" : "source"}
                        files={files}
                        setFiles={setFiles}
                        disabled={materialsLocked}
                      />
                    </section>
                  )}
                  {!proposal && hasMaterials && (
                    <div className="flex min-w-0 flex-col items-start gap-3">
                      <Separator />
                      {reviews.map((review) => {
                        const saved = savedReviews.includes(review.id)
                        return (
                          <Card
                            key={review.id}
                            className="w-full min-w-0 gap-2 py-3"
                          >
                            <CardContent className="grid gap-2 px-3">
                              <div className="flex items-center justify-end">
                                <Button
                                  size="icon-sm"
                                  variant="ghost"
                                  disabled={busy || saved}
                                  aria-label={t("common.delete")}
                                  onClick={() =>
                                    setReviews((items) =>
                                      items.filter(
                                        (item) => item.id !== review.id,
                                      ),
                                    )
                                  }
                                >
                                  <Trash2 />
                                </Button>
                              </div>
                              <PaperReviewFields
                                value={review}
                                disabled={busy || saved}
                                onChange={(value) =>
                                  setReviews((items) =>
                                    items.map((item) =>
                                      item.id === review.id
                                        ? { ...value, id: review.id }
                                        : item,
                                    ),
                                  )
                                }
                              />
                            </CardContent>
                          </Card>
                        )
                      })}
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        disabled={busy}
                        onClick={() => {
                          const reviewId = nextReviewId.current++
                          setReviews((items) => [
                            ...items,
                            { id: reviewId, label: "", title: "", content: "" },
                          ])
                        }}
                      >
                        <Plus data-icon="inline-start" />
                        {t("paper.workspace.intake.addReview")}
                      </Button>
                      {(invalidReview || reviewsNeedSource) && (
                        <Alert variant="destructive">
                          <AlertCircle />
                          <AlertDescription>
                            {invalidReview && (
                              <p>
                                {t("paper.workspace.intake.reviewIncomplete")}
                              </p>
                            )}
                            {reviewsNeedSource && (
                              <p>
                                {t("paper.workspace.intake.reviewNeedsSource")}
                              </p>
                            )}
                          </AlertDescription>
                        </Alert>
                      )}
                    </div>
                  )}
                </CardContent>
              </Card>
            )}

            {page === "model" && (
              <Card className="gap-4 py-4">
                <CardContent className="flex flex-col gap-4 px-4">
                  <div className="flex flex-col gap-2">
                    <Label>{t("paper.model.model")}</Label>
                    <ModelPicker
                      value={binding}
                      onChange={setBinding}
                      disabled={busy || modelSaved}
                    />
                    {!validModel && (
                      <p className="text-xs text-muted-foreground">
                        {t("paper.model.required")}
                      </p>
                    )}
                  </div>
                  <Separator />
                  <div className="flex flex-col gap-2">
                    <span
                      id={`${fieldId}-start`}
                      className="text-sm font-medium"
                    >
                      {t("paper.workspace.intake.startMode")}
                    </span>
                    <ProjectOptionGroup
                      labelId={`${fieldId}-start`}
                      labels={[
                        t("paper.workspace.intake.manual"),
                        t("paper.workspace.intake.automatic"),
                      ]}
                      selected={autoStart}
                      onChange={setAutoStart}
                      disabled={busy}
                    />
                    {autoStart && !proposal && !rebuttalReady && (
                      <p className="text-sm text-muted-foreground">
                        {t("paper.workspace.intake.rebuttalNeedsMaterials")}
                      </p>
                    )}
                  </div>
                </CardContent>
              </Card>
            )}
          </div>
        </ScrollArea>

        <div className="flex shrink-0 flex-col gap-3 border-t bg-background px-5 py-3 sm:px-6">
          {isRetryPage && (
            <Alert variant="destructive">
              <AlertCircle />
              <AlertDescription>
                {t(`paper.workspace.intake.error.${failedStep}`)}
              </AlertDescription>
            </Alert>
          )}
          <DialogFooter className="grid grid-cols-2 sm:flex sm:justify-end">
            {page === "choose" && (
              <Button
                className="col-start-2"
                type="button"
                variant="outline"
                onClick={() => onOpenChange(false)}
              >
                {t("common.cancel")}
              </Button>
            )}
            {canGoBack && (
              <Button
                className="h-auto min-h-9 min-w-0 whitespace-normal"
                type="button"
                variant="outline"
                disabled={busy}
                onClick={() => setPage(previousPage)}
              >
                {t(
                  previousPage === "choose"
                    ? "paper.workspace.intake.backToChoice"
                    : "paper.workspace.intake.previous",
                )}
              </Button>
            )}
            {page !== "choose" && (
              <Button
                className={cn(
                  "h-auto min-h-9 min-w-0 whitespace-normal",
                  !canGoBack && "col-start-2",
                )}
                type="button"
                disabled={!canAdvance || busy}
                onClick={() => {
                  if (shouldSubmit) void submit()
                  else setPage(nextStep)
                }}
              >
                {busy && (
                  <Loader2 data-icon="inline-start" className="animate-spin" />
                )}
                {t(
                  isRetryPage
                    ? "common.retry"
                    : !shouldSubmit
                      ? "paper.workspace.intake.next"
                      : willStart
                        ? "paper.workspace.intake.createAndStart"
                        : "paper.workspace.intake.createAndOpen",
                )}
              </Button>
            )}
          </DialogFooter>
        </div>
      </DialogContent>
    </Dialog>
  )
}
