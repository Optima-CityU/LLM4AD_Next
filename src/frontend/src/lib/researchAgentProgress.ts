import { z } from "zod"

export const researchAgentProgressSchema = z.object({
  sessionId: z.string(),
  status: z.enum(["ready", "running", "waiting", "idle", "interrupted"]),
  currentTool: z.string().nullable(),
  agents: z.array(
    z.object({
      id: z.string(),
      parentId: z.string(),
      stage: z.string(),
      description: z.string(),
      status: z.enum(["running", "completed", "failed", "interrupted"]),
      toolCount: z.number().int().nonnegative(),
      currentTool: z.string().nullable(),
      tools: z.array(
        z.object({ name: z.string(), done: z.boolean(), failed: z.boolean() }),
      ),
    }),
  ),
})

export type ResearchAgentProgress = z.infer<typeof researchAgentProgressSchema>
