import { Link } from "@tanstack/react-router"

import { cn } from "@/lib/utils"
import icon from "/assets/images/logo.svg"
import logo from "/assets/images/logo.svg"

import "./Logo.css"

interface LogoProps {
  variant?: "full" | "icon" | "responsive"
  className?: string
  asLink?: boolean
  to?: string
}

function LogoText() {
  return (
    <svg
      viewBox="0 0 200 32"
      className="logo-text-svg h-6 w-auto shrink-0 group-data-[collapsible=icon]:hidden"
      aria-label="OpenLoopX"
    >
      <defs>
        {/* Animated gradient fill */}
        <linearGradient id="logo-grad" x1="0%" y1="0%" x2="100%" y2="0%">
          <stop offset="0%" stopColor="#60efff">
            <animate
              attributeName="stop-color"
              values="#60efff;#00d4ff;#0088ff;#60efff"
              dur="4s"
              repeatCount="indefinite"
            />
          </stop>
          <stop offset="50%" stopColor="#00d4ff">
            <animate
              attributeName="stop-color"
              values="#00d4ff;#0088ff;#60efff;#00d4ff"
              dur="4s"
              repeatCount="indefinite"
            />
          </stop>
          <stop offset="100%" stopColor="#0088ff">
            <animate
              attributeName="stop-color"
              values="#0088ff;#60efff;#00d4ff;#0088ff"
              dur="4s"
              repeatCount="indefinite"
            />
          </stop>
        </linearGradient>

        {/* Glow filter */}
        <filter id="logo-glow" x="-20%" y="-40%" width="140%" height="180%">
          <feGaussianBlur in="SourceGraphic" stdDeviation="2" result="blur" />
          <feColorMatrix
            in="blur"
            type="matrix"
            values="0 0 0 0 0  0 0.83 0 0 0  0 0 1 0 0  0 0 0 0.6 0"
            result="glow"
          />
          <feMerge>
            <feMergeNode in="glow" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>

      {/* Main text — bold, heavy weight */}
      <text
        x="0"
        y="24"
        fontFamily="'Inter', 'SF Pro Display', -apple-system, system-ui, sans-serif"
        fontSize="22"
        fontWeight="600"
        letterSpacing="0.5"
        fill="url(#logo-grad)"
        filter="url(#logo-glow)"
      >
        OpenLoopX
      </text>
    </svg>
  )
}

export function Logo({
  variant = "full",
  className,
  asLink = true,
  to = "/",
}: LogoProps) {
  const content =
    variant === "responsive" ? (
      <>
        <div className="flex items-center gap-3">
          <img
            src={logo}
            alt="OpenLoopX"
            className={cn(
              "h-14 w-auto group-data-[collapsible=icon]:hidden",
              className,
            )}
          />
          <LogoText />
        </div>
        <img
          src={icon}
          alt="OpenLoopX"
          className={cn(
            "size-5 hidden group-data-[collapsible=icon]:block",
            className,
          )}
        />
      </>
    ) : (
      <img
        src={variant === "full" ? logo : icon}
        alt="OpenLoopX"
        className={cn(variant === "full" ? "h-6 w-auto" : "size-5", className)}
      />
    )

  if (!asLink) {
    return content
  }

  return <Link to={to}>{content}</Link>
}
