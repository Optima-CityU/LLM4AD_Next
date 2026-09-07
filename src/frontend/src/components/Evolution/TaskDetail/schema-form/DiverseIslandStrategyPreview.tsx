import {
  BrainCircuit,
  GitBranch,
  GitFork,
  RefreshCw,
  ShieldCheck,
  Sparkles,
} from "lucide-react"
import { useTranslation } from "react-i18next"

import { Badge } from "@/components/ui/badge"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import {
  buildIslandStrategyPreview,
  getDiverseIslandPreviewConfig,
  getIslandPreviewRole,
} from "./islandStrategyPreview"

interface DiverseIslandStrategyPreviewProps {
  value: Record<string, unknown> | undefined
  memoryEnabled: boolean
}

const numberValue = (
  value: Record<string, unknown> | undefined,
  key: string,
  fallback: number,
) => {
  const candidate = value?.[key]
  return typeof candidate === "number" && Number.isFinite(candidate)
    ? candidate
    : fallback
}

const booleanValue = (
  value: Record<string, unknown> | undefined,
  key: string,
  fallback: boolean,
) => (typeof value?.[key] === "boolean" ? value[key] : fallback)

const percent = (value: number) => `${Math.round(value * 100)}%`

export default function DiverseIslandStrategyPreview({
  value,
  memoryEnabled,
}: DiverseIslandStrategyPreviewProps) {
  const { t } = useTranslation()
  const previewConfig = getDiverseIslandPreviewConfig(value)
  const profiles = buildIslandStrategyPreview(previewConfig)
  const adaptiveMigration = booleanValue(value, "adaptive_migration", true)
  const stagnationThreshold = numberValue(
    value,
    "migration_stagnation_threshold",
    2,
  )
  const migrationInterval = numberValue(value, "migration_interval", 3)
  const shortTaskThreshold = numberValue(
    value,
    "short_task_generation_threshold",
    10,
  )
  const shortTaskMaxMigrations = numberValue(
    value,
    "short_task_max_migrations",
    1,
  )
  const noveltyRatio = numberValue(value, "novelty_survivor_ratio", 0.2)

  return (
    <section
      data-testid="island-strategy-preview"
      className="overflow-hidden rounded-xl border border-border/70 bg-card"
    >
      <div className="border-b border-border/60 bg-linear-to-r from-primary/6 via-background to-emerald-500/6 px-4 py-3">
        <div className="flex min-w-0 items-start gap-3">
          <div className="mt-0.5 rounded-lg border border-primary/15 bg-primary/10 p-2 text-primary">
            <GitBranch className="size-4" />
          </div>
          <div className="min-w-0">
            <h4 className="text-sm font-semibold">
              {t("evolution.schemaForm.islandPreview.title")}
            </h4>
            <p className="mt-0.5 text-xs leading-5 text-muted-foreground">
              {t("evolution.schemaForm.islandPreview.compactDescription")}
            </p>
          </div>
        </div>
      </div>

      <div className="space-y-4 p-4">
        <div>
          <div className="mb-2 flex justify-between text-[11px] font-medium text-muted-foreground">
            <span>{t("evolution.schemaForm.islandPreview.reuseEnd")}</span>
            <span>{t("evolution.schemaForm.islandPreview.exploreEnd")}</span>
          </div>
          <div className="relative mx-3.5 h-7">
            <div className="absolute inset-x-0 top-3 h-1 rounded-full bg-linear-to-r from-sky-500 via-amber-400 to-emerald-500/80" />
            {profiles.map((profile) => (
              <div
                key={profile.islandId}
                className="absolute top-0 flex size-7 -translate-x-1/2 items-center justify-center rounded-full border-2 border-background bg-foreground text-[10px] font-semibold text-background shadow-sm transition-[left] duration-300"
                style={{ left: `${profile.position * 100}%` }}
              >
                {profile.islandId + 1}
              </div>
            ))}
          </div>
        </div>

        <div className="grid grid-cols-[repeat(auto-fit,minmax(150px,1fr))] gap-2">
          {profiles.map((profile) => {
            const role = getIslandPreviewRole(profile, memoryEnabled)
            return (
              <Tooltip key={profile.islandId}>
                <TooltipTrigger asChild>
                  <button
                    type="button"
                    data-testid="island-profile-chip"
                    className="group rounded-lg border border-border/60 bg-muted/15 px-3 py-2 text-left transition-colors hover:border-primary/35 hover:bg-accent/35 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-semibold">
                        {t("evolution.schemaForm.islandPreview.island", {
                          index: profile.islandId + 1,
                        })}
                      </span>
                      <span className="truncate text-[10px] text-muted-foreground">
                        {t(`evolution.schemaForm.islandPreview.roles.${role}`)}
                      </span>
                    </div>
                    <div className="mt-2 flex h-1.5 overflow-hidden rounded-full bg-muted">
                      <div
                        className="bg-sky-500 transition-[width] duration-300"
                        style={{ width: percent(profile.exploitation) }}
                      />
                      <div
                        className="bg-emerald-500/80 transition-[width] duration-300"
                        style={{ width: percent(profile.exploration) }}
                      />
                    </div>
                    <div className="mt-1.5 flex justify-between text-[10px] tabular-nums text-muted-foreground">
                      <span>
                        {t(
                          "evolution.schemaForm.islandPreview.exploitationShort",
                          {
                            value: percent(profile.exploitation),
                          },
                        )}
                      </span>
                      <span>
                        {t(
                          "evolution.schemaForm.islandPreview.explorationShort",
                          {
                            value: percent(profile.exploration),
                          },
                        )}
                      </span>
                    </div>
                  </button>
                </TooltipTrigger>
                <TooltipContent side="top" className="max-w-xs space-y-1.5 p-3">
                  <p className="font-semibold">
                    {t("evolution.schemaForm.islandPreview.islandDetailTitle", {
                      index: profile.islandId + 1,
                      role: t(
                        `evolution.schemaForm.islandPreview.roles.${role}`,
                      ),
                    })}
                  </p>
                  <p>
                    {t("evolution.schemaForm.islandPreview.searchBalance", {
                      exploitation: percent(profile.exploitation),
                      exploration: percent(profile.exploration),
                    })}
                  </p>
                </TooltipContent>
              </Tooltip>
            )
          })}
        </div>

        <div className="grid gap-3 lg:grid-cols-2">
          <section
            data-testid="island-generation-source"
            className="rounded-lg border border-border/60 bg-muted/10 p-3"
          >
            <div className="flex items-start gap-2.5">
              <div className="rounded-md bg-sky-500/10 p-1.5 text-sky-600 dark:text-sky-400">
                <GitFork className="size-3.5" />
              </div>
              <div className="min-w-0">
                <h5 className="text-xs font-semibold">
                  {t(
                    "evolution.schemaForm.islandPreview.generationSourceTitle",
                  )}
                </h5>
                <p className="mt-0.5 text-[11px] leading-4 text-muted-foreground">
                  {t(
                    "evolution.schemaForm.islandPreview.generationSourceDescription",
                    { count: stagnationThreshold },
                  )}
                </p>
              </div>
            </div>
            <div className="mt-3 space-y-2">
              {profiles.map((profile) => (
                <div key={profile.islandId}>
                  <div className="mb-1 flex items-center justify-between gap-2 text-[10px] tabular-nums text-muted-foreground">
                    <span>
                      {t("evolution.schemaForm.islandPreview.island", {
                        index: profile.islandId + 1,
                      })}
                    </span>
                    <span>
                      {t(
                        "evolution.schemaForm.islandPreview.parentAndRestart",
                        {
                          parent: percent(1 - profile.restartProbability),
                          restart: percent(profile.restartProbability),
                        },
                      )}
                    </span>
                  </div>
                  <div className="flex h-1.5 overflow-hidden rounded-full bg-muted">
                    <div
                      className="bg-sky-500"
                      style={{
                        width: percent(1 - profile.restartProbability),
                      }}
                    />
                    <div
                      className="bg-emerald-500/80"
                      style={{ width: percent(profile.restartProbability) }}
                    />
                  </div>
                </div>
              ))}
            </div>
          </section>

          <div className={memoryEnabled ? "" : "opacity-50 grayscale"}>
            <section
              data-testid="island-memory-injection"
              aria-disabled={!memoryEnabled}
              className="h-full rounded-lg border border-border/60 bg-muted/10 p-3"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex min-w-0 items-start gap-2.5">
                  <div className="rounded-md bg-amber-500/10 p-1.5 text-amber-600 dark:text-amber-400">
                    <BrainCircuit className="size-3.5" />
                  </div>
                  <div className="min-w-0">
                    <h5 className="text-xs font-semibold">
                      {t(
                        "evolution.schemaForm.islandPreview.memoryInjectionTitle",
                      )}
                    </h5>
                    <p className="mt-0.5 text-[11px] leading-4 text-muted-foreground">
                      {t(
                        memoryEnabled
                          ? "evolution.schemaForm.islandPreview.memoryInjectionDescription"
                          : "evolution.schemaForm.islandPreview.memoryDisabledDescription",
                      )}
                    </p>
                  </div>
                </div>
                <Badge variant={memoryEnabled ? "secondary" : "outline"}>
                  {t(
                    memoryEnabled
                      ? "evolution.schemaForm.islandPreview.memoryActive"
                      : "evolution.schemaForm.islandPreview.memoryDisabled",
                  )}
                </Badge>
              </div>
              <div className="mt-3 space-y-2">
                {profiles.map((profile) => (
                  <div key={profile.islandId}>
                    <div className="mb-1 flex items-center justify-between gap-2 text-[10px] tabular-nums text-muted-foreground">
                      <span>
                        {t("evolution.schemaForm.islandPreview.island", {
                          index: profile.islandId + 1,
                        })}
                      </span>
                      <span>
                        {profile.memoryPolicy === "none"
                          ? t("evolution.schemaForm.islandPreview.noMemory")
                          : t("evolution.schemaForm.islandPreview.memoryMix", {
                              success: percent(profile.successMemoryRatio),
                              error: percent(profile.errorMemoryRatio),
                            })}
                      </span>
                    </div>
                    <div className="flex h-1.5 overflow-hidden rounded-full bg-muted">
                      {profile.memoryPolicy === "none" ? (
                        <div className="w-full bg-muted-foreground/20" />
                      ) : (
                        <>
                          <div
                            className="bg-amber-500"
                            style={{
                              width: percent(profile.successMemoryRatio),
                            }}
                          />
                          <div
                            className="bg-rose-500/75"
                            style={{
                              width: percent(profile.errorMemoryRatio),
                            }}
                          />
                        </>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </section>
          </div>
        </div>

        <div className="grid gap-2 border-t border-border/60 pt-3 text-[11px] text-muted-foreground md:grid-cols-3">
          <div className="flex items-start gap-2">
            <RefreshCw className="mt-0.5 size-3.5 shrink-0 text-primary" />
            <span>
              {adaptiveMigration
                ? t("evolution.schemaForm.islandPreview.adaptiveMigration", {
                    count: stagnationThreshold,
                  })
                : t("evolution.schemaForm.islandPreview.intervalMigration", {
                    count: migrationInterval,
                  })}
            </span>
          </div>
          <div className="flex items-start gap-2">
            <ShieldCheck className="mt-0.5 size-3.5 shrink-0 text-amber-500" />
            <span>
              {t("evolution.schemaForm.islandPreview.shortTaskRule", {
                generations: shortTaskThreshold,
                count: shortTaskMaxMigrations,
              })}
            </span>
          </div>
          <div className="flex items-start gap-2">
            <Sparkles className="mt-0.5 size-3.5 shrink-0 text-emerald-500" />
            <span>
              {t("evolution.schemaForm.islandPreview.noveltyRule", {
                value: percent(noveltyRatio),
              })}
            </span>
          </div>
        </div>
      </div>
    </section>
  )
}
