# F5 metadata comparison regression check

Install Node.js, Chrome, and the optional Playwright package in your test environment (`npm install --no-save --package-lock=false playwright`). Run from the repository root:

```sh
node tests/f5_metadata_compare.cjs
```

The test defaults to installed Chrome. Set PLAYWRIGHT_CHANNEL to another installed Playwright-supported channel if needed.

This isolated browser fixture loads the production get_meta.js with the bundled jQuery and Underscore. Search responses are mocked; no API key or real book library is needed. It checks seven fields, unchecked defaults, no mutation on preview, selective application, unchanged unrelated fields, cancellation, candidate reset, missing/invalid values, small cover previews and failed-image fallback, readable HTML descriptions, no active HTML in the preview, and preservation of original description markup on application.

Before merging, the reviewer must also run the real application with a disposable library and verify TinyMCE, datepicker, modal closing, saved metadata, and cover downloads. This fixture does not verify those integrations or live provider APIs. Follow TEAM.md: main requires a teammate approval and applicable CI checks (local reviewer execution until F9 CI is available).
