"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  clearToken,
  createJob,
  getJob,
  getMe,
  getToken,
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

// ── Constants ────────────────────────────────────────────────────────────────

const TERMINAL = new Set(["succeeded", "failed", "cancelled"]);
const POLL_MS = 2000;

const TASK_DOT: Record<string, string> = {
  pending:   "bg-gray-300",
  queued:    "bg-yellow-400",
  running:   "bg-blue-500 animate-pulse",
  succeeded: "bg-green-500",
  failed:    "bg-red-500",
  skipped:   "bg-gray-300",
};

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
      ? `Something went wrong: ${detail.error ?? "unknown error"}`
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

// ── Component ────────────────────────────────────────────────────────────────

export default function ChatPage() {
  const router       = useRouter();
  const searchParams = useSearchParams();
  const jobId        = searchParams.get("jobId");

  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput]       = useState("");
  const [busy, setBusy]         = useState(false);
  const bottomRef   = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

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

    const userId      = crypto.randomUUID();
    const assistantId = crypto.randomUUID();

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
                  : `Something went wrong: ${detail.error ?? "unknown error"}`,
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

  // ── Render ──────────────────────────────────────────────────────────────

  return (
    <div className="flex flex-1 flex-col overflow-hidden">

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
                    <p className="whitespace-pre-wrap">{msg.content}</p>
                  </div>

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

                  {/* Step dots — visible only while in-progress */}
                  {msg.tasks &&
                    msg.tasks.length > 0 &&
                    !TERMINAL.has(msg.jobStatus ?? "") && (
                      <div className="space-y-1 pl-2">
                        {msg.tasks
                          .filter((t) => t.task_type !== "synthesis")
                          .sort((a, b) => a.sequence - b.sequence)
                          .map((t) => (
                            <div key={t.id} className="flex items-center gap-2 text-xs text-gray-400">
                              <span
                                className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                                  TASK_DOT[t.status] ?? "bg-gray-300"
                                }`}
                              />
                              <span className={t.status === "running" ? "text-blue-500" : ""}>
                                {t.name}
                              </span>
                            </div>
                          ))}
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
          <p className="mt-2 text-center text-xs text-gray-400">
            The agent plans and executes multi-step tasks automatically.
          </p>
        </form>
      </div>

    </div>
  );
}
