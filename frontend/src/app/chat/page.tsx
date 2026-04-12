"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  clearToken,
  createJob,
  deleteDocument,
  getJob,
  getMe,
  getToken,
  ingestDocument,
  listDocuments,
  uploadDocument,
  type DocumentRecord,
  type JobDetail,
  type Task,
} from "@/lib/api";

// ── Types ────────────────────────────────────────────────────────────────────

type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  jobStatus?: string;
  jobId?: string;    // set on terminal assistant messages to link /jobs/{id}
  tasks?: Task[];
};

type SourceChunk = {
  document_id: string;
  title: string;
  chunk_index: number;
  text: string;
  score: number;
};

// ── Constants ────────────────────────────────────────────────────────────────

const TERMINAL = new Set(["succeeded", "failed", "cancelled"]);
const POLL_MS = 2000;

// Step status icon: checkmark for done, animated dot for running, plain dot otherwise
function StepIcon({ status }: { status: string }) {
  if (status === "succeeded")
    return (
      <svg className="h-3.5 w-3.5 shrink-0 text-green-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
      </svg>
    );
  if (status === "running")
    return <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-blue-500 animate-pulse" />;
  if (status === "failed")
    return <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-red-500" />;
  // pending / queued / skipped
  return <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-gray-300" />;
}

// ── StatusBadge — document ingestion status pill ─────────────────────────────

function StatusBadge({ status }: { status: string }) {
  if (status === "ready")
    return <span className="rounded-full bg-green-100 px-2 py-0.5 text-xs font-medium text-green-700">Ready</span>;
  if (status === "failed")
    return <span className="rounded-full bg-red-100 px-2 py-0.5 text-xs font-medium text-red-600">Failed</span>;
  return <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-700">Ingesting…</span>;
}

// ── Source extraction ────────────────────────────────────────────────────────

/**
 * Pull retrieval chunks out of task outputs.
 * Looks for the first succeeded retrieval task and returns its chunks array.
 * Returns [] when no retrieval was used or the task is not yet complete.
 */
function extractSources(tasks: Task[]): SourceChunk[] {
  for (const t of tasks) {
    if (
      t.tool_name === "retrieval" &&
      t.status === "succeeded" &&
      t.tool_output
    ) {
      const chunks = t.tool_output.chunks;
      if (Array.isArray(chunks) && chunks.length > 0) {
        return chunks as SourceChunk[];
      }
    }
  }
  return [];
}

// ── SourcesBlock component ───────────────────────────────────────────────────

