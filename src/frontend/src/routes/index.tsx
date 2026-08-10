import { useQuery } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import {
  ArrowRight,
  ArrowUpRight,
  BookOpen,
  Box,
  Brain,
  CalendarDays,
  Combine,
  Cpu,
  Crown,
  ExternalLink,
  FlaskConical,
  Gamepad2,
  Github,
  GraduationCap,
  Images,
  Layers,
  LineChart,
  Mail,
  Menu,
  MessageSquareText,
  Network,
  Newspaper,
  Play,
  Sigma,
  Sparkles,
  Trophy,
  Workflow,
  Wrench,
  X,
} from "lucide-react"
import { useEffect, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import { NewsService } from "@/client"
import IslandBackground from "@/components/Common/IslandBackground"
import LanguageToggle from "@/components/Common/LanguageToggle"
import { FooterMetadataLinks } from "@/components/Common/Footer"
import ThemeToggle from "@/components/Common/ThemeToggle"
import { ContactUsDialog } from "@/components/Feedback/ContactUsDialog"
import { UserManualDialog } from "@/components/Guide/UserManualDialog"
import { useTheme } from "@/components/theme-provider"
import { Button } from "@/components/ui/button"
import { isLoggedIn } from "@/hooks/useAuth"

import icon from "/assets/images/logo.svg"

export const Route = createFileRoute("/")({
  component: LandingPage,
  head: () => ({
    meta: [
      {
        title: "OpenLoopX - Algorithm Design with Large Language Models",
      },
    ],
  }),
})

// === Hooks ===
function useScrollY() {
  const [y, setY] = useState(0)
  useEffect(() => {
    const onScroll = () => setY(window.scrollY)
    window.addEventListener("scroll", onScroll, { passive: true })
    return () => window.removeEventListener("scroll", onScroll)
  }, [])
  return y
}

function useReveal<T extends HTMLElement>(threshold = 0.15) {
  // Use a state-backed callback ref instead of `useRef` so this hook keeps
  // working when the observed element mounts *later* than the hook itself —
  // e.g. a section that returns `null` while data loads, then renders content
  // after a query resolves. With `useRef` the effect only runs once on mount
  // (deps `[threshold]`), sees a null ref, and never re-attaches; with the
  // callback ref, `el` transitions null → element, which re-triggers the
  // effect and attaches the IntersectionObserver at the right moment.
  const [el, setEl] = useState<T | null>(null)
  const [visible, setVisible] = useState(false)
  useEffect(() => {
    if (!el) return
    const obs = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setVisible(true)
          obs.disconnect()
        }
      },
      { threshold },
    )
    obs.observe(el)
    return () => obs.disconnect()
  }, [el, threshold])
  // Wrap setEl in an arrow so JSX sees a plain callback ref instead of a
  // state setter — the setter's `SetStateAction` overload confuses React's
  // ref type inference.
  const ref = (node: T | null) => setEl(node)
  return { ref, visible }
}

// === Decorative components ===
function FloatingBlobs() {
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden -z-0">
      <div className="absolute top-[-10%] left-[-5%] size-[420px] rounded-full bg-primary/15 blur-3xl landing-float-slow" />
      <div
        className="absolute top-[20%] right-[-8%] size-[360px] rounded-full bg-primary/10 blur-3xl landing-float-slow"
        style={{ animationDelay: "-5s" }}
      />
      <div
        className="absolute bottom-[-10%] left-[30%] size-[480px] rounded-full bg-primary/8 blur-3xl landing-float-slow"
        style={{ animationDelay: "-9s" }}
      />
    </div>
  )
}

