import { useQuery, useQueryClient } from "@tanstack/react-query"
import axios from "axios"
import { useCallback } from "react"

import {
  Llm4AdEmbeddingProvidersService,
  Llm4AdProvidersService,
  Llm4AdUserDefaultModelsService,
  OpenAPI,
} from "@/client"

export const providersQueryKey = ["providers"] as const
export const embeddingProvidersQueryKey = ["embedding-providers"] as const
export const userDefaultModelsQueryKey = ["user-default-models"] as const
export const builtinProviderQuotaQueryKey = [
  "providers",
  "builtin-quota",
] as const

export interface BuiltinProviderQuotaResponse {
  available: boolean
  provider_id: string | null
  provider_name: string | null
  spend: number | null
  budget: number | null
  remaining: number | null
  currency: string
  message: string
}

function authHeaders() {
  const token = localStorage.getItem("access_token") || ""
  return token ? { Authorization: `Bearer ${token}` } : undefined
}

async function fetchBuiltinProviderQuota() {
  const response = await axios.get<BuiltinProviderQuotaResponse>(
    `${OpenAPI.BASE}/api/v1/llm4ad/providers/builtin/quota`,
    {
      headers: authHeaders(),
    },
  )
  return response.data
}

export const providersQueryOptions = {
  queryKey: providersQueryKey,
  queryFn: () => Llm4AdProvidersService.listProviders({ skip: 0, limit: 100 }),
  staleTime: 5 * 60 * 1000,
}

export const userDefaultModelsQueryOptions = {
  queryKey: userDefaultModelsQueryKey,
  queryFn: () => Llm4AdUserDefaultModelsService.getUserDefaultModel(),
  staleTime: 5 * 60 * 1000,
}

export const embeddingProvidersQueryOptions = {
  queryKey: embeddingProvidersQueryKey,
  queryFn: () =>
    Llm4AdEmbeddingProvidersService.listEmbeddingProviders({
      skip: 0,
      limit: 100,
    }),
  staleTime: 5 * 60 * 1000,
}

export function useProviders(options?: { enabled?: boolean }) {
  return useQuery({
    ...providersQueryOptions,
    enabled: options?.enabled ?? true,
  })
}

export function useUserDefaultModels() {
  return useQuery(userDefaultModelsQueryOptions)
}

export function useBuiltinProviderQuota() {
  return useQuery({
    queryKey: builtinProviderQuotaQueryKey,
    queryFn: fetchBuiltinProviderQuota,
    staleTime: 60 * 1000,
  })
}

export function useEmbeddingProviders(options?: { enabled?: boolean }) {
  return useQuery({
    ...embeddingProvidersQueryOptions,
    enabled: options?.enabled ?? true,
  })
}

export function usePrefetchProviders() {
  const queryClient = useQueryClient()
  return useCallback(() => {
    queryClient.prefetchQuery(providersQueryOptions)
    queryClient.prefetchQuery(embeddingProvidersQueryOptions)
    queryClient.prefetchQuery(userDefaultModelsQueryOptions)
    queryClient.prefetchQuery({
      queryKey: builtinProviderQuotaQueryKey,
      queryFn: fetchBuiltinProviderQuota,
    })
  }, [queryClient])
}
