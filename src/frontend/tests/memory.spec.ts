import { expect, test } from "@playwright/test"

const memoryConfig = {
  id: "cfg_1",
  created_time: "2026-07-07T00:00:00Z",
  updated_time: "2026-07-07T00:00:00Z",
  enabled: true,
  include_user_memory: true,
  include_project_memory: true,
  include_task_memory: true,
  user_memory_limit: 5,
  project_memory_limit: 5,
  task_memory_limit: 5,
  mindmemos_search_strategy: "fast",
  mindmemos_rerank: false,
  mindmemos_score_threshold: null,
  mindmemos_fail_open: true,
  mindmemos_binding_id: "binding_1",
  mindmemos_chat_provider_id: "chat_1",
  mindmemos_chat_model: "gpt-test",
  mindmemos_embedding_provider_id: "emb_1",
  mindmemos_embedding_model: "text-embedding-test",
  mindmemos_embedding_dim: 1536,
  system_enabled: true,
  system_base_url: "",
  system_api_key_configured: true,
  system_chat_configured: true,
  system_embedding_configured: true,
  system_embedding_dimensions: 1536,
  system_rerank_enabled: false,
  system_rerank_configured: false,
  system_runtime_available: true,
  user_id: "user_1",
}

test("opening memory default strategy does not refetch in a loop", async ({ page }) => {
  const requestCounts = {
    health: 0,
    binding: 0,
    config: 0,
    cards: 0,
  }

  await page.route("**/api/v1/llm4ad/memory/health", async (route) => {
    requestCounts.health += 1
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        message: "ready",
        system_runtime_available: true,
        system_enabled: true,
        system_chat_configured: true,
        system_embedding_configured: true,
        system_api_key_configured: true,
        system_rerank_enabled: false,
        system_rerank_configured: false,
        service_reachable: true,
        auth_ok: true,
      }),
    })
  })
  await page.route("**/api/v1/llm4ad/memory/provider-binding", async (route) => {
    requestCounts.binding += 1
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        configured: true,
        binding_id: "binding_1",
        project_id: "llm4ad_user_1",
        user_id: "user_1",
        chat_provider_id: "chat_1",
        chat_model: "gpt-test",
        embedding_provider_id: "emb_1",
        embedding_model: "text-embedding-test",
        embedding_dim: 1536,
        embedding_locked: true,
        message: "configured",
      }),
    })
  })
  await page.route("**/api/v1/llm4ad/memory/user-config", async (route) => {
    requestCounts.config += 1
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(memoryConfig),
    })
  })
  await page.route("**/api/v1/llm4ad/memory/cards?**", async (route) => {
    requestCounts.cards += 1
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ items: [], page: 1, page_size: 20, total: 0, has_more: false }),
    })
  })
  await page.route("**/api/v1/llm-providers/**", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ items: [] }),
    })
  })

  await page.goto("/memory")
  await expect(page.getByText("用户全局记忆")).toBeVisible()
  await page.getByRole("button", { name: "默认策略" }).click()
  await expect(page.getByText("默认注入策略")).toBeVisible()

  await page.waitForTimeout(1200)

  expect(requestCounts.health).toBeLessThanOrEqual(2)
  expect(requestCounts.binding).toBeLessThanOrEqual(3)
  expect(requestCounts.config).toBe(1)
})