function OrbitalDecoration() {
  return (
    <div className="pointer-events-none absolute inset-0 flex items-center justify-center -z-0">
      <svg
        viewBox="0 0 600 600"
        className="w-[min(90vw,720px)] h-[min(90vw,720px)] opacity-70"
        aria-hidden="true"
      >
        <defs>
          <radialGradient id="orb-core" cx="50%" cy="50%" r="50%">
            <stop
              offset="0%"
              className="[stop-color:var(--primary)]"
              stopOpacity="0.5"
            />
            <stop
              offset="40%"
              className="[stop-color:var(--primary)]"
              stopOpacity="0.2"
            />
            <stop
              offset="80%"
              className="[stop-color:var(--primary)]"
              stopOpacity="0.05"
            />
            <stop
              offset="100%"
              className="[stop-color:var(--primary)]"
              stopOpacity="0"
            />
          </radialGradient>
          <linearGradient id="orb-stroke" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="currentColor" stopOpacity="0.35" />
            <stop offset="100%" stopColor="currentColor" stopOpacity="0.05" />
          </linearGradient>
        </defs>

        <circle
          cx="300"
          cy="300"
          r="120"
          fill="url(#orb-core)"
          className="text-primary landing-pulse-glow"
        />

        <g
          className="text-primary landing-spin-slow"
          style={{ transformOrigin: "300px 300px" }}
        >
          <ellipse
            cx="300"
            cy="300"
            rx="220"
            ry="90"
            fill="none"
            stroke="url(#orb-stroke)"
            strokeWidth="1.2"
          />
          <circle cx="520" cy="300" r="6" fill="currentColor" />
          <circle cx="300" cy="210" r="3" fill="currentColor" opacity="0.6" />
        </g>

        <g
          className="text-primary landing-spin-reverse"
          style={{ transformOrigin: "300px 300px", transform: "rotate(35deg)" }}
        >
          <ellipse
            cx="300"
            cy="300"
            rx="260"
            ry="120"
            fill="none"
            stroke="url(#orb-stroke)"
            strokeWidth="1"
          />
          <circle cx="40" cy="300" r="5" fill="currentColor" opacity="0.8" />
        </g>

        <g
          className="text-primary landing-spin-slow"
          style={{
            transformOrigin: "300px 300px",
            transform: "rotate(-25deg)",
            animationDuration: "55s",
          }}
        >
          <ellipse
            cx="300"
            cy="300"
            rx="180"
            ry="60"
            fill="none"
            stroke="url(#orb-stroke)"
            strokeWidth="0.8"
          />
          <circle cx="480" cy="300" r="4" fill="currentColor" opacity="0.7" />
        </g>
      </svg>
    </div>
  )
}

function NetworkDivider() {
  return (
    <div
      className="pointer-events-none relative h-24 max-w-7xl mx-auto px-4"
      aria-hidden="true"
    >
      <svg
        viewBox="0 0 1200 120"
        className="w-full h-full text-primary/40"
        preserveAspectRatio="none"
      >
        <title>Divider</title>
        <path
          d="M0,60 C200,20 400,100 600,60 C800,20 1000,100 1200,60"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.2"
          className="landing-draw-path"
        />
        {[100, 300, 500, 700, 900, 1100].map((cx, i) => (
          <circle
            key={cx}
            cx={cx}
            cy={60 + (i % 2 === 0 ? -8 : 8)}
            r="3.5"
            fill="currentColor"
            className="landing-pulse-glow"
            style={{ animationDelay: `${i * 0.4}s` }}
          />
        ))}
      </svg>
    </div>
  )
}

function GridGlow() {
  return (
    <div className="pointer-events-none absolute inset-0 -z-0 opacity-[0.18] dark:opacity-25">
      <svg width="100%" height="100%" aria-hidden="true">
        <defs>
          <pattern
            id="grid-glow"
            width="56"
            height="56"
            patternUnits="userSpaceOnUse"
          >
            <path
              d="M 56 0 L 0 0 0 56"
              fill="none"
              stroke="currentColor"
              strokeWidth="0.4"
            />
          </pattern>
          <radialGradient id="grid-fade" cx="50%" cy="40%" r="60%">
            <stop offset="0%" stopColor="white" stopOpacity="1" />
            <stop offset="100%" stopColor="white" stopOpacity="0" />
          </radialGradient>
          <mask id="grid-mask">
            <rect width="100%" height="100%" fill="url(#grid-fade)" />
          </mask>
        </defs>
        <rect
          width="100%"
          height="100%"
          fill="url(#grid-glow)"
          className="text-primary"
          mask="url(#grid-mask)"
        />
      </svg>
    </div>
  )
}

