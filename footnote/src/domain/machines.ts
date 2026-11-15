import { checkFlowChecks } from "./check-flow.machine.ts";
import { checkoutChecks } from "./checkout.machine.ts";
import { demoChecks } from "./demo.machine.ts";
import { howChecks } from "./how.machine.ts";

export const allMachineChecks = [demoChecks, checkFlowChecks, howChecks, checkoutChecks];
