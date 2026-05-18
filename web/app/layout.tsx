import type { Metadata } from "next";
import "./globals.css";
import { Shell } from "@/components/shell";

export const metadata: Metadata = {
  title: "JAMES OS · Brand Manager",
  description: "Ingest a brand's voice, enforce its guidelines, produce on-voice content — grounded, cited, never drifting.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="font-sans">
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
