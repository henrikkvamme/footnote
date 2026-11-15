import { createServerFn } from "@tanstack/react-start";
import { Schema } from "effect";

import { CheckFailed, CheckInput } from "#/domain/check.ts";
import { Check } from "#/server/check.ts";
import { runApi } from "#/server/runtime.ts";

export const check = createServerFn({ method: "POST" })
  .validator(Schema.toStandardSchemaV1(CheckInput))
  .handler(({ data }) => runApi(CheckFailed)(Check.use((service) => service.run(data))));
