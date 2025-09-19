"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useMemo } from "react";

type NavItem = {
  href: string;
  label: string;
};

export default function NavBar(): JSX.Element {
  const pathname = usePathname();

  const items: NavItem[] = useMemo(
    () => [
      { href: "/", label: "Home" },
      { href: "/scrape", label: "Scrape" },
      { href: "/dashboard", label: "Dashboard" },
    ],
    [],
  );

  function isActive(href: string): boolean {
    if (href === "/") return pathname === "/";
    return pathname?.startsWith(href) ?? false;
  }

  return (
    <nav className="border-b">
      <div className="mx-auto max-w-6xl px-4 py-3 flex items-center justify-between">
        <Link href="/" className="font-semibold tracking-tight">
          Arc’teryx Tools
        </Link>
        <div className="flex gap-3">
          {items.map((it) => {
            const active = isActive(it.href);
            const base = "rounded px-4 py-2 text-sm";
            const cls = active
              ? "bg-black text-white " + base
              : "bg-white text-gray-900 border " + base;
            return (
              <Link key={it.href} href={it.href} className={cls}>
                {it.label}
              </Link>
            );
          })}
        </div>
      </div>
    </nav>
  );
}


