// Plain module (not "use client") so server components get the real values.
export const ROLE_META: Record<string, { label: string; initials: string; hue: number }> = {
  founders_office: { label: "Founder's office", initials: "FO", hue: 222 },
  business_analyst: { label: "Business analyst", initials: "BA", hue: 152 },
  market_researcher: { label: "Market researcher", initials: "MR", hue: 280 },
  lead_generator: { label: "Lead generator", initials: "LG", hue: 24 },
  marketing_analyst: { label: "Marketing analyst", initials: "MA", hue: 340 },
};

export const ROLE_LABEL: Record<string, string> = Object.fromEntries(
  Object.entries(ROLE_META).map(([k, v]) => [k, v.label]),
);
