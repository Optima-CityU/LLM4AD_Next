import i18n from "i18next"
import { initReactI18next } from "react-i18next"

import en from "./locales/en.json"
import zh from "./locales/zh.json"

const STORAGE_KEY = "llm4ad-language"

const detectLang = (): "zh" | "en" => {
  const saved = localStorage.getItem(STORAGE_KEY) as "zh" | "en" | null
  if (saved) return saved
  // First visit: use browser language. Chinese -> zh, otherwise -> en.
  const browserLang = typeof navigator !== "undefined" ? navigator.language : ""
  return browserLang.toLowerCase().startsWith("zh") ? "zh" : "en"
}

const savedLang = detectLang()

i18n.use(initReactI18next).init({
  resources: {
    zh: { translation: zh },
    en: { translation: en },
  },
  lng: savedLang,
  fallbackLng: "zh",
  interpolation: {
    escapeValue: false,
  },
})

export default i18n
