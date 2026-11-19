import { afterEach, beforeEach, expect, test, vi } from "vitest"

vi.mock("axios", () => ({
  default: {
    post: vi.fn(),
    request: vi.fn(),
    isAxiosError: (error: unknown) =>
      Boolean((error as { response?: unknown })?.response),
  },
}))
import axios from "axios"
import { authFetch, handleUnauthorized } from "../src/utils/auth"

const post = vi.mocked(axios.post)
const token = (exp: number) => `e30.${btoa(JSON.stringify({ exp }))}.sig`

beforeEach(() => {
  localStorage.clear()
  localStorage.setItem("access_token", "old")
  localStorage.setItem("refresh_token", "refresh")
  post.mockReset()
})
afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

test("concurrent and late 401s share renewal and reuse the rotated token", async () => {
  post.mockResolvedValue({
    data: { access_token: "new", refresh_token: "rotated" },
  })
  expect(
    await Promise.all([handleUnauthorized("old"), handleUnauthorized("old")]),
  ).toEqual(["new", "new"])
  expect(await handleUnauthorized("old")).toBe("new")
  expect(post).toHaveBeenCalledTimes(1)
})

test.each([
  new TypeError("offline"),
  { response: { status: 503 } },
])("transient renewal failures retain both credentials: %o", async (failure) => {
  post.mockRejectedValue(failure)
  await expect(handleUnauthorized("old")).rejects.toBe(failure)
  expect(localStorage.getItem("access_token")).toBe("old")
  expect(localStorage.getItem("refresh_token")).toBe("refresh")
})

test("a late renewal cannot resurrect a logged out session", async () => {
  let finish!: (value: unknown) => void
  post.mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve
      }) as ReturnType<typeof axios.post>,
  )
  const pending = handleUnauthorized("old")
  localStorage.clear()
  finish({ data: { access_token: "new", refresh_token: "rotated" } })
  await expect(pending).rejects.toThrow()
  expect(localStorage.getItem("access_token")).toBeNull()
})

test("a transport error never replays a mutation", async () => {
  const fetchMock = vi.fn().mockRejectedValue(new TypeError("offline"))
  vi.stubGlobal("fetch", fetchMock)
  await expect(
    authFetch("/submit", { method: "POST", body: "prompt" }),
  ).rejects.toThrow("offline")
  expect(fetchMock).toHaveBeenCalledTimes(1)
  expect(post).not.toHaveBeenCalled()
})

test("wake events coalesce renewal, preserve offline auth, and recover on online", async () => {
  const { startAuthSessionLifecycle } = await import(
    "../src/utils/authLifecycle"
  )
  localStorage.setItem("access_token", token(1))
  post.mockRejectedValueOnce(new TypeError("offline"))
  const stop = startAuthSessionLifecycle()
  window.dispatchEvent(new Event("focus"))
  window.dispatchEvent(new Event("pageshow"))
  await vi.waitFor(() => expect(post).toHaveBeenCalledTimes(1))
  expect(localStorage.getItem("refresh_token")).toBe("refresh")
  post.mockResolvedValue({
    data: {
      access_token: token(Date.now() / 1000 + 600),
      refresh_token: "rotated",
    },
  })
  window.dispatchEvent(new Event("online"))
  await vi.waitFor(() =>
    expect(localStorage.getItem("refresh_token")).toBe("rotated"),
  )
  stop()
  window.dispatchEvent(new Event("focus"))
  expect(post).toHaveBeenCalledTimes(2)
})

test("the embedded bridge accepts only its own same-origin frame and coalesces requests", async () => {
  const { installEmbeddedAuthBridge } = await import(
    "../src/utils/authLifecycle"
  )
  const frame = { postMessage: vi.fn() } as unknown as Window
  let finish!: () => void
  const renew = vi.fn(
    () =>
      new Promise<void>((resolve) => {
        finish = resolve
      }),
  )
  const stop = installEmbeddedAuthBridge(() => frame, renew)
  const send = (id: string, source: Window, origin = window.location.origin) =>
    window.dispatchEvent(
      new MessageEvent("message", {
        origin,
        source,
        data: { type: "llm4ad:auth-renew", requestId: id },
      }),
    )
  send("foreign", frame, "https://foreign.invalid")
  send("other-frame", window)
  send("one", frame)
  send("two", frame)
  await vi.waitFor(() => expect(renew).toHaveBeenCalledTimes(1))
  finish()
  await vi.waitFor(() => expect(frame.postMessage).toHaveBeenCalledTimes(2))
  expect(frame.postMessage).toHaveBeenCalledWith(
    { type: "llm4ad:auth-renewed", requestId: "one", success: true },
    window.location.origin,
  )
  stop()
  send("after-unmount", frame)
  expect(renew).toHaveBeenCalledTimes(1)
})

test("a genuine refresh rejection clears the rejected session", async () => {
  vi.spyOn(console, "error").mockImplementation(() => {})
  const rejection = { response: { status: 401 } }
  post.mockRejectedValue(rejection)
  await expect(handleUnauthorized("old")).rejects.toBe(rejection)
  expect(localStorage.getItem("access_token")).toBeNull()
  expect(localStorage.getItem("refresh_token")).toBeNull()
})

test("an in-flight refresh rejection cannot clear another login", async () => {
  let fail!: (reason: unknown) => void
  post.mockImplementation(
    () =>
      new Promise((_resolve, reject) => {
        fail = reject
      }) as ReturnType<typeof axios.post>,
  )
  const pending = handleUnauthorized("old")
  localStorage.setItem("access_token", "other-login")
  localStorage.setItem("refresh_token", "other-refresh")
  fail({ response: { status: 401 } })
  expect(await pending).toBe("other-login")
  expect(localStorage.getItem("refresh_token")).toBe("other-refresh")
})

test("fetch retries an authentication rejection once and never loops on another 401", async () => {
  vi.spyOn(console, "error").mockImplementation(() => {})
  post.mockResolvedValue({
    data: { access_token: "new", refresh_token: "rotated" },
  })
  const fetchMock = vi.fn(async () => new Response("{}", { status: 401 }))
  vi.stubGlobal("fetch", fetchMock)
  const response = await authFetch("/protected")
  expect(response.status).toBe(401)
  expect(fetchMock).toHaveBeenCalledTimes(2)
  expect(post).toHaveBeenCalledTimes(1)
  expect(localStorage.getItem("access_token")).toBeNull()
})

test("the SDK interceptor uses the request token and exposes transient renewal failures", async () => {
  const handlers: Array<(response: any) => Promise<any>> = []
  vi.doMock("@/client", () => ({
    OpenAPI: {
      interceptors: {
        request: { use: vi.fn() },
        response: { use: (handler: any) => handlers.push(handler) },
      },
    },
  }))
  const { initOpenApi } = await import("../src/utils/request")
  initOpenApi()
  const response = {
    status: 401,
    headers: {},
    config: { url: "/api/user", headers: { Authorization: "Bearer old" } },
  }
  const offline = new TypeError("offline")
  post.mockRejectedValue(offline)
  await expect(handlers[0](response)).rejects.toBe(offline)
  expect(localStorage.getItem("access_token")).toBe("old")
  localStorage.setItem("access_token", "newer")
  vi.mocked(axios.request).mockResolvedValue({ status: 200 })
  expect((await handlers[0](response)).status).toBe(200)
  expect(axios.request).toHaveBeenCalledWith(
    expect.objectContaining({ headers: { Authorization: "Bearer newer" } }),
  )
  expect(post).toHaveBeenCalledTimes(1)
})