test("generates a memory preview from raw input before saving", async ({ page }) => {
  let cardsRequestCount = 0
  let extractionPayload: unknown = null
  let commitPayload: unknown = null

  await page.route("**/api/v1/llm4ad/memory/health", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        message: "ready",
        system_runtime_available: true,
        system_enabled: true,
        system_chat_configured: true,
        system_embedding_configured: true,
        system_api_key_configured: true,
        system_rerank_enabled: false,
        system_rerank_configured: false,
        service_reachable: true,
        auth_ok: true,
      }),
    })
  })
  await page.route("**/api/v1/llm4ad/memory/provider-binding", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        configured: true,
        binding_id: "binding_1",
        project_id: "llm4ad_user_1",
        user_id: "user_1",
        chat_provider_id: "chat_1",
        chat_model: "gpt-test",
        embedding_provider_id: "emb_1",
        embedding_model: "text-embedding-test",
        embedding_dim: 1536,
        embedding_locked: true,
        message: "configured",
      }),
    })
  })
  await page.route("**/api/v1/llm4ad/memory/cards?**", async (route) => {
    cardsRequestCount += 1
    const items = []
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ items, page: 1, page_size: 20, total: items.length, has_more: false }),
    })
  })
  await page.route("**/api/v1/llm4ad/memory/cards/extractions?**", async (route) => {
    extractionPayload = route.request().postDataJSON()
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({
        preview_id: "preview_1",
        message: "",
        items: [
          {
            id: "memory_preview_1",
            type: "general_insight",
            title: "2-opt 对中小规模 TSP 实例稳定",
            content: "2-opt 对中小规模 TSP 实例稳定，但大规模实例需要限制邻域数量。",
            enabled: false,
            source: "mindmemos",
            tags: [],
            metadata: { llm4ad_generation_id: "preview_1" },
          },
        ],
      }),
    })
  })
  await page.route("**/api/v1/llm4ad/memory/cards/extractions/preview_1/commit?**", async (route) => {
    commitPayload = route.request().postDataJSON()
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        preview_id: "preview_1",
        items: [
          {
            id: "memory_preview_1",
            type: "general_insight",
            title: "2-opt 对中小规模 TSP 实例稳定",
            content: "2-opt 对中小规模 TSP 实例稳定，但大规模实例需要限制邻域数量。",
            enabled: true,
            source: "mindmemos",
            tags: [],
            metadata: {},
          },
        ],
        message: "",
      }),
    })
  })

  await page.goto("/memory")
  await page.getByRole("button", { name: "新增记忆" }).click()
  await expect(page.getByText("输入一段希望系统记住的内容")).toBeVisible()

  await page.getByRole("button", { name: "算法经验" }).click()
  await expect(page.getByLabel("原始内容")).toHaveValue(/2-opt/)
  await expect(page.getByLabel("原始内容")).not.toHaveValue(/标签/)
  await expect(page.getByRole("button", { name: "错误反思" })).toBeVisible()
  await expect(page.getByRole("button", { name: "领域知识" })).toBeVisible()
  await expect(page.getByRole("button", { name: "通用经验" })).toBeVisible()
  await page.getByLabel("提取语言").click()
  await page.getByRole("option", { name: "English" }).click()

  await page.getByRole("button", { name: "生成预览" }).click()
  await expect(page.getByText("2-opt 对中小规模 TSP 实例稳定")).toBeVisible()
  await expect(page.getByText("生成后已默认保存为已禁用记忆，启用后才会参与注入。")).toBeVisible()
  await page.keyboard.press("Escape")
  await expect(page.getByRole("dialog", { name: "新增记忆" })).toBeVisible()

  await page.getByRole("button", { name: "启用选中" }).click()
  await expect(page.getByRole("dialog", { name: "新增记忆" })).toBeHidden()
  await expect(page.getByText("2-opt 对中小规模 TSP 实例稳定")).toBeVisible()
  await expect.poll(() => commitPayload).toEqual({
    selected_ids: ["memory_preview_1"],
    all_ids: ["memory_preview_1"],
  })
  expect(extractionPayload).toEqual(
    expect.objectContaining({
      content: expect.stringContaining("2-opt"),
      prompt_language: "EN",
    }),
  )
})

