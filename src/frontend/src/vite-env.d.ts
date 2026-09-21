/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_APP_VERSION?: string
  readonly VITE_AUTODISCOVERY_ENABLED?: string
  readonly VITE_AUTOREBUTTAL_ENABLED?: string
  readonly VITE_FOOTER_BEIAN?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
