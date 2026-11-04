import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"

import { type AutoDesignSessionResponse, Llm4AdPapersService } from "@/client"
import ProviderModelPicker from "@/components/AutoResearch/ProviderModelPicker"
import { Button } from "@/components/ui/button"
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
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { autoDesignKeys } from "@/lib/autoDesign"

type Props = {
  session: AutoDesignSessionResponse
  kind: "model" | "preferences"
  onClose: () => void
}

export default function AutoDesignSettings({ session, kind, onClose }: Props) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [portalContainer, setPortalContainer] = useState<HTMLDivElement | null>(
    null,
  )
  const [provider, setProvider] = useState(session.analysis_provider_id ?? "")
  const [model, setModel] = useState(session.analysis_model_name ?? "")
  const [contextTokens, setContextTokens] = useState(
    session.analysis_context_window_tokens,
  )
  const [outputTokens, setOutputTokens] = useState(
    session.analysis_max_output_tokens,
  )
  const [language, setLanguage] = useState(
    session.conversation_preferences?.reply_language ?? "auto",
  )
  const [guidance, setGuidance] = useState(
    session.conversation_preferences?.additional_guidance ?? "",
  )
  const save = useMutation({
    mutationFn: async () =>
      kind === "model"
        ? await Llm4AdPapersService.updateModelBinding({
            workspaceId: session.id,
            requestBody: {
              provider_id: provider,
              model_name: model.trim(),
              context_window_tokens: contextTokens,
              max_output_tokens: outputTokens,
            },
          })
        : await Llm4AdPapersService.updateWorkspace({
            workspaceId: session.id,
            requestBody: {
              conversation_preferences: {
                reply_language: language,
                additional_guidance: guidance.trim(),
              },
            },
          }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: autoDesignKeys.all })
      toast.success(
        t(kind === "model" ? "paper.model.saved" : "paper.preferences.saved"),
      )
      onClose()
    },
    onError: () => toast.error(t("autodesign.actionFailed")),
  })
  const invalidModel =
    !provider ||
    !model.trim() ||
    !Number.isInteger(contextTokens) ||
    !Number.isInteger(outputTokens) ||
    contextTokens < 8192 ||
    outputTokens < 1024 ||
    outputTokens > contextTokens

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !save.isPending) onClose()
      }}
    >
      <DialogContent ref={setPortalContainer} className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {t(
              kind === "model"
                ? "paper.model.dialogTitle"
                : "paper.preferences.dialogTitle",
            )}
          </DialogTitle>
          <DialogDescription>
            {t(
              kind === "model"
                ? "autodesign.modelDescription"
                : "autodesign.preferencesDescription",
            )}
          </DialogDescription>
        </DialogHeader>
        {kind === "model" ? (
          <div className="flex flex-col gap-4">
            <ProviderModelPicker
              provider={provider}
              model={model}
              onChange={(nextProvider, nextModel) => {
                setProvider(nextProvider)
                setModel(nextModel)
              }}
              excludeSpecial
              portalContainer={portalContainer}
            />
            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-2">
                <Label htmlFor="autodesign-context-tokens">
                  {t("paper.model.context")}
                </Label>
                <Input
                  id="autodesign-context-tokens"
                  type="number"
                  min={8192}
                  step={1}
                  value={contextTokens}
                  onChange={(event) =>
                    setContextTokens(Number(event.target.value))
                  }
                />
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor="autodesign-output-tokens">
                  {t("paper.model.output")}
                </Label>
                <Input
                  id="autodesign-output-tokens"
                  type="number"
                  min={1024}
                  max={contextTokens}
                  step={1}
                  value={outputTokens}
                  onChange={(event) =>
                    setOutputTokens(Number(event.target.value))
                  }
                />
              </div>
            </div>
          </div>
        ) : (
          <div className="flex flex-col gap-4">
            <div className="flex flex-col gap-2">
              <Label htmlFor="autodesign-reply-language">
                {t("paper.preferences.language")}
              </Label>
              <Select
                value={language}
                onValueChange={(value: "auto" | "zh" | "en") =>
                  setLanguage(value)
                }
              >
                <SelectTrigger id="autodesign-reply-language">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectGroup>
                    <SelectItem value="auto">
                      {t("paper.preferences.languageAuto")}
                    </SelectItem>
                    <SelectItem value="zh">
                      {t("paper.preferences.languageChinese")}
                    </SelectItem>
                    <SelectItem value="en">
                      {t("paper.preferences.languageEnglish")}
                    </SelectItem>
                  </SelectGroup>
                </SelectContent>
              </Select>
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="autodesign-guidance">
                {t("paper.preferences.additionalGuidance")}
              </Label>
              <Textarea
                id="autodesign-guidance"
                value={guidance}
                onChange={(event) => setGuidance(event.target.value)}
                maxLength={4000}
                className="min-h-28"
              />
            </div>
          </div>
        )}
        <DialogFooter>
          <Button variant="outline" disabled={save.isPending} onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button
            disabled={save.isPending || (kind === "model" && invalidModel)}
            onClick={() => save.mutate()}
          >
            {t("common.save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