test("memory preview dialog stays open while generation is pending", async ({ page }) => {
  await page.route("**/api/v1/llm4ad/memory/health", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        message: "ready",
        system_runtime_available: true,
        system_enabled: true,
        system_chat_configured: true,
        system_embedding_configured: true,
        system_api_key_configured: true,
        system_rerank_enabled: false,
        system_rerank_configured: false,
        service_reachable: true,
        auth_ok: true,
      }),
    })
  })
  await page.route("**/api/v1/llm4ad/memory/provider-binding", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        configured: true,
        binding_id: "binding_1",
        project_id: "llm4ad_user_1",
        user_id: "user_1",
        chat_provider_id: "chat_1",
        chat_model: "gpt-test",
        embedding_provider_id: "emb_1",
        embedding_model: "text-embedding-test",
        embedding_dim: 1536,
        embedding_locked: true,
        message: "configured",
      }),
    })
  })
  await page.route("**/api/v1/llm4ad/memory/cards?**", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ items: [], page: 1, page_size: 20, total: 0, has_more: false }),
    })
  })
  await page.route("**/api/v1/llm4ad/memory/cards/extractions?**", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 400))
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({
        preview_id: "preview_1",
        message: "",
        items: [
          {
            id: "memory_preview_1",
            type: "general_insight",
            title: "稳定性优先",
            content: "后续算法优先选择稳定、可解释的策略。",
            enabled: false,
            source: "mindmemos",
            tags: [],
            metadata: { llm4ad_generation_id: "preview_1" },
          },
        ],
      }),
    })
  })

  await page.goto("/memory")
  await page.getByRole("button", { name: "新增记忆" }).click()
  await page.getByLabel("原始内容").fill("这个项目更关注稳定性和可解释性。")
  await page.getByRole("button", { name: "生成预览" }).click()
  await page.mouse.click(8, 8)

  await expect(page.getByRole("dialog", { name: "新增记忆" })).toBeVisible()
  await expect(page.getByText("稳定性优先")).toBeVisible()
})

test("memory generated cards can be kept as disabled cards when closing dialog", async ({ page }) => {
  let deleteCalled = false
  await page.route("**/api/v1/llm4ad/memory/health", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        message: "ready",
        system_runtime_available: true,
        system_enabled: true,
        system_chat_configured: true,
        system_embedding_configured: true,
        system_api_key_configured: true,
        system_rerank_enabled: false,
        system_rerank_configured: false,
        service_reachable: true,
        auth_ok: true,
      }),
    })
  })
  await page.route("**/api/v1/llm4ad/memory/provider-binding", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        configured: true,
        binding_id: "binding_1",
        project_id: "llm4ad_user_1",
        user_id: "user_1",
        chat_provider_id: "chat_1",
        chat_model: "gpt-test",
        embedding_provider_id: "emb_1",
        embedding_model: "text-embedding-test",
        embedding_dim: 1536,
        embedding_locked: true,
        message: "configured",
      }),
    })
  })
  await page.route("**/api/v1/llm4ad/memory/cards?**", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ items: [], page: 1, page_size: 20, total: 0, has_more: false }),
    })
  })
  await page.route("**/api/v1/llm4ad/memory/cards/extractions?**", async (route) => {
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({
        preview_id: "preview_1",
        message: "",
        items: [
          {
            id: "memory_preview_1",
            type: "general_insight",
            title: "稳定性优先",
            content: "后续算法优先选择稳定、可解释的策略。",
            enabled: false,
            source: "mindmemos",
            tags: [],
            metadata: { llm4ad_generation_id: "preview_1" },
          },
        ],
      }),
    })
  })
  await page.route("**/api/v1/llm4ad/memory/cards/extractions/preview_1?**", async (route) => {
    deleteCalled = true
    await route.fulfill({ status: 204 })
  })

  await page.goto("/memory")
  await page.getByRole("button", { name: "新增记忆" }).click()
  await page.getByLabel("原始内容").fill("这个项目更关注稳定性和可解释性。")
  await page.getByRole("button", { name: "生成预览" }).click()
  await expect(page.getByText("稳定性优先")).toBeVisible()
  await page.getByLabel("关闭新增记忆").click()
  await page.getByRole("button", { name: "保留为已禁用" }).click()

  await expect(page.getByRole("dialog", { name: "新增记忆" })).toBeHidden()
  await expect(page.getByText("稳定性优先")).toBeVisible()
  await expect(page.getByText("已禁用")).toBeVisible()
  expect(deleteCalled).toBe(false)
})

