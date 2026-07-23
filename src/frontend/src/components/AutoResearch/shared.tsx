import type { ResearchMode } from "@/client"

/**
 * ARC HITL 模式。后端已接原生 ``HITLSession``，8 个值与 CLI ``--mode`` 一一对应：
 * 在哪些 stage 停返、命中给哪些 available_actions 全部由原生 preset 决定。
 *
 * 前端暴露 **6 种真实行为**（按介入程度递增排列）。原生 8 值里另两个是别名、
 * 行为完全重复，故不单列，避免用户看到两个一模一样的选项：
 *   - ``thorough`` ≡ ``checkpoint``（都停在 8 个 phase 边界）
 *   - ``learning`` ≡ ``step-by-step``（都每个 stage 停）
 * 详见 backend ``ARC_MODE_参数详解.md``。
 *
 * 各模式停点（与原生一致）：
 *   full-auto 不停 · gate-only [5,9,20] · express [8,9,20] ·
 *   checkpoint 8 个 phase 边界 · co-pilot 分层审批 · step-by-step 每个 stage。
 *
 * 当前只显示：full-auto（全自动）和 co-pilot（协作）
 */
export const MODE_OPTIONS: ResearchMode[] = [
  "full-auto",
  "co-pilot",
]
