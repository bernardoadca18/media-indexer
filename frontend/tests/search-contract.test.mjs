import assert from "node:assert/strict";
import test from "node:test";
import { api } from "../src/api/client.ts";

test("media search uses the existing POST project contract and preserves response metadata", async () => {
  const original = globalThis.fetch;
  const projectId = "00000000-0000-4000-8000-000000000001";
  const response = {
    query: "paisagem",
    scope: "project",
    project_id: projectId,
    results: [],
    searched_sessions: 2,
    skipped_sessions: 1,
    session_summaries: [],
  };
  globalThis.fetch = async (url, options) => {
    assert.equal(new URL(url).pathname, `/media-search/projects/${projectId}`);
    assert.equal(options.method, "POST");
    assert.deepEqual(JSON.parse(options.body), {
      query: "paisagem",
      max_results: 20,
    });
    return new Response(JSON.stringify(response), { status: 200 });
  };
  try {
    assert.deepEqual(await api.searchSessions(projectId, "paisagem"), response);
  } finally {
    globalThis.fetch = original;
  }
});

test("search failure is not converted to a successful empty response", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () =>
    new Response("No completed temporal index", { status: 409 });
  try {
    await assert.rejects(
      api.searchSessions("project", "query"),
      /409.*No completed temporal index/,
    );
  } finally {
    globalThis.fetch = original;
  }
});

test("a selected search index and interval are retained by the timeline request", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async (url) => {
    const params = new URL(url).searchParams;
    assert.equal(params.get("temporal_index_id"), "original-index");
    assert.equal(params.get("start_seconds"), "12");
    assert.equal(params.get("end_seconds"), "24");
    return new Response("{}", { status: 200 });
  };
  try {
    await api.getSemanticTimeline("session", {
      temporal_index_id: "original-index",
      start_seconds: 12,
      end_seconds: 24,
    });
  } finally {
    globalThis.fetch = original;
  }
});
