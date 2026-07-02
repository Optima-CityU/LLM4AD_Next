import { createFileRoute, redirect } from "@tanstack/react-router"
import { z } from "zod"

export const Route = createFileRoute("/oidc/callback")({
  validateSearch: z.object({
    access_token: z.string().optional(),
    refresh_token: z.string().optional(),
    redirect: z.string().optional(),
  }),
  beforeLoad: ({ search }) => {
    if (!search.access_token || !search.refresh_token) {
      throw redirect({ to: "/login" })
    }
    localStorage.setItem("access_token", search.access_token)
    localStorage.setItem("refresh_token", search.refresh_token)
    throw redirect({ to: search.redirect || "/projects" })
  },
})
