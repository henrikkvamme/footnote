import { createFileRoute } from "@tanstack/react-router";

import { recentChecks } from "#/api/recent.ts";

const Home = () => {
  const result = Route.useLoaderData();
  const checks = result.ok ? result.value : [];

  return (
    <main className="mx-auto flex max-w-2xl flex-col gap-6 p-4 sm:p-8">
      <header className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold">footnote-agent</h1>
        <p>
          The fact-check step of Footnote. It takes one claim, searches the web once, and says
          whether established sources contradict it. One model call through the Hostinger AI Router.
        </p>
        <p className="font-mono text-sm">POST /api/check · GET /api/health</p>
      </header>
      <section className="flex flex-col gap-2">
        <h2 className="text-lg font-semibold">Last checks</h2>
        {checks.length === 0 ? (
          <p>No checks since the last start.</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {checks.map((entry) => (
              <li className="flex flex-wrap items-baseline gap-x-3" key={entry.at}>
                <span className="font-mono text-sm">{entry.stance}</span>
                <span className="min-w-0 flex-1 break-words">{entry.claim}</span>
                <span className="font-mono text-sm">
                  {entry.ms} ms · {entry.provider}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
};

export const Route = createFileRoute("/")({
  component: Home,
  loader: () => recentChecks(),
});
