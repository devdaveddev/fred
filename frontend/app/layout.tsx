import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Fred · Your analyst team",
  description: "Hire an AI-native business analyst, market researcher, founder's office, lead generator and marketing analyst.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Inter+Tight:wght@300;400;500;600&display=swap" />
        <meta name="theme-color" content="#050506" />
      </head>
      <body>{children}</body>
    </html>
  );
}
