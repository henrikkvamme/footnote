import { createServerFn } from "@tanstack/react-start";

import { BetaFailed } from "#/domain/beta.ts";
import { Beta } from "#/server/beta.ts";
import { runApi } from "#/server/runtime.ts";

export const beta = createServerFn({ method: "GET" }).handler(() =>
  runApi(BetaFailed)(Beta.use((service) => service.state())),
);
