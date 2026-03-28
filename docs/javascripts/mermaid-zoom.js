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
  var prevOverflow = "";    // saved body overflow before opening
  var sourcesByPath = {};   // pathname → [source, source, …]
  var renderCounter = 0;
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
    var preElems = document.querySelectorAll("pre.mermaid");

    // Strategy 2: class already removed — scan all <pre><code> for keywords
    if (preElems.length === 0) {
      var candidates = [];
      document.querySelectorAll("pre > code").forEach(function (code) {
        if (MERMAID_KW.test(code.textContent.trim())) {
          candidates.push(code.parentElement);
        }
      });
      preElems = candidates;
    }

    Array.prototype.forEach.call(preElems, function (pre) {
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
    document$.subscribe(function () {
      captureSources();
      scheduleMarkZoomable();
    });
  } else {
    // Fallback: watch for URL changes
    var lastPath = window.location.pathname;
    var navObserver = new MutationObserver(function () {
      if (window.location.pathname !== lastPath) {
        lastPath = window.location.pathname;
        captureSources();
        scheduleMarkZoomable();
      }
    });
    navObserver.observe(document.body, { childList: true, subtree: true });
  }

  // Initial zoomability probe
  scheduleMarkZoomable();

  /* ── Overlay ───────────────────────────────────────────────────────── */

  function getOverlay() {
    if (overlay) return overlay;
    overlay = document.createElement("div");
    overlay.className = "mermaid-zoom-overlay";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
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

    // Mermaid 11 produces SVGs with width="100%" and an inline
    // style="max-width: 3248px".  Inside a flex container "100%" has
    // no intrinsic size, so the SVG collapses.  Fix: promote the
    // max-width pixel value to the actual width so the SVG has a
    // concrete intrinsic size; CSS on the container then caps it.
    var svg = container.querySelector("svg");
    if (svg) {
      var mw = svg.style.maxWidth;          // e.g. "3248.91px"
      if (mw && mw.endsWith("px")) {
        svg.style.width = mw;              // give it a real pixel width
        svg.style.maxWidth = "100%";       // let container constrain it
      }
    }

    el.appendChild(container);
    document.body.appendChild(el);
    void el.offsetWidth;                    // force reflow for transition
    el.classList.add("active");
    prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
  }

  function closeOverlay() {
    if (!overlay) return;
    overlay.classList.remove("active");
    document.body.style.overflow = prevOverflow;

    function removeOverlay() {
      if (overlay && overlay.parentNode) overlay.parentNode.removeChild(overlay);
    }
    // Timeout fallback: if transitionend never fires (reduced-motion,
    // CSS not loaded, etc.), remove the overlay anyway after 300ms.
    var fallback = setTimeout(removeOverlay, 300);
    overlay.addEventListener("transitionend", function handler() {
      overlay.removeEventListener("transitionend", handler);
      clearTimeout(fallback);
      removeOverlay();
    }, { once: true });
  }

  /* ── Zoomability detection ──────────────────────────────────────────── */

  /**
   * Probe-render each diagram to determine its natural SVG width.
   * If the diagram already fits at full size within its on-page container
   * (i.e. zooming would not enlarge it), mark it with .mermaid-no-zoom so
   * the CSS cursor hint and click handler are suppressed.
   */
  function markZoomable() {
    var path = window.location.pathname;
    var containers = document.querySelectorAll(".mermaid");
    var sources = sourcesByPath[path];
    if (!containers.length || !sources) return;

    Array.prototype.forEach.call(containers, function (container, index) {
      if (index >= sources.length) return;

      var id = "__mermaid_probe_" + (renderCounter++);
      mermaid.render(id, sources[index]).then(function (result) {
        // Stale-page guard: if the user navigated away, discard results
        if (window.location.pathname !== path) return;

        // Clean up temporary elements Mermaid may have left behind
        var temp = document.getElementById(id);
        if (temp) temp.remove();
        temp = document.getElementById("d" + id);
        if (temp) temp.remove();

        // Parse the SVG to extract its natural (max-width) dimension
        var parser = new DOMParser();
        var doc = parser.parseFromString(result.svg, "image/svg+xml");
        var svg = doc.querySelector("svg");
        if (!svg) return;

        // Mermaid v11: width="100%", style="max-width: Xpx"
        var mw = svg.style.maxWidth;
        var naturalWidth = 0;
        if (mw && mw.endsWith("px")) {
          naturalWidth = parseFloat(mw);
        } else {
          var w = svg.getAttribute("width");
          if (w && /^[\d.]+px$/.test(w)) naturalWidth = parseFloat(w);
        }
        if (naturalWidth <= 0) return;  // can't determine — leave zoom on

        // If the diagram already renders at full size, zoom won't enlarge it
        if (naturalWidth <= container.clientWidth) {
          container.classList.add("mermaid-no-zoom");
        } else {
          container.classList.remove("mermaid-no-zoom");
        }
      }).catch(function () {
        // Probe failed — leave zoom enabled (safe default)
      });
    });
  }

  /**
   * Wait for the mermaid runtime to be available and diagrams to be
   * rendered, then run the zoomability probe.
   *
   * Debounced: if called again (e.g. SPA navigation) while a previous
   * poll is still running, the old poll is cancelled so only the latest
   * navigation's probe chain survives.
   */
  var pendingPoll = null;
  function scheduleMarkZoomable() {
    if (pendingPoll) { clearTimeout(pendingPoll); pendingPoll = null; }
    var attempts = 0;
    function attempt() {
      pendingPoll = null;
      if (typeof mermaid === "undefined" ||
          document.querySelectorAll(".mermaid").length === 0) {
        if (++attempts < 50) pendingPoll = setTimeout(attempt, 200); // retry ≤ 10 s
        return;
      }
      // Brief settling delay so Mermaid finishes rendering all diagrams
      pendingPoll = setTimeout(markZoomable, 300);
    }
    attempt();
  }

  /* ── Click handling (event delegation) ─────────────────────────────── */

  /**
   * Walk up from the click target to find the .mermaid container,
   * then determine its index among all .mermaid containers on the page.
   * Returns -1 for non-zoomable diagrams.
   */
  function findMermaidIndex(target) {
    var el = target;
    for (var i = 0; i < 10 && el && el !== document.body; i++) {
      if (el.classList && el.classList.contains("mermaid")) {
        if (el.classList.contains("mermaid-no-zoom")) return -1;
        var all = document.querySelectorAll(".mermaid");
        return Array.prototype.indexOf.call(all, el);
      }
      el = el.parentElement;
    }
    return -1;
  }

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
      openOverlayWithSvg(result.svg);
    }).catch(function (err) {
      console.error("[mermaid-zoom] render failed:", err);
    });
  }, true);     // capture phase to beat any stopPropagation in the theme

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && overlay && overlay.classList.contains("active")) {
      closeOverlay();
    }
  });
})();
