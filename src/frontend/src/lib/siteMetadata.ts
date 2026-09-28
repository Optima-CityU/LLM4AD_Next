export const GITHUB_PROJECT_URL =
  "https://github.com/Optima-CityU/LLM4AD_Next"
export const GITHUB_ISSUES_URL = `${GITHUB_PROJECT_URL}/issues/new/choose`
/** AutoResearchClaw 官方仓里 ARC-Bench 课题模板（experiments/arc_bench/config）的源码位置。 */
export const AUTORESEARCHCLAW_TEMPLATE_SOURCE_URL =
  "https://github.com/aiming-lab/AutoResearchClaw/tree/main/experiments/arc_bench/config"

type SiteEnvironment = Partial<{
  VITE_APP_VERSION: string
  VITE_FOOTER_BEIAN: string
}>

export function getSiteMetadata(environment: SiteEnvironment) {
  const version = environment.VITE_APP_VERSION?.trim() || "develop"
  const beian = environment.VITE_FOOTER_BEIAN?.trim() || undefined

  return { version, beian }
}

export const siteMetadata = getSiteMetadata(import.meta.env)
