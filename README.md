# CodeCare static website

This folder contains the standalone CodeCare website files.

## Run locally

From this folder in PowerShell, run:

```powershell
py -m http.server 8765
```

Then open http://127.0.0.1:8765/ in a browser.

Light theme is the default. The header toggle switches between light and dark themes and remembers the choice in browser local storage.

The pricing and product capabilities are marked as proposed. Contact form submissions are not sent. Replace the site-domain placeholder in `sitemap.xml` and complete the legal-page placeholders before publishing.
