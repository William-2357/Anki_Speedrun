// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

import { expect, test } from "vitest";

import { normalizeTagTopicMap, resolveTopicId } from "./grouping";

function mapOf(entries: Record<string, string>): Map<string, string> {
    return normalizeTagTopicMap(entries);
}

test("canonical cfa::topic tags resolve without the map, aliases fold", () => {
    const map = mapOf({});
    expect(resolveTopicId("cfa::topic::fixed_income", map)).toBe("fixed_income");
    // alias suffixes fold onto canonical ids, like the dashboard table
    expect(resolveTopicId("cfa::topic::quant", map)).toBe("quantitative_methods");
    expect(resolveTopicId("CFA::Topic::Econ", map)).toBe("economics");
    // unknown suffixes group nothing - attribution is never invented
    expect(resolveTopicId("cfa::topic::astrology", map)).toBeNull();
});

test("canonical tags beat the user map (engine precedence)", () => {
    const map = mapOf({ "cfa::topic::quant": "economics" });
    expect(resolveTopicId("cfa::topic::quant", map)).toBe("quantitative_methods");
});

test("exact map match beats a shorter prefix match", () => {
    const map = mapOf({
        finance: "derivatives",
        "finance::bonds": "fixed_income",
    });
    expect(resolveTopicId("finance::bonds", map)).toBe("fixed_income");
    expect(resolveTopicId("finance::equity", map)).toBe("derivatives");
    // longest prefix wins for deeper tags
    expect(resolveTopicId("finance::bonds::duration", map)).toBe("fixed_income");
});

test("prefix keys match whole :: segments only", () => {
    const map = mapOf({ finance: "fixed_income" });
    expect(resolveTopicId("financeplus::bonds", map)).toBeNull();
    expect(resolveTopicId("finance", map)).toBe("fixed_income");
});

test("keys are normalized like the engine: case, trim, trailing ::", () => {
    const map = mapOf({ "  Finance::  ": "fixed_income" });
    expect(resolveTopicId("FINANCE::Bonds::Duration", map)).toBe("fixed_income");
});

test("ignore and unknown topic values group nothing", () => {
    const map = mapOf({ reading42: "ignore", reading43: "not_a_topic" });
    expect(resolveTopicId("reading42", map)).toBeNull();
    expect(resolveTopicId("reading43", map)).toBeNull();
});
