import type { Metadata } from "next";
import "./globals.css";

const siteUrl =
  process.env.NEXT_PUBLIC_SITE_URL ||
  (process.env.VERCEL_URL ? `https://${process.env.VERCEL_URL}` : "http://localhost:3000");

export const metadata: Metadata = {
  title: "Bellwether — Long Island",
  description:
    "Campaign headquarters for Long Island. Voter outreach, live election maps, donation analytics, and AI-powered targeting — everything the team needs in one place.",
  metadataBase: new URL(siteUrl),
  openGraph: {
    title: "Bellwether — Long Island Campaign Platform",
    description:
      "Voter outreach, live election maps, donation analytics, and AI-powered targeting for the 2026 race.",
    siteName: "Bellwether LI",
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "Bellwether — Long Island Campaign Platform",
    description:
      "Voter outreach, live election maps, donation analytics, and AI-powered targeting for the 2026 race.",
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Spectral:ital,wght@0,400;0,500;0,600;0,700;1,400&family=Inter:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>{children}</body>
    </html>
  );
}
