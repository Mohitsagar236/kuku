import test from "node:test";
import assert from "node:assert/strict";
import { offsetInPromo, tsToSec } from "./format.js";

test("stitched hook offsets include prior segments", () => {
  const segments = [
    { episode: 1, start_s: 10, end_s: 20 },
    { episode: 2, start_s: 30, end_s: 50 },
  ];
  assert.equal(offsetInPromo({ episode: 2, ts: "00:32" }, segments), 12);
  assert.equal(offsetInPromo({ episode: 3, ts: "00:32" }, segments), null);
});

test("hook offset changes when the editor moves the start", () => {
  assert.equal(offsetInPromo({ episode: 1, ts: "00:13" }, [
    { episode: 1, start_s: 12, end_s: 40 },
  ]), 1);
  assert.equal(tsToSec("1:02:03.5"), 3723.5);
  assert.equal(tsToSec("bad"), null);
});
