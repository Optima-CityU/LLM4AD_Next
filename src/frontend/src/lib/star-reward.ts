import { authFetch } from "@/utils/auth"

export interface StarRewardStatus {
  starred: boolean | null
  reward_eligible: boolean
  reward_granted: boolean
  reward_granted_now: boolean
  api_key_cache_refreshed: boolean | null
  reward_amount: number
  repo: string
  message: string
}

export const starRewardStatusQueryKey = ["star-reward", "status"] as const

export async function fetchStarRewardStatus(): Promise<StarRewardStatus> {
  const response = await authFetch("/api/v1/star-reward/status")
  if (!response.ok) {
    throw new Error(`Failed to fetch star reward status: ${response.status}`)
  }
  return response.json()
}

export const starRewardStatusQueryOptions = {
  queryKey: starRewardStatusQueryKey,
  queryFn: fetchStarRewardStatus,
  retry: false,
}
