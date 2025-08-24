import fs from "node:fs/promises";
import path from "node:path";
import express from "express";

// ✅ v1 API imports (no `stdio` named export)
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js"; // <- correct
import { z } from "zod";

const ROOT = path.resolve(process.env.FS_MCP_ROOT || "/app/data");
const PORT = Number(process.env.PORT || 8765);

// ----- core handlers reused by both MCP + HTTP
function resolveSafe(rel) {
  const abs = path.resolve(ROOT, rel || ".");
  const rootAbs = ROOT + path.sep;
  if (!abs.startsWith(rootAbs) && abs !== ROOT) throw new Error("Path escapes FS_MCP_ROOT");
  return abs;
}

const handlers = {
  list_files: async ({ path: p = "." }) => {
    const dir = resolveSafe(p);
    const ents = await fs.readdir(dir, { withFileTypes: true });
    const items = await Promise.all(ents.map(async (e) => {
      const full = path.join(dir, e.name);
      const st = await fs.stat(full);
      return { name: e.name, type: e.isDirectory() ? "dir" : "file", size: e.isDirectory() ? null : st.size, mtime: st.mtime.toISOString() };
    }));
    return { content: [{ type: "json", value: items }] };
  },
  read_text: async ({ path: p, max_bytes = 1048576 }) => {
    const file = resolveSafe(p);
    const st = await fs.stat(file);
    if (st.size > max_bytes) throw new Error(`File too large: ${st.size} > ${max_bytes}`);
    const data = await fs.readFile(file, "utf8");
    return { content: [{ type: "text", text: data }] };
  },
  write_text: async ({ path: p, content, overwrite = false, create_dirs = true }) => {
    const file = resolveSafe(p);
    if (create_dirs) await fs.mkdir(path.dirname(file), { recursive: true });
    if (!overwrite) { try { await fs.access(file); throw new Error("File exists and overwrite=false"); } catch {} }
    await fs.writeFile(file, content, "utf8");
    return { content: [{ type: "json", value: { ok: true, path: path.relative(ROOT, file) } }] };
  },
  stat: async ({ path: p }) => {
    const fp = resolveSafe(p);
    const s = await fs.stat(fp);
    return { content: [{ type: "json", value: { path: path.relative(ROOT, fp), is_dir: s.isDirectory(), size: s.isDirectory() ? null : s.size, mtime: s.mtime.toISOString(), ctime: s.ctime.toISOString() } }] };
  }
};

// ----- MCP stdio (optional; enable with ENABLE_STDIO=1)
if (process.env.ENABLE_STDIO === "1") {
  const mcp = new McpServer({ name: "fs-mcp", version: "0.1.0" });

  mcp.registerTool("list_files", {
    title: "List Files",
    description: "List files and folders under a relative path within FS_MCP_ROOT.",
    inputSchema: { path: z.string().default(".").optional() }
  }, handlers.list_files);

  mcp.registerTool("read_text", {
    title: "Read Text",
    description: "Read a UTF-8 text file (size-limited).",
    inputSchema: { path: z.string(), max_bytes: z.number().int().positive().default(1048576).optional() }
  }, handlers.read_text);

  mcp.registerTool("write_text", {
    title: "Write Text",
    description: "Write a UTF-8 text file within FS_MCP_ROOT.",
    inputSchema: { path: z.string(), content: z.string(), overwrite: z.boolean().default(false).optional(), create_dirs: z.boolean().default(true).optional() }
  }, handlers.write_text);

  mcp.registerTool("stat", {
    title: "Stat Path",
    description: "Stat a file or directory within FS_MCP_ROOT.",
    inputSchema: { path: z.string() }
  }, handlers.stat);

  const transport = new StdioServerTransport();
  // Connect asynchronously; don't block the HTTP server
  mcp.connect(transport).then(() => {
    console.log("MCP stdio server connected.");
  }).catch((e) => {
    console.warn("MCP stdio failed:", e?.message || e);
  });
}

// ----- HTTP façade (for your REPL)
const app = express();
app.use(express.json({ limit: "2mb" }));

app.get("/health", (_req, res) => res.json({ ok: true, root: ROOT }));

const toolDefs = {
  list_files: { name: "list_files", description: "List files/folders", input_schema: { type: "object", properties: { path: { type: "string", default: "." } } } },
  read_text:  { name: "read_text",  description: "Read a UTF-8 text file (size-limited)", input_schema: { type: "object", required: ["path"], properties: { path: { type: "string" }, max_bytes: { type: "integer", default: 1048576 } } } },
  write_text: { name: "write_text", description: "Write a UTF-8 text file", input_schema: { type: "object", required: ["path","content"], properties: { path: { type: "string" }, content: { type: "string" }, overwrite: { type: "boolean", default: false }, create_dirs: { type: "boolean", default: true } } } },
  stat:       { name: "stat",       description: "Stat a file or directory", input_schema: { type: "object", required: ["path"], properties: { path: { type: "string" } } } }
};

app.get("/tools", (_req, res) => res.json(Object.values(toolDefs)));

app.post("/call", async (req, res) => {
  try {
    const { tool, arguments: args } = req.body || {};
    if (!handlers[tool]) return res.status(404).json({ ok: false, error: `unknown tool: ${tool}` });
    const result = await handlers[tool](args || {});
    res.json({ ok: true, result });
  } catch (e) {
    res.status(400).json({ ok: false, error: (e && e.message) || String(e) });
  }
});

app.listen(PORT, () => console.log(`fs-mcp-server HTTP listening on :${PORT} (root=${ROOT})`));
