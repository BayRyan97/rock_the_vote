import type { Metadata } from "next";
import { Spectral, Inter, IBM_Plex_Mono } from "next/font/google";
import "./globals.css";

const spectral = Spectral({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  style: ["normal", "italic"],
  variable: "--font-spectral",
  display: "swap",
});

const inter = Inter({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-inter",
  display: "swap",
});

const ibmPlexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-mono",
  display: "swap",
});

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
    <html lang="en" className={`${spectral.variable} ${inter.variable} ${ibmPlexMono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
