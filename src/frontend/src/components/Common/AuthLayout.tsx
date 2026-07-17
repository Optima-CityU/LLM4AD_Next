import IslandBackground from "@/components/Common/IslandBackground"
import LanguageToggle from "@/components/Common/LanguageToggle"
import { Logo } from "@/components/Common/Logo"
import { FooterMetadataLinks } from "@/components/Common/Footer"
import ThemeToggle from "@/components/Common/ThemeToggle"
import { useTheme } from "@/components/theme-provider"

interface AuthLayoutProps {
  children: React.ReactNode
}

export function AuthLayout({ children }: AuthLayoutProps) {
  const { theme } = useTheme()
  const variant = theme === "light" ? "light" : "dark"

  return (
    <div className="relative min-h-svh overflow-hidden bg-background">
      <IslandBackground variant={variant} />

      {/* Language and Theme toggles */}
      <div className="absolute top-4 right-4 z-20 flex items-center gap-2">
        <LanguageToggle />
        <ThemeToggle />
      </div>

      {/* Content layer */}
      <div className="relative z-10 flex min-h-svh items-center justify-center p-4">
        <div className="w-full max-w-md">
          {/* Glass card */}
          <div
            className="rounded-2xl px-6 py-8 backdrop-blur-xl"
            style={{
              background:
                theme === "light"
                  ? "rgba(255, 255, 255, 0.7)"
                  : "rgba(13, 26, 45, 0.7)",
              border:
                theme === "light"
                  ? "1px solid rgba(139, 92, 246, 0.15)"
                  : "1px solid rgba(0, 212, 255, 0.15)",
              boxShadow:
                theme === "light"
                  ? "0 0 40px rgba(139, 92, 246, 0.12), inset 0 1px 0 rgba(0,0,0,0.05)"
                  : "0 0 40px rgba(0, 212, 255, 0.08), inset 0 1px 0 rgba(255,255,255,0.05)",
            }}
          >
            {/* Logo */}
            <div className="mb-6 flex flex-col items-center gap-3">
              <Logo variant="icon" className="size-12" asLink={false} />
              <span className="text-lg font-bold tracking-[0.15em] uppercase landing-gradient">
                LLM4AD_Next
              </span>
            </div>

            {/* Form content */}
            <div className="text-foreground">{children}</div>
          </div>

          {/* Footer */}
          <div className="mt-4 space-y-1 text-center text-xs text-muted-foreground">
            <p>LLM4AD_Next - {new Date().getFullYear()}</p>
            <FooterMetadataLinks />
          </div>
        </div>
      </div>
    </div>
  )
}
