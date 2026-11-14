import { describe, expect, it } from "vitest";
import { prepareDishPhoto, scaleDimensions } from "../src/lib/imagePrepare";

describe("dish photo client preparation", () => {
  it("preserves dimensions at or below maxEdge and scales larger photos proportionally", () => {
    expect(scaleDimensions(1200, 900, 1600)).toEqual({
      width: 1200,
      height: 900,
    });
    expect(scaleDimensions(4000, 3000, 1600)).toEqual({
      width: 1600,
      height: 1200,
    });
    expect(scaleDimensions(3024, 4032, 1600)).toEqual({
      width: 1200,
      height: 1600,
    });
  });

  it("rejects empty, non-image and >25 MB files while keeping compact valid JPEGs", async () => {
    await expect(
      prepareDishPhoto(new File([], "empty.jpg", { type: "image/jpeg" })),
    ).rejects.toThrow("有效");

    await expect(
      prepareDishPhoto(
        new File(["plain-text"], "notes.txt", { type: "text/plain" }),
      ),
    ).rejects.toThrow("JPG、PNG 或 WebP");

    const huge = {
      name: "camera.jpg",
      type: "image/jpeg",
      size: 26 * 1024 * 1024,
    } as File;
    await expect(prepareDishPhoto(huge)).rejects.toThrow("25 MB");

    const small = new File([new Uint8Array(256)], "dish.jpg", {
      type: "image/jpeg",
    });
    const prepared = await prepareDishPhoto(small);
    expect(prepared).toBe(small);
  });
});
