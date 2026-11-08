# DESIGN.md: RunwayML.com — feature/research showcase section

## Source
- URL: https://www.monet.design/c/runwayml-com-feature-3 (Monet registry showcase of runwayml.com)
- Component source: github.com/monet-design/monet-registry `src/components/registry/runwayml-com-feature-3/index.tsx`
- Capture date: 2026-11-08
- Evidence: scraped markdown + React component source (exact Tailwind classes and color tokens); background image is Mux video poster (abstract blurred warm tones) — NOT reused; we synthesize it with CSS

## Reference
- Showcase preview: https://registry.monet.design/live-preview/runwayml-com-feature-3
- Background asset described as: "Abstract blurred background with warm yellow, green and earthy tones, soft gradient, out of focus landscape-like imagery"

## Design Summary
A dark, cinematic, self-contained band floating inside the page (`rounded-lg`, `my-20 lg:my-40`). Full-bleed blurred atmospheric background with a light black overlay. Two asymmetric columns: left = small 12–14px label + 18–24px leading-tight manifesto heading + a frosted glass button; right = a stack of three "research link" rows separated by hairline dividers, each with title + ↗ arrow and a muted one-line description. Everything is white-on-dark regardless of page theme. The feel: quiet, premium, editorial — low energy, high confidence.

## Tokens (from component source)
- Band: `rounded-lg`, full-bleed background media under content, overlay `bg-black/10`
- Content inset: `w-11/12 md:w-[87.5%] mx-auto`, padding `py-20 md:py-32 lg:py-40`; outer spacing `my-20 lg:my-40`; container `max-w-7xl px-4 sm:px-6 lg:px-8`
- Columns: left `lg:w-[37.5%]`, right `lg:w-[31.25%]`, gap-10; right column sits at the far right (justify-between)
- Text (always white on the dark band): primary `#FFFFFF`, secondary `rgba(255,255,255,0.9)`, muted `rgba(255,255,255,0.6)`, dividers `rgba(255,255,255,0.95)`
- Label: 12px (lg:14px), no uppercase tracking, leading-none, margin-bottom ~24px
- Heading: 18px → 24px (xl), leading-tight, sentence case, no bold
- Button: frosted — bg `rgba(247,247,247,0.05)`, hover `0.15`, outline `1px solid rgba(255,255,255,0.75)`, `rounded-md`, `backdrop-blur`, text-sm
- Link rows: `py-7`, title 18px (xl:20px) + ↗ arrow right-aligned, description 12–14px muted, `border-t border-b divide-y` on the stack; hover: text transitions to pure white
- Motion: whileInView fade+rise (0.4–0.6s ease-out, staggered 0.1s per row), once

## Agent Build Instructions (for SwitchIt's stack: static HTML, no Tailwind)
- Recreate the band with plain CSS: `border-radius: 10px; overflow: hidden; position: relative; color: #fff;`
- Background: synthesize the blurred warm landscape with pure CSS radial-gradients (warm yellow/green/earthy hues, large soft blobs, slight blur via `filter: blur(60px)` on oversized pseudo-elements) + `rgba(0,0,0,0.10)` overlay. No external image.
- Keep the exact asymmetric grid (37.5% / 31.25% with space-between), the hairline-divided link stack, the frosted button, and the label/heading scale above.
- Hover transitions: 200ms linear to pure white; keep under reduced-motion rules (no translate animations).
- Voice: label = category, heading = one honest manifesto sentence, rows = three real capability callouts with ↗ anchors.

## Rerun Inputs
workflow: firecrawl-website-design-clone
source_url: https://www.monet.design/c/runwayml-com-feature-3
target_stack: static HTML/CSS (self-contained, no build)
output: DESIGN.md (this file)
