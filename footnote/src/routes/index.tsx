import { createFileRoute } from "@tanstack/react-router";
import { Schema } from "effect";

import { beta } from "#/api/beta.ts";
import { Pricing } from "#/features/billing/pricing.tsx";
import { CheckPanel } from "#/features/check/check-panel.tsx";
import { HeroDemo } from "#/features/demo/hero-demo.tsx";
import { How } from "#/features/page/how.tsx";
import {
  AnyApp,
  Closing,
  Film,
  Foot,
  HeroText,
  Hostinger,
  Notes,
  Statement,
  Top,
} from "#/features/page/sections.tsx";

const Home = () => {
  const { betaActive } = Route.useLoaderData();
  const { checkout } = Route.useSearch();

  return (
    <>
      <Top />
      <main>
        <section className="hero">
          <HeroText />
          <HeroDemo />
        </section>
        <AnyApp />
        <Statement />
        <Film />
        <How />
        <CheckPanel />
        <Pricing cancelled={checkout === "cancelled"} />
        <Hostinger />
        <Closing betaActive={betaActive} />
        <Notes />
      </main>
      <Foot />
    </>
  );
};

const HomeSearch = Schema.Struct({
  checkout: Schema.optional(Schema.String),
});

export const Route = createFileRoute("/")({
  component: Home,
  loader: async () => {
    const result = await beta();

    return { betaActive: result.ok && result.value.active };
  },
  validateSearch: Schema.toStandardSchemaV1(HomeSearch),
});
