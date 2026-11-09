import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Trash2 } from "lucide-react"
import { useState } from "react"
import { useTranslation } from "react-i18next"

import { Llm4AdProvidersService } from "@/client"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { DropdownMenuItem } from "@/components/ui/dropdown-menu"
import { LoadingButton } from "@/components/ui/loading-button"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

interface DeleteProviderProps {
  id: string
  onSuccess: () => void
}

const DeleteProvider = ({ id, onSuccess }: DeleteProviderProps) => {
  const [isOpen, setIsOpen] = useState(false)
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const { t } = useTranslation()

  const mutation = useMutation({
    mutationFn: (providerId: string) =>
      Llm4AdProvidersService.deleteProvider({ providerId }),
    onSuccess: () => {
      showSuccessToast(t("llmProvider.deleteSuccess"))
      setIsOpen(false)
      onSuccess()
    },
    onError: handleError.bind(showErrorToast),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["providers"] })
      queryClient.invalidateQueries({ queryKey: ["local-codex-status"] })
      queryClient.invalidateQueries({ queryKey: ["user-default-models"] })
    },
  })

  return (
    <Dialog open={isOpen} onOpenChange={setIsOpen}>
      <DropdownMenuItem
        variant="destructive"
        onSelect={(e) => e.preventDefault()}
        onClick={() => setIsOpen(true)}
      >
        <Trash2 />
        {t("common.delete")}
      </DropdownMenuItem>
      <DialogContent className="sm:max-w-md">
        <form
          onSubmit={(e) => {
            e.preventDefault()
            mutation.mutate(id)
          }}
        >
          <DialogHeader>
            <DialogTitle>{t("llmProvider.deleteProvider")}</DialogTitle>
            <DialogDescription>
              {t("llmProvider.deleteProviderConfirmation")}
            </DialogDescription>
          </DialogHeader>

          <DialogFooter className="mt-4">
            <DialogClose asChild>
              <Button variant="outline" disabled={mutation.isPending}>
                {t("common.cancel")}
              </Button>
            </DialogClose>
            <LoadingButton
              variant="destructive"
              type="submit"
              loading={mutation.isPending}
            >
              {t("common.delete")}
            </LoadingButton>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

export default DeleteProvider
