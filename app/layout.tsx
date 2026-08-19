import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "臺股市場溫度計",
  description: "整合市場廣度與外資期貨部位的研究型儀表板",
  other: { "codex-preview": "development" },
  icons: { icon: "/favicon.svg", shortcut: "/favicon.svg" },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="zh-Hant"><body>{children}</body></html>;
}
