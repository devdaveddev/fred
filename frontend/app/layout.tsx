import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Fred · Your analyst team",
  description: "Hire an AI-native business analyst, market researcher, founder's office, lead generator and marketing analyst.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