// === Sections ===
function Navbar() {
  const { t } = useTranslation()
  const [mobileOpen, setMobileOpen] = useState(false)
  const scrollY = useScrollY()
  const logged = isLoggedIn()
  const scrolled = scrollY > 12

  const navLinks = [
    { label: t("landing.nav.demo"), href: "#demo" },
    { label: t("landing.nav.features"), href: "#features" },
    { label: t("landing.nav.domains"), href: "#domains" },
    { label: t("landing.nav.achievements"), href: "#achievements" },
    { label: t("landing.nav.news"), href: "#news" },
  ]

  return (
    <nav
      className={`fixed top-0 left-0 right-0 z-50 backdrop-blur-xl border-b transition-all duration-300 ${
        scrolled
          ? "border-border/60 bg-background shadow-sm"
          : "border-transparent bg-background/60"
      }`}
    >
      <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
        <div className="flex h-16 items-center justify-between">
          <div className="flex items-center gap-3">
            <img
              src={icon}
              alt="OpenLoopX"
              className="h-8 w-auto landing-spin-periodic"
            />
            <span className="text-lg font-bold tracking-wider landing-gradient-animated">
              OpenLoopX
            </span>
          </div>

          <div className="hidden md:flex items-center gap-6">
            {navLinks.map((link) => (
              <a
                key={link.href}
                href={link.href}
                className="text-sm text-muted-foreground hover:text-primary transition-colors relative group"
              >
                {link.label}
                <span className="absolute -bottom-1 left-0 h-px w-0 bg-primary transition-all duration-300 group-hover:w-full" />
              </a>
            ))}
          </div>

          <div className="hidden md:flex items-center gap-2">
            <LanguageToggle />
            <ThemeToggle />
            <div className="w-px h-5 bg-border mx-1" />
            {logged ? (
              <Link to="/projects">
                <Button size="sm">
                  {t("landing.hero.cta")}
                  <ArrowRight className="ml-1 size-4" />
                </Button>
              </Link>
            ) : (
              <>
                <Link to="/login">
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-muted-foreground hover:text-foreground"
                  >
                    {t("landing.nav.login")}
                  </Button>
                </Link>
                <Link to="/signup">
                  <Button size="sm">{t("landing.nav.signup")}</Button>
                </Link>
              </>
            )}
          </div>

          <button
            type="button"
            className="md:hidden text-muted-foreground p-2"
            onClick={() => setMobileOpen(!mobileOpen)}
            aria-label={mobileOpen ? "Close menu" : "Open menu"}
            aria-expanded={mobileOpen}
          >
            {mobileOpen ? (
              <X className="size-5" />
            ) : (
              <Menu className="size-5" />
            )}
          </button>
        </div>
      </div>

      {/* Mobile menu */}
      <div
        className={`md:hidden border-t border-border/50 px-4 bg-background/95 backdrop-blur-xl overflow-hidden transition-all duration-300 ease-out ${
          mobileOpen ? "max-h-80 py-4 opacity-100" : "max-h-0 py-0 opacity-0"
        }`}
      >
        <div className="space-y-3">
          {navLinks.map((link) => (
            <a
              key={link.href}
              href={link.href}
              className="block text-sm text-muted-foreground hover:text-primary py-2"
              onClick={() => setMobileOpen(false)}
            >
              {link.label}
            </a>
          ))}
          <div className="flex items-center gap-2 pt-2 border-t border-border/50">
            <LanguageToggle />
            <ThemeToggle />
          </div>
          <div className="flex gap-2 pt-2">
            {logged ? (
              <Link to="/projects" className="flex-1">
                <Button size="sm" className="w-full">
                  {t("landing.hero.cta")}
                </Button>
              </Link>
            ) : (
              <>
                <Link to="/login" className="flex-1">
                  <Button variant="outline" size="sm" className="w-full">
                    {t("landing.nav.login")}
                  </Button>
                </Link>
                <Link to="/signup" className="flex-1">
                  <Button size="sm" className="w-full">
                    {t("landing.nav.signup")}
                  </Button>
                </Link>
              </>
            )}
          </div>
        </div>
      </div>
    </nav>
  )
}

