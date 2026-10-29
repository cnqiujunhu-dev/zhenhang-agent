import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const environment = loadEnv(mode, "..", "ZX_");
  const configuredPort = Number(environment.ZX_HTTP_PORT || 18965);
  const apiPort = Number.isInteger(configuredPort) && configuredPort > 0 && configuredPort < 65536 ? configuredPort : 18965;
  return {
  base: "/ui/",
  build: { outDir: "../src/zhenxing/static/ui", emptyOutDir: true },
  server: { proxy: { "/api": `http://127.0.0.1:${apiPort}` } },
  };
});
