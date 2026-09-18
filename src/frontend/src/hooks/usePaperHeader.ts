import { createContext, type ReactNode, useContext } from "react"

export interface PaperHeaderContextValue {
  setHeaderCenter: (node: ReactNode) => void
  setHeaderRight: (node: ReactNode) => void
}

export const PaperHeaderContext = createContext<PaperHeaderContextValue | null>(
  null,
)

const EMPTY_PAPER_HEADER_CONTEXT: PaperHeaderContextValue = {
  setHeaderCenter: () => {},
  setHeaderRight: () => {},
}

/** Return the header injection points exposed by the paper workspace layout. */
export function usePaperHeader(): PaperHeaderContextValue {
  return useContext(PaperHeaderContext) ?? EMPTY_PAPER_HEADER_CONTEXT
}
