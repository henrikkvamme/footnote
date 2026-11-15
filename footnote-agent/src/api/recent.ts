import { createServerFn } from "@tanstack/react-start";
import { Schema } from "effect";

import { Checker } from "#/server/checker.ts";
import { runApi } from "#/server/runtime.ts";

export const recentChecks = createServerFn({ method: "GET" }).handler(() =>
  runApi(Schema.Never)(Checker.use((checker) => checker.recent)),
);
