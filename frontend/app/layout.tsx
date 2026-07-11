import type { Metadata } from "next";
import { Fraunces, Spline_Sans, Spline_Sans_Mono } from "next/font/google";
import "./globals.css";

// Fonts are declared once here and shared across the whole app via CSS variables.
// --font-display (Fraunces) for headings, --font-sans (Spline Sans) for body,
// --font-mono (Spline Sans Mono) for labels / eyebrows.
const display = Fraunces({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  style: ["normal", "italic"],
  variable: "--font-display",
});
const sans = Spline_Sans({
  subsets: ["latin"],
  weight: ["300", "400", "500", "600"],
  variable: "--font-sans",
});
const mono = Spline_Sans_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-mono",
});

export const metadata: Metadata = {
  title: "DocuMedAI Chatbot",
  description: "DocuMedAI assistant",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${display.variable} ${sans.variable} ${mono.variable}`}
    >
      <body>
        {children}
      </body>
    </html>
  );
}
