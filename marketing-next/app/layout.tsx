import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { Header, Footer } from "../components/site-chrome";

const geistSans = Inter({ variable: "--font-geist-sans", subsets: ["latin"] });
const jetbrainsMono = JetBrains_Mono({ variable: "--font-jetbrains-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  icons: { icon: "/icon.svg" },
  title: "SwitchIt! — move your AI workflow to a new provider, with evidence",
  description:
    "SwitchIt! replays your agent's real traces against a candidate provider and reports what survives the switch — pass, fail, or uncertain.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${geistSans.variable} ${jetbrainsMono.variable}`}>
      <body className="font-sans"><a href="#main-content" className="skip-link">Skip to content</a><Header />{children}<Footer /></body>
    </html>
  );
}
