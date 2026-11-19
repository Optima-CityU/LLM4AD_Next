import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import { createRoot } from "react-dom/client"
import { I18nextProvider } from "react-i18next"
import { ThemeProvider, useTheme } from "../src/components/theme-provider"
import type { PaperVersionHistoryProps } from "../src/components/Paper/PaperVersionHistory"
import PaperVersionHistory from "../src/components/Paper/PaperVersionHistory"
import i18n from "../src/i18n"
import "../src/index.css"

const client = new QueryClient({
  defaultOptions: { queries: { retry: false } },
})
function ThemeControls() {
  const { setTheme } = useTheme()
  return (
    <button type="button" onClick={() => setTheme("dark")}>
      Dark theme
    </button>
  )
}
function Fixture() {
  const [data, setData] =
    useState<Omit<PaperVersionHistoryProps, "onChanged">>()
  const [changes, setChanges] = useState(0)
  useEffect(() => {
    void fetch("/__history_state__")
      .then((response) => response.json())
      .then(setData)
  }, [changes])
  return (
    <I18nextProvider i18n={i18n}>
      <ThemeProvider
        attribute="class"
        defaultTheme="light"
        enableSystem={false}
      >
        <main className="space-y-4 p-5">
          <h1 className="text-xl">Research workspace history</h1>
          <ThemeControls />
          <output data-testid="changes">{changes}</output>
          <button
            type="button"
            onClick={() =>
              setData(
                (current) =>
                  current && {
                    ...current,
                    activeSource: current.activeSource && {
                      ...current.activeSource,
                      id: "source-new",
                      content_hash: "new-hash",
                    },
                  },
              )
            }
          >
            Advance current source
          </button>
          {data && (
            <PaperVersionHistory
              {...data}
              onChanged={() => setChanges((value) => value + 1)}
            />
          )}
        </main>
      </ThemeProvider>
    </I18nextProvider>
  )
}
createRoot(document.getElementById("root")!).render(
  <QueryClientProvider client={client}>
    <Fixture />
  </QueryClientProvider>,
)
