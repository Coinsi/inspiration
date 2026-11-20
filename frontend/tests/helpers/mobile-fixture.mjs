// Browser-only fixtures. Every API request is fulfilled locally; no account,
// project, provider or model service is contacted.
export const projectId = "mobile-fixture";
const now = "2026-10-23T08:00:00Z";
const project = { id: projectId, name: "黎明之前 · 移动端创作样片", code: "DAWN", description: "在手机上继续创作，整理角色、分镜与影片。", settings: {}, created_at: now };
const assets = ["林间旅人", "旧城街角", "雨后的灯光"].map((name, i) => ({ id: `asset-${i}`, name, code: `AS0${i + 1}`, type: i ? "location" : "character", status: "draft", summary: "窗边光线映出人物的轮廓，远处传来细微的雨声。", tags: ["开场", "参考"], representative_blob_hash: "fixture-image", shot_count: 2, ref_count: 1, gen_count: 2 }));
const shots = ["清晨的告别", "归途的背影"].map((title, i) => ({ id: `shot-${i}`, title, code: `SH0${i + 1}`, scene_id: "scene-1", description: "人物穿过洒满晨光的长廊。", ordinal: i + 1, production_status: "approved", selected_generation_id: `gen-${i}`, storyboard: { duration_sec: 3, shot_size: "medium", dialogue: "我们会再见的。" } }));
const generations = shots.map((s, i) => ({ id: `gen-${i}`, target_type: "shot", target_id: s.id, provider: "mock", output_type: "image", output_blob_hash: "fixture-image", prompt_snapshot: "清晨的电影画面", is_selected: true, is_favorite: false, input_refs: {}, created_at: now }));
const media = { id: "media-1", name: "晨光参考图", description: "开场镜头的视觉参考", tags: ["开场"], metadata_revision: 1, deleted_at: null, usage_count: 1, versions: [{ id: "version-1", media_id: "media-1", media_type: "image", ordinal: 1, filename: "morning-reference.jpg", size_bytes: 320000, uploaded_bytes: 320000, fingerprint: "fixture", chunk_size: 1048576, status: "ready", error: null, original_hash: "fixture-image", proxy_hash: null, poster_hash: "fixture-image", duration_ms: null, width: 960, height: 640 }] };
const material = { id: media.id, name: media.name, source: "upload", kind: "image", status: "ready", adopted: true, is_test: false, created_at: now, deleted_at: null, poster_hash: "fixture-image", blob_hash: "fixture-image", preview_hash: null, size_bytes: 320000, duration_ms: null, version_id: "version-1", tags: ["开场"] };
const canvas = { id: "canvas-1", name: "开场创意画布", revision: 1, document: { nodes: [{ id: "node-1", type: "studio", position: { x: 30, y: 40 }, data: { kind: "text", label: "清晨的告别", text: "人物穿过洒满晨光的长廊，停下脚步回望。", target_type: "shot", target_id: null, generation_id: null, generate: { provider: "mock", request_type: "image", count: 1, params: {}, provider_params: {}, use_references: false } } }], edges: [], viewport: { x: 10, y: 10, zoom: 0.8 } } };
const stage = { id: "stage-1", name: "窗边对话预演", revision: 1, document: { objects: [{ id: "person-1", name: "旅人", kind: "person", position: [0, 0, 0], rotation: 0, scale: 1, color: "#c4a574", pose: "standing" }], camera: { position: [6, 4, 8], target: [0, 1, 0], fov: 45 }, end_camera: { position: [4, 3, 6], target: [0, 1, 0], fov: 45 }, lighting: { position: [4, 7, 4], intensity: 2, ambient: 0.7, color: "#ffffff" }, background: "#1b222e", ground: "#434c5d", aspect: "16:9", duration: 5 } };
const timeline = { id: "timeline-1", name: "黎明之前 · 粗剪", kind: "main", revision: 1, items: [] };
const document = { revision: 1, items: shots.map((s, i) => ({ id: `clip-${i}`, shot_id: s.id, generation_id: `gen-${i}`, duration_ms: 3000, in_point_ms: 0, out_point_ms: 0 })), audio: [], subtitles: [], visuals: [] };
const task = { id: "job-1", kind: "generation", target_type: "shot", target_id: "shot-0", target_name: "清晨的告别", target_available: true, status: "succeeded", provider: "mock", model_name: "测试模型", request_type: "image", operation: "generate", created_at: now, updated_at: now, error: null, has_warnings: false, result_count: 1, last_event: "图像生成完成" };
const conversation = { id: "chat-fixture", title: "开场镜头讨论", work_id: null, archived: false, updated_at: now, has_older: false, messages: [
  { id: "message-1", ordinal: 1, role: "user", content: "请给这段清晨告别安排三个镜头，保持人物的克制与安静。", status: "completed", created_at: now, context: {}, progress: [] },
  { id: "message-2", ordinal: 2, role: "assistant", content: "先用远景交代被晨光照亮的长廊，再切到旅人的脚步。最后停在他回望的近景，让动作和光线共同表达未说出口的告别。", status: "completed", created_at: now, context: {}, progress: [] },
] };
const emptyCatalog = { channels: [], legacy_count: 0, suggestions: {}, protocols: {} };
const emptyCollections = new Set(["/novels", "/scripts", "/works", "/works/timeline-links", "/providers", "/baselines", "/skills/templates", "/agent/tools", "/canvases/canvas-1/runs", "/canvases/canvas-1/history", "/creative-tool-tasks/recent", "/library/uploads", "/library/folders", "/director-scenes/stage-1/history", "/timelines/timeline-1/history", "/skills/skill-1/history"]);
const skill = { id: "skill-1", project_id: projectId, name: "电影镜头设计", description: "组织景别、光线与角色动作，生成清晰的镜头说明。", instructions: "从人物动作和情绪出发设计镜头。", required_tools: [], source: "manual", category: "shot", files: [{ path: "references/shot-guide.md", content: "# 镜头参考\n先明确人物目标，再安排景别、运动与光线。", encoding: "utf-8" }], file_count: 2, revision: 1 };
const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="960" height="640"><defs><linearGradient id="a"><stop stop-color="#274f61"/><stop offset="1" stop-color="#d7b57c"/></linearGradient></defs><rect width="960" height="640" fill="url(#a)"/><circle cx="670" cy="150" r="85" fill="#f7e1ac"/><path d="M0 490L270 280L510 480L790 300L960 460V640H0Z" fill="#273e4b"/><text x="48" y="90" fill="#fff" font-size="35">黎明之前 · 视觉参考</text></svg>';

