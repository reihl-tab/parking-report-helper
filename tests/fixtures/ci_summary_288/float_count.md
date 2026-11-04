## Test results: unavailable

The test runner did not produce valid aggregate metrics. Check the job logs.

Coverage: not measured or unavailable in this job.

<details>
<summary>Per-test results (failures and skips first)</summary>

| Status | Test | What it checks | Reason |
| --- | --- | --- | --- |
| failed | test_beta.BetaTests.test_markup | &lt;b&gt;Escapes&lt;[path] &#124; pipes | AssertionError for [email] at [path] token=[redacted] [number] |
| error | test_delta.D.test_err |  | ValueError |
| unexpected success | test_eps.E.test_us |  |  |
| skipped | test_gamma.GammaTests.test_skip_ctx | Optional tool | Optional tool not installed |
| skipped | test_gamma.GammaTests.test_skip_bare |  | bare |
| expected failure | test_eps.E.test_xf |  |  |
| passed | test_alpha.AlphaTests.test_ok | Totals add up |  |
| mystery | test_zeta.Z.test_unknown_status |  |  |
|  | test_eta.H.test_no_status | multi line |  |

Expand an individual test for its structural plan and observed assertions.

<details>
<summary>failed: test_beta.BetaTests.test_markup</summary>

Source plan (static, branches may not execute; literal inputs withheld):

- Line 3: `assertIn` ().
- Line 4: `assertTrue` ().

Observed assertions (runtime values are privacy-filtered):

| Assertion | Inputs | Expected | Actual | Observed outcome |
| --- | --- | --- | --- | --- |
| assertIn | str | not captured | not captured | failed |

Strings, objects and large numbers are withheld. Context-manager assertions are not claimed passed until the test completes.

</details>

<details>
<summary>error: test_delta.D.test_err</summary>

Source plan (static, branches may not execute; literal inputs withheld):


Observed assertions (runtime values are privacy-filtered):

| Assertion | Inputs | Expected | Actual | Observed outcome |
| --- | --- | --- | --- | --- |
| None captured | | | | May use helpers, plain assert, skip or setup failure. See test status. |

Strings, objects and large numbers are withheld. Context-manager assertions are not claimed passed until the test completes.

</details>

<details>
<summary>unexpected success: test_eps.E.test_us</summary>

Source plan (static, branches may not execute; literal inputs withheld):


Observed assertions (runtime values are privacy-filtered):

| Assertion | Inputs | Expected | Actual | Observed outcome |
| --- | --- | --- | --- | --- |
| None captured | | | | May use helpers, plain assert, skip or setup failure. See test status. |

Strings, objects and large numbers are withheld. Context-manager assertions are not claimed passed until the test completes.

</details>

<details>
<summary>skipped: test_gamma.GammaTests.test_skip_ctx</summary>

Source plan (static, branches may not execute; literal inputs withheld):

Why skipped: No tool here

Where it runs instead: Docker image


Observed assertions (runtime values are privacy-filtered):

| Assertion | Inputs | Expected | Actual | Observed outcome |
| --- | --- | --- | --- | --- |
| None captured | | | | May use helpers, plain assert, skip or setup failure. See test status. |

Strings, objects and large numbers are withheld. Context-manager assertions are not claimed passed until the test completes.

</details>

<details>
<summary>skipped: test_gamma.GammaTests.test_skip_bare</summary>

Source plan (static, branches may not execute; literal inputs withheld):

Why skipped: Not recorded.

Where it runs instead: Not verified.


Observed assertions (runtime values are privacy-filtered):

| Assertion | Inputs | Expected | Actual | Observed outcome |
| --- | --- | --- | --- | --- |
| None captured | | | | May use helpers, plain assert, skip or setup failure. See test status. |

Strings, objects and large numbers are withheld. Context-manager assertions are not claimed passed until the test completes.

</details>

<details>
<summary>expected failure: test_eps.E.test_xf</summary>

Source plan (static, branches may not execute; literal inputs withheld):


Observed assertions (runtime values are privacy-filtered):

| Assertion | Inputs | Expected | Actual | Observed outcome |
| --- | --- | --- | --- | --- |
| None captured | | | | May use helpers, plain assert, skip or setup failure. See test status. |

Strings, objects and large numbers are withheld. Context-manager assertions are not claimed passed until the test completes.

</details>

<details>
<summary>passed: test_alpha.AlphaTests.test_ok</summary>

Source plan (static, branches may not execute; literal inputs withheld):

- Line 12: `assertEqual` (int, int).

Observed assertions (runtime values are privacy-filtered):

| Assertion | Inputs | Expected | Actual | Observed outcome |
| --- | --- | --- | --- | --- |
| assertEqual | int, int | 4 | 4 | passed |

Strings, objects and large numbers are withheld. Context-manager assertions are not claimed passed until the test completes.

</details>

<details>
<summary>mystery: test_zeta.Z.test_unknown_status</summary>

Source plan (static, branches may not execute; literal inputs withheld):


Observed assertions (runtime values are privacy-filtered):

| Assertion | Inputs | Expected | Actual | Observed outcome |
| --- | --- | --- | --- | --- |
| None captured | | | | May use helpers, plain assert, skip or setup failure. See test status. |

Strings, objects and large numbers are withheld. Context-manager assertions are not claimed passed until the test completes.

</details>

<details>
<summary>: test_eta.H.test_no_status</summary>

Source plan (static, branches may not execute; literal inputs withheld):


Observed assertions (runtime values are privacy-filtered):

| Assertion | Inputs | Expected | Actual | Observed outcome |
| --- | --- | --- | --- | --- |
| None captured | | | | May use helpers, plain assert, skip or setup failure. See test status. |

Strings, objects and large numbers are withheld. Context-manager assertions are not claimed passed until the test completes.

</details>

</details>

Assertion bodies/tracebacks stay in job logs; source names and safe reasons are shown.
