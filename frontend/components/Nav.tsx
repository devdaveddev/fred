"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import type { Me } from "@/lib/api";
import { Avatar } from "./team";

export function Nav({ me }: { me: Me }) {
  const path = usePathname();
  const router = useRouter();
  const [switchErr, setSwitchErr] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  useEffect(() => setOpen(false), [path]); // close the phone menu after navigating
  const link = (href: string, label: string, sub = false) => (
    <Link key={href} href={href} className={`navlink ${sub ? "sub" : ""} ${path === href ? "active" : ""}`}>{label}</Link>
  );
  const signOut = async () => {
    await fetch("/api/auth/logout", { method: "POST" });
    router.push("/login");
    router.refresh();
  };
  return (
    <>
    <button className="menu-btn" aria-label="Open menu" aria-expanded={open} onClick={() => setOpen(true)}>☰</button>
    {open && <div className="nav-scrim" onClick={() => setOpen(false)} />}
    <nav className={`sidebar ${open ? "open" : ""}`}>
      <Link href="/" className="brand">fred<span>.</span></Link>
      {me.workspaces.length > 1 ? (
        <select className="workspace-switch" value={me.workspace.id} aria-label="Workspace"
          onChange={async (e) => {
            const r = await fetch("/api/auth/switch", { method: "POST", headers: { "content-type": "application/json" },
              body: JSON.stringify({ tenant_id: e.target.value }) });
            if (!r.ok) { const j = await r.json().catch(() => ({})); setSwitchErr(j.detail ?? "Could not switch."); return; }
            setSwitchErr(null); router.push("/"); router.refresh();
          }}>
          {me.workspaces.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
        </select>
      ) : <div className="workspace">{me.workspace.name}</div>}
      {switchErr && <p className="nav-empty err">{switchErr}</p>}
      <div className="nav-section">
        {link("/", "Today")}
        {link("/actions", "Follow-ups")}
        {link("/documents", "Documents")}
        {link("/budget", "Budget")}
        {link("/accuracy", "Track record")}
        {link("/team", "Your team")}
      </div>
      {me.nav.map((m) => (
        <div key={m.module} className="nav-section">
          <div className="nav-label"><Avatar id={m.module} size={18} /> {m.title}</div>
          {m.pages.map((p) => link(p.href, p.label, true))}
        </div>
      ))}
      {me.nav.length === 0 && <p className="nav-empty">Hire your first role to get started.</p>}
      <div className="sidebar-foot">
        <div className="nav-section">{link("/settings/data", "Data sources")}{link("/settings/notifications", "Notifications")}{link("/settings/people", "People")}{link("/settings/security", "Security")}{link("/models", "How it works")}</div>
        <div className="me">
          <span>{me.user.name}</span>
          <button className="linklike" onClick={signOut}>Sign out</button>
        </div>
      </div>
    </nav>
    </>
  );
}
