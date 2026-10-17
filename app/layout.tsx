import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "QUANT RESEARCH DESK｜臺股市場溫度計",
  description: "以 GitHub canonical research contract 呈現的臺股研究證據監測介面",
  icons: { icon: "/favicon.svg", shortcut: "/favicon.svg" },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="zh-Hant"><body>{children}</body></html>;
}
