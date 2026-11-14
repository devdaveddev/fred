import type { Metadata } from "next";
import { cookies } from "next/headers";
import { Landing } from "@/components/Landing";

export const metadata: Metadata = {
  title: "Fred · Your analyst team",
  description: "Fred does the analysis work of BizOps, business analysis, market research, lead qualification and marketing analysis on your own data, with every number traced to its source.",
  openGraph: { title: "Fred · Your analyst team", description: "The analysis work of five non-technical roles, done every morning on your own data. Every number shown working." },
};

export default async function WelcomePage() {
  const signedIn = (await cookies()).has("fred_session");
  return <Landing signedIn={signedIn} />;
}
