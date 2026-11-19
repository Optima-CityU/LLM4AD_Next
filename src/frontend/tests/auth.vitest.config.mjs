import { fileURLToPath } from "node:url"

export default {
  root: fileURLToPath(new URL("..", import.meta.url)),
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("../src", import.meta.url)),
      vitest: fileURLToPath(
        new URL(
          "../../../third_party/CloudCLI/node_modules/vitest/dist/index.js",
          import.meta.url,
        ),
      ),
    },
  },
  test: {
    environment: "jsdom",
    include: ["tests/auth-session.test.ts"],
    restoreMocks: true,
  },
}
