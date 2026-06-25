/// <reference types="vite/client" />

declare module "*.md?raw" {
  const content: string
  export default content
}

interface ImportMetaEnv {
  readonly VITE_API_URL: string
  readonly VITE_PLAUSIBLE_ENABLED?: string
  readonly VITE_PLAUSIBLE_DOMAIN?: string
  readonly VITE_PLAUSIBLE_ENDPOINT?: string
  readonly VITE_PLAUSIBLE_AUTO_CAPTURE_PAGEVIEWS?: string
  readonly VITE_PLAUSIBLE_HASH_BASED_ROUTING?: string
  readonly VITE_PLAUSIBLE_OUTBOUND_LINKS?: string
  readonly VITE_PLAUSIBLE_FILE_DOWNLOADS?: string
  readonly VITE_PLAUSIBLE_FORM_SUBMISSIONS?: string
  readonly VITE_PLAUSIBLE_CAPTURE_ON_LOCALHOST?: string
  readonly VITE_PLAUSIBLE_LOGGING?: string
  readonly VITE_PLAUSIBLE_BIND_TO_WINDOW?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
