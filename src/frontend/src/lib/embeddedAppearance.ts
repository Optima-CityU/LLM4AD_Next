const HEX_COLOR = /^#([\da-f]{3}|[\da-f]{6})$/i
const RGB_COLOR =
  /^rgba?\(\s*([\d.]+)\s*[, ]\s*([\d.]+)\s*[, ]\s*([\d.]+)(?:\s*[,/]\s*[\d.]+%?)?\s*\)$/i

function formatChannel(value: number): string {
  return String(Number(value.toFixed(3)))
}

function parseRgb(value: string): [number, number, number] | null {
  const normalized = value.trim()
  const hexMatch = normalized.match(HEX_COLOR)
  if (hexMatch) {
    const raw = hexMatch[1]
    const expanded =
      raw.length === 3
        ? raw
            .split("")
            .map((channel) => `${channel}${channel}`)
            .join("")
        : raw
    return [0, 2, 4].map((offset) =>
      Number.parseInt(expanded.slice(offset, offset + 2), 16),
    ) as [number, number, number]
  }

  const rgbMatch = normalized.match(RGB_COLOR)
  if (!rgbMatch) return null
  const channels = rgbMatch.slice(1, 4).map(Number)
  if (channels.some((channel) => channel < 0 || channel > 255)) return null
  return channels as [number, number, number]
}

/** Convert a CSS hex or RGB color into the HSL channels expected by CloudCLI. */
export function cssColorToHslChannels(value: string): string | null {
  const rgb = parseRgb(value)
  if (!rgb) return null

  const [red, green, blue] = rgb.map((channel) => channel / 255)
  const maximum = Math.max(red, green, blue)
  const minimum = Math.min(red, green, blue)
  const delta = maximum - minimum
  const lightness = (maximum + minimum) / 2
  let hue = 0

  if (delta > 0) {
    if (maximum === red) hue = ((green - blue) / delta) % 6
    else if (maximum === green) hue = (blue - red) / delta + 2
    else hue = (red - green) / delta + 4
    hue = (hue * 60 + 360) % 360
  }

  const saturation = delta === 0 ? 0 : delta / (1 - Math.abs(2 * lightness - 1))
  return `${formatChannel(hue)} ${formatChannel(saturation * 100)}% ${formatChannel(lightness * 100)}%`
}
