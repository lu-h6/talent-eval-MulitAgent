import { fileURLToPath, URL } from "node:url";

import { defineConfig } from "vite";

export default defineConfig({
  build: {
    rollupOptions: {
      input: {
        main: fileURLToPath(new URL("./index.html", import.meta.url)),
        modules: fileURLToPath(new URL("./modules.html", import.meta.url)),
      },
    },
  },
});
