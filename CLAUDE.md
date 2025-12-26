# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Project Name:** 45black Website
**Type:** static
**Tech Stack:** HTML/CSS/JS
**Created:** 2024

45black company website - a static HTML site showcasing pension technology consulting services, case studies, and product offerings including Apex Governance and Conveyancing Companion.

## Tech Stack

- **Type**: Static HTML/CSS/JS
- **Hosting**: Cloudflare Pages
- **Domain**: 45black.tech

## Structure

```
/
├── index.html           # Homepage
├── about.html          # About page
├── contact.html        # Contact page
├── case-study.html     # Case studies
├── conveyancing-companion.html  # Product page
├── css/               # Stylesheets
├── js/                # JavaScript files
├── assets/            # Images and media
└── .github/           # GitHub Actions for deployment
```

## Development

No build step required. Open HTML files directly in browser or use a local server:

```bash
# Python
python -m http.server 8000

# Node
npx serve .
```

## Deployment

Deployed automatically via GitHub Pages or Cloudflare Pages on push to main branch.

## Environment Variables

None required - fully static site.

## Testing Notes

Visual testing only. Check all pages render correctly across browsers.
