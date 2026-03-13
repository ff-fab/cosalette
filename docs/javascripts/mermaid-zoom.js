/* Mermaid diagram zoom — click to fullscreen overlay ---------------------- */
/*                                                                             */
/* Why re-render instead of cloning the SVG?                                   */
/* Zensical (Material for MkDocs) renders Mermaid SVGs inside a *closed*       */
/* Shadow DOM:  r.attachShadow({mode:"closed"}).  This makes the SVG           */
/* completely inaccessible via querySelector or any DOM API.                    */
/* So we capture the Mermaid source text before it's processed, and            */
/* re-render it on demand via mermaid.render() when the user clicks.           */

;(function () {
  "use strict";

  var overlay = null;
  var sourcesByPath = {};   // pathname → [source, source, …]
  var MERMAID_KW = /^(graph|flowchart|sequenceDiagram|classDiagram|stateDiagram|erDiagram|journey|gantt|pie|gitgraph|mindmap|timeline|quadrantChart|sankey|xychart)\b/i;

  /* ── Source capture ────────────────────────────────────────────────── */

  /**
   * Grab the raw Mermaid code from <pre class="mermaid"><code>…</code></pre>
   * elements that exist in the DOM right now — before the theme's JS replaces
   * them with shadow-DOM containers.
   *
   * The Zensical bundle removes the "mermaid" class synchronously when it
   * starts processing, so we also fall back to sniffing <pre><code> text for
   * Mermaid keywords (graph, flowchart, sequenceDiagram, etc.).
   */
  function captureSources() {
    var key = window.location.pathname;
    if (sourcesByPath[key]) return;          // already captured this page

    var sources = [];

    // Strategy 1: elements still have the class
    var pres = document.querySelectorAll("pre.mermaid");

    // Strategy 2: class already removed — scan all <pre><code> for keywords
    if (pres.length === 0) {
      var candidates = [];
      document.querySelectorAll("pre > code").forEach(function (code) {
        if (MERMAID_KW.test(code.textContent.trim())) {
          candidates.push(code.parentElement);
        }
      });
      pres = candidates;
    }

    Array.prototype.forEach.call(pres, function (pre) {
      var code = pre.querySelector("code");
      sources.push(code ? code.textContent.trim() : pre.textContent.trim());
    });

    if (sources.length > 0) {
      sourcesByPath[key] = sources;
    }
  }

  // Capture immediately — our script runs before the async CDN mermaid load
  captureSources();

  // Re-capture after SPA navigations (Zensical instant loading)
  if (typeof document$ !== "undefined") {
    // Zensical/Material exposes a document$ observable on content swap
    document$.subscribe(function () { captureSources(); });
  } else {
    // Fallback: watch for URL changes
    var lastPath = window.location.pathname;
    var navObserver = new MutationObserver(function () {
      if (window.location.pathname !== lastPath) {
        lastPath = window.location.pathname;
        captureSources();
      }
    });
    navObserver.observe(document.body, { childList: true, subtree: true });
  }

  /* ── Overlay ───────────────────────────────────────────────────────── */

  function getOverlay() {
    if (overlay) return overlay;
    overlay = document.createElement("div");
    overlay.className = "mermaid-zoom-overlay";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-label",
      "Zoomed diagram — click or press Escape to close");

    var hint = document.createElement("span");
    hint.className = "mermaid-zoom-close";
    hint.textContent = "Click or press Esc to close";
    overlay.appendChild(hint);

    overlay.addEventListener("click", function (e) {
      // Only close when clicking the backdrop, not the diagram content
      if (e.target === overlay || e.target === hint) { closeOverlay(); }
    });
    return overlay;
  }

  function openOverlayWithSvg(svgMarkup) {
    var el = getOverlay();
    // Remove any previous content
    var prev = el.querySelector(".mermaid-zoom-content");
    if (prev) prev.remove();

    var container = document.createElement("div");
    container.className = "mermaid-zoom-content";
    container.innerHTML = svgMarkup;

    el.appendChild(container);
    document.body.appendChild(el);
    void el.offsetWidth;                    // force reflow for transition
    el.classList.add("active");
    document.body.style.overflow = "hidden";
  }

  function closeOverlay() {
    if (!overlay) return;
    overlay.classList.remove("active");
    document.body.style.overflow = "";
    overlay.addEventListener("transitionend", function handler() {
      overlay.removeEventListener("transitionend", handler);
      if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
    });
  }

  /* ── Click handling (event delegation) ─────────────────────────────── */

  /**
   * Walk up from the click target to find the .mermaid container,
   * then determine its index among all .mermaid containers on the page.
   */
  function findMermaidIndex(target) {
    var el = target;
    for (var i = 0; i < 10 && el && el !== document.body; i++) {
      if (el.classList && el.classList.contains("mermaid")) {
        var all = document.querySelectorAll(".mermaid");
        return Array.prototype.indexOf.call(all, el);
      }
      el = el.parentElement;
    }
    return -1;
  }

  var renderCounter = 0;

  document.addEventListener("click", function (e) {
    if (overlay && overlay.classList.contains("active")) return;

    var index = findMermaidIndex(e.target);
    if (index < 0) return;

    var sources = sourcesByPath[window.location.pathname];
    if (!sources || index >= sources.length) return;

    // mermaid is loaded by the theme from CDN — it must be available by
    // the time the user can see (and click) a rendered diagram.
    if (typeof mermaid === "undefined") return;

    e.preventDefault();
    e.stopPropagation();

    var id = "__mermaid_zoom_" + (renderCounter++);
    var source = sources[index];

    mermaid.render(id, source).then(function (result) {
      /* DEBUG — remove after confirming it works */
      console.log("[mermaid-zoom] SVG length:", result.svg.length,
                  "| starts with:", result.svg.substring(0, 120));
      openOverlayWithSvg(result.svg);
    }).catch(function (err) {
      console.error("[mermaid-zoom] render failed:", err,
                    "| source:", source.substring(0, 200));
    });
  }, true);     // capture phase to beat any stopPropagation in the theme

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && overlay && overlay.classList.contains("active")) {
      closeOverlay();
    }
  });
})();
