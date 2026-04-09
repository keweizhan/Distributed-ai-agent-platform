"use client";

import { useState, FormEvent } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { createJob } from "@/lib/api";

const EXAMPLES = [
  "Summarize the key differences between transformer and state-space models for sequence modeling.",
  "Write a step-by-step plan to migrate a REST API to GraphQL, including risks and rollback steps.",
  "Compare three open-source vector databases and recommend one for a semantic search use case.",
];

export default function NewJobPage() {
  const router = useRouter();
  const [prompt, setPrompt] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const job = await createJob(prompt.trim());
      router.push(`/jobs/${job.id}`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to create job");
      setLoading(false);
    }
  }

  return (
    <div className="mx-auto max-w-2xl px-4 py-8">
      <div className="mb-6">
        <Link href="/dashboard" className="text-sm text-blue-600 hover:underline">
          ← Dashboard
        </Link>
        <h1 className="mt-2 text-xl font-bold">New Job</h1>
        <p className="text-sm text-gray-500">
          Describe the task you want the AI agent to complete.
        </p>
      </div>

      {/* Example prompts */}
      <div className="mb-4">
        <p className="mb-2 text-xs font-medium text-gray-400 uppercase tracking-wide">
          Try an example
        </p>
        <div className="space-y-2">
          {EXAMPLES.map((ex) => (
            <button
              key={ex}
              type="button"
              onClick={() => setPrompt(ex)}
              className="block w-full rounded border border-gray-200 bg-white px-3 py-2 text-left text-sm text-gray-600 hover:border-blue-400 hover:text-gray-900"
            >
              {ex}
            </button>
          ))}
        </div>
      </div>

      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label className="mb-1 block text-sm font-medium">Prompt</label>
          <textarea
            required
            rows={6}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="Or write your own prompt here…"
            className="w-full rounded border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
          />
          <p className="mt-1 text-xs text-gray-400">{prompt.length} / 4096</p>
        </div>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <div className="flex gap-3">
          <button
            type="submit"
            disabled={loading || prompt.trim().length === 0}
            className="rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {loading ? "Submitting…" : "Submit Job"}
          </button>
          <Link
            href="/dashboard"
            className="rounded border border-gray-300 px-4 py-2 text-sm hover:bg-gray-50"
          >
            Cancel
          </Link>
        </div>
      </form>
    </div>
  );
}
