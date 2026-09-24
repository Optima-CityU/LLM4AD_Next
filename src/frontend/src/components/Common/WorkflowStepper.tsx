import {
  AlertTriangle,
  Check,
  ChevronLeft,
  ChevronRight,
  RotateCcw,
} from "lucide-react"
import { useLayoutEffect, useRef, useState } from "react"

import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

export interface WorkflowStep<T extends string = string> {
  id: T
  label: string
  shortLabel?: string
  description?: string
  status?: "pending" | "complete" | "revision" | "stale"
  statusLabel?: string
}

interface WorkflowStepperProps<T extends string> {
  steps: WorkflowStep<T>[]
  value: T
  onValueChange: (value: T) => void
  label: string
  previousLabel: string
  nextLabel: string
}

function StepMarker({
  index,
  active = false,
  status,
}: {
  index: number
  active?: boolean
  status: WorkflowStep["status"]
}) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "relative z-10 flex size-6 shrink-0 items-center justify-center rounded-full border bg-card text-xs font-semibold tabular-nums",
        active
          ? "border-primary bg-primary text-primary-foreground ring-3 ring-primary/15"
          : status === "complete"
            ? "border-primary/35 text-primary"
            : status === "revision"
              ? "border-amber-500/50 text-amber-600 dark:text-amber-400"
              : "border-border text-muted-foreground",
      )}
    >
      {!active && status === "complete" ? (
        <Check className="size-3.5" />
      ) : !active && status === "revision" ? (
        <AlertTriangle className="size-3.5" />
      ) : !active && status === "stale" ? (
        <RotateCcw className="size-3.5" />
      ) : (
        index + 1
      )}
    </span>
  )
}

export default function WorkflowStepper<T extends string>({
  steps,
  value,
  onValueChange,
  label,
  previousLabel,
  nextLabel,
}: WorkflowStepperProps<T>) {
  const containerRef = useRef<HTMLDivElement>(null)
  const labelsRef = useRef<HTMLDivElement>(null)
  const [compact, setCompact] = useState(false)
  const activeIndex = Math.max(
    0,
    steps.findIndex((step) => step.id === value),
  )
  const activeStep = steps[activeIndex]

  useLayoutEffect(() => {
    if (steps.length === 0) return
    const container = containerRef.current
    const labels = labelsRef.current
    if (!container || !labels) return

    // Measure translated labels at their rendered font size, not viewport breakpoints.
    const updateLayout = () => {
      setCompact(
        labels.getBoundingClientRect().width + 4 > container.clientWidth,
      )
    }
    const observer = new ResizeObserver(updateLayout)
    observer.observe(container)
    observer.observe(labels)
    updateLayout()
    return () => observer.disconnect()
  }, [steps.length])

  if (!activeStep) return null

  return (
    <nav
      aria-label={label}
      data-layout={compact ? "compact" : "expanded"}
      className="relative h-14 min-w-0 shrink-0 border-b bg-card px-3 text-foreground"
    >
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 overflow-hidden invisible"
      >
        <div
          ref={labelsRef}
          className="flex w-max text-[13px] font-medium leading-4"
        >
          {steps.map((step) => (
            <span
              key={step.id}
              className="min-w-10 shrink-0 whitespace-nowrap px-2"
            >
              {step.shortLabel ?? step.label}
            </span>
          ))}
        </div>
      </div>
      <div ref={containerRef} className="flex h-full min-w-0 items-center">
        {compact ? (
          <div className="flex w-full min-w-0 items-center gap-1">
            <Button
              variant="ghost"
              size="icon"
              aria-label={previousLabel}
              title={previousLabel}
              disabled={activeIndex === 0}
              onClick={() => onValueChange(steps[activeIndex - 1].id)}
            >
              <ChevronLeft />
            </Button>
            <Select
              value={activeStep.id}
              onValueChange={(id) => {
                const step = steps.find((item) => item.id === id)
                if (step) onValueChange(step.id)
              }}
            >
              <SelectTrigger
                aria-label={label}
                title={activeStep.label}
                className="min-w-0 flex-1 gap-2 border-transparent px-2 shadow-none hover:bg-accent"
              >
                <StepMarker
                  index={activeIndex}
                  active
                  status={activeStep.status}
                />
                <SelectValue>
                  <span className="truncate font-medium">
                    {activeStep.label}
                  </span>
                </SelectValue>
                <span className="ml-auto shrink-0 text-xs tabular-nums text-muted-foreground">
                  {activeIndex + 1}/{steps.length}
                </span>
              </SelectTrigger>
              <SelectContent align="start" className="max-w-[calc(100vw-2rem)]">
                {steps.map((step, index) => (
                  <SelectItem
                    key={step.id}
                    value={step.id}
                    textValue={step.label}
                    className="py-2"
                  >
                    <StepMarker
                      index={index}
                      active={step.id === activeStep.id}
                      status={step.status}
                    />
                    <span className="min-w-0 whitespace-normal break-words">
                      {step.label}
                    </span>
                    {step.statusLabel && (
                      <span className="sr-only">{step.statusLabel}</span>
                    )}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button
              variant="ghost"
              size="icon"
              aria-label={nextLabel}
              title={nextLabel}
              disabled={activeIndex === steps.length - 1}
              onClick={() => onValueChange(steps[activeIndex + 1].id)}
            >
              <ChevronRight />
            </Button>
          </div>
        ) : (
          <ol
            className="grid w-full items-center"
            style={{
              gridTemplateColumns: `repeat(${steps.length}, minmax(max-content, 1fr))`,
            }}
          >
            {steps.map((step, index) => (
              <li key={step.id} className="relative">
                {index > 0 && (
                  <span
                    aria-hidden="true"
                    className={cn(
                      "absolute top-3 right-[calc(50%+18px)] left-0 h-px bg-border",
                      steps[index - 1].status === "complete" && "bg-primary/45",
                    )}
                  />
                )}
                {index < steps.length - 1 && (
                  <span
                    aria-hidden="true"
                    className={cn(
                      "absolute top-3 right-0 left-[calc(50%+18px)] h-px bg-border",
                      step.status === "complete" && "bg-primary/45",
                    )}
                  />
                )}
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      aria-current={
                        step.id === activeStep.id ? "step" : undefined
                      }
                      aria-label={`${index + 1}. ${step.label}${step.statusLabel ? ` · ${step.statusLabel}` : ""}`}
                      onClick={() => onValueChange(step.id)}
                      className="group relative flex h-11 w-full flex-col gap-1 px-2 py-0 hover:bg-transparent dark:hover:bg-transparent"
                    >
                      <StepMarker
                        index={index}
                        active={step.id === activeStep.id}
                        status={step.status}
                      />
                      <span
                        className={cn(
                          "whitespace-nowrap text-[13px] font-medium leading-4 group-hover:text-primary",
                          step.id === activeStep.id
                            ? "text-primary"
                            : "text-muted-foreground",
                        )}
                      >
                        {step.shortLabel ?? step.label}
                      </span>
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent
                    side="bottom"
                    sideOffset={8}
                    className="max-w-72 text-sm"
                  >
                    <p className="font-medium">
                      {index + 1}. {step.label}
                    </p>
                    {step.statusLabel && (
                      <p className="mt-1 opacity-80">{step.statusLabel}</p>
                    )}
                    {step.description && (
                      <p className="mt-1 leading-relaxed opacity-80">
                        {step.description}
                      </p>
                    )}
                  </TooltipContent>
                </Tooltip>
              </li>
            ))}
          </ol>
        )}
      </div>
    </nav>
  )
}