test("memory preview dialog close button closes an empty draft", async ({ page }) => {
  await page.route("**/api/v1/llm4ad/memory/health", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        message: "ready",
        system_runtime_available: true,
        system_enabled: true,
        system_chat_configured: true,
        system_embedding_configured: true,
        system_api_key_configured: true,
        system_rerank_enabled: false,
        system_rerank_configured: false,
        service_reachable: true,
        auth_ok: true,
      }),
    })
  })
  await page.route("**/api/v1/llm4ad/memory/provider-binding", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        configured: true,
        binding_id: "binding_1",
        project_id: "llm4ad_user_1",
        user_id: "user_1",
        chat_provider_id: "chat_1",
        chat_model: "gpt-test",
        embedding_provider_id: "emb_1",
        embedding_model: "text-embedding-test",
        embedding_dim: 1536,
        embedding_locked: true,
        message: "configured",
      }),
    })
  })
  await page.route("**/api/v1/llm4ad/memory/cards?**", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ items: [], page: 1, page_size: 20, total: 0, has_more: false }),
    })
  })

  await page.goto("/memory")
  await page.getByRole("button", { name: "新增记忆" }).click()
  await expect(page.getByRole("dialog", { name: "新增记忆" })).toBeVisible()
  await page.getByLabel("关闭新增记忆").click()

  await expect(page.getByRole("dialog", { name: "新增记忆" })).toBeHidden()
})

test("memory preview dialog close button is disabled while generation is pending", async ({ page }) => {
  await page.route("**/api/v1/llm4ad/memory/health", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        message: "ready",
        system_runtime_available: true,
        system_enabled: true,
        system_chat_configured: true,
        system_embedding_configured: true,
        system_api_key_configured: true,
        system_rerank_enabled: false,
        system_rerank_configured: false,
        service_reachable: true,
        auth_ok: true,
      }),
    })
  })
  await page.route("**/api/v1/llm4ad/memory/provider-binding", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        configured: true,
        binding_id: "binding_1",
        project_id: "llm4ad_user_1",
        user_id: "user_1",
        chat_provider_id: "chat_1",
        chat_model: "gpt-test",
        embedding_provider_id: "emb_1",
        embedding_model: "text-embedding-test",
        embedding_dim: 1536,
        embedding_locked: true,
        message: "configured",
      }),
    })
  })
  await page.route("**/api/v1/llm4ad/memory/cards?**", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ items: [], page: 1, page_size: 20, total: 0, has_more: false }),
    })
  })
  await page.route("**/api/v1/llm4ad/memory/cards/extractions?**", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 500))
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({
        preview_id: "preview_1",
        message: "",
        items: [],
      }),
    })
  })

  await page.goto("/memory")
  await page.getByRole("button", { name: "新增记忆" }).click()
  await page.getByLabel("原始内容").fill("这个项目更关注稳定性和可解释性。")
  await page.getByRole("button", { name: "生成预览" }).click()

  await expect(page.getByLabel("关闭新增记忆")).toBeDisabled()
  await expect(page.getByText("正在调用 MindMemOS 提取，可能需要几十秒。")).toBeVisible()
  await expect(page.getByRole("dialog", { name: "新增记忆" })).toBeVisible()
})

test("memory list does not display a stale total when no cards are renderable", async ({ page }) => {
  await page.route("**/api/v1/llm4ad/memory/health", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        message: "ready",
        system_runtime_available: true,
        system_enabled: true,
        system_chat_configured: true,
        system_embedding_configured: true,
        system_api_key_configured: true,
        system_rerank_enabled: false,
        system_rerank_configured: false,
        service_reachable: true,
        auth_ok: true,
      }),
    })
  })
  await page.route("**/api/v1/llm4ad/memory/provider-binding", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        configured: true,
        binding_id: "binding_1",
        project_id: "llm4ad_user_1",
        user_id: "user_1",
        chat_provider_id: "chat_1",
        chat_model: "gpt-test",
        embedding_provider_id: "emb_1",
        embedding_model: "text-embedding-test",
        embedding_dim: 1536,
        embedding_locked: true,
        message: "configured",
      }),
    })
  })
  await page.route("**/api/v1/llm4ad/memory/cards?**", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ items: [], page: 1, page_size: 20, total: 1, has_more: false }),
    })
  })

  await page.goto("/memory")

  await expect(page.getByText("暂无记忆")).toBeVisible()
  await expect(page.getByText("第 1 页，共 1 条")).toHaveCount(0)
  await expect(page.getByText("第 1 页，共 0 条")).toBeVisible()
})

