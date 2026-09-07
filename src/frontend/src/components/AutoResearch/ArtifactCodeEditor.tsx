import Editor, { loader } from "@monaco-editor/react"
import * as monaco from "monaco-editor"

import { useTheme } from "@/components/theme-provider"
import { getLanguageFromPath } from "@/components/Evolution/TaskDetail/steps/EvolveAnnotationStep/types"
import { cn } from "@/lib/utils"

loader.config({ monaco })

interface Props {
  filePath: string
  value: string
  readOnly?: boolean
  onChange?: (value: string) => void
  className?: string
}

export default function ArtifactCodeEditor({
  filePath,
  value,
  readOnly,
  onChange,
  className,
}: Props) {
  const { resolvedTheme } = useTheme()

  return (
    <div className={cn("h-full min-h-[300px]", className)}>
      <Editor
        height="100%"
        language={getLanguageFromPath(filePath)}
        value={value}
        onChange={(next) => onChange?.(next ?? "")}
        theme={resolvedTheme !== "light" ? "custom-dark" : "custom-light"}
        beforeMount={(m) => {
          m.editor.defineTheme("custom-dark", {
            base: "vs-dark",
            inherit: true,
            rules: [],
            colors: { "editor.background": "#0D1A2D" },
          })
          m.editor.defineTheme("custom-light", {
            base: "vs",
            inherit: true,
            rules: [],
            colors: { "editor.background": "#ffffff" },
          })
        }}
        options={{
          minimap: { enabled: false },
          fontSize: 13,
          lineNumbers: "on",
          scrollBeyondLastLine: false,
          wordWrap: "on",
          automaticLayout: true,
          tabSize: 2,
          readOnly,
        }}
      />
    </div>
  )
}