function SourcesBlock({ tasks }: { tasks: Task[] }) {
  const sources = extractSources(tasks);
  if (sources.length === 0) return null;

  return (
    <div className="pl-1 space-y-1.5">
      <p className="text-xs font-medium text-gray-400 uppercase tracking-wide">
        Sources
      </p>
      {sources.map((s, i) => (
        <div
          key={`${s.document_id}-${s.chunk_index}-${i}`}
          className="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2"
        >
          <p className="text-xs font-medium text-gray-700 leading-snug">
            {s.title}
            {s.chunk_index > 0 && (
              <span className="ml-1.5 font-normal text-gray-400">
                §{s.chunk_index + 1}
              </span>
            )}
          </p>
          <p className="mt-0.5 text-xs text-gray-500 line-clamp-2 leading-relaxed">
            {s.text}
          </p>
        </div>
      ))}
    </div>
  );
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function progressText(detail: JobDetail): string {
  const { status, tasks } = detail;
  if (status === "pending")  return "Thinking...";
  if (status === "planning") return "Planning...";
  if (status === "planned")  return "Running tools...";
  if (status === "running") {
    const synthRunning = tasks.some(
      (t) => t.task_type === "synthesis" && t.status === "running"
    );
    return synthRunning ? "Synthesizing answer..." : "Running tools...";
  }
  return "Working...";
}

function jobToMessages(detail: JobDetail): Message[] {
  const content =
    detail.status === "succeeded"
      ? (detail.result ?? "Done.")
      : detail.status === "failed"
      ? "The agent couldn't complete this request. Try rephrasing your prompt."
      : progressText(detail);

  return [
    { id: `${detail.id}-user`, role: "user", content: detail.prompt },
    {
      id:        `${detail.id}-assistant`,
      role:      "assistant",
      content,
      jobStatus: detail.status,
      jobId:     detail.id,
      tasks:     detail.tasks,
    },
  ];
}

// Animated three-dot indicator shown while a job is in-progress
function LoadingDots() {
  return (
    <span className="ml-1 inline-flex items-center gap-0.5 align-middle">
      <span className="h-1 w-1 animate-bounce rounded-full bg-current [animation-delay:-0.3s]" />
      <span className="h-1 w-1 animate-bounce rounded-full bg-current [animation-delay:-0.15s]" />
      <span className="h-1 w-1 animate-bounce rounded-full bg-current" />
    </span>
  );
}

function generateId() {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  return Math.random().toString(36).substring(2) + Date.now().toString(36);
}

// ── Component ────────────────────────────────────────────────────────────────

function ChatPage() {
  const router       = useRouter();
  const searchParams = useSearchParams();
  const jobId        = searchParams.get("jobId");

  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput]       = useState("");
  const [busy, setBusy]         = useState(false);
  const bottomRef   = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // ── Ingest modal state ───────────────────────────────────────────────────
  const [showIngest, setShowIngest]         = useState(false);
  const [ingestTitle, setIngestTitle]       = useState("");
  const [ingestContent, setIngestContent]   = useState("");
  const [ingestFile, setIngestFile]         = useState<File | null>(null);   // PDF only
  const [ingestFileError, setIngestFileError] = useState<string | null>(null);
  const [ingestBusy, setIngestBusy]         = useState(false);
  const [ingestStatus, setIngestStatus]     = useState<{ ok: boolean; text: string } | null>(null);

  // ── Knowledge library modal state ────────────────────────────────────────
  const [showLibrary, setShowLibrary]       = useState(false);
  const [libraryDocs, setLibraryDocs]       = useState<DocumentRecord[]>([]);
  const [libraryLoading, setLibraryLoading] = useState(false);
  const [libraryError, setLibraryError]     = useState<string | null>(null);

  // ── Auth guard ──────────────────────────────────────────────────────────
  useEffect(() => {
    if (!getToken()) { router.replace("/login"); return; }
    getMe().catch(() => { clearToken(); router.replace("/login"); });
  }, [router]);

  // ── Load historical job when ?jobId is present ──────────────────────────
  // Resets the message list whenever the URL's jobId changes (including to null).
  useEffect(() => {
    setMessages([]);
    if (!jobId) return;

    getJob(jobId)
      .then((detail) => setMessages(jobToMessages(detail)))
      .catch(() =>
        setMessages([
          {
            id:        "load-error",
            role:      "assistant",
            content:   "Failed to load this conversation.",
            jobStatus: "failed",
          },
        ])
      );
  }, [jobId]);

  // ── Auto-scroll ─────────────────────────────────────────────────────────
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // ── Helpers ─────────────────────────────────────────────────────────────

  function patchAssistant(id: string, patch: Partial<Message>) {
    setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, ...patch } : m)));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const prompt = input.trim();
    if (!prompt || busy) return;

    setBusy(true);
    setInput("");
    if (textareaRef.current) textareaRef.current.style.height = "auto";

    const userId      = generateId();
    const assistantId = generateId();

    setMessages((prev) => [
      ...prev,
      { id: userId,      role: "user",     content: prompt },
      { id: assistantId, role: "assistant", content: "Thinking...", jobStatus: "pending" },
    ]);

    try {
      const job = await createJob(prompt);

      const interval = setInterval(async () => {
        try {
          const detail = await getJob(job.id);

          if (TERMINAL.has(detail.status)) {
            clearInterval(interval);
            setBusy(false);

            patchAssistant(assistantId, {
              content:
                detail.status === "succeeded"
                  ? (detail.result ?? "Done.")
                  : "The agent couldn't complete this request. Try rephrasing your prompt.",
              jobStatus: detail.status,
              jobId:     job.id,
              tasks:     detail.tasks,
            });
          } else {
            patchAssistant(assistantId, {
              content:   progressText(detail),
              jobStatus: detail.status,
              tasks:     detail.tasks,
            });
          }
        } catch {
          clearInterval(interval);
          setBusy(false);
          patchAssistant(assistantId, {
            content:   "Failed to fetch job status.",
            jobStatus: "failed",
          });
        }
      }, POLL_MS);
    } catch (err: unknown) {
      setBusy(false);
      const msg = err instanceof Error ? err.message : "Failed to submit.";
      patchAssistant(assistantId, { content: `Error: ${msg}`, jobStatus: "failed" });
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e as unknown as React.FormEvent);
    }
  }

  function handleInputChange(e: React.ChangeEvent<HTMLTextAreaElement>) {
    setInput(e.target.value);
    e.target.style.height = "auto";
    e.target.style.height = `${Math.min(e.target.scrollHeight, 120)}px`;
  }

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setIngestFileError(null);

    const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
    if (!["txt", "md", "pdf"].includes(ext)) {
      setIngestFileError("Unsupported type. Please use .txt, .md, or .pdf.");
      e.target.value = "";
      return;
    }

    // Auto-fill title from filename when title field is empty
    if (!ingestTitle.trim()) {
      setIngestTitle(file.name.replace(/\.[^.]+$/, "").replace(/[-_]/g, " "));
    }

    if (ext === "pdf") {
      // PDF text extraction happens server-side
      setIngestFile(file);
      setIngestContent("");
    } else {
      // txt / md — read on the client, populate content textarea
      const text = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result as string);
        reader.onerror = reject;
        reader.readAsText(file, "utf-8");
      });
      setIngestFile(null);
      setIngestContent(text);
    }
  }

  function clearFile() {
    setIngestFile(null);
    setIngestFileError(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  async function handleIngest(e: React.FormEvent) {
    e.preventDefault();
    const title = ingestTitle.trim();
    if (!title) return;
    if (!ingestFile && !ingestContent.trim()) return;

    setIngestBusy(true);
    setIngestStatus(null);
    try {
      if (ingestFile) {
        await uploadDocument(ingestFile, title);
      } else {
        await ingestDocument(title, ingestContent.trim());
      }
      setIngestStatus({ ok: true, text: "Document added — the agent can now retrieve it." });
      setIngestTitle("");
      setIngestContent("");
      setIngestFile(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
      setTimeout(() => { setShowIngest(false); setIngestStatus(null); }, 1800);
    } catch (err) {
      setIngestStatus({
        ok: false,
        text: err instanceof Error ? err.message : "Failed to add document.",
      });
    } finally {
      setIngestBusy(false);
    }
  }

  function closeIngestModal() {
    setShowIngest(false);
    setIngestTitle("");
    setIngestContent("");
    setIngestFile(null);
    setIngestFileError(null);
    setIngestStatus(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  async function openLibrary() {
    setShowLibrary(true);
    setLibraryError(null);
    setLibraryLoading(true);
    try {
      setLibraryDocs(await listDocuments());
    } catch (err) {
      setLibraryError(err instanceof Error ? err.message : "Failed to load documents.");
    } finally {
      setLibraryLoading(false);
    }
  }

  async function handleDeleteDocument(id: string) {
    if (!window.confirm("Remove this document from the knowledge base?")) return;
    try {
      await deleteDocument(id);
      setLibraryDocs((prev) => prev.filter((d) => d.id !== id));
    } catch (err) {
      alert(err instanceof Error ? err.message : "Delete failed.");
    }
  }

  // ── Render ──────────────────────────────────────────────────────────────

  return (
    <div className="flex flex-1 flex-col overflow-hidden">

      {/* ── Add-knowledge modal ──────────────────────────────────────────── */}
      {showIngest && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4"
          onClick={(e) => { if (e.target === e.currentTarget) closeIngestModal(); }}
        >
          <div className="w-full max-w-md rounded-2xl bg-white shadow-xl">
            <div className="flex items-center justify-between border-b border-gray-100 px-5 py-4">
              <h2 className="text-sm font-semibold text-gray-800">Add to knowledge base</h2>
              <button
                onClick={closeIngestModal}
                className="text-gray-400 hover:text-gray-600"
                aria-label="Close"
              >
                ✕
              </button>
            </div>

            <form onSubmit={handleIngest} className="px-5 py-4 space-y-3">
              {/* Title */}
              <div>
                <label className="mb-1 block text-xs font-medium text-gray-600">Title</label>
                <input
                  type="text"
                  required
                  value={ingestTitle}
                  onChange={(e) => setIngestTitle(e.target.value)}
                  placeholder="e.g. Q3 Product Report"
                  className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-800 placeholder-gray-400 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                />
              </div>

              {/* File picker */}
              <div>
                <label className="mb-1 block text-xs font-medium text-gray-600">
                  Upload file
                  <span className="ml-1 font-normal text-gray-400">.txt · .md · .pdf</span>
                </label>

                {ingestFile ? (
                  /* PDF selected — show badge instead of file input */
                  <div className="flex items-center justify-between rounded-lg border border-blue-200 bg-blue-50 px-3 py-2">
                    <span className="text-sm text-blue-700 truncate">
                      📄 {ingestFile.name}
                      <span className="ml-2 text-xs text-blue-400">
                        ({(ingestFile.size / 1024).toFixed(0)} KB)
                      </span>
                    </span>
                    <button
                      type="button"
                      onClick={clearFile}
                      className="ml-2 shrink-0 text-xs text-blue-400 hover:text-blue-700"
                    >
                      ✕ clear
                    </button>
                  </div>
                ) : (
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept=".txt,.md,.pdf"
                    onChange={handleFileChange}
                    className="w-full text-sm text-gray-500 file:mr-3 file:rounded-lg file:border-0 file:bg-gray-100 file:px-3 file:py-1.5 file:text-xs file:font-medium file:text-gray-600 hover:file:bg-gray-200"
                  />
                )}
                {ingestFileError && (
                  <p className="mt-1 text-xs text-red-500">{ingestFileError}</p>
                )}
              </div>

              {/* Content textarea — hidden when a PDF is staged */}
              {!ingestFile && (
                <div>
                  <label className="mb-1 block text-xs font-medium text-gray-600">
                    Content
                    <span className="ml-1 font-normal text-gray-400">or paste text directly</span>
                  </label>
                  <textarea
                    rows={6}
                    value={ingestContent}
                    onChange={(e) => setIngestContent(e.target.value)}
                    placeholder="Paste plain text here…"
                    className="w-full resize-none rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-800 placeholder-gray-400 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                  />
                </div>
              )}

              {ingestStatus && (
                <p className={`text-xs ${ingestStatus.ok ? "text-green-600" : "text-red-600"}`}>
                  {ingestStatus.text}
                </p>
              )}

              <div className="flex justify-end gap-2 pt-1">
                <button
                  type="button"
                  onClick={closeIngestModal}
                  className="rounded-lg border border-gray-200 px-4 py-1.5 text-sm text-gray-500 hover:bg-gray-50"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={ingestBusy || !ingestTitle.trim() || (!ingestFile && !ingestContent.trim())}
                  className="rounded-lg bg-blue-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-40"
                >
                  {ingestBusy ? "Adding…" : "Add document"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* ── Knowledge library modal ─────────────────────────────────────── */}
      {showLibrary && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4"
          onClick={(e) => { if (e.target === e.currentTarget) setShowLibrary(false); }}
        >
          <div className="w-full max-w-lg rounded-2xl bg-white shadow-xl flex flex-col max-h-[80vh]">
            {/* Header */}
            <div className="flex items-center justify-between border-b border-gray-100 px-5 py-4 shrink-0">
              <h2 className="text-sm font-semibold text-gray-800">Knowledge base</h2>
              <button
                onClick={() => setShowLibrary(false)}
                className="text-gray-400 hover:text-gray-600"
                aria-label="Close"
              >
                ✕
              </button>
            </div>

            {/* Body */}
            <div className="overflow-y-auto flex-1 px-5 py-4">
              {libraryLoading && (
                <p className="text-sm text-gray-400 text-center py-8">Loading…</p>
              )}
              {libraryError && (
                <p className="text-sm text-red-500 text-center py-8">{libraryError}</p>
              )}
              {!libraryLoading && !libraryError && libraryDocs.length === 0 && (
                <p className="text-sm text-gray-400 text-center py-8">
                  No documents yet — click <strong>+ Add knowledge</strong> to get started.
                </p>
              )}
              {!libraryLoading && !libraryError && libraryDocs.length > 0 && (
                <ul className="space-y-2">
                  {libraryDocs.map((doc) => (
                    <li
                      key={doc.id}
                      className="group flex items-start justify-between gap-3 rounded-lg border border-gray-100 px-4 py-3 hover:bg-gray-50"
                    >
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-medium text-gray-800">{doc.title}</p>
                        <p className="mt-0.5 text-xs text-gray-400">
                          {doc.chunk_count} chunk{doc.chunk_count !== 1 ? "s" : ""}
                          {" · "}
                          {new Date(doc.created_at).toLocaleDateString(undefined, {
                            month: "short", day: "numeric", year: "numeric",
                          })}
                        </p>
                      </div>
                      <div className="flex shrink-0 items-center gap-2">
                        <StatusBadge status={doc.status} />
                        <button
                          onClick={() => handleDeleteDocument(doc.id)}
                          className="opacity-0 group-hover:opacity-100 rounded p-1 text-gray-400 hover:bg-red-50 hover:text-red-500 transition-opacity"
                          aria-label="Delete document"
                          title="Delete"
                        >
                          <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8}
                              d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                          </svg>
                        </button>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            {/* Footer */}
            <div className="shrink-0 border-t border-gray-100 px-5 py-3 flex justify-end">
              <button
                onClick={() => { setShowLibrary(false); setShowIngest(true); }}
                className="text-sm text-blue-600 hover:underline"
              >
                + Add document
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Message list ─────────────────────────────────────────────────── */}
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-3xl space-y-6 px-4 py-6">

          {/* Empty state */}
          {messages.length === 0 && (
            <div className="flex flex-col items-center justify-center pt-24 text-center">
              <p className="text-2xl font-semibold text-gray-800">AI Agent Platform</p>
              <p className="mt-2 max-w-sm text-sm text-gray-400">
                Ask anything — the agent plans and executes multi-step tasks automatically.
              </p>
              <div className="mt-6 flex flex-wrap justify-center gap-2">
                {[
                  "Search for the latest AI research papers",
                  "Summarise today's tech news",
                  "Find the top 5 Python web frameworks",
                ].map((example) => (
                  <button
                    key={example}
                    onClick={() => setInput(example)}
                    className="rounded-full border border-gray-200 bg-white px-4 py-1.5 text-xs text-gray-500 hover:border-blue-300 hover:text-blue-600"
                  >
                    {example}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Messages */}
          {messages.map((msg) => (
            <div
              key={msg.id}
              className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
            >
              {msg.role === "user" ? (
                /* User bubble */
                <div className="max-w-[75%] rounded-2xl bg-blue-600 px-4 py-2.5 text-sm text-white">
                  {msg.content}
                </div>
              ) : (
                /* Assistant bubble + step progress */
                <div className="max-w-[85%] space-y-2">
                  <div
                    className={`rounded-2xl border px-4 py-3 text-sm leading-relaxed ${
                      msg.jobStatus === "failed"
                        ? "border-red-200 bg-red-50 text-red-700"
                        : msg.jobStatus === "succeeded"
                        ? "border-gray-200 bg-white text-gray-800"
                        : "border-gray-200 bg-gray-50 text-gray-500"
                    }`}
                  >
                    {msg.jobStatus && !TERMINAL.has(msg.jobStatus) ? (
                      /* In-progress: single-line status + animated dots */
                      <p className="flex items-center">
                        {msg.content}
                        <LoadingDots />
                      </p>
                    ) : (
                      /* Terminal: preserve whitespace for multi-line LLM output */
                      <p className="whitespace-pre-wrap">{msg.content}</p>
                    )}
                  </div>

                  {/* Step list — shown while running and kept (dimmed) after terminal */}
                  {msg.tasks && msg.tasks.length > 0 && (
                    <div className={`space-y-1.5 pl-1 transition-opacity ${
                      TERMINAL.has(msg.jobStatus ?? "") ? "opacity-50" : ""
                    }`}>
                      {msg.tasks
                        .filter((t) => t.task_type !== "synthesis")
                        .sort((a, b) => a.sequence - b.sequence)
                        .map((t) => (
                          <div key={t.id} className="flex items-center gap-2 text-xs text-gray-500">
                            <StepIcon status={t.status} />
                            <span className={t.status === "running" ? "font-medium text-blue-600" : ""}>
                              {t.name}
                            </span>
                          </div>
                        ))}
                    </div>
                  )}

                  {/* Sources — shown after terminal state when retrieval was used */}
                  {TERMINAL.has(msg.jobStatus ?? "") && msg.tasks && (
                    <SourcesBlock tasks={msg.tasks} />
                  )}

                  {/* "View technical details" — shown after job reaches terminal state */}
                  {TERMINAL.has(msg.jobStatus ?? "") && msg.jobId && (
                    <div className="pl-1">
                      <Link
                        href={`/jobs/${msg.jobId}`}
                        className="text-xs text-gray-400 hover:text-gray-600 hover:underline"
                      >
                        View technical details →
                      </Link>
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}

          <div ref={bottomRef} />
        </div>
      </div>

      {/* ── Input ────────────────────────────────────────────────────────── */}
      <div className="shrink-0 border-t border-gray-200 bg-white">
        <form onSubmit={handleSubmit} className="mx-auto max-w-3xl px-4 py-4">
          <div className="flex items-end gap-3 rounded-xl border border-gray-300 bg-white px-4 py-3 focus-within:border-blue-500 focus-within:ring-1 focus-within:ring-blue-500">
            <textarea
              ref={textareaRef}
              rows={1}
              value={input}
              onChange={handleInputChange}
              onKeyDown={handleKeyDown}
              disabled={busy}
              placeholder="Ask the agent anything… (Enter to send, Shift+Enter for newline)"
              className="flex-1 resize-none bg-transparent text-sm text-gray-800 placeholder-gray-400 focus:outline-none disabled:opacity-50"
              style={{ maxHeight: "120px", overflowY: "auto" }}
            />
            <button
              type="submit"
              disabled={!input.trim() || busy}
              className="shrink-0 rounded-lg bg-blue-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-40"
            >
              {busy ? "···" : "Send"}
            </button>
          </div>
          <div className="mt-2 flex items-center justify-between">
            <p className="text-xs text-gray-400">
              The agent plans and executes multi-step tasks automatically.
            </p>
            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={openLibrary}
                className="text-xs text-gray-400 hover:text-blue-600"
              >
                Knowledge base
              </button>
              <button
                type="button"
                onClick={() => setShowIngest(true)}
                className="text-xs text-gray-400 hover:text-blue-600"
              >
                + Add knowledge
              </button>
            </div>
          </div>
        </form>
      </div>

    </div>
  );
}

export default function ChatPageWrapper() {
  return (
    <Suspense fallback={null}>
      <ChatPage />
    </Suspense>
  );
}
