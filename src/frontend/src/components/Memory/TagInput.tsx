import { X } from "lucide-react"
import { type KeyboardEvent, useState } from "react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"

function normalizeTag(value: string) {
  return value.trim().replace(/\s+/g, " ")
}

function addTagValue(tags: string[], rawValue: string) {
  const tag = normalizeTag(rawValue)
  if (!tag) return tags
  const exists = tags.some((item) => item.toLowerCase() === tag.toLowerCase())
  if (exists) return tags
  return [...tags, tag.slice(0, 64)]
}

export default function TagInput({
  id,
  value,
  onChange,
  placeholder,
  disabled = false,
  className,
}: {
  id?: string
  value: string[]
  onChange: (value: string[]) => void
  placeholder?: string
  disabled?: boolean
  className?: string
}) {
  const [inputValue, setInputValue] = useState("")

  const commitInput = () => {
    const parts = inputValue.split(/[,，;；\n]+/)
    const next = parts.reduce(addTagValue, value)
    if (next !== value) onChange(next)
    setInputValue("")
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter" || event.key === "," || event.key === "，") {
      event.preventDefault()
      commitInput()
    }
    if (event.key === "Backspace" && !inputValue && value.length > 0) {
      onChange(value.slice(0, -1))
    }
  }

  return (
    <div
      className={cn(
        "flex min-h-10 flex-wrap items-center gap-1.5 rounded-md border bg-background px-2 py-1.5",
        disabled && "cursor-not-allowed opacity-60",
        className,
      )}
    >
      {value.map((tag) => (
        <span
          key={tag}
          className="inline-flex max-w-40 items-center gap-1 rounded-md bg-secondary px-2 py-1 text-xs text-secondary-foreground"
        >
          <span className="truncate">{tag}</span>
          <Button
            type="button"
            size="icon"
            variant="ghost"
            disabled={disabled}
            aria-label={`删除标签 ${tag}`}
            className="size-4 rounded-sm p-0 hover:bg-background/80"
            onClick={() => onChange(value.filter((item) => item !== tag))}
          >
            <X className="size-3" />
          </Button>
        </span>
      ))}
      <Input
        id={id}
        value={inputValue}
        disabled={disabled}
        onChange={(event) => setInputValue(event.target.value)}
        onKeyDown={handleKeyDown}
        onBlur={commitInput}
        placeholder={value.length === 0 ? placeholder : ""}
        className="h-7 min-w-28 flex-1 border-0 bg-transparent px-1 py-0 shadow-none focus-visible:ring-0"
      />
    </div>
  )
}
