// agentdash pi extension: reports this session to the local agentdash node,
// accepts prompts sent from the dashboard, and (while the machine is armed)
// routes tool calls through the dashboard's decisions inbox.
// Installed by `agentdash install pi` into ~/.pi/agent/extensions/.

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const NODE = process.env.AGENTDASH_NODE_URL ?? "http://127.0.0.1:8791";

export default function (pi: ExtensionAPI) {
  let sessionId = "";
  let cwd = "";
  let armed = false;
  let polling = false;
  let stopped = false;
  let status = "idle";
  let model = "";

  async function post(path: string, body: unknown, timeoutMs = 3000): Promise<any> {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), timeoutMs);
    try {
      const r = await fetch(NODE + path, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
        signal: ctl.signal,
      });
      return r.ok ? await r.json() : null;
    } catch {
      return null;
    } finally {
      clearTimeout(t);
    }
  }

  async function report(event: string, extra: Record<string, unknown> = {}) {
    if (!sessionId) return;
    const r = await post("/pi/report", {
      session_id: sessionId,
      event,
      status,
      cwd,
      pid: process.pid,
      model,
      name: cwd.split("/").pop() ?? "",
      ...extra,
    });
    if (r && typeof r.armed === "boolean") armed = r.armed;
  }

  async function pollInbox() {
    if (polling) return;
    polling = true;
    while (!stopped && sessionId) {
      try {
        const ctl = new AbortController();
        const t = setTimeout(() => ctl.abort(), 30000);
        const r = await fetch(`${NODE}/pi/inbox/${encodeURIComponent(sessionId)}?wait=20`, { signal: ctl.signal });
        clearTimeout(t);
        if (r.ok) {
          const j = await r.json();
          if (j && j.prompt) {
            pi.sendUserMessage(j.prompt, { deliverAs: "followUp" });
          }
        } else {
          await new Promise((res) => setTimeout(res, 5000));
        }
      } catch {
        await new Promise((res) => setTimeout(res, 5000));
      }
    }
    polling = false;
  }

  pi.on("session_start", async (_event, ctx) => {
    sessionId = ctx.sessionManager.getSessionId();
    cwd = ctx.cwd;
    try {
      model = (ctx as any).model?.id ?? "";
    } catch {}
    status = "idle";
    await report("session_start", { session_file: ctx.sessionManager.getSessionFile() });
    void pollInbox();
  });

  pi.on("session_shutdown", async () => {
    stopped = true;
    await report("session_shutdown");
  });

  pi.on("agent_start", async () => {
    status = "busy";
    await report("agent_start");
  });

  pi.on("agent_end", async () => {
    status = "idle";
    await report("agent_end");
  });

  pi.on("tool_call", async (event, ctx) => {
    if (!armed || !sessionId) return undefined;
    const r = await post(
      "/pi/toolcall",
      { session_id: sessionId, tool_name: event.toolName, tool_input: event.input, cwd },
      1_800_000,
    );
    if (r && r.behavior === "deny") {
      return { block: true, reason: r.reason || "denied from agentdash" };
    }
    return undefined;
  });
}
