import type { ErrorComponentProps } from "@tanstack/react-router"
import { createRootRoute, HeadContent, Outlet } from "@tanstack/react-router"
import ErrorComponent from "@/components/Common/ErrorComponent"
import NotFound from "@/components/Common/NotFound"
import { FeedbackFAB } from "@/components/Feedback/FeedbackFAB"
import { isStaleChunkError } from "@/utils/staleAssets"
import { notifyRefresh } from "@/utils/updateNotify"

/**
 * Router error boundary. Falls back to a stale-assets refresh prompt when the
 * error is a failed dynamic import (old chunk removed by a redeploy); the
 * primary detection path is the `vite:preloadError` event.
 */
const RootErrorComponent = ({ error }: ErrorComponentProps) => {
  if (isStaleChunkError(error)) {
    notifyRefresh("stale-assets")
  }
  return <ErrorComponent />
}

export const Route = createRootRoute({
  component: () => (
    <>
      <HeadContent />
      <Outlet />
      <FeedbackFAB />
      {/* 禁用开发工具 */}
      {/* <TanStackRouterDevtools position="bottom-right" /> */}
      {/* <ReactQueryDevtools initialIsOpen={false} /> */}
    </>
  ),
  notFoundComponent: () => <NotFound />,
  errorComponent: RootErrorComponent,
})