function HeroSection() {
  const { t } = useTranslation()
  const logged = isLoggedIn()
  const scrollY = useScrollY()
  const [manualOpen, setManualOpen] = useState(false)

  const heroLift = scrollY * 0.25
  const heroOpacity = Math.max(0, 1 - scrollY / 600)

  return (
    <section className="relative min-h-screen flex items-center justify-center px-4 sm:px-6 lg:px-8 pt-16 overflow-hidden">
      <FloatingBlobs />
      <GridGlow />
      <OrbitalDecoration />

      <div
        className="relative z-10 max-w-4xl mx-auto text-center"
        style={{
          opacity: heroOpacity,
          transform: `translate3d(0, ${-heroLift}px, 0)`,
        }}
      >
        <h1
          className="text-3xl sm:text-5xl lg:text-7xl font-bold tracking-tight leading-[1.1] landing-reveal is-visible"
          style={{ transitionDelay: "0.15s" }}
        >
          <span className="text-foreground">{t("landing.hero.title")}</span>
          <br />
          <span className="landing-gradient-animated">
            {t("landing.hero.titleHighlight")}
          </span>
        </h1>

        <p
          className="mt-6 text-base sm:text-lg text-muted-foreground max-w-2xl mx-auto leading-relaxed landing-reveal is-visible"
          style={{ transitionDelay: "0.3s" }}
        >
          {t("landing.hero.description")}
        </p>

        <div
          className="mt-10 flex flex-col sm:flex-row items-center justify-center gap-4 landing-reveal is-visible"
          style={{ transitionDelay: "0.45s" }}
        >
          <Link to={logged ? "/projects" : "/login"}>
            <Button
              size="lg"
              className="min-w-44 px-8 py-6 text-base shadow-lg shadow-primary/30 hover:shadow-primary/50 hover:-translate-y-0.5 transition-all"
            >
              {t("landing.hero.cta")}
              <ArrowRight className="ml-2 size-5" />
            </Button>
          </Link>
          <Link to="/autoresearch">
            <Button
              variant="outline"
              size="lg"
              className="relative overflow-visible min-w-44 px-8 py-6 text-base border-primary/40 text-primary hover:bg-primary/10 hover:-translate-y-0.5 transition-all"
            >
              <Sparkles className="mr-2 size-5" />
              {t("landing.hero.autoresearch")}
              <span className="pointer-events-none absolute -top-2.5 -right-3 text-[9px] font-semibold px-1.5 py-0.5 rounded-full bg-amber-500/15 text-amber-600 dark:text-amber-400 border border-amber-500/30 leading-none">
                Beta
              </span>
            </Button>
          </Link>
          <Button
            variant="outline"
            size="lg"
            className="min-w-44 px-8 py-6 text-base hover:-translate-y-0.5 transition-all"
            onClick={() => setManualOpen(true)}
          >
            <BookOpen className="mr-2 size-5" />
            {t("landing.hero.docs")}
          </Button>
          <Button
            variant="outline"
            size="lg"
            className="min-w-44 px-8 py-6 text-base hover:-translate-y-0.5 transition-all"
            onClick={() =>
              window.open(
                "https://github.com/Optima-CityU/LLM4AD_Next",
                "_blank",
                "noopener,noreferrer",
              )
            }
          >
            <Github className="mr-2 size-5" />
            {t("landing.hero.github")}
          </Button>
        </div>
      </div>

      <div
        className="absolute bottom-8 left-1/2 -translate-x-1/2 flex flex-col items-center gap-2 text-muted-foreground/70 transition-opacity duration-500"
        style={{ opacity: Math.max(0, 1 - scrollY / 200) }}
      >
        <span className="text-[10px] tracking-[0.2em] uppercase">scroll</span>
        <div className="h-9 w-5 rounded-full border border-current/40 flex items-start justify-center p-1">
          <span className="block h-2 w-1 rounded-full bg-current landing-bounce-soft" />
        </div>
      </div>

      <UserManualDialog open={manualOpen} onOpenChange={setManualOpen} />
    </section>
  )
}

const DEMO_VIDEO = {
  zh: {
    src: "/assets/videos/llm4ad-intro-zh-v2-202606101810.mp4",
    poster: "/assets/videos/llm4ad-intro-zh-v2.webp",
    posterLight: "/assets/videos/llm4ad-intro-zh-v2_light.webp",
  },
  en: {
    src: "/assets/videos/llm4ad-intro-en-v2-202606101810.mp4",
    poster: "/assets/videos/llm4ad-intro-en-v2.webp",
    posterLight: "/assets/videos/llm4ad-intro-en-v2_light.webp",
  },
} as const

function DemoSection() {
  const { t, i18n } = useTranslation()
  const { resolvedTheme } = useTheme()
  const { ref, visible } = useReveal<HTMLDivElement>()
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const variant = i18n.language?.startsWith("zh")
    ? DEMO_VIDEO.zh
    : DEMO_VIDEO.en
  const videoSrc = variant.src
  // Swap to the light-mode cover so the poster matches the active theme.
  const posterSrc =
    resolvedTheme === "dark" ? variant.poster : variant.posterLight
  // Defer loading the (large) video file until the user explicitly starts
  // playback, so the landing page first paint stays fast.
  const [activated, setActivated] = useState(false)

  // Reset to the poster when the language switches so the newly selected
  // (localized) video is shown instead of a stale one.
  // biome-ignore lint/correctness/useExhaustiveDependencies: reset on language change only
  useEffect(() => {
    setActivated(false)
  }, [i18n.language])

  const handlePlay = () => {
    setActivated(true)
    // Wait for the <video> to mount before kicking off playback.
    requestAnimationFrame(() => {
      videoRef.current?.play().catch(() => {
        /* Autoplay may be blocked; native controls remain available. */
      })
    })
  }

  return (
    <section
      id="demo"
      ref={ref}
      className="relative z-10 py-24 px-4 sm:px-6 lg:px-8 scroll-mt-20"
    >
      <div className="max-w-5xl mx-auto">
        <div
          className={`text-center mb-12 landing-reveal ${visible ? "is-visible" : ""}`}
        >
          <h2 className="text-2xl sm:text-3xl lg:text-4xl font-bold text-foreground">
            {t("landing.demo.title")}
          </h2>
          <p className="mt-4 text-muted-foreground max-w-2xl mx-auto">
            {t("landing.demo.subtitle")}
          </p>
        </div>

        <div
          className={`group relative aspect-video w-full overflow-hidden rounded-2xl border border-border/60 bg-card/60 shadow-lg shadow-primary/5 backdrop-blur-xl landing-reveal ${visible ? "is-visible" : ""}`}
          style={{ transitionDelay: "0.15s" }}
        >
          {activated ? (
            // Pure-narration intro; no caption track exists yet.
            // biome-ignore lint/a11y/useMediaCaption: no captions available
            <video
              ref={videoRef}
              className="absolute inset-0 size-full bg-black"
              src={videoSrc}
              poster={posterSrc}
              controls
              controlsList="nodownload"
              playsInline
              preload="auto"
              onEnded={() => setActivated(false)}
            />
          ) : (
            <button
              type="button"
              onClick={handlePlay}
              aria-label={t("landing.demo.play")}
              className="absolute inset-0 flex size-full flex-col items-center justify-center gap-5 bg-cover bg-center"
              style={{ backgroundImage: `url(${posterSrc})` }}
            >
              {/* Scrim: darkens the cover and lifts on hover for affordance.
                  Skipped in light mode so the lighter cover stays vivid. */}
              {resolvedTheme === "dark" && (
                <span className="absolute inset-0 bg-gradient-to-t from-black/60 via-black/25 to-black/40 transition-colors duration-300 group-hover:from-black/70 group-hover:via-black/35" />
              )}

              {/* Play button: rippling ring + transparent disc with white border. */}
              <span className="relative flex items-center justify-center">
                <span className="absolute size-20 rounded-full border-2 border-white/60 landing-play-ripple" />
                <span className="relative flex size-20 items-center justify-center rounded-full border-2 border-white/80 bg-white/5 backdrop-blur-sm shadow-lg transition-all duration-300 group-hover:scale-110 group-hover:border-white group-hover:bg-white/15">
                  <Play className="size-8 translate-x-0.5 fill-white text-white drop-shadow" />
                </span>
              </span>

              <span className="relative text-sm font-medium text-white/95 tracking-wide drop-shadow-lg">
                {t("landing.demo.play")}
              </span>
            </button>
          )}
        </div>
      </div>
    </section>
  )
}

