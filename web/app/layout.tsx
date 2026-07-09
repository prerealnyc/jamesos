import type { Metadata } from "next";
import "./globals.css";
import { Shell } from "@/components/shell";
import { ThemeSwitcher } from "@/components/theme-switcher";

export const metadata: Metadata = {
  title: "JAMES OS · Brand Manager",
  description:
    "Ingest a brand's voice, enforce its guidelines, produce on-voice content — grounded, cited, never drifting.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="theme-manager">
      <head>
        {/* Apply the saved theme choice before paint (no flash). Brand
            Manager 2.0's design (light, stone/paper, terracotta) is the
            default; Classic (the pre-merge dark look) remains reachable
            via the switcher until cutover sign-off. */}
        <script
          dangerouslySetInnerHTML={{
            __html:
              "try{if(localStorage.getItem('jos-theme-preview')==='classic'){var c=document.documentElement.classList;c.remove('theme-manager');c.add('dark')}}catch(e){}",
          }}
        />
        {/* Same typefaces as the approved dashboard build */}
        <link
          href="https://api.fontshare.com/v2/css?f[]=general-sans@400,500,600,700&display=swap"
          rel="stylesheet"
        />
        <link
          href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500&display=swap"
          rel="stylesheet"
        />
      </head>
      <body className="font-sans">
        <Shell>{children}</Shell>
        <ThemeSwitcher />
      </body>
    </html>
  );
}
