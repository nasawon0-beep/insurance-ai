# Moadoom shared UI contract

The Windows app, Android app and public site share the existing M mark, electric blue actions, navy text, pale blue selected states, mint success chips, 8 px/dp button corners and 10 px/dp cards. Keep platform navigation and data behavior unchanged. Android uses system fonts for native text scaling; Windows and the site bundle the existing SUIT variable font with its OFL license.

`ui-tokens.json` records the common palette. Windows implements it in `desktop/src/styles/blue.css`, Android in `MoaDesign.kt`, and the updates site in `assets/ui-tokens.css`. Secondary text is #5B6883 on white (5.60:1); #6E7B96 is reserved for decorative outlines, not small body text. Android body text is 14sp and secondary text 13sp. Interactive mobile controls are at least 48dp/px; Windows retains compact mouse controls with a 48px coarse-pointer minimum.

Design reference: Higgsfield GPT Image 2.6 job `2e43cb8e-76fb-4d14-9241-3d1c5ae1fb5d`, created 2026-11-28. The board informed spacing, palette and component hierarchy only. Its generated product labels are not a specification: Android remains a debug beta; do not add stable Android downloads, nonexistent desktop navigation or fictional features from the image.

Preserve keyboard focus, reduced-motion behavior, text wrapping, dark-mode colors, original production routes, accessibility labels and all account/download logic. The site's archived B mark is retained as an unused historical asset; the user selected the existing app M mark for current surfaces.

Windows 2.5.21 publication metadata is intentionally unchanged. These Windows refinements require a future separately versioned installer before they affect an installed desktop app; a source preview is not an installer verification.
