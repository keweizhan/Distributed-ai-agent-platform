"use client";

import { useEffect, useState } from "react";
import { useRouter, usePathname } from "next/navigation";
import Link from "next/link";
import { getMe, clearToken, getToken, User } from "@/lib/api";

// Pages where the navbar should not appear
const AUTH_PATHS = ["/login", "/register"];

export default function Navbar() {
  const router = useRouter();
  const pathname = usePathname();
  const [user, setUser] = useState<User | null>(null);

  useEffect(() => {
    if (AUTH_PATHS.includes(pathname)) return;
    if (!getToken()) return;
    getMe()
      .then(setUser)
      .catch(() => {
        clearToken();
        router.push("/login");
      });
  }, [pathname, router]);

  if (AUTH_PATHS.includes(pathname)) return null;
  if (!getToken()) return null;

  function handleLogout() {
    clearToken();
    router.push("/login");
  }

  return (
    <nav className="border-b border-gray-200 bg-white">
      <div className="mx-auto flex max-w-3xl items-center justify-between px-4 py-3">
        {/* Left: brand + links */}
        <div className="flex items-center gap-6">
          <Link href="/dashboard" className="text-sm font-bold tracking-tight text-gray-900">
            AI Agent Platform
          </Link>
          <div className="flex items-center gap-4">
            <Link
              href="/dashboard"
              className="text-sm text-gray-500 hover:text-gray-900"
            >
              Dashboard
            </Link>
            <Link
              href="/jobs/new"
              className="text-sm text-gray-500 hover:text-gray-900"
            >
              New Job
            </Link>
          </div>
        </div>

        {/* Right: user email + logout */}
        <div className="flex items-center gap-4">
          {user && (
            <span className="hidden text-xs text-gray-400 sm:block">{user.email}</span>
          )}
          <button
            onClick={handleLogout}
            className="rounded border border-gray-300 px-3 py-1 text-xs hover:bg-gray-50"
          >
            Sign out
          </button>
        </div>
      </div>
    </nav>
  );
}
