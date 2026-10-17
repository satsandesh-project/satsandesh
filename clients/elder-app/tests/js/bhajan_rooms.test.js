// Logic of the bhajan rooms screen (assets/bhajan_rooms.js), no DOM and no dependencies:
//   node --test tests/js/
// The server decides who may take the floor and who leads; these tests pin what the app DRAWS
// from that answer, so a change that put a button in front of the wrong person fails here.

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const logic = require(path.join(__dirname, "..", "..", "assets", "bhajan_rooms.js"));

const you = (over) =>
  Object.assign(
    { state: null, position: null, is_lead: false, can_take_floor: false, can_manage: false },
    over
  );
const someone = { user_id: "u", name: "Anand" };

test("a listener who has not raised a hand sees only 'raise hand'", () => {
  assert.deepEqual(logic.buttonsFor(you(), null), ["raise"]);
  assert.deepEqual(logic.buttonsFor(you({ state: "done" }), someone), ["raise"]);
  assert.deepEqual(logic.buttonsFor(you({ state: "removed" }), someone), ["raise"]);
});

test("a person waiting sees 'lower hand', and nothing that takes the floor unless the server says so", () => {
  assert.deepEqual(logic.buttonsFor(you({ state: "waiting", position: 2 }), someone), ["lower"]);
});

test("the first in line, with nobody leading, may take the next bhajan, and may still lower their hand", () => {
  const first = you({ state: "waiting", position: 1, can_take_floor: true });
  assert.deepEqual(logic.buttonsFor(first, null), ["take", "lower"]);
});

test("the lead sees 'I am done', and neither raise nor lower", () => {
  const lead = you({ state: "leading", is_lead: true, can_take_floor: true });
  assert.deepEqual(logic.buttonsFor(lead, someone), ["pass"]);
});

test("a moderator can hand over from someone else's turn, and can also raise their own hand", () => {
  const mod = you({ can_take_floor: true, can_manage: true });
  assert.deepEqual(logic.buttonsFor(mod, someone), ["handover", "raise"]);
  assert.deepEqual(logic.buttonsFor(mod, null), ["take", "raise"]);
});

test("nobody is shown a take button the server did not allow", () => {
  for (const state of [null, "waiting", "done", "removed"]) {
    for (const lead of [null, someone]) {
      const ids = logic.buttonsFor(you({ state, can_take_floor: false }), lead);
      assert.ok(!ids.includes("take") && !ids.includes("handover") && !ids.includes("pass"), JSON.stringify({ state, lead, ids }));
    }
  }
});

test("without a 'you' block nothing is drawn", () => {
  assert.deepEqual(logic.buttonsFor(null, null), []);
  assert.deepEqual(logic.buttonsFor(undefined, someone), []);
});

test("the microphone follows the server's word about who leads", () => {
  assert.equal(logic.micPlan(you({ is_lead: true }), false), "on");
  assert.equal(logic.micPlan(you({ is_lead: true }), true), "none");
  assert.equal(logic.micPlan(you({ is_lead: false }), true), "off");
  assert.equal(logic.micPlan(you({ is_lead: false }), false), "none");
  assert.equal(logic.micPlan(null, true), "off");
  assert.equal(logic.micPlan(null, false), "none");
});

test("a handoff turns the old lead's microphone off and the new lead's on", () => {
  // Poll N: Anand leads and his mic is on. Poll N+1: the server says Bhavya leads.
  let anandMic = true;
  let bhavyaMic = false;
  const anandSees = you({ is_lead: false });
  const bhavyaSees = you({ is_lead: true });
  if (logic.micPlan(anandSees, anandMic) === "off") anandMic = false;
  if (logic.micPlan(bhavyaSees, bhavyaMic) === "on") bhavyaMic = true;
  assert.deepEqual([anandMic, bhavyaMic], [false, true]);
});

test("a lead whose token could not publish needs a fresh token; nobody else does", () => {
  assert.equal(logic.needsFreshToken(you({ is_lead: true }), false), true);
  assert.equal(logic.needsFreshToken(you({ is_lead: true }), true), false);
  assert.equal(logic.needsFreshToken(you({ is_lead: false }), false), false);
  assert.equal(logic.needsFreshToken(you({ is_lead: true }), undefined), false);
  assert.equal(logic.needsFreshToken(null, false), false);
});

test("each failure has its own message, and a lost connection is not 'forbidden'", () => {
  const cases = { 0: "err_network", 401: "err_signin", 403: "err_forbidden", 404: "err_notfound", 409: "err_closed", 502: "err_audio", 503: "err_audio", 500: "err_generic", 422: "err_generic" };
  for (const [status, key] of Object.entries(cases)) {
    assert.equal(logic.errorKey(Number(status)), key, status);
  }
});

test("a number is put into the text, and an unknown placeholder disappears", () => {
  assert.equal(logic.fill("You are number {n} in line", { n: 3 }), "You are number 3 in line");
  assert.equal(logic.fill("x {n} y", {}), "x  y");
  assert.equal(logic.fill("no placeholder", { n: 1 }), "no placeholder");
});

test("the people in the room are listed once each, by name, in order", () => {
  assert.deepEqual(logic.hereNames("You", ["Chitra", "Anand", "Chitra", ""]), ["Anand", "Chitra", "You"]);
  assert.deepEqual(logic.hereNames("", []), []);
});

test("the screen is rebuilt only when what it shows has changed", () => {
  const a = logic.signature([["raise"], null, false, ["u1"]]);
  assert.equal(a, logic.signature([["raise"], null, false, ["u1"]]));
  assert.notEqual(a, logic.signature([["raise"], null, false, ["u1", "u2"]]));
  assert.notEqual(a, logic.signature([["lower"], 1, false, ["u1"]]));
});
