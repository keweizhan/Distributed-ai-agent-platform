const BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

// ── token helpers ──────────────────────────────────────────────────────────

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("access_token");
}

export function setToken(token: string): void {
  localStorage.setItem("access_token", token);
}

export function clearToken(): void {
  localStorage.removeItem("access_token");
}

// ── low-level fetch ────────────────────────────────────────────────────────

type FetchOptions = {
  method?: string;
  body?: unknown;
  auth?: boolean;
  form?: boolean; // send as application/x-www-form-urlencoded
};

async function request<T>(path: string, opts: FetchOptions = {}): Promise<T> {
  const { method = "GET", body, auth = true, form = false } = opts;

  const headers: Record<string, string> = {};
  if (auth) {
    const token = getToken();
    if (token) headers["Authorization"] = `Bearer ${token}`;
  }

  let encodedBody: string | undefined;
  if (body) {
    if (form) {
      headers["Content-Type"] = "application/x-www-form-urlencoded";
      encodedBody = new URLSearchParams(body as Record<string, string>).toString();
    } else {
      headers["Content-Type"] = "application/json";
      encodedBody = JSON.stringify(body);
    }
  }

  const res = await fetch(`${BASE}${path}`, { method, headers, body: encodedBody });

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err?.detail ?? "Request failed");
  }

  return res.json() as Promise<T>;
}

// ── types ──────────────────────────────────────────────────────────────────

export type User = {
  id: string;
  email: string;
  created_at: string;
};

export type TokenResponse = {
  access_token: string;
  token_type: string;
};

export type JobStatus =
  | "pending"
  | "planning"
  | "planned"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled";

export type Job = {
  id: string;
  workspace_id: string | null;
  prompt: string;
  status: JobStatus;
  result: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
};

export type Task = {
  id: string;
  job_id: string;
  step_id: string | null;
  task_type: string;
  name: string;
  description: string | null;
  tool_name: string | null;
  tool_input: Record<string, unknown> | null;
  tool_output: Record<string, unknown> | null;
  dependencies: string[];
  priority: number;
  status: string;
  error: string | null;
  sequence: number;
  expected_output: string | null;
  attempt_count: number;
  started_at: string | null;
  finished_at: string | null;
};

export type JobDetail = Job & { tasks: Task[] };

// ── auth endpoints ─────────────────────────────────────────────────────────

export async function register(
  email: string,
  password: string,
  workspaceName?: string
): Promise<User> {
  return request<User>("/auth/register", {
    method: "POST",
    auth: false,
    body: { email, password, workspace_name: workspaceName ?? null },
  });
}

export async function login(email: string, password: string): Promise<TokenResponse> {
  return request<TokenResponse>("/auth/token", {
    method: "POST",
    auth: false,
    form: true,
    body: { username: email, password },
  });
}

export async function getMe(): Promise<User> {
  return request<User>("/auth/me");
}

// ── job endpoints ──────────────────────────────────────────────────────────

export async function listJobs(): Promise<Job[]> {
  return request<Job[]>("/jobs");
}

export async function createJob(prompt: string): Promise<Job> {
  return request<Job>("/jobs", { method: "POST", body: { prompt } });
}

export async function getJob(id: string): Promise<JobDetail> {
  return request<JobDetail>(`/jobs/${id}`);
}

export async function cancelJob(id: string): Promise<Job> {
  return request<Job>(`/jobs/${id}/cancel`, { method: "POST" });
}

export async function deleteJob(id: string): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>(`/jobs/${id}`, { method: "DELETE" });
}

// ── document endpoints ─────────────────────────────────────────────────────

export type IngestDocumentResponse = {
  document_id: string;
  status: string;
  title: string;
  chunk_count: number;
};

export type DocumentRecord = {
  id: string;
  title: string;
  chunk_count: number;
  status: "ingesting" | "ready" | "failed";
  created_at: string;
};

export async function ingestDocument(
  title: string,
  content: string
): Promise<IngestDocumentResponse> {
  return request<IngestDocumentResponse>("/documents", {
    method: "POST",
    body: { title, content },
  });
}

export async function listDocuments(): Promise<DocumentRecord[]> {
  return request<DocumentRecord[]>("/documents");
}

export async function uploadDocument(
  file: File,
  title: string,
): Promise<IngestDocumentResponse> {
  // multipart/form-data — cannot use the JSON request() helper
  const token = getToken();
  const form = new FormData();
  form.append("file", file);
  form.append("title", title);

  const res = await fetch(`${BASE}/documents/upload`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: form,
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err?.detail ?? "Upload failed");
  }
  return res.json() as Promise<IngestDocumentResponse>;
}

export async function deleteDocument(id: string): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>(`/documents/${id}`, { method: "DELETE" });
}
