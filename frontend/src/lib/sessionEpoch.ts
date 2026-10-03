// A response only describes the identity that initiated its request.
let epoch = 0;
export const sessionEpoch = () => epoch;
export function advanceSessionEpoch() {
  return ++epoch;
}
