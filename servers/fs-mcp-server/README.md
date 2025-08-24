# fs-mcp-server

Tiny filesystem MCP server (stdio) that exposes safe file tools within a sandbox root.

## Tools

- `list_files { "path": "." }` → list files/dirs
- `read_text { "path": "foo.txt", "max_bytes": 1048576 }`
- `write_text { "path": "out/foo.txt", "content": "hello", "overwrite": false, "create_dirs": true }`
- `stat { "path": "foo.txt" }`

All paths are resolved inside `FS_MCP_ROOT` (default `/app/data`) and cannot escape that root.

## Install

From the repo root:

```bash
npm install --prefix servers/fs-mcp-server
