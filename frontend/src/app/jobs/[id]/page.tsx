"use client";

import { useEffect, useState, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { getJob, cancelJob, clearToken, JobDetail, Task } from "@/lib/api";

const STATUS_COLORS: Record<string, string> = {
  pending:   "bg-yellow-100 text-yellow-800",
  planning:  "bg-yellow-100 text-yellow-800",
  planned:   "bg-blue-100 text-blue-800",
  running:   "bg-blue-100 text-blue-800",
  succeeded: "bg-green-100 text-green-800",
  failed:    "bg-red-100 text-red-800",
  cancelled: "bg-gray-100 text-gray-600",
  skipped:   "bg-gray-100 text-gray-400",
};

const TERMINAL = new Set(["succeeded", "failed", "cancelled"]);

function CodeBlock({ value }: { value: unknown }) {
  return (
    <pre className="max-h-48 overflow-auto rounded border border-gray-200 bg-gray-50 p-3 font-mono text-xs leading-relaxed text-gray-700">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

function TaskRow({ task }: { task: Task }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded border border-gray-200 bg-white">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between px-4 py-3 text-left"
      >
        <div className="min-w-0 flex-1 flex items-center gap-2">
          <span className="text-xs tabular-nums text-gray-400">#{task.sequence}</span>
          <span className="text-sm font-medium truncate">{task.name}</span>
          {task.tool_name && (
            <span className="shrink-0 rounded bg-gray-100 px-1.5 py-0.5 text-xs text-gray-500">
              {task.tool_name}
            </span>
          )}
        </div>
        <span
          className={`ml-3 shrink-0 rounded px-2 py-0.5 text-xs font-semibold ${
            STATUS_COLORS[task.status] ?? "bg-gray-100 text-gray-600"
          }`}
        >
          {task.status}
        </span>
      </button>

      {open && (
        <div className="border-t border-gray-100 px-4 py-3 space-y-3">
          {task.description && (
            <p className="text-sm text-gray-600">{task.description}</p>
          )}
          {task.tool_input && (
            <div>
              <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-400">Input</p>
              <CodeBlock value={task.tool_input} />
            </div>
          )}
          {task.tool_output && (
            <div>
              <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-400">Output</p>
              <CodeBlock value={task.tool_output} />
            </div>
          )}
          {task.error && (
            <p className="text-sm text-red-600">Error: {task.error}</p>
          )}
          {(task.started_at || task.finished_at) && (
            <p className="text-xs text-gray-400">
              {task.started_at && <>Started: {new Date(task.started_at).toLocaleString()}</>}
              {task.started_at && task.finished_at && " · "}
              {task.finished_at && <>Finished: {new Date(task.finished_at).toLocaleString()}</>}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

export default function JobDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [job, setJob] = useState<JobDetail | null>(null);
  const [error, setError] = useState("");
  const [cancelling, setCancelling] = useState(false);

  const fetchJob = useCallback(async () => {
    try {
      setJob(await getJob(id));
    } catch (err: unknown) {
      if (err instanceof Error && err.message.toLowerCase().includes("401")) {
        clearToken();
        router.push("/login");
      } else {
        setError(err instanceof Error ? err.message : "Failed to load job");
      }
    }
  }, [id, router]);

  useEffect(() => {
    fetchJob();
  }, [fetchJob]);

  // Poll every 3 s while job is not terminal
  useEffect(() => {
    if (!job || TERMINAL.has(job.status)) return;
    const timer = setInterval(fetchJob, 3000);
    return () => clearInterval(timer);
  }, [job, fetchJob]);

  async function handleCancel() {
    if (!job) return;
    setCancelling(true);
    try {
      const updated = await cancelJob(job.id);
      setJob((prev) => (prev ? { ...prev, ...updated } : null));
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Cancel failed");
    } finally {
      setCancelling(false);
    }
  }

  if (!job) {
    return (
      <div className="mx-auto max-w-2xl px-4 py-8">
        {error ? (
          <p className="text-red-600">{error}</p>
        ) : (
          <p className="text-sm text-gray-400">Loading…</p>
        )}
      </div>
    );
  }

  const isTerminal = TERMINAL.has(job.status);

  return (
    <div className="mx-auto max-w-2xl px-4 py-8">
      <Link href="/dashboard" className="text-sm text-blue-600 hover:underline">
        ← Dashboard
      </Link>

      {/* Job header card */}
      <div className="mt-4 rounded border border-gray-200 bg-white px-5 py-4">
        <div className="flex items-start justify-between gap-4">
          <p className="min-w-0 flex-1 font-medium text-gray-900">{job.prompt}</p>
          <span
            className={`shrink-0 rounded px-2.5 py-1 text-xs font-semibold ${
              STATUS_COLORS[job.status] ?? "bg-gray-100 text-gray-600"
            }`}
          >
            {job.status}
          </span>
        </div>
        <p className="mt-1.5 text-xs text-gray-400">
          {new Date(job.created_at).toLocaleString()} · <span className="font-mono">{job.id}</span>
        </p>

        {/* Actions (non-terminal only) */}
        {!isTerminal && (
          <div className="mt-4 flex items-center gap-3">
            <button
              onClick={handleCancel}
              disabled={cancelling}
              className="rounded border border-red-300 px-3 py-1.5 text-sm text-red-600 hover:bg-red-50 disabled:opacity-50"
            >
              {cancelling ? "Cancelling…" : "Cancel Job"}
            </button>
            <span className="text-xs text-gray-400">Auto-refreshing every 3 s…</span>
          </div>
        )}
      </div>

      {/* Result — prominent card */}
      {job.result && (
        <div className="mt-4 rounded-lg border border-green-200 bg-green-50 px-5 py-4">
          <div className="mb-2 flex items-center gap-2">
            <span className="text-green-600 text-lg">✓</span>
            <h2 className="text-sm font-semibold text-green-800">Result</h2>
          </div>
          <p className="whitespace-pre-wrap text-sm text-green-900 leading-relaxed">
            {job.result}
          </p>
        </div>
      )}

      {/* Error card */}
      {job.error && (
        <div className="mt-4 rounded-lg border border-red-200 bg-red-50 px-5 py-4">
          <h2 className="mb-1 text-sm font-semibold text-red-700">Error</h2>
          <p className="text-sm text-red-700">{job.error}</p>
        </div>
      )}

      {/* Tasks */}
      {job.tasks.length > 0 && (
        <div className="mt-6">
          <h2 className="mb-3 text-sm font-semibold text-gray-700">
            Tasks <span className="font-normal text-gray-400">({job.tasks.length})</span>
          </h2>
          <div className="space-y-2">
            {job.tasks
              .slice()
              .sort((a, b) => a.sequence - b.sequence)
              .map((task) => (
                <TaskRow key={task.id} task={task} />
              ))}
          </div>
        </div>
      )}

      {error && <p className="mt-4 text-sm text-red-600">{error}</p>}
    </div>
  );
}
