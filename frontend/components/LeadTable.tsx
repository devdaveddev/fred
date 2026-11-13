"use client";

import { useState } from "react";
import type { ScoredLead } from "@/lib/api";
import { fmt } from "@/lib/format";
import { ProbBar } from "./team";

const LANES = [
  { id: "all", label: "All" },
  { id: "fast_lane", label: "AE fast lane" },
  { id: "sdr", label: "SDR sequence" },
  { id: "nurture", label: "Nurture" },
] as const;
const LANE_LABEL = { fast_lane: "Fast lane", sdr: "SDR", nurture: "Nurture" };

export function LeadTable({ leads, counts }: { leads: ScoredLead[]; counts: Record<string, number> }) {
  const [lane, setLane] = useState<string>("fast_lane");
  const rows = lane === "all" ? leads : leads.filter((l) => l.lane === lane);
  return (
    <>
      <div className="tabs">
        {LANES.map((l) => (
          <button key={l.id} className={`preset ${lane === l.id ? "on" : ""}`} onClick={() => setLane(l.id)}>
            {l.label} {l.id !== "all" && <span className="muted">({counts[l.id] ?? 0})</span>}
          </button>
        ))}
      </div>
      <div className="card table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Lead</th><th>Conversion odds</th><th>Why</th><th className="r">Expected value</th><th>Next action</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((l) => (
              <tr key={l.lead_id}>
                <td>
                  <b>{l.company}</b>
                  <span className="sub">{l.contact_name} · {l.seniority} · {l.industry}</span>
                  <span className="sub">{l.employees.toLocaleString("en-US")} employees · {l.country} · via {l.source_channel.replace("_", " ")}</span>
                </td>
                <td><ProbBar p={l.p} /><span className="sub"><span className={`lane lane-${l.lane}`}>{LANE_LABEL[l.lane]}</span></span></td>
                <td>
                  <ul className="reasons">{l.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
                  {l.risks.length > 0 && <ul className="reasons risk">{l.risks.map((r) => <li key={r}>{r}</li>)}</ul>}
                </td>
                <td className="r num">
                  {fmt(l.expected_value, "currency")}
                  <span className="sub">of {fmt(l.first_year_value, "currency")} {l.segment}</span>
                </td>
                <td style={{ maxWidth: 240 }}>{l.next_action}</td>
              </tr>
            ))}
            {rows.length === 0 && <tr><td colSpan={5} className="muted">No leads in this lane.</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
}
