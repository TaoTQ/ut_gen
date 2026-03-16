# Java Unit Test Generator — Strategy v3.1

> This file is generated from `agent/skills/skill_pack.json`.
> Edit the JSON pack instead of editing this file manually.

## Execution Steps
1. Read `task_context.md` and list all target classes.
2. For each class, read source code and enumerate public methods.
3. Apply branch, boundary, loop, and exception rules method-by-method.
4. Write tests directly to the requested `test_output_path`.

## Coverage Targets
- Line: >= 90.0%
- Branch: >= 85.0%
- Method: >= 85.0%

## Generation Settings
- Test class suffix: `Test`
- Min cases per method: 3
- Prefer parameterized tests: true
- Use @DisplayName: true

## Core Rules
### Branch Focus
- each if/else must include true and false path tests
- each switch must include every case and default
- each try/catch must include success and exception path
- recursive method must include base case and recursive path
- null checks must include null and non-null inputs
- empty string checks must include empty and non-empty inputs
- string palindrome checks must include null, empty, single-char, even/odd length, case-sensitive, and special characters

### Boundary Three-Point
- x <= K -> test K-1, K, K+1
- length checks -> test 0, boundary-1, boundary
- numeric range -> test min-1, min, max, max+1
- string length -> test empty, single-char, multi-char
- negative values -> test -1, 0, positive for length/indices
- truncation boundaries -> test maxLen-1, maxLen, maxLen+1, zero, negative

### Loop Paths
- for/while -> test zero, one, and multiple iterations
- collection traversal -> empty, single-element, and multi-element
- string/array iteration -> test empty, single-element, multi-element
- word reversal -> test empty, single-word, multiple-words, leading/trailing spaces

### Condition Matrix
- a || b -> include (false,false), (false,true), (true,false)
- a && b -> include all 4 truth combinations

### Exception Policy
- prefer assertThrowsExactly for explicit exception types
- validate key message fragment for expected exceptions
- test IllegalArgumentException for invalid arguments
- test NullPointerException for null inputs when documented
- test StringIndexOutOfBoundsException for invalid string indices
- test IllegalArgumentException for negative counts or lengths

### Test Quality
- method naming style: method_scenario_expected
- avoid meaningless assertions such as assertTrue(true)
- every generated test method must contain at least one strong assertion
- test string methods with null, empty, whitespace-only inputs
- verify regex patterns with edge cases and special characters
- test case conversion methods with mixed case, numbers, and special characters

## Focus Hints From Optimizer
- StringUtils.isPangram branch=91.67%
- StringUtils.isPalindrome needs null and edge case tests
- StringUtils.truncate needs negative maxLen exception test
- StringUtils.camelToSnake needs leading underscore handling
- StringUtils.reverseWords needs whitespace handling tests
- StringUtils.countOccurrences needs overlapping substring tests
- StringUtils.repeat needs zero/negative count exception tests
- StringUtils.capitalize needs multi-word and punctuation tests

## Constraints
- Use JUnit 5 only.
- Test class name = source class + configured suffix.
- Every generated test method must include real assertions.
- No markdown fences or explanations in generated Java output.
