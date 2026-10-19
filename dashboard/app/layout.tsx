import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Verdict",
  description:
    "Coding agents fix the same real bug; other agents write the tests that judge them; executed tests decide which patch ships.",
};

const NAV = [
  { href: "/suite/test", label: "Held-out results" },
  { href: "/suite/dev", label: "Development" },
  { href: "/method", label: "Method" },
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="border-b" style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}>
          <div className="mx-auto flex max-w-6xl flex-wrap items-baseline gap-x-6 gap-y-2 px-4 py-4 sm:px-6">
            <Link href="/" className="text-lg font-semibold tracking-tight">
              Verdict
            </Link>
            <nav className="flex gap-4 text-sm" style={{ color: "var(--text-secondary)" }}>
              {NAV.map((item) => (
                <Link key={item.href} href={item.href} className="hover:underline underline-offset-4">
                  {item.label}
                </Link>
              ))}
            </nav>
            <a
              href="https://github.com/Keerthirajan58/verdict"
              className="ml-auto text-sm underline underline-offset-4"
              style={{ color: "var(--text-secondary)" }}
            >
              source
            </a>
          </div>
        </header>
        <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6">{children}</main>
        <footer
          className="mt-16 border-t px-6 py-6 text-center text-xs"
          style={{ borderColor: "var(--border)", color: "var(--text-muted)" }}
        >
          Every number on this site is written by <code>verdict arena export</code> from content-hashed run
          artifacts. Nothing is hand-entered.
        </footer>
      </body>
    </html>
  );
}
