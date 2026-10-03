export function allows(
  order: { allowed_actions?: string[] } | null | undefined,
  action: string,
  fallback = false,
) {
  return Array.isArray(order?.allowed_actions)
    ? order.allowed_actions.includes(action)
    : fallback;
}
export function hasFinancialHold(
  order: { financial_hold_reason?: string } | null | undefined,
) {
  return !!order?.financial_hold_reason;
}
