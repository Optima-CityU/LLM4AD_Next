import { describe, expect, spyOn, test } from "bun:test"
import { QueryClient } from "@tanstack/react-query"

import {
  type AutoDesignSessionResponse,
  Llm4AdAutodesignService,
} from "../src/client"
import { autoDesignTaskSessionOptions } from "../src/hooks/useAutoDesign"
import en from "../src/i18n/locales/en.json"
import zh from "../src/i18n/locales/zh.json"
import {
  autoDesignFileTree,
  autoDesignKeys,
  autoDesignModelProfile,
  autoDesignTaskSearch,
  canUseTaskAgent,
  isAutoDesignExecutionLocked,
  isAutoDesignReferenceName,
  rememberAgentVersion,
} from "../src/lib/autoDesign"

describe("AutoDesign version context", () => {
  test("retains each opened Agent version without changing its identity on return", () => {
    const root = rememberAgentVersion([], "root")
    const child = rememberAgentVersion(root, "child")
    expect(child).toEqual(["root", "child"])
    expect(rememberAgentVersion(child, "root")).toBe(child)
    expect(root).toEqual(["root"])
  })

  test("isolates context and files by session, selected version, and area", () => {
    expect(autoDesignKeys.context("session", "root")).not.toEqual(
      autoDesignKeys.context("session", "child"),
    )
    expect(autoDesignKeys.context("session")).not.toEqual(
      autoDesignKeys.context("session", "root"),
    )
    expect(
      autoDesignKeys.file("session", "input", "config.yaml", "root"),
    ).not.toEqual(
      autoDesignKeys.file("session", "draft", "config.yaml", "root"),
    )
    expect(
      autoDesignKeys.file("session", "run", "result.json", "root"),
    ).not.toEqual(autoDesignKeys.file("session", "run", "result.json", "child"))
  })

  test("reuses the stable task session when revisiting a version", async () => {
    const calls: string[] = []
    const resolve = spyOn(
      Llm4AdAutodesignService,
      "getTaskSession",
    ).mockImplementation(({ taskId }) => {
      calls.push(taskId)
      return Promise.resolve({
        id: `session-${taskId}`,
        task_id: taskId,
      }) as ReturnType<typeof Llm4AdAutodesignService.getTaskSession>
    })
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    try {
      const root = await queryClient.fetchQuery(
        autoDesignTaskSessionOptions("root"),
      )
      const child = await queryClient.fetchQuery(
        autoDesignTaskSessionOptions("child"),
      )
      const revisited = await queryClient.fetchQuery(
        autoDesignTaskSessionOptions("root"),
      )
      expect(root.id).toBe("session-root")
      expect(child.id).toBe("session-child")
      expect(revisited).toBe(root)
      expect(calls).toEqual(["root", "child"])
      expect(autoDesignTaskSessionOptions("root").refetchOnWindowFocus).toBe(
        false,
      )
    } finally {
      resolve.mockRestore()
      queryClient.clear()
    }
  })

  test("published links select the published version instead of a project's first task", () => {
    expect(
      autoDesignTaskSearch({ id: "published-child", project_id: "project" }),
    ).toEqual({ projectId: "project", taskId: "published-child" })
  })

  test("context and validation refreshes do not change the runtime model profile", () => {
    const session = {
      analysis_provider_id: "provider",
      analysis_model_name: "model",
      analysis_context_window_tokens: 128000,
      analysis_max_output_tokens: 8192,
    } as AutoDesignSessionResponse
    const original = autoDesignModelProfile(session)
    expect(
      autoDesignModelProfile({
        ...session,
        title: "Updated",
        package_ready: true,
        validation_report: { status: "passed" },
      } as AutoDesignSessionResponse),
    ).toBe(original)
    expect(
      autoDesignModelProfile({ ...session, analysis_model_name: "other" }),
    ).not.toBe(original)
  })
})

test("generated files reuse tree paths without flattening directories", () => {
  const tree = autoDesignFileTree([
    { path: "knapsack/data/sample.json", size: 40 },
    { path: "knapsack/data/other.json", size: 30 },
    { path: "knapsack/config.yaml", size: 20 },
    { path: "README.md", size: 10 },
  ])
  expect(tree[0].path).toBe("knapsack")
  expect(tree[0].children?.map((node) => node.path)).toEqual([
    "knapsack/data",
    "knapsack/config.yaml",
  ])
  expect(tree[0].children?.[0].children?.map((node) => node.path)).toEqual([
    "knapsack/data/sample.json",
    "knapsack/data/other.json",
  ])
  expect(tree[1]).toEqual({
    path: "README.md",
    name: "README.md",
    type: "file",
  })
})

test("AutoDesign and Algorithm Projects labels and starter prompts are bilingual", () => {
  expect(en.layout.projectManagement).toBe("Algorithm Evolution Projects")
  expect(zh.layout.projectManagement).toBe("算法演化项目")
  for (const locale of [en, zh]) {
    expect(locale.autodesign.title).toContain("AutoDesign")
    expect(locale.autodesign.starters).toHaveLength(3)
    expect(locale.paper.workflow.stage.autodesignExamples).toEqual(
      locale.autodesign.starters,
    )
    expect(locale.autodesign.noMaterialsDescription).toBeTruthy()
    expect(locale.autodesign.packageHint).toBeTruthy()
    expect(JSON.stringify(locale)).not.toContain("AutoDiscovery")
    expect(JSON.stringify(locale)).not.toContain("项目管理")
  }
})

test("reference selection allows text documents and rejects binary document formats", () => {
  for (const name of ["brief.md", "notes.TXT", "paper.tex", "config.yaml"])
    expect(isAutoDesignReferenceName(name)).toBe(true)
  for (const name of [
    "paper.pdf",
    "paper.docx",
    "files.zip",
    "image.svg",
    "script.html",
  ])
    expect(isAutoDesignReferenceName(name)).toBe(false)
})

test("queued and running executions keep the Agent closed", () => {
  expect(isAutoDesignExecutionLocked("pending")).toBe(true)
  expect(isAutoDesignExecutionLocked("running")).toBe(true)
  for (const status of ["uninitialized", "completed", "failed", undefined])
    expect(isAutoDesignExecutionLocked(status)).toBe(false)
})

test("task Agent access waits for the selected version and the entire idle family", () => {
  const root = { id: "root", status: "completed" } as const
  const child = { id: "child", status: "uninitialized" } as const
  const family = { root, children: [child] }
  expect(canUseTaskAgent("child", child, family)).toBe(true)
  expect(canUseTaskAgent("child", undefined, family)).toBe(false)
  expect(canUseTaskAgent("child", root, family)).toBe(false)
  expect(canUseTaskAgent("child", child, undefined)).toBe(false)
  expect(canUseTaskAgent("other", { ...child, id: "other" }, family)).toBe(
    false,
  )
  for (const status of ["pending", "running"] as const) {
    expect(canUseTaskAgent("child", { ...child, status }, family)).toBe(false)
    expect(
      canUseTaskAgent("child", child, {
        ...family,
        root: { ...root, status },
      }),
    ).toBe(false)
    expect(
      canUseTaskAgent("root", root, {
        root,
        children: [{ ...child, status }],
      }),
    ).toBe(false)
  }
})

test("conversation preferences change the native runtime profile", () => {
  const session = {
    analysis_provider_id: "provider",
    analysis_model_name: "model",
    analysis_context_window_tokens: 128000,
    analysis_max_output_tokens: 16000,
  }
  expect(
    autoDesignModelProfile({
      ...session,
      conversation_preferences: { reply_language: "zh" },
    }),
  ).not.toBe(autoDesignModelProfile(session))
})
