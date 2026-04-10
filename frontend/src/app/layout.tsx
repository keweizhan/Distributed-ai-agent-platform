import { Suspense } from "react";
import type { Metadata } from "next";
import "./globals.css";
import Navbar from "./components/Navbar";
import Sidebar from "./components/Sidebar";

export const metadata: Metadata = {
  title: "AI Agent Platform",
  description: "Distributed AI Agent Platform demo",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="flex min-h-screen flex-col bg-gray-50 text-gray-900 antialiased">
        <Navbar />
        {/* Sidebar + page content share the remaining height in a flex row */}
        <div className="flex flex-1 overflow-hidden">
          <Suspense fallback={null}>
            <Sidebar />
          </Suspense>
          <main className="flex flex-1 flex-col overflow-hidden">{children}</main>
        </div>
      </body>
    </html>
  );
}
