"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
// User is fetched here only to guard auth; display is handled by Navbar
import { getMe, listJobs, clearToken, Job } from "@/lib/api";

const STATUS_COLORS: Record<string, string> = {
  pending:   "bg-yellow-100 text-yellow-800",
  planning:  "bg-yellow-100 text-yellow-800",
  planned:   "bg-blue-100 text-blue-800",
  running:   "bg-blue-100 text-blue-800",
  succeeded: "bg-green-100 text-green-800",
  failed:    "bg-red-100 text-red-800",
  cancelled: "bg-gray-100 text-gray-600",
};

export default function DashboardPage() {
  const router = useRouter();
  const [jobs, setJobs] = useState<Job[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([getMe(), listJobs()])
      .then(([, j]) => setJobs(j))
      .catch(() => {
        clearToken();
        router.push("/login");
      });
  }, [router]);

  return (
    <div className="mx-auto max-w-3xl px-4 py-8">
      {/* Header */}
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-base font-semibold text-gray-700">Jobs</h1>
        <Link
          href="/jobs/new"
          className="rounded bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
        >
          + New Job
        </Link>
      </div>

      {/* Job list */}
      {error && <p className="mb-4 text-sm text-red-600">{error}</p>}

      {jobs.length === 0 ? (
        <div className="rounded border border-dashed border-gray-300 py-16 text-center text-sm text-gray-400">
          No jobs yet.{" "}
          <Link href="/jobs/new" className="text-blue-600 hover:underline">
            Submit your first job
          </Link>
          .
        </div>
      ) : (
        <div className="space-y-2">
          {jobs.map((job) => (
            <Link
              key={job.id}
              href={`/jobs/${job.id}`}
              className="flex items-start justify-between rounded border border-gray-200 bg-white px-4 py-3 hover:border-blue-400"
            >
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{job.prompt}</p>
                <p className="mt-0.5 text-xs text-gray-400">
                  {new Date(job.created_at).toLocaleString()}
                </p>
              </div>
              <span
                className={`ml-4 shrink-0 rounded px-2 py-0.5 text-xs font-medium ${
                  STATUS_COLORS[job.status] ?? "bg-gray-100 text-gray-600"
                }`}
              >
                {job.status}
              </span>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
