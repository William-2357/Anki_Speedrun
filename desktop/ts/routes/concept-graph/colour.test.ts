// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

import { expect, test } from "vitest";

import type { GraphNode } from "./graph";
import { difficultyColour, recallColour } from "./graph";

function makeNode(overrides: Partial<GraphNode>): GraphNode {
    return {
        id: 0,
        tag: "reading::x",
        label: "x",
        topicId: null,
        cardCount: 4,
        studiedCards: 0,
        averageRetrievability: 0,
        gradedAnswers: 0,
        againHardAnswers: 0,
        radius: 12,
        x: 0,
        y: 0,
        ...overrides,
    };
}

const GREY = "#9ca3af";

test("the two colour modes read different stats and can disagree", () => {
    // struggled historically (75% Again/Hard) but crammed recently, so
    // current predicted recall is high: red-ish difficulty, green recall
    const node = makeNode({
        gradedAnswers: 8,
        againHardAnswers: 6,
        studiedCards: 4,
        averageRetrievability: 0.95,
    });
    const difficulty = difficultyColour(node);
    const recall = recallColour(node, true);
    expect(difficulty).toBe("hsl(30, 70%, 45%)"); // 25% accuracy: red-orange
    expect(recall).toBe("hsl(114, 70%, 45%)"); // 95% recall: green
    expect(difficulty).not.toBe(recall);
});

test("each mode abstains to grey on its own missing data", () => {
    // studied (has FSRS state) but never graded: only difficulty abstains
    const studiedNotGraded = makeNode({ studiedCards: 3, averageRetrievability: 0.8 });
    expect(difficultyColour(studiedNotGraded)).toBe(GREY);
    expect(recallColour(studiedNotGraded, true)).not.toBe(GREY);

    // graded historically but no FSRS memory state: only recall abstains
    const gradedNotStudied = makeNode({ gradedAnswers: 5, againHardAnswers: 1 });
    expect(difficultyColour(gradedNotStudied)).not.toBe(GREY);
    expect(recallColour(gradedNotStudied, true)).toBe(GREY);

    // FSRS off: recall always abstains rather than guessing
    const studied = makeNode({ studiedCards: 3, averageRetrievability: 0.8 });
    expect(recallColour(studied, false)).toBe(GREY);
});
