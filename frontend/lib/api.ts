export type Unit = "currency" | "ratio" | "percent" | "months" | "count" | "multiple";
export type Status = "good" | "watch" | "bad" | "neutral";

export interface Receipt {
  id: string;
  metric: string;
  value: number | null;
  formula: string;
  formula_version: string;
  input_snapshot: string;
  inputs: Record<string, unknown>;
  seed: number | null;
  model_version: string | null;
}

export interface Metric {
  id: string;
  label: string;
  group: string;
  unit: Unit;
  value: number | null;
  status: Status;
  benchmark: string | null;
  help: string;
  receipt: Receipt;
}

export interface Envelope {
  warnings: string[];
  data_coverage: number;
  snapshot_id: string;
}

export interface Overview extends Envelope {
  tenant: { slug: string; name: string; currency: string };
  as_of: string;
  metrics: Metric[];
  mrr_series: { month: string; value: number; receipt_id: string }[];
}

export interface Shock {
  price_change: number;
  churn_multiplier: number;
  sm_spend_change: number;
  cac_change: number;
}

export interface ScenarioRequest {
  shock: Shock;
  price_elasticity: number;
  months: number;
  runs: number;
  seed: number;
}

export interface Bands { p10: number; p50: number; p90: number }

export interface SimOutcome {
  arr_end: Bands;
  arr_path: ({ month: number } & Bands)[];
  nrr_12m: Bands;
  p_cash_out: number;
  runway_months_p50: number;
  cac: number | null;
  cac_payback_months: number | null;
  ltv_cac_wacc: number | null;
  receipts: Record<string, Receipt>;
}

export interface ScenarioResult extends Envelope {
  assumptions: Record<string, unknown>;
  baseline: SimOutcome;
  scenario: SimOutcome;
}

// ---------- analyst team ----------
export type Severity = "critical" | "high" | "medium" | "low" | "info";
export type MethodKind = "rule" | "statistics" | "ml" | "optimization" | "llm";

export interface Finding {
  id: string;
  role: string;
  severity: Severity;
  title: string;
  summary: string;
  method: { kind: MethodKind; detail: string; model_version: string | null };
  actions: string[];
  impact_usd: number | null;
  impact_label: string | null;
  receipts: string[];
  link: string | null;
  frameworks: string[];
  tasked?: boolean;
}

export interface TeamEnvelope {
  snapshot_id: string;
  as_of: string;
  llm_calls: number;
}

export interface Benchmark {
  metric: string; value: number; target: number; concern: number; direction: "up" | "down";
  status: "at_or_above_target" | "between" | "below_concern"; basis: string;
}

export interface KnowledgeStatus { version: string; as_of: string; age_days: number; stale: boolean; review_every_days: number }

export interface Briefing extends TeamEnvelope {
  roles: { id: string; label: string; replaces: string; tagline: string; findings: number; top_severity: Severity; pages: { href: string; label: string }[] }[];
  findings: Finding[];
  methods: Record<string, number>;
  compute_ms: Record<string, number>;
  stage: { id: string; label: string };
  benchmarks: Benchmark[];
  knowledge: Record<string, KnowledgeStatus>;
  warnings?: string[];
  hired?: string[];
  follow_ups?: { open: number; overdue: number };
  muted?: number;
}

export interface ModelCard {
  kind: string; version: string; algorithm: string; snapshot_id: string; features: string[];
  n_train: number; n_test: number; params: Record<string, unknown>; notes: string[];
  metrics: { auc: number; brier: number; calibration_error: number; base_rate: number; top_decile_rate: number; top_decile_lift: number };
}

export interface ScoredLead {
  lead_id: string; company: string; contact_name: string; industry: string; employees: number; country: string;
  seniority: string; source_channel: string; created_at: string; demo_requested: boolean; pricing_views: number;
  p: number; expected_value: number; first_year_value: number; segment: string; lane: "fast_lane" | "sdr" | "nurture";
  next_action: string; reasons: string[]; risks: string[];
}

export interface IcpRow { dimension: string; value: string; leads: number; conversion: number; lift: number; z: number; significant: boolean }

export interface LeadsResponse extends TeamEnvelope {
  model: ModelCard; lanes: Record<string, number>; total_open: number; thresholds: Record<string, number>;
  icp: IcpRow[]; leads: ScoredLead[];
}

