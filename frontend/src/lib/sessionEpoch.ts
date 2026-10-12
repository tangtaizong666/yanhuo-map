// A response only describes the identity that initiated its request.
let epoch = 0;
let actor: string | null = null;
let verification: Promise<void> | null = null;
let verificationFailed = false;
let verifier: (() => Promise<void>) | null = null;
export const sessionEpoch = () => epoch;
export const sessionActor = () => actor;
export function setSessionActor(userId: number | null) {
  actor = userId === null ? "anonymous" : String(userId);
}
export function advanceSessionEpoch() {
  // Explicit login/logout or a confirmed identity change supersedes the old
  // check. Its callers still reject their captured epoch before sending writes.
  verification = null;
  verificationFailed = false;
  return ++epoch;
}

export function setSessionVerifier(value: () => Promise<void>) {
  verifier = value;
}

export function trackSessionVerification(operation: Promise<void>) {
  verificationFailed = false;
  const tracked = operation.then(
    () => {
      if (verification === tracked) verification = null;
    },
    (error) => {
      if (verification === tracked) {
        verification = null;
        verificationFailed = true;
      }
      throw error;
    },
  );
  verification = tracked;
  return tracked;
}

export async function verifySessionForWrite() {
  // A failed focus check is not proof that the previous account is still
  // logged in. A later user retry performs a fresh check, shared by all writers.
  if (verificationFailed) {
    if (!verifier) throw new Error("Identity verification unavailable");
    await verifier();
  }
  // A newer focus check can replace the one we just awaited.
  while (verification) await verification;
  if (verificationFailed) throw new Error("Identity verification failed");
}
