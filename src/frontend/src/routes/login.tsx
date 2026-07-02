import { zodResolver } from "@hookform/resolvers/zod"
import {
  createFileRoute,
  Link as RouterLink,
  redirect,
} from "@tanstack/react-router"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { useTranslation } from "react-i18next"
import { z } from "zod"
import { Github } from "lucide-react"

import type { Body_login_login_access_token as AccessToken } from "@/client"
import { PrivacyPolicyService } from "@/client"
import { AuthLayout } from "@/components/Common/AuthLayout"
import { PrivacyPolicyDialog } from "@/components/Common/PrivacyPolicyDialog"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { LoadingButton } from "@/components/ui/loading-button"
import { PasswordInput } from "@/components/ui/password-input"
import useAuth, { isLoggedIn } from "@/hooks/useAuth"
import i18n from "@/i18n"

export const Route = createFileRoute("/login")({
  component: Login,
  validateSearch: z.object({
    redirect: z.string().optional(),
  }),
  beforeLoad: async () => {
    document.cookie =
      "code_token=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/"
    if (isLoggedIn()) {
      throw redirect({
        to: "/projects",
      })
    }
  },
  head: () => ({
    meta: [
      {
        title: i18n.t("login.pageTitle"),
      },
    ],
  }),
})

function Login() {
  const { t } = useTranslation()
  const { redirect: redirectTo } = Route.useSearch()
  const [showPrivacyDialog, setShowPrivacyDialog] = useState(false)
  const [pendingLoginData, setPendingLoginData] = useState<AccessToken | null>(
    null,
  )

  const { loginMutation } = useAuth({
    redirectTo: redirectTo || undefined,
    onPrivacyPolicyRequired: (email, password) => {
      setPendingLoginData({ username: email, password })
      setShowPrivacyDialog(true)
    },
  })

  const formSchema = z.object({
    username: z.email().max(255),
    password: z
      .string()
      .min(1, { message: t("validation.passwordRequired") })
      .min(8, { message: t("validation.passwordMinLength") })
      .max(255),
  }) satisfies z.ZodType<AccessToken>

  type FormData = z.infer<typeof formSchema>

  const form = useForm<FormData>({
    resolver: zodResolver(formSchema),
    mode: "onBlur",
    criteriaMode: "all",
    defaultValues: {
      username: "",
      password: "",
    },
  })

  const onSubmit = (data: FormData) => {
    if (loginMutation.isPending) return
    loginMutation.mutate(data)
  }

  const handleGithubLogin = () => {
    const apiBase = import.meta.env.VITE_API_URL || ""
    const redirect = encodeURIComponent(redirectTo || "/projects")
    window.location.href = `${apiBase}/api/v1/oidc/github/authorize?redirect=${redirect}`
  }

  const handleAcceptPrivacyPolicy = async () => {
    if (!pendingLoginData) return

    try {
      await PrivacyPolicyService.acceptPrivacyPolicyBeforeLogin({
        requestBody: {
          email: pendingLoginData.username,
          password: pendingLoginData.password,
        },
      })
      setShowPrivacyDialog(false)
      loginMutation.mutate(pendingLoginData)
    } catch (error) {
      console.error("Failed to accept privacy policy:", error)
    }
  }

  const handleDeclinePrivacyPolicy = () => {
    setShowPrivacyDialog(false)
    setPendingLoginData(null)
  }

  return (
    <AuthLayout>
      <Form {...form}>
        <form
          onSubmit={form.handleSubmit(onSubmit)}
          className="flex flex-col gap-5"
        >
          <div className="flex flex-col items-center gap-1 text-center mb-1">
            <h1 className="text-2xl font-bold tracking-wide landing-gradient">
              {t("login.title")}
            </h1>
            <p className="text-sm text-muted-foreground">
              {t("login.subtitle")}
            </p>
          </div>

          <div className="grid gap-3.5">
            <FormField
              control={form.control}
              name="username"
              render={({ field }) => (
                <FormItem>
                  <FormLabel className="text-foreground">
                    {t("login.emailLabel")}
                  </FormLabel>
                  <FormControl>
                    <Input
                      data-testid="email-input"
                      placeholder="user@example.com"
                      type="email"
                      {...field}
                    />
                  </FormControl>
                  <FormMessage className="text-xs" />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="password"
              render={({ field }) => (
                <FormItem>
                  <FormLabel className="text-foreground">
                    {t("login.passwordLabel")}
                  </FormLabel>
                  <FormControl>
                    <PasswordInput
                      data-testid="password-input"
                      placeholder={t("login.passwordPlaceholder")}
                      {...field}
                    />
                  </FormControl>
                  <FormMessage className="text-xs" />
                </FormItem>
              )}
            />

            <div className="flex justify-end">
              <RouterLink
                to="/recover-password"
                className="text-xs text-primary hover:text-primary/80 transition-colors"
              >
                {t("login.forgotPassword")}
              </RouterLink>
            </div>

            <LoadingButton
              type="submit"
              loading={loginMutation.isPending}
              className="w-full"
            >
              {t("login.loginButton")}
            </LoadingButton>

            <Button
              type="button"
              variant="outline"
              className="w-full"
              onClick={handleGithubLogin}
            >
              <Github data-icon="inline-start" />
              {t("login.githubLoginButton")}
            </Button>
          </div>

          <div className="text-center text-sm text-muted-foreground">
            {t("login.noAccount")}{" "}
            <RouterLink
              to="/signup"
              className="text-primary hover:text-primary/80 transition-colors"
            >
              {t("login.signupLink")}
            </RouterLink>
          </div>
        </form>
      </Form>

      <PrivacyPolicyDialog
        open={showPrivacyDialog}
        onAccept={handleAcceptPrivacyPolicy}
        onDecline={handleDeclinePrivacyPolicy}
        allowClose={false}
      />
    </AuthLayout>
  )
}
