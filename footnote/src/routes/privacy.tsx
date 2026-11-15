import { createFileRoute } from "@tanstack/react-router";

import { Foot, Top } from "#/features/page/sections.tsx";

const parts = [
  {
    items: [
      "Footnote starts only when you press Start listening and start the screen broadcast. It stops when you stop the broadcast.",
      "While it runs, the broadcast sees the whole screen. The app takes at most one image of the screen each second, and the sound of what you watch.",
      "Footnote analyses public media only: a video, a reel, a public post, a news article or a livestream.",
    ],
    title: "What the app reads",
  },
  {
    items: [
      "A private or sensitive screen pauses Footnote: messages and chats, mail, banking and payments, passwords and sign-in forms, health, photos, settings, the home screen and a keyboard.",
      "The server discards the image of such a screen at once and reads no text from it.",
      "Sound that comes during a pause is deleted from disk at once. It waits in memory for at most 10 seconds and is then dropped without transcription, unless public media is on the screen again.",
    ],
    title: "Private screens",
  },
  {
    items: [
      "The images and the sound go to our server on a Hostinger VPS in Frankfurt.",
      "The server sends them to language models through the Hostinger AI Router, with OpenRouter as the second path, to get the text and to decide if a sentence is a claim that sources can check.",
      "For a claim that passes, the fact-check agent searches the web with the text of the claim.",
    ],
    title: "Where the data goes",
  },
  {
    items: [
      "No image is written to disk, and no image is kept in memory.",
      "The server keeps the text it read from the screen, the transcript of the session and the checks with their sources.",
      "The state of a session is freed after 10 minutes without a request.",
    ],
    title: "What the server keeps",
  },
  {
    items: [
      "This website sets no cookies and has no analytics.",
      "When you use Check a claim, the sentence you type goes to our server and to the fact-check agent, which searches the web with it.",
    ],
    title: "This website",
  },
];

const Privacy = () => (
  <>
    <Top />
    <main className="legal">
      <p className="meta">Footnote, 15 November 2026</p>
      <h1 className="h2">
        <span className="mk" data-on="true">
          Privacy
        </span>
      </h1>
      <p className="legal-lead">
        Footnote is a prototype from Junction 2026. This page says what the iPhone app and this
        website do with your data.
      </p>
      {parts.map((part) => (
        <section key={part.title}>
          <span className="rule" data-on="true" />
          <h2>{part.title}</h2>
          <ul>
            {part.items.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </section>
      ))}
    </main>
    <Foot />
  </>
);

export const Route = createFileRoute("/privacy")({
  component: Privacy,
  head: () => ({ meta: [{ title: "Footnote - Privacy" }] }),
});