export interface RiskAccount {
  account_id: string; name: string; segment: string; industry: string; acquisition_channel: string; mrr: number;
  p: number; mrr_at_risk: number; util_now: number; util_trend: number; tenure_months: number; reasons: string[]; play: string;
}

export interface AtRiskResponse extends TeamEnvelope {
  model: ModelCard; totals: { accounts: number; high_risk: number; mrr_at_risk: number; mrr: number }; accounts: RiskAccount[];
}

export interface ChannelRow {
  channel: string; paid: boolean; spend: number; spend_loaded: number; leads: number; conversion: number;
  new_customers: number; arpa: number; cac_media: number | null; cac_loaded: number | null;
  payback_months: number | null; ltv_cac: number | null;
}

export interface MarketingResponse extends TeamEnvelope {
  channels: ChannelRow[];
  curves: Record<string, { a: number; b: number; r2: number; n_months: number; conversion: number; current_spend: number }>;
  reallocation: {
    moves: { channel: string; current: number; proposed: number; change: number; customers_now: number; customers_after: number; marginal_cac_now: number }[];
    frozen: string[]; budget: number; customers_before?: number; customers_after?: number; delta_customers_monthly: number;
  };
  anomalies: { channel: string; since: string; days: number; typical_daily: number; recent_daily: number; excess_spend: number; max_z: number; lead_ratio: number | null; verdict: string }[];
  arpa_new: number;
}

export interface Plan {
  target_arr: number; months: number; baseline_arr: number; gap: number; monthly_growth_factor: number;
  lever_spend: { new_customers_needed: number; new_customers_now: number; sm_monthly_needed: number; sm_monthly_now: number;
    cash_out_month: number | null; end_cash: number; runway_after_months: number | null; spend_elasticity: number };
  lever_retention: { revenue_churn_now: number; revenue_churn_needed: number | null; achievable: boolean };
  feasible_with_spend: boolean; out_of_sample: boolean; warnings: string[]; verdict: string;
}

export interface ForecastResponse extends TeamEnvelope {
  method: string; backtest_mape: Record<string, number>;
  history: { month: string; mrr: number }[];
  forecast: { month: string; mrr: number; low: number; high: number }[];
  drivers: { revenue_churn: number; net_expansion: number; new_mrr_monthly: number };
  default_goal: { target_arr: number; months: number };
  plan: Plan;
}

export interface ModelsResponse extends TeamEnvelope {
  models: ModelCard[];
  history: { id: string; kind: string; snapshot_id: string; metrics: ModelCard["metrics"]; n_train: number; n_test: number; trained_at: string }[];
  knowledge: { status: Record<string, KnowledgeStatus>; benchmarks: { version: string; as_of: string; metrics: Record<string, { basis: string; direction: string; bands: Record<string, { target: number; concern: number }> }> };
    playbooks: { id: string; role: string; severity: Severity; title: string; why: string; actions: string[]; frameworks: string[]; when: [string, string, number][] }[] };
  compute_ms: Record<string, number>;
  methods_policy: { tier: number; kind: MethodKind; used_for: string; cost: string }[];
}

