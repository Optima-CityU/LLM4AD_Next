import { createContext, type ReactNode, useContext } from "react"

/**
 * 让子路由（autoresearch 页）把「当前会话标题条」注入到布局顶栏中间列，
 * 并把「全宽流水线进度带」通过 portal 渲染到顶栏正下方。
 *
 * 布局（`_layout_autoresearch.tsx`）提供 `setHeaderCenter`（顶栏 center 列）
 * 与 `bandEl`（header 下方全宽挂载 DOM 节点）。页面/ChatPanel 用 portal 把
 * 常驻进度带渲染进 `bandEl`——组件仍留在 ChatPanel 树内（SSE/状态逻辑不迁移），
 * 只把「渲染位置」提到全宽区。避免把会话状态提升到布局或引入全局 store。
 */
export interface AutoResearchHeaderCtx {
  setHeaderCenter: (node: ReactNode) => void
  /**
   * 顶栏左侧列（logo 右侧）注入点：侧边栏收起时页面把「会话/分组切换器」
   * 注入到这里，供无侧栏时快速切换会话。
   */
  setHeaderLeft: (node: ReactNode) => void
  /**
   * 顶栏右侧列（语言/主题切换器左侧）注入点：右侧产物面板收起时，产物面板把
   * 「报告分析 / 研究日志 / 打开 IDE / 下载产物」四枚紧凑操作按钮注入到这里，
   * 供无产物面板时仍能一键触发（弹层/抽屉走 portal，收起也能正常弹出）。
   */
  setHeaderRight: (node: ReactNode) => void
  /** header 下方的全宽挂载点（portal target）；未就绪时为 null。 */
  bandEl: HTMLElement | null
}

export const AutoResearchHeaderContext =
  createContext<AutoResearchHeaderCtx | null>(null)

/** 无 Provider 时的兜底：提到模块级常量，保证身份稳定（否则每次 render 新建
 *  对象，下游 `useEffect([setHeaderCenter])` 会被无限触发）。 */
const NOOP_HEADER_CTX: AutoResearchHeaderCtx = {
  setHeaderCenter: () => {},
  setHeaderLeft: () => {},
  setHeaderRight: () => {},
  bandEl: null,
}

export function useAutoResearchHeader(): AutoResearchHeaderCtx {
  const ctx = useContext(AutoResearchHeaderContext)
  // 页面可能在无 Provider 的场景（测试）渲染，给个 no-op 兜底。
  return ctx ?? NOOP_HEADER_CTX
}