function FeaturesSection() {
  const { t } = useTranslation()
  const { ref, visible } = useReveal<HTMLDivElement>()

  const features = [
    {
      icon: Combine,
      title: t("landing.features.collaboration.title"),
      description: t("landing.features.collaboration.description"),
    },
    {
      icon: Layers,
      title: t("landing.features.engines.title"),
      description: t("landing.features.engines.description"),
    },
    {
      icon: MessageSquareText,
      title: t("landing.features.conversational.title"),
      description: t("landing.features.conversational.description"),
    },
    {
      icon: Workflow,
      title: t("landing.features.multiStage.title"),
      description: t("landing.features.multiStage.description"),
    },
    {
      icon: Cpu,
      title: t("landing.features.llmSupport.title"),
      description: t("landing.features.llmSupport.description"),
    },
    {
      icon: Images,
      title: t("landing.features.multimodal.title"),
      description: t("landing.features.multimodal.description"),
    },
    {
      icon: Network,
      title: t("landing.features.multiDistribution.title"),
      description: t("landing.features.multiDistribution.description"),
    },
    {
      icon: LineChart,
      title: t("landing.features.visualization.title"),
      description: t("landing.features.visualization.description"),
    },
  ]

  return (
    <section
      id="features"
      ref={ref}
      className="relative z-10 py-24 px-4 sm:px-6 lg:px-8"
    >
      <div className="max-w-6xl mx-auto">
        <div
          className={`text-center mb-16 landing-reveal ${visible ? "is-visible" : ""}`}
        >
          <h2 className="text-2xl sm:text-3xl lg:text-4xl font-bold text-foreground">
            {t("landing.features.title")}
          </h2>
          <p className="mt-4 text-muted-foreground max-w-2xl mx-auto">
            {t("landing.features.subtitle")}
          </p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
          {features.map((feature, i) => (
            <div
              key={feature.title}
              className={`group landing-card-shimmer rounded-2xl p-6 border border-border/60 hover:border-primary/40 bg-card/60 backdrop-blur-xl shadow-sm hover:shadow-lg hover:shadow-primary/10 hover:-translate-y-1 transition-all duration-500 landing-reveal ${visible ? "is-visible" : ""}`}
              style={{ transitionDelay: `${0.1 + i * 0.1}s` }}
            >
              <div className="size-12 rounded-xl flex items-center justify-center mb-4 bg-primary/10 border border-primary/20 group-hover:bg-primary/20 group-hover:scale-110 transition-all duration-300">
                <feature.icon className="size-6 text-primary group-hover:rotate-6 transition-transform" />
              </div>
              <h3 className="text-lg font-semibold text-foreground mb-2 group-hover:text-primary transition-colors">
                {feature.title}
              </h3>
              <p className="text-sm text-muted-foreground leading-relaxed">
                {feature.description}
              </p>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}

function DomainsSection() {
  const { t } = useTranslation()
  const { ref, visible } = useReveal<HTMLDivElement>()

  const domains = [
    {
      icon: Box,
      name: t("landing.domains.optimization"),
      desc: t("landing.domains.optimizationDesc"),
    },
    {
      icon: Brain,
      name: t("landing.domains.ml"),
      desc: t("landing.domains.mlDesc"),
    },
    {
      icon: FlaskConical,
      name: t("landing.domains.science"),
      desc: t("landing.domains.scienceDesc"),
    },
    {
      icon: Gamepad2,
      name: t("landing.domains.gameTheory"),
      desc: t("landing.domains.gameTheoryDesc"),
    },
    {
      icon: Wrench,
      name: t("landing.domains.engineering"),
      desc: t("landing.domains.engineeringDesc"),
    },
    {
      icon: Sigma,
      name: t("landing.domains.modeling"),
      desc: t("landing.domains.modelingDesc"),
    },
  ]

  return (
    <section
      id="domains"
      ref={ref}
      className="relative z-10 py-24 px-4 sm:px-6 lg:px-8"
    >
      <div className="max-w-6xl mx-auto">
        <div
          className={`text-center mb-16 landing-reveal ${visible ? "is-visible" : ""}`}
        >
          <h2 className="text-2xl sm:text-3xl lg:text-4xl font-bold text-foreground">
            {t("landing.domains.title")}
          </h2>
          <p className="mt-4 text-muted-foreground max-w-2xl mx-auto">
            {t("landing.domains.subtitle")}
          </p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {domains.map((domain, i) => (
            <div
              key={domain.name}
              className={`group flex items-start gap-4 rounded-xl p-5 border border-border/60 hover:border-primary/30 bg-card/40 backdrop-blur-sm hover:bg-card/70 hover:-translate-y-0.5 transition-all duration-500 landing-reveal ${visible ? "is-visible" : ""}`}
              style={{ transitionDelay: `${0.08 * i}s` }}
            >
              <div className="shrink-0 size-10 rounded-lg flex items-center justify-center bg-primary/10 border border-primary/15 group-hover:bg-primary/20 group-hover:rotate-3 transition-all">
                <domain.icon className="size-5 text-primary" />
              </div>
              <div className="min-w-0 flex-1">
                <h3 className="font-semibold text-foreground mb-1 group-hover:text-primary transition-colors truncate">
                  {domain.name}
                </h3>
                <p className="text-sm text-muted-foreground leading-relaxed line-clamp-2">
                  {domain.desc}
                </p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}

function AchievementsSection() {
  const { t } = useTranslation()
  const { ref, visible } = useReveal<HTMLDivElement>()

  return (
    <section
      id="achievements"
      ref={ref}
      className="relative z-10 py-24 px-4 sm:px-6 lg:px-8"
    >
      <div className="max-w-6xl mx-auto">
        <div
          className={`text-center mb-16 landing-reveal ${visible ? "is-visible" : ""}`}
        >
          <h2 className="text-2xl sm:text-3xl lg:text-4xl font-bold text-foreground">
            {t("landing.achievements.title")}
          </h2>
          {t("landing.achievements.subtitle") && (
            <p className="mt-4 text-muted-foreground max-w-2xl mx-auto">
              {t("landing.achievements.subtitle")}
            </p>
          )}
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
          {[
            {
              icon: Trophy,
              title: t("landing.achievements.cvrp.title"),
              desc: t("landing.achievements.cvrp.description"),
              link: t("landing.achievements.cvrp.link"),
              linkLabel: t("landing.achievements.cvrp.linkLabel"),
              badge: true,
            },
            {
              icon: GraduationCap,
              title: t("landing.achievements.survey.title"),
              desc: t("landing.achievements.survey.description"),
              link: t("landing.achievements.survey.link"),
              linkLabel: t("landing.achievements.survey.linkLabel"),
              badge: false,
            },
          ].map((item, i) => (
            <div
              key={item.title}
              className={`group landing-card-shimmer rounded-2xl p-6 border border-primary/15 bg-gradient-to-br from-primary/5 via-primary/[0.03] to-transparent hover:border-primary/30 hover:-translate-y-1 transition-all duration-500 landing-reveal ${visible ? "is-visible" : ""}`}
              style={{ transitionDelay: `${0.2 + i * 0.15}s` }}
            >
              <div className="flex items-center gap-3 mb-3">
                <div className="relative size-10 rounded-lg flex items-center justify-center bg-primary/10 border border-primary/20 group-hover:scale-110 transition-transform">
                  <item.icon className="size-5 text-primary" />
                  {item.badge && (
                    <Crown className="absolute -top-2.5 -right-2 size-4 rotate-12 text-primary fill-primary drop-shadow" />
                  )}
                </div>
                <h3 className="text-lg font-semibold text-foreground">
                  {item.title}
                </h3>
              </div>
              <p className="text-sm text-muted-foreground leading-relaxed">
                {item.desc}
              </p>
              {item.link && (
                <a
                  href={item.link}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="mt-4 inline-flex items-center gap-1.5 text-sm font-medium text-primary hover:underline"
                >
                  {item.linkLabel}
                  <ExternalLink className="size-3.5" />
                </a>
              )}
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}

/**
 * Format a wiki publish-date string for display.
 *
 * Wiki dates come through as raw strings — anything from `"2026年7月6日"` to
 * `"Jul 6, 2026"` to `"2026-07-06"`. If `Date` can parse it, pretty-print in
 * the active locale; otherwise fall through and show the wiki's original text.
 * Empty input renders as empty (caller hides the meta cell).
 */
function formatNewsDate(raw: string | null | undefined, language: string) {
  if (!raw) return ""
  const locale = language.startsWith("zh") ? "zh-CN" : "en-US"
  const parsed = new Date(raw)
  if (Number.isNaN(parsed.getTime())) return raw
  return parsed.toLocaleDateString(locale, {
    year: "numeric",
    month: "short",
    day: "numeric",
  })
}

// Landing shows a teaser only — cap items and link the "see more" affordance to
// the wiki index page, which is the canonical archive. One URL per UI language.
const MAX_NEWS_ITEMS = 4
const NEWS_INDEX_URL: Record<"zh" | "en", string> = {
  zh: "https://github.com/Optima-CityU/LLM4AD_Next/wiki/News%E2%80%90and%E2%80%90Articles%E2%80%90Index_zh",
  en: "https://github.com/Optima-CityU/LLM4AD_Next/wiki/News%E2%80%90and%E2%80%90Articles%E2%80%90Index_en",
}

function NewsSection() {
  const { t, i18n } = useTranslation()
  const { ref, visible } = useReveal<HTMLDivElement>()
  const lang: "zh" | "en" = i18n.language?.startsWith("zh") ? "zh" : "en"

  // Fetch the live feed for the current UI language. Query key includes `lang`
  // so a language switch re-fetches from the correct wiki source. Cached data
  // is treated as fresh for 5 min to keep the section snappy.
  const { data } = useQuery({
    queryKey: ["landing-news", lang],
    queryFn: () => NewsService.listNews({ lang }),
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
  })

  const allItems = data?.items ?? []
  // Backend order is preserved (newest first per wiki convention); the landing
  // shows only the first MAX_NEWS_ITEMS and links to the wiki index for the rest.
  const items = allItems.slice(0, MAX_NEWS_ITEMS)
  const hasMore = allItems.length > MAX_NEWS_ITEMS

  // Hide the section entirely when the backend returns nothing (language not
  // configured / wiki unreachable / empty wiki) — avoids a lone heading.
  if (items.length === 0) return null

  return (
    <section
      id="news"
      ref={ref}
      className="relative z-10 py-24 px-4 sm:px-6 lg:px-8 scroll-mt-20"
    >
      <div className="max-w-6xl mx-auto">
        <div
          className={`text-center mb-16 landing-reveal ${visible ? "is-visible" : ""}`}
        >
          <h2 className="text-2xl sm:text-3xl lg:text-4xl font-bold text-foreground">
            {t("landing.news.title")}
          </h2>
          <p className="mt-4 text-muted-foreground max-w-2xl mx-auto">
            {t("landing.news.subtitle")}
          </p>
        </div>

        {/* 2-column card grid matching the Achievements section, so the two
            adjacent sections read as one visual system. */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
          {items.map((article, i) => {
            const date = formatNewsDate(
              article.published_at,
              i18n.language ?? "en",
            )
            // Top meta row: tag badge + source (both are article-category info,
            // grouped together). Date + "read more" live in the bottom row so
            // the card foot reads as a single publication footer. Hide either
            // row entirely when it has no content to render.
            const hasTopMeta = Boolean(article.tag || article.source)
            return (
              <a
                key={article.url}
                href={article.url}
                target="_blank"
                rel="noopener noreferrer"
                className={`group landing-card-shimmer flex flex-col rounded-2xl p-6 border border-primary/15 bg-gradient-to-br from-primary/5 via-primary/[0.03] to-transparent hover:border-primary/30 hover:-translate-y-1 transition-all duration-500 landing-reveal ${visible ? "is-visible" : ""}`}
                style={{ transitionDelay: `${0.15 + i * 0.1}s` }}
              >
                {hasTopMeta && (
                  <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground mb-3">
                    {article.tag && (
                      <span className="inline-flex items-center rounded-full border px-2.5 py-0.5 font-medium bg-primary/10 text-primary border-primary/20">
                        {article.tag}
                      </span>
                    )}
                    {article.source && (
                      <span className="inline-flex items-center gap-1 min-w-0 truncate">
                        <Newspaper className="size-3.5 shrink-0" />
                        <span className="truncate">{article.source}</span>
                      </span>
                    )}
                  </div>
                )}

                <h3 className="text-lg font-semibold text-foreground leading-snug group-hover:text-primary transition-colors line-clamp-2">
                  {article.title}
                </h3>

                {article.summary && (
                  <p className="mt-3 text-sm text-muted-foreground leading-relaxed line-clamp-3">
                    {article.summary}
                  </p>
                )}

                {/* Bottom row: "Read full article" on the left, date (if
                    provided) on the right. `mt-auto` pins it to the card
                    footer regardless of summary length so cards align. */}
                <div className="mt-auto pt-4 flex items-center justify-between gap-3">
                  <span className="inline-flex items-center gap-1.5 text-sm font-medium text-primary">
                    {t("landing.news.readMore")}
                    <ExternalLink className="size-3.5" />
                  </span>
                  {date && (
                    <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                      <CalendarDays className="size-3.5" />
                      {date}
                    </span>
                  )}
                </div>
              </a>
            )
          })}
        </div>

        {/* "See more" CTA — only when the backend has more than we chose to
            show. Sends users to the wiki index page for the archive. */}
        {hasMore && (
          <div
            className={`mt-10 flex justify-center landing-reveal ${visible ? "is-visible" : ""}`}
            style={{ transitionDelay: `${0.15 + items.length * 0.1}s` }}
          >
            <a
              href={NEWS_INDEX_URL[lang]}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 rounded-full border border-primary/30 bg-primary/5 px-5 py-2.5 text-sm font-medium text-primary hover:bg-primary/10 hover:border-primary/50 hover:-translate-y-0.5 transition-all"
            >
              {t("landing.news.viewMore")}
              <ArrowUpRight className="size-4" />
            </a>
          </div>
        )}
      </div>
    </section>
  )
}

function LandingFooter() {
  const { t } = useTranslation()
  const [manualOpen, setManualOpen] = useState(false)
  const [contactOpen, setContactOpen] = useState(false)

  return (
    <footer className="relative z-10 border-t border-border/50 py-10 px-4 sm:px-6 lg:px-8">
      <div className="max-w-7xl mx-auto">
        <div className="flex flex-col sm:flex-row items-start justify-between gap-8">
          <div className="flex flex-col gap-2">
            <div className="flex items-center gap-3">
              <img src={icon} alt="OpenLoopX" className="h-6 w-auto" />
              <span className="text-sm font-medium text-foreground">
                OpenLoopX
              </span>
            </div>
            <p className="text-xs text-muted-foreground/70 whitespace-nowrap">
              {t("landing.footer.copyright")}
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              className="flex items-center gap-1.5 rounded-lg px-2.5 py-2 text-xs text-muted-foreground hover:text-primary hover:bg-primary/5 transition-colors whitespace-nowrap"
              onClick={() => setManualOpen(true)}
            >
              <BookOpen className="size-3.5" />
              {t("sidebar.userManual")}
            </button>
            <button
              type="button"
              className="flex items-center gap-1.5 rounded-lg px-2.5 py-2 text-xs text-muted-foreground hover:text-primary hover:bg-primary/5 transition-colors whitespace-nowrap"
              onClick={() => setContactOpen(true)}
            >
              <Mail className="size-3.5" />
              {t("common.contactUs")}
            </button>
          </div>
        </div>

        <div className="mt-8 pt-4 border-t border-border/30 flex flex-col sm:flex-row items-center justify-between gap-2">
          <p className="text-xs text-muted-foreground/50">
            &copy; {new Date().getFullYear()} OpenLoopX Team. All rights
            reserved.
          </p>
          <div className="flex flex-col items-center gap-1 sm:items-end">
            <FooterMetadataLinks />
            {t("landing.footer.team") && (
              <p className="text-xs text-muted-foreground/50">
                {t("landing.footer.team")}
              </p>
            )}
          </div>
        </div>
      </div>
      <UserManualDialog open={manualOpen} onOpenChange={setManualOpen} />
      <ContactUsDialog open={contactOpen} onOpenChange={setContactOpen} />
    </footer>
  )
}

function LandingPage() {
  const { resolvedTheme } = useTheme()
  const isDark = resolvedTheme === "dark"

  return (
    <div className="relative min-h-screen overflow-x-hidden bg-background">
      <IslandBackground variant={isDark ? "dark" : "light"} />
      <Navbar />
      <HeroSection />
      <DemoSection />
      <FeaturesSection />
      <NetworkDivider />
      <DomainsSection />
      <AchievementsSection />
      <NewsSection />
      <LandingFooter />
    </div>
  )
}
