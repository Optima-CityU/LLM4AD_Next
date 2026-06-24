import { useEffect, useState } from "react"
import { useTranslation } from "react-i18next"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

import type { ResearchConfig } from "./mock-data"

export default function ConfigDialog({
  open,
  onOpenChange,
  config,
  onSave,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  config: ResearchConfig
  onSave: (config: ResearchConfig) => void
}) {
  const { t } = useTranslation()
  const [localConfig, setLocalConfig] = useState(config)

  useEffect(() => {
    setLocalConfig(config)
  }, [config])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md" preventOutsideClose>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-base">
            {t("research.config.title")}
          </DialogTitle>
        </DialogHeader>
        <div className="space-y-5 py-2">
          <div className="space-y-2">
            <Label className="text-xs">{t("research.config.provider")}</Label>
            <Select
              value={localConfig.provider}
              onValueChange={(v) =>
                setLocalConfig({ ...localConfig, provider: v })
              }
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="openai">OpenAI</SelectItem>
                <SelectItem value="anthropic">Anthropic</SelectItem>
                <SelectItem value="deepseek">DeepSeek</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <Label className="text-xs">{t("research.config.model")}</Label>
            <Select
              value={localConfig.model}
              onValueChange={(v) =>
                setLocalConfig({ ...localConfig, model: v })
              }
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="gpt-4o">GPT-4o</SelectItem>
                <SelectItem value="gpt-4o-mini">GPT-4o Mini</SelectItem>
                <SelectItem value="claude-sonnet-4-6">
                  Claude Sonnet 4.6
                </SelectItem>
                <SelectItem value="deepseek-chat">DeepSeek Chat</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <Label className="text-xs">
              {t("research.config.evolutionAlgorithm")}
            </Label>
            <Select
              value={localConfig.evolutionAlgorithm}
              onValueChange={(v) =>
                setLocalConfig({
                  ...localConfig,
                  evolutionAlgorithm: v as "IslandGA" | "DyCA" | "MEoH",
                })
              }
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="IslandGA">IslandGA</SelectItem>
                <SelectItem value="DyCA">DyCA</SelectItem>
                <SelectItem value="MEoH">MEoH</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label className="text-xs">
                {t("research.config.maxIterations")}
              </Label>
              <Input
                type="number"
                value={localConfig.maxIterations}
                onChange={(e) =>
                  setLocalConfig({
                    ...localConfig,
                    maxIterations: Number(e.target.value),
                  })
                }
              />
            </div>
            <div className="space-y-2">
              <Label className="text-xs">
                {t("research.config.populationSize")}
              </Label>
              <Input
                type="number"
                value={localConfig.populationSize}
                onChange={(e) =>
                  setLocalConfig({
                    ...localConfig,
                    populationSize: Number(e.target.value),
                  })
                }
              />
            </div>
          </div>

          <div className="space-y-2">
            <Label className="text-xs">
              {t("research.config.temperature")}
            </Label>
            <Input
              type="number"
              step="0.1"
              min="0"
              max="2"
              value={localConfig.temperature}
              onChange={(e) =>
                setLocalConfig({
                  ...localConfig,
                  temperature: Number(e.target.value),
                })
              }
            />
          </div>

          <Button className="w-full" onClick={() => onSave(localConfig)}>
            {t("research.config.save")}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
