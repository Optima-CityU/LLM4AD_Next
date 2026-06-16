import { useEffect, useRef } from "react"
import { getPrimaryRGB } from "@/utils/theme-colors"

interface Particle {
  x: number
  y: number
  vx: number
  vy: number
  radius: number
  opacity: number
}

const PARTICLE_COUNT = 40
const CONNECTION_DISTANCE = 120
const CONNECTION_DIST_SQ = CONNECTION_DISTANCE * CONNECTION_DISTANCE

function createParticles(w: number, h: number): Particle[] {
  return Array.from({ length: PARTICLE_COUNT }, () => ({
    x: Math.random() * w,
    y: Math.random() * h,
    vx: (Math.random() - 0.5) * 0.3,
    vy: (Math.random() - 0.5) * 0.3,
    radius: 1 + Math.random() * 1.5,
    opacity: 0.3 + Math.random() * 0.4,
  }))
}

export default function TechBackground() {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const colorRef = useRef(getPrimaryRGB())

  const isDarkRef = useRef(document.documentElement.classList.contains("dark"))

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext("2d")
    if (!ctx) return

    let animId = 0
    let particles: Particle[] = []
    let w = 0
    let h = 0

    const resize = () => {
      const dpr = window.devicePixelRatio || 1
      w = window.innerWidth
      h = window.innerHeight
      canvas.width = w * dpr
      canvas.height = h * dpr
      canvas.style.width = `${w}px`
      canvas.style.height = `${h}px`
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      particles = createParticles(w, h)
    }

    const draw = () => {
      const COLOR = colorRef.current
      const opacityScale = isDarkRef.current ? 1 : 0.3
      ctx.clearRect(0, 0, w, h)

      for (let i = 0; i < particles.length; i++) {
        const p = particles[i]
        p.x += p.vx
        p.y += p.vy
        if (p.x < 0) p.x = w
        else if (p.x > w) p.x = 0
        if (p.y < 0) p.y = h
        else if (p.y > h) p.y = 0

        ctx.beginPath()
        ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2)
        ctx.fillStyle = `rgba(${COLOR},${p.opacity * 0.5 * opacityScale})`
        ctx.fill()
      }

      ctx.lineWidth = 0.5
      for (let i = 0; i < particles.length; i++) {
        for (let j = i + 1; j < particles.length; j++) {
          const dx = particles[i].x - particles[j].x
          const dy = particles[i].y - particles[j].y
          const distSq = dx * dx + dy * dy
          if (distSq < CONNECTION_DIST_SQ) {
            const alpha =
              (1 - Math.sqrt(distSq) / CONNECTION_DISTANCE) *
              0.08 *
              opacityScale
            ctx.beginPath()
            ctx.moveTo(particles[i].x, particles[i].y)
            ctx.lineTo(particles[j].x, particles[j].y)
            ctx.strokeStyle = `rgba(${COLOR},${alpha})`
            ctx.stroke()
          }
        }
      }

      animId = requestAnimationFrame(draw)
    }

    const updateColor = () => {
      colorRef.current = getPrimaryRGB()
      isDarkRef.current = document.documentElement.classList.contains("dark")
    }

    const observer = new MutationObserver(updateColor)
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    })

    resize()
    animId = requestAnimationFrame(draw)
    window.addEventListener("resize", resize)

    return () => {
      cancelAnimationFrame(animId)
      window.removeEventListener("resize", resize)
      observer.disconnect()
    }
  }, [])

  return (
    <canvas ref={canvasRef} className="pointer-events-none fixed inset-0 z-0" />
  )
}