export async function installMobileFixture(context, { theme = "dark" } = {}) {
  const requests = [], blockedWrites = [], unmatched = [];
  await context.addInitScript(({ projectId, theme }) => {
    localStorage.setItem("inspiration_token", "mobile-fixture-token");
    localStorage.setItem("inspiration_lang", "zh");
    localStorage.setItem("inspiration_theme", theme);
    localStorage.setItem(`wb.chapter.${projectId}`, JSON.stringify(""));
  }, { projectId, theme });
  await context.route("**/api/**", async route => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname.replace("/api/v1", "");
    requests.push(`${req.method()} ${path}${url.search}`);
    const send = (json, status = 200) => route.fulfill({ status, json });
    if (req.method() !== "GET") { blockedWrites.push(`${req.method()} ${path}`); return send({ error: { message: "Browser fixture blocks server mutations" } }, 403); }
    if (path.includes("/blobs/")) return route.fulfill({ contentType: "image/svg+xml", body: svg });
    if (path === "/me") return send({ user: { id: "mobile-user", username: "mobile-test", display_name: "创作者", is_active: true }, memberships: [{ project_id: projectId, role: "admin" }] });
    if (path === "/me/model-channels") return send(emptyCatalog);
    if (path === "/model-provider-templates") return send([]);
    if (path === "/projects" || path === "/projects/with-cover") return send([project]);
    if (path === `/projects/${projectId}`) return send(project);
    const sub = path.replace(`/projects/${projectId}`, "");
    if (emptyCollections.has(sub)) return send([]);
    if (sub === "/conversations") return send([conversation]);
    if (sub === "/conversations/chat-fixture") return send(conversation);
    if (sub === "/attachments" || sub === "/workbench/references" || sub === "/audio-sources") return send({ items: [], has_more: false });
    if (sub === "/assets") return send(assets);
    if (sub === "/shots") return send(shots);
    if (sub === "/scenes") return send([{ id: "scene-1", code: "SC01", title: "清晨", shot_count: 2 }]);
    if (sub.endsWith("/generations")) return send(generations);
    if (sub.match(/^\/generations\/gen-/)) return send(generations.find(g => sub.endsWith(g.id)) ?? generations[0]);
    if (sub === "/library/materials") return send({ items: [material], total: 1 });
    if (sub === "/library/items" || sub === "/library/catalog") return send({ items: [media], total: 1 });
    if (sub === "/library/items/media-1") return send(media);
    if (sub === "/library/version-catalog") return send({ items: [], total: 0, has_more: false });
    if (sub === "/library/processing/providers") return send({ enabled: false, automatic: false, providers: [] });
    if (sub === "/activity") return send({ items: [task], total: 1, has_more: false, counts: { succeeded: 1, all: 1 } });
    if (sub === "/jobs") return send([{ ...task, input_snapshot: { operation: "generate" } }]);
    if (sub === "/canvases") return send([canvas]);
    if (sub === "/canvases/canvas-1") return send(canvas);
    if (sub === "/director-scenes") return send([stage]);
    if (sub === "/director-scenes/stage-1") return send(stage);
    if (sub === "/timelines") return send([timeline]);
    if (sub === "/timelines/timeline-1/document") return send(document);
    if (sub === "/timeline-sources") return send({ items: generations.map((g, i) => ({ id: g.id, source_type: "generation", kind: "image", title: shots[i].title, blob_hash: g.output_blob_hash, thumbnail_hash: g.output_blob_hash, duration_ms: null, ordinal: null })), has_more: false });
    if (sub === "/skills/catalog") return send({ items: [skill], total: 1 });
    if (sub === "/skills/skill-1") return send(skill);
    if (sub === "/model-channels") return send(emptyCatalog);
    if (sub === "/prompts") return send([{ id: "prompt-1", name: "清晨的电影质感", positive: "自然光线、柔和色彩、细腻的画面层次", negative: "模糊、噪点", tags: [], created_at: now, updated_at: now }]);
    if (sub === "/prompt-fragments") return send([{ id: "fragment-1", name: "逆光与轮廓", text: "柔和逆光，清晰轮廓，电影画面", category: "quality", created_at: now, updated_at: now }]);
    if (sub === "/quota") return send({ used_cost: 12, limit_cost: 100 });
    if (sub.endsWith("/capabilities")) return send({ modalities: ["image", "video"], features: [], param_schema: {} });
    unmatched.push(path);
    return send({ error: { code: "UNMATCHED_FIXTURE", message: `Missing browser fixture: ${path}` } }, 501);
  });
  return { requests, blockedWrites, unmatched };
}
