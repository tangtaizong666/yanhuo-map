import type { Portion, Product } from "./types";

// Old drafts have no per-portion data. Keep the first N portions when quantity
// changes; newly added portions start without a special request.
export function normalizePortions(value: unknown, quantity: number): Portion[] {
  const rows = Array.isArray(value) ? value : [];
  return Array.from(
    { length: Math.max(0, Math.min(99, quantity)) },
    (_, index) => {
      const row = rows[index];
      const options =
        row?.options &&
        typeof row.options === "object" &&
        !Array.isArray(row.options)
          ? Object.fromEntries(
              Object.entries(row.options).filter(
                (entry): entry is [string, string] =>
                  typeof entry[1] === "string" && !!entry[1],
              ),
            )
          : {};
      return {
        options,
        note: typeof row?.note === "string" ? row.note.slice(0, 100) : "",
      };
    },
  );
}

export function specifiedPortions(value: unknown, quantity: number) {
  const rows = normalizePortions(value, quantity);
  return rows.some((row) => Object.keys(row.options).length || row.note.trim())
    ? rows
    : undefined;
}

export function invalidPortions(
  product: Product,
  value: unknown,
  quantity: number,
) {
  return normalizePortions(value, quantity).some((row) =>
    Object.entries(row.options).some(
      ([name, choice]) =>
        !product.taste_options
          ?.find((group) => group.name === name)
          ?.choices.includes(choice),
    ),
  );
}

export function portionText(portion: Portion) {
  return [
    ...Object.entries(portion.options).map(
      ([key, value]) => `${key}：${value}`,
    ),
    portion.note.trim(),
  ]
    .filter(Boolean)
    .join(" · ");
}
