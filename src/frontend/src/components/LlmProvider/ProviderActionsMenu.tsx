import { EllipsisVertical } from "lucide-react"
import { useState } from "react"

import type { ProviderResponse } from "@/client"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import DeleteProvider from "./DeleteProvider"
import EditProvider from "./EditProvider"

interface ProviderActionsMenuProps {
  provider: ProviderResponse
}

export const ProviderActionsMenu = ({ provider }: ProviderActionsMenuProps) => {
  const [open, setOpen] = useState(false)

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon">
          <EllipsisVertical />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        {!provider.is_local_codex && <EditProvider provider={provider} onSuccess={() => setOpen(false)} />}
        {!provider.is_builtin && (
          <DeleteProvider id={provider.id} onSuccess={() => setOpen(false)} />
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