/** Server components call the API directly on the internal network; browsers go through /api. */
function base(): string {
  if (typeof window === "undefined") return process.env.API_INTERNAL_URL ?? "http://localhost:8000";
  return "/api";
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export type Caller = <T>(path: string, init?: RequestInit) => Promise<T>;

export async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${base()}${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {}
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export function makeApi(call: Caller) {
  return {
    overview: () => call<Overview>("/overview"),
    runScenario: (body: ScenarioRequest, signal?: AbortSignal) =>
      call<ScenarioResult>("/scenarios", { method: "POST", body: JSON.stringify(body), signal }),
    briefing: () => call<Briefing>("/briefing"),
    leads: (limit = 200) => call<LeadsResponse>(`/leads?limit=${limit}`),
    atRisk: (limit = 60) => call<AtRiskResponse>(`/accounts/at-risk?limit=${limit}`),
    marketing: () => call<MarketingResponse>("/marketing"),
    forecast: () => call<ForecastResponse>("/forecast"),
    plan: (target_arr: number, months: number) =>
      call<TeamEnvelope & { plan: Plan; receipt_id: string }>("/plan", { method: "POST", body: JSON.stringify({ target_arr, months }) }),
    models: () => call<ModelsResponse>("/models"),
    receipt: (id: string) => call<Receipt & { as_of_month: string; row_counts: Record<string, number>; created_at: string }>(`/receipts/${id}`),
    bva: (month?: string) => call<Bva>(`/finance/bva${month ? `?month=${month}` : ""}`),
    me: () => call<Me>("/auth/me"),
    team: () => call<{ roles: Role[] }>("/team"),
    market: () => call<Market>("/market"),
    voice: () => call<Voice>("/voice"),
    tasks: () => call<{ tasks: Task[]; muted: Muted[] }>("/tasks"),
    documents: () => call<{ documents: DocMeta[]; can_create: boolean }>("/documents"),
    document: (id: string) => call<Doc>(`/documents/${id}`),
    sources: () => call<Sources>("/sources"),
    notifications: () => call<Notifications>("/notifications"),
    members: () => call<Members>("/members"),
    accuracy: () => call<Accuracy>("/accuracy"),
    budget: () => call<BudgetOverview>("/budget"),
    crew: () => call<CrewOverview>("/crew"),
    sso: () => call<SsoInfo>("/sso"),
    sessions: () => call<{ sessions: SessionRow[] }>("/security/sessions"),
    auditLog: (group?: string) => call<{ events: AuditEvent[]; groups: string[]; next: number | null }>(`/security/audit${group ? `?group=${group}` : ""}`),
    accounts: () => call<{ accounts: GlAccount[]; departments: string[] }>("/mapping/accounts"),
  };
}

/** Browser client: same-origin /api, the session cookie rides along automatically. */
export const api = makeApi(call);

// ---------- strategy & finance ----------
export interface PnlRow {
  key: string; label: string; level: "group" | "account" | "total"; group: string; cost: boolean;
  plan_m: number; actual_m: number; var_m: number; plan_ytd: number; actual_ytd: number; var_ytd: number;
  var_pct_ytd: number | null; favorable_ytd: boolean; material_ytd: boolean; material_m: boolean; receipt_id: string;
}

export interface BvaFinding {
  key: string; area: string; variance: number; favorable: boolean; early_warning?: boolean;
  title: string; explanation: string; components: Record<string, number | string>; method: string; receipt_id: string;
}

export interface Bva {
  snapshot_id: string; llm_calls: number;
  plan: { id: string; name: string; approved_on: string; assumptions: Record<string, number> };
  month: string; ytd_months: string[]; closed_months: string[];
  pnl: PnlRow[];
  revenue_bridge: {
    ytd: { volume: number; price: number; churn: number; expansion: number }; ytd_total: number;
    month: Record<string, number | string>;
    new_customers: { actual: number; plan: number };
    revenue_churn: { actual: number; plan: number };
    net_expansion: { actual: number; plan: number };
  };
  payroll: {
    departments: Record<string, { plan: number; actual: number; variance: number; components: Record<string, number>;
      headcount_plan: number; headcount_actual: number }>;
    items: { department: string; kind: string; amount: number; label: string }[];
  };
  cash: { start: number; plan: number; actual: number; operating_variance: number };
  findings: BvaFinding[];
  headcount: {
    month: string; active: number; by_department: Record<string, number>; departures_ytd: number;
    plan_hires: { role: string; department: string; location: string; planned_start: string; actual_start: string | null;
      salary_plan: number; status: string }[];
  };
}


// ---------- SaaS: session, team, research ----------
export interface Me {
  user: { id: string; email: string; name: string; role: string; auth_method: string };
  workspace: { id: string; name: string; data_source: string };
  workspaces: { id: string; name: string; role: string }[];
  hired: string[];
  nav: { module: string; title: string; pages: { href: string; label: string }[] }[];
}

export interface Role {
  id: string; title: string; tagline: string; replaces: string; salary_equivalent: number;
  delivers: string[]; methods: string[]; pages: { href: string; label: string }[]; needs: string[]; hired: boolean;
}

export interface Segment {
  segment_id: number; name: string; customers: number; mrr: number; avg_mrr: number; mrr_share: number;
  retention_12m: number | null; nrr_12m: number | null; lead_conversion: number | null; score: number | null;
  examples: string[]; industries: string[];
}

export interface PretrainedCard { name: string; source: string; license: string; dimensions: number; size_mb: number; runtime: string; used_for: string[] }

export interface Market extends TeamEnvelope {
  k: number; silhouette: number; segments: Segment[]; best: Segment | null; worst: Segment | null;
  lookalikes: { lead_id: string; company: string; description: string; similarity: number }[];
  base_conversion: number; model: PretrainedCard;
}

export interface Voice extends TeamEnvelope {
  churn_reasons: { theme: string; label: string; action: string; responses: number; share: number; lost_mrr: number; mrr_share: number; quotes: string[] }[];
  detractor_themes: { theme: string; label: string; count: number; share: number; quotes: string[] }[];
  nps: { score: number; responses: number; promoters: number; detractors: number } | null;
  tagged: number; exit_responses: number; themes_version: string; model: PretrainedCard;
}

// ---------- work loop: tasks + documents ----------
export interface Task {
  id: string; finding_id: string; module: string; title: string; steps: string[]; owner: string; due_date: string;
  status: "open" | "in_progress" | "done" | "cancelled"; baseline_impact: number | null; created_at: string;
  completed_at: string | null; overdue: boolean;
  progress: { state: "resolved" | "improving" | "worse" | "flat" | "waiting" | "open"; text: string; change?: number };
}
export interface Muted { finding_id: string; label: string; note: string; suppress_until: string; created_at: string }

export interface DocBlock {
  type: "p" | "bullets" | "kpis" | "table" | "callout";
  text?: string; tone?: "good" | "watch" | "bad"; items?: (string | { label: string; value: string; note?: string })[];
  columns?: string[]; rows?: string[][];
}
export interface DocBody { title: string; subtitle: string; sections: { id: string; heading: string; blocks: DocBlock[] }[] }
export interface Verification { numbers: number; traced: number; untraced: string[]; facts: number; facts_with_receipt: number; ok: boolean }
export interface DocMeta { id: string; kind: string; title: string; period: string; created_at: string; verification: Verification; author: string | null }
export interface Doc extends DocMeta {
  snapshot_id: string; body: DocBody;
  facts: { key: string; value: number | null; unit: string; receipt_id: string | null; label: string; shown: string[] }[];
}

// ---------- data sources ----------
export interface Readiness { ok: boolean; needs: string; have: string }
export interface SyncRun { id: string; source: string; status: "running" | "ok" | "error"; started_at: string; finished_at: string | null; stats: Record<string, unknown>; warnings: string[]; error: string | null }
export interface Sources {
  data_source: "demo" | "connected" | "empty";
  connections: { kind: string; secret_hint: string; created_at: string; config: Record<string, string | boolean> | null }[];
  runs: SyncRun[]; last_ok: Record<string, string>; readiness: Record<string, Readiness>;
  templates: Record<string, { label: string; columns: string[]; help: string }>;
  mapping_to_review: number; can_store_keys: boolean;
  catalog: { kind: string; name: string; category: string; gives: string; auth: "key" | "oauth"; how: string; key_label: string | null;
    fields: { name: string; label: string; required: boolean }[]; writeback: boolean; available: boolean }[];
}
export interface GlAccount { code: string; name: string; section: string | null; pl_group: string; kind: string; department: string | null; mapped_by: string; confidence: number | null; total: number }

export interface DigestItem { id: string; title: string; url: string; impact?: string | null; role?: string; severity?: Severity;
  owner?: string; state?: string; text?: string; days?: number }
export interface Notifications {
  slack: { connected: boolean; hint: string | null };
  email: { available: boolean; enabled: boolean; mine: boolean; address: string };
  daily_sync: boolean; digest_hour: number; timezone: string; next_run: string;
  preview: { empty: boolean; summary: string; findings: DigestItem[]; more_findings: number; task_updates: DigestItem[];
    overdue: DigestItem[]; sync_problems: { source: string; error: string; url: string }[] } | null;
  last_job: { period: string; finished_at: string | null; outcome: Record<string, unknown> | null } | null;
  log: { kind: string; channel: string; recipient: string; subject: string; status: string; error: string | null; created_at: string }[];
  can_store_keys: boolean; can_edit: boolean;
}

export interface Member { id: string; name: string; email: string; role: "owner" | "admin" | "member"; created_at: string;
  last_login_at: string | null; sso_only: boolean; you: boolean }
export interface Members { members: Member[]; invites: { id: string; email: string; role: string; created_at: string;
  expires_at: string; invited_by: string | null }[]; can_manage: boolean; is_owner: boolean }
export interface SsoInfo { config: { issuer: string; client_id: string; domains: string[]; default_role: string; enforce: boolean;
  enabled: boolean; verified_at: string | null } | null; callback_url: string; can_edit: boolean; your_domain: string; can_store_keys: boolean }
export interface SessionRow { id: string; workspace: string; created_at: string; last_seen_at: string | null; expires_at: string;
  ip: string | null; user_agent: string | null; auth_method: string; current: boolean }
export interface AuditEvent { id: number; actor: string | null; action: string; target: string | null; detail: Record<string, unknown>;
  ip: string | null; user_agent: string | null; created_at: string }

export interface Accuracy {
  as_of: string;
  verdict: { check: string; state: "good" | "watch" | "bad"; text: string }[];
  forecast: { rows: { month: string; horizon: number; made_in: string; predicted: number; low: number; high: number; actual: number;
    error: number; naive_error: number; in_band: boolean; method: string }[];
    summary: Record<string, { n: number; mape: number; bias: number; naive_mape: number; beats_naive: boolean; band_coverage: number }> };
  early_warnings?: { calls: { flagged_in: string; line: string; said: string; months_after: number; held_share: number; right: boolean }[]; n: number; right: number };
  explanations?: { months: { month: string; gap: number; explained: number; share: number; findings: number }[]; share: number | null };
  agreement?: { rows: { month: string; books: number; billing: number; diff: number }[]; max_abs_diff: number | null };
  models: { kind: string; label: string; outcome: string; n_test: number; auc: number; top_decile_rate: number; base_rate: number; lift: number; calibration_error: number }[];
}

export interface BudgetLine { account: string; monthly: number[]; name?: string; last12?: number }
export interface BudgetHire { role: string; location?: string; planned_start: string; salary_annual: number }
export interface BudgetSubmission { department: string; owner_user_id: string | null; owner_name: string | null; status: "draft" | "submitted" | "approved" | "returned";
  lines: BudgetLine[]; hires: BudgetHire[]; note: string | null; can_edit: boolean; mine: boolean;
  staff: { name: string; title: string; salary_annual: number; end_date: string | null }[] }
export interface BudgetRoundView {
  round: { id: string; fiscal_year: number; name: string; status: string; due_date: string | null; published_at: string | null;
    revenue: { start_mrr: number; new_customers: number; arpa_new: number; revenue_churn: number; net_expansion: number };
    baseline: { groups: Record<string, number>; payroll_by_dept: Record<string, number>; last_closed: string } };
  submissions: BudgetSubmission[];
  plan: { departments: Record<string, { spend: number; payroll: number; hires: number; headcount_end: number; total: number }>;
    summary: { revenue: number; cogs: number; sm: number; rnd: number; ga: number; gross_margin: number | null; operating_income: number;
      cash_start: number | null; cash_end: number | null; headcount_end: number }; revenue: number[] };
  checks: { department: string | null; level: "info" | "watch" | "bad"; text: string }[];
  events: { department: string | null; actor: string; action: string; note: string | null; created_at: string }[];
  can_admin: boolean; ready_to_publish: boolean; accounts: { code: string; name: string }[];
}
export interface BudgetOverview { rounds: { id: string; fiscal_year: number; name: string; status: string }[]; current: BudgetRoundView | null;
  members: { id: string; name: string; email: string }[]; departments: string[]; has_books: boolean; can_admin: boolean; next_fiscal_year: number }

export interface CrewActivity { id: string; role: string; kind: "new_task" | "due_soon" | "overdue" | "worse" | "weekly";
  status: "draft" | "sent" | "failed" | "dismissed" | "done"; subject: string; body: string; writer: string; error: string | null;
  created_at: string; decided_at: string | null; recipient: string | null; recipient_email: string | null }
export interface CrewOverview { mode: "off" | "draft" | "auto"; weekly_day: number; last_run_at: string | null;
  writer: { llm: boolean; model: string }; email_available: boolean; can_edit: boolean;
  members: { id: string; name: string; email: string }[];
  agents: { id: string; title: string; tagline: string; replaces: string; owner: string | null; owner_is_default: boolean;
    week: number; drafts: number; last: string | null }[];
  activity: CrewActivity[] }