test("memory card toggle is debounced and edit form does not expose injection switch", async ({ page }) => {
  let patchCount = 0
  let patchPayload: unknown = null
  let cardsRequestCount = 0

  await page.route("**/api/v1/llm4ad/memory/health", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        message: "ready",
        system_runtime_available: true,
        system_enabled: true,
        system_chat_configured: true,
        system_embedding_configured: true,
        system_api_key_configured: true,
        system_rerank_enabled: false,
        system_rerank_configured: false,
        service_reachable: true,
        auth_ok: true,
      }),
    })
  })
  await page.route("**/api/v1/llm4ad/memory/provider-binding", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        configured: true,
        binding_id: "binding_1",
        project_id: "llm4ad_user_1",
        user_id: "user_1",
        chat_provider_id: "chat_1",
        chat_model: "gpt-test",
        embedding_provider_id: "emb_1",
        embedding_model: "text-embedding-test",
        embedding_dim: 1536,
        embedding_locked: true,
        message: "configured",
      }),
    })
  })
  await page.route("**/api/v1/llm4ad/memory/cards?**", async (route) => {
    cardsRequestCount += 1
    const enabled = cardsRequestCount === 1
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        items: [
          {
            id: "memory_toggle_1",
            type: "general_insight",
            title: "稳定性优先",
            content: "后续算法优先选择稳定、可解释的策略。",
            enabled,
            source: "mindmemos",
            tags: ["policy"],
            metadata: {},
          },
        ],
        page: 1,
        page_size: 20,
        total: 1,
        has_more: false,
      }),
    })
  })
  await page.route("**/api/v1/llm4ad/memory/cards/memory_toggle_1?**", async (route) => {
    if (route.request().method() === "PATCH") {
      patchCount += 1
      patchPayload = route.request().postDataJSON()
      await new Promise((resolve) => setTimeout(resolve, 250))
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          id: "memory_toggle_1",
          type: "general_insight",
          title: "稳定性优先",
          content: "后续算法优先选择稳定、可解释的策略。",
          enabled: false,
          source: "mindmemos",
          tags: ["policy"],
          metadata: {},
        }),
      })
      return
    }
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ message: "deleted" }),
    })
  })

  await page.goto("/memory")
  await expect(page.getByText("稳定性优先")).toBeVisible()

  await page.getByRole("button", { name: "编辑记忆" }).click()
  await expect(page.getByRole("dialog", { name: "编辑记忆" })).toBeVisible()
  await expect(page.getByText("允许注入后续提示词")).toHaveCount(0)
  await page.keyboard.press("Escape")

  await page.getByRole("button", { name: "禁用记忆" }).evaluate((button) => {
    const toggleButton = button as HTMLButtonElement
    toggleButton.click()
    toggleButton.click()
  })
  await expect(page.getByRole("button", { name: "正在禁用记忆" })).toBeDisabled()
  await expect(page.getByRole("button", { name: "启用记忆" })).toBeVisible()

  expect(patchCount).toBe(1)
  expect(patchPayload).toEqual(
    expect.objectContaining({
      enabled: false,
    }),
  )
  expect(cardsRequestCount).toBe(1)

  await page.getByRole("button", { name: "删除记忆" }).click()
  await page.getByRole("button", { name: "永久删除" }).click()
  await expect(page.getByText("稳定性优先")).toBeHidden()
  expect(cardsRequestCount).toBe(1)
})
