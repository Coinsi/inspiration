# Inspiration creative MCP bridge

This optional stdio process connects an external MCP client to one Inspiration project. The web Agent uses the server directly and does not need this process on a user's computer.

Use a separate Python 3.12 virtual environment and install `requirements.txt`. Configure the MCP client to launch its Python executable with the absolute path of `server.py`. Provide these environment variables using the client's secret configuration:

- `INSPIRATION_API_URL`: API root, e.g. `https://studio.example/api/v1`. HTTP is allowed only on localhost.
- `INSPIRATION_PROJECT_ID`: existing project UUID.
- `INSPIRATION_TOKEN`: the user's valid application access token. Its membership permissions remain enforced by the API. Replace it after expiry; never commit it to client configuration in a repository.

Tools: `list_creative_tools`, `invoke_creative_tool`, `read_creative_run`, `list_creative_skills`. Read calls return project data. Write calls create a durable review request in the web Agent page. A new UUID `request_key` is required for each write; reuse it when retrying the same uncertain request. Approval uses the existing object permissions, current state check, immutable versions and execution logs. No shell, arbitrary file or SQL access is exposed.

This is a stdio bridge, not a public HTTP MCP endpoint. Hosting a remote MCP endpoint, OAuth discovery and automatic token refresh are separate work. No external account catalogue connector or remote original-media download is included in JSON source imports.

Verified with MCP Python SDK 2.2.0 over a real child-process stdio connection. See `docs/23-技能与外部工具验收.md` for application acceptance coverage.
