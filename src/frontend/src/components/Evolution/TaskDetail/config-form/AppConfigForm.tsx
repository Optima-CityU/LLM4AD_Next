import { zodResolver } from "@hookform/resolvers/zod"
import { useForm } from "react-hook-form"
import { useTranslation } from "react-i18next"

import { Form } from "@/components/ui/form"

import { type AppConfig, appConfigSchema } from "./appConfigSchema"
import CoderSection from "./CoderSection"
import CollapsibleSection from "./CollapsibleSection"
import EvaluatorSection from "./EvaluatorSection"
import EvolutionSection from "./EvolutionSection"
import GeneralSection from "./GeneralSection"
import LoggingSection from "./LoggingSection"
import MemorySection from "./MemorySection"
import PlannerSection from "./PlannerSection"
import ProvidersSection from "./ProvidersSection"
import RepoAnalyzerSection from "./RepoAnalyzerSection"
import VersionControlSection from "./VersionControlSection"
import WorkspaceSection from "./WorkspaceSection"

interface AppConfigFormProps {
  defaultValues?: Record<string, unknown>
  onSubmit: (data: AppConfig) => void
  footer?: React.ReactNode
}

export default function AppConfigForm({
  defaultValues,
  onSubmit,
  footer,
}: AppConfigFormProps) {
  const form = useForm({
    resolver: zodResolver(appConfigSchema) as any,
    mode: "onBlur" as const,
    defaultValues: (defaultValues as Record<string, unknown>) ?? {
      project_name: "llm4ad",
      background: "",
      random_seed: 42,
      base_dir: "./runs",
      providers: [],
      evaluator: {
        module: "",
        dataset: {},
        metrics: [],
        timeout: 60,
        max_retries: 1,
        parallel: true,
        batch_size: 10,
      },
      evolution: {},
      coder: { type: "custom" },
      memory: {},
      logging: {},
      workspace: {},
      version_control: {},
      planner: null,
    },
  })

  const coderType = (form.watch("coder.type") as unknown as string) ?? "custom"
  const { t } = useTranslation()

  return (
    <Form {...form}>
      <form
        onSubmit={form.handleSubmit((data) =>
          onSubmit(data as unknown as AppConfig),
        )}
        className="space-y-4"
      >
        <CollapsibleSection
          title={t("configForm.sections.general")}
          defaultOpen
        >
          <GeneralSection />
        </CollapsibleSection>

        <CollapsibleSection
          title={t("configForm.sections.providers")}
          defaultOpen
        >
          <ProvidersSection />
        </CollapsibleSection>

        <CollapsibleSection
          title={t("configForm.sections.evaluator")}
          defaultOpen
        >
          <EvaluatorSection />
        </CollapsibleSection>

        <CollapsibleSection title={t("configForm.sections.evolution")}>
          <EvolutionSection />
        </CollapsibleSection>

        <CollapsibleSection title={t("configForm.sections.coder")}>
          <CoderSection coderType={coderType} />
        </CollapsibleSection>

        <CollapsibleSection title={t("configForm.sections.memory")}>
          <MemorySection />
        </CollapsibleSection>

        <CollapsibleSection title={t("configForm.sections.logging")}>
          <LoggingSection />
        </CollapsibleSection>

        <CollapsibleSection title={t("configForm.sections.workspace")}>
          <WorkspaceSection />
        </CollapsibleSection>

        <CollapsibleSection title={t("configForm.sections.versionControl")}>
          <VersionControlSection />
        </CollapsibleSection>

        <CollapsibleSection title={t("configForm.sections.repoAnalyzer")}>
          <RepoAnalyzerSection />
        </CollapsibleSection>

        <CollapsibleSection title={t("configForm.sections.planner")}>
          <PlannerSection />
        </CollapsibleSection>

        {footer}
      </form>
    </Form>
  )
}
