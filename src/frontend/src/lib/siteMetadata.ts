export const GITHUB_PROJECT_URL = "https://github.com/Optima-CityU/LLM4AD_Next"
export const GITHUB_ISSUES_URL = `${GITHUB_PROJECT_URL}/issues/new/choose`
export const ICP_BEIAN_NUMBER = "粤ICP备2026092246号"
export const ICP_BEIAN_URL = "https://beian.miit.gov.cn"

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
