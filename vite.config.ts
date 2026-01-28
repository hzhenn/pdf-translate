import { defineConfig } from "vite";
import react from "@vitejs/plugin-react-swc";
import tsconfigPaths from "vite-tsconfig-paths";
import path from "node:path";
import { fileURLToPath } from "node:url";

const configDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = configDir;

export default defineConfig({
  root: path.resolve(repoRoot, "src/renderer"),
  publicDir: path.resolve(repoRoot, "build"),
  plugins: [
    react(),
    tsconfigPaths({
      projects: [path.resolve(repoRoot, "tsconfig.json")],
    }),
  ],
  base: "",
  build: {
    outDir: path.resolve(repoRoot, "dist"),
    emptyOutDir: true,
  },
});
