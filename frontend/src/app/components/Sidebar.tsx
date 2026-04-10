"use client";

import { useEffect, useState } from "react";
import { useRouter, usePathname, useSearchParams } from "next/navigation";
import { deleteJob, getToken, listJobs, type Job } from "@/lib/api";

// Only mark failed jobs — keep the list clean for everything else
function jobIndicator(status: string) {
  if (status === "failed") return <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-red-400" />;
  return null;
}

export default function Sidebar() {
  const pathname     = usePathname();
  const searchParams = useSearchParams();
  const router       = useRouter();
  const [collapsed, setCollapsed] = useState(false);
  const [jobs, setJobs]           = useState<Job[]>([]);

  const isChat      = pathname === "/chat";
  const activeJobId = searchParams.get("jobId");

  // Fetch + poll job list while on /chat
  useEffect(() => {
    if (!isChat || !getToken()) return;
    const fetch = () => listJobs().then(setJobs).catch(() => {});
    fetch();
    const id = setInterval(fetch, 5000);
    return () => clearInterval(id);
  }, [isChat]);

  async function handleDelete(id: string) {
    if (!window.confirm("Delete this conversation?")) return;
    try {
      await deleteJob(id);
      setJobs((prev) => prev.filter((j) => j.id !== id));
      if (id === activeJobId) router.push("/chat");
    } catch {
      // silently ignore — job list will re-sync on next poll
    }
  }

  if (!isChat || !getToken()) return null;

  return (
    <aside
      className={`flex shrink-0 flex-col overflow-hidden border-r border-gray-200 bg-white transition-[width] duration-200 ${
        collapsed ? "w-12" : "w-64"
      }`}
    >
      {/* ── Header ───────────────────────────────────────────────────────── */}
      <div
        className={`flex h-11 shrink-0 items-center border-b border-gray-100 ${
          collapsed ? "justify-center" : "justify-between px-3"
        }`}
      >
        {!collapsed && (
          <span className="text-xs font-semibold uppercase tracking-wider text-gray-400">
            History
          </span>
        )}
        <button
          onClick={() => setCollapsed((c) => !c)}
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          className="rounded p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-600"
        >
          {/* chevron left / right */}
          <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d={collapsed ? "M9 5l7 7-7 7" : "M15 19l-7-7 7-7"}
            />
          </svg>
        </button>
      </div>

      {/* ── New chat ─────────────────────────────────────────────────────── */}
      <div className={`shrink-0 p-2 ${collapsed ? "flex justify-center" : ""}`}>
        <button
          onClick={() => router.push("/chat")}
          title="New chat"
          className={`flex items-center gap-2 rounded-lg text-sm text-gray-600 hover:bg-gray-100 active:bg-gray-200 ${
            collapsed ? "p-2" : "w-full px-3 py-2"
          }`}
        >
          {/* plus icon */}
          <svg className="h-4 w-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
          </svg>
          {!collapsed && <span>New chat</span>}
        </button>
      </div>

      {/* ── Job list ─────────────────────────────────────────────────────── */}
      <div className="flex-1 overflow-y-auto">
        {collapsed ? (
          /* Collapsed — one button per job, tooltip shows prompt */
          <div className="flex flex-col items-center gap-1 py-2">
            {jobs.slice(0, 40).map((job) => (
              <button
                key={job.id}
                onClick={() => router.push(`/chat?jobId=${job.id}`)}
                title={job.prompt}
                className={`flex h-7 w-7 items-center justify-center rounded text-gray-400 hover:bg-gray-100 ${
                  job.id === activeJobId ? "bg-blue-50 text-blue-500" : ""
                }`}
              >
                {/* Show a red dot only for failed; otherwise a neutral dash */}
                {job.status === "failed" ? (
                  <span className="h-2 w-2 rounded-full bg-red-400" />
                ) : (
                  <span className={`h-1 w-4 rounded-full ${job.id === activeJobId ? "bg-blue-300" : "bg-gray-200"}`} />
                )}
              </button>
            ))}
          </div>
        ) : jobs.length === 0 ? (
          <p className="px-4 py-6 text-center text-xs text-gray-400">No jobs yet.</p>
        ) : (
          /* Expanded — prompt text only; red dot on failed; delete on hover */
          <ul className="space-y-px px-2 pb-4">
            {jobs.map((job) => (
              <li key={job.id} className="group relative">
                <button
                  onClick={() => router.push(`/chat?jobId=${job.id}`)}
                  className={`flex w-full items-center gap-2 rounded-lg px-2 py-2 pr-8 text-left transition-colors ${
                    job.id === activeJobId
                      ? "bg-blue-50 text-blue-700"
                      : "text-gray-600 hover:bg-gray-100"
                  }`}
                >
                  {jobIndicator(job.status)}
                  <span className="truncate text-xs leading-snug">{job.prompt}</span>
                </button>
                {/* Delete button — appears on row hover */}
                <button
                  onClick={(e) => { e.stopPropagation(); handleDelete(job.id); }}
                  title="Delete"
                  className="absolute right-1 top-1/2 -translate-y-1/2 rounded p-1 text-gray-300 opacity-0 transition-opacity hover:bg-red-50 hover:text-red-500 group-hover:opacity-100"
                >
                  <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                  </svg>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </aside>
  );
}
