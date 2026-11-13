// Background preview entrypoint. Keep the server independent of CLI stdin handlers.
import { createServer } from "vite";
import { fileURLToPath } from "node:url";

const host = process.argv[2] || "127.0.0.1";
if (!["127.0.0.1", "0.0.0.0"].includes(host)) {
  throw new Error("Preview host must be 127.0.0.1 or 0.0.0.0.");
}
process.chdir(fileURLToPath(new URL("../", import.meta.url)));
process.env.CI = "true";
process.on("uncaughtExceptionMonitor", (error) => {
  console.error(new Date().toISOString(), error);
});
process.on("exit", (code) => {
  console.error(new Date().toISOString(), "Preview process exited", code);
});
const hmr = process.env.VITE_HMR === "true";
const server = await createServer({ server: { host, hmr }, clearScreen: false });
await server.listen();
server.printUrls();
let closing = false;
async function close() {
  if (closing) return;
  closing = true;
  await server.close();
  process.exit(0);
}
process.on("SIGINT", close);
process.on("SIGTERM", close);
