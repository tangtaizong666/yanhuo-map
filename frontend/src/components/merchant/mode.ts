/** Eligibility is stable across closing, pausing new orders and selling out. */
export function hasPickupEligibility(stall: any): boolean {
  const eligibility = stall?.capabilities?.pickup_orders?.eligible;
  if (typeof eligibility === "boolean") return eligibility;
  // Compatibility for servers that predate the capability contract.
  return stall?.transaction_enabled === true;
}
