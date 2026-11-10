// Sections fade and slide in as they scroll into view, and fade out as they leave.
// Safe by design: visibility is worked out from each element's position (on scroll, resize and whenever
// content is added), so nothing can stay hidden if an observer or an animation frame never fires. Content
// stays fully visible without JavaScript, and nothing moves when the device asks for reduced motion.

const SELECTOR = [
  ".hero > *", ".room-stage", ".consent-card", ".confirm-card", ".assessment .as-section", ".figures .figure",
  ".what-if-card", ".outcome", ".advisor", ".ev-section", ".ev-case", ".ws-block", ".case", ".site-footer",
].join(",");

/** True when an element's box overlaps the viewport (with a small margin at the top and bottom). */
export function inView(rect, height) {
  return rect.top < height * 0.94 && rect.bottom > height * 0.06;
}

export function startReveal() {
  if (typeof window === "undefined" || typeof document === "undefined") return () => {};
  if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return () => {};
  const tracked = new Set();

  const update = () => {
    const h = window.innerHeight || document.documentElement.clientHeight;
    for (const el of tracked) {
      if (!el.isConnected) {
        tracked.delete(el);
        continue;
      }
      el.classList.toggle("reveal-in", inView(el.getBoundingClientRect(), h));
    }
  };

  const scan = (root) => {
    const nodes = root.querySelectorAll ? root.querySelectorAll(SELECTOR) : [];
    const h = window.innerHeight || document.documentElement.clientHeight;
    for (const el of nodes) {
      if (tracked.has(el)) continue;
      tracked.add(el);
      // Already on screen: show it at once (no hidden first frame); below the fold: wait for the scroll.
      if (inView(el.getBoundingClientRect(), h)) el.classList.add("reveal-in");
      el.classList.add("reveal");
    }
  };

  let queued = false;
  const onScroll = () => {
    if (queued) return;
    queued = true;
    setTimeout(() => {
      queued = false;
      update();
    }, 16);
  };
  scan(document);
  const mo = new MutationObserver((muts) => {
    for (const m of muts) m.addedNodes.forEach((n) => n.nodeType === 1 && scan(n.parentElement || n));
    update();
  });
  mo.observe(document.body, { childList: true, subtree: true });
  window.addEventListener("scroll", onScroll, { passive: true, capture: true });
  window.addEventListener("resize", onScroll, { passive: true });
  // Safety net: some browsers (and hidden tabs) skip scroll events; a light check keeps nothing hidden.
  const tick = setInterval(update, 300);
  return () => {
    clearInterval(tick);
    mo.disconnect();
    window.removeEventListener("scroll", onScroll, { capture: true });
    window.removeEventListener("resize", onScroll);
  };
}
