import { createFileRoute, redirect } from "@tanstack/react-router"
import { z } from "zod"

export const Route = createFileRoute("/_layout/autodesign")({
  validateSearch: z.object({
    projectId: z.string().optional(),
    sessionId: z.string().optional(),
  }),
  beforeLoad: ({ search }) => {
    throw redirect({
      to: search.sessionId ? "/evolution" : "/projects",
      search: {
        projectId: search.projectId,
        designSessionId: search.sessionId,
      },
      replace: true,
    })
  },
})
