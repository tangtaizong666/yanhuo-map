import { ApiError } from "./api";

/**
 * Returns true only when a fresh (non-uncertain) idempotent write attempt was
 * conclusively rejected before mutating server state. Once an earlier attempt
 * failed with an unknown/5xx/network error (`uncertain = true`), a later 4xx
 * (such as CSRF 403) says nothing about the original write and must keep the
 * pending idempotency record intact.
 */
export function isDefinitiveIdempotentRejection(
  cause: unknown,
  uncertain: boolean,
  excludedCodes: readonly string[] = [],
): cause is ApiError {
  if (uncertain || !(cause instanceof ApiError)) return false;
  if (cause.code && excludedCodes.includes(cause.code)) return false;
  return (
    cause.data?.submitted === false ||
    [400, 403, 404, 422].includes(cause.status)
  );
}
