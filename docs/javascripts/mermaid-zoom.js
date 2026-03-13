/* Mermaid diagram zoom — click to fullscreen overlay ---------------------- */
/* Uses event delegation so it works even after Mermaid replaces DOM elements  */
/* and after SPA-style instant navigation in Zensical / Material for MkDocs.  */

;(function () {
  "use strict";

  var overlay = null;

  /** Lazy-create the reusable overlay element. */
  function getOverlay() {
    if (overlay) return overlay;
    overlay = document.createElement("div");
    overlay.className = "mermaid-zoom-overlay";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-label", "Zoomed diagram — click or press Escape to close");

    var hint = document.createElement("span");
    hint.className = "mermaid-zoom-close";
    hint.textContent = "Click or press Esc to close";
    overlay.appendChild(hint);

    overlay.addEventListener("click", function () { closeOverlay(); });
    return overlay;
  }

  /** Open the overlay with a clone of the given SVG. */
  function openOverlay(svg) {
    var el = getOverlay();
    // Remove any previous SVG clone
    var prev = el.querySelector("svg");
    if (prev) prev.remove();

    var clone = svg.cloneNode(true);
    // Remove fixed dimensions so CSS max-width/max-height take effect
    clone.removeAttribute("width");
    clone.removeAttribute("height");
    clone.style.width = "";
    clone.style.height = "";
    el.appendChild(clone);

    document.body.appendChild(el);
    // Force reflow before adding class for CSS transition
    void el.offsetWidth;
    el.classList.add("active");
    document.body.style.overflow = "hidden";
  }

  /** Close the overlay. */
  function closeOverlay() {
    if (!overlay) return;
    overlay.classList.remove("active");
    document.body.style.overflow = "";
    overlay.addEventListener("transitionend", function handler() {
      overlay.removeEventListener("transitionend", handler);
      if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
    });
  }

  /**
   * Walk up from a clicked element to find a Mermaid container with an SVG.
   * Mermaid rendering can produce different DOM shapes:
   *   <pre class="mermaid"><svg …></pre>
   *   <div class="mermaid"><svg …></div>
   *   <svg …> (the SVG itself may be the target)
   */
  function findMermaidSvg(target) {
    var el = target;
    // Walk up at most 10 levels
    for (var i = 0; i < 10 && el && el !== document.body; i++) {
      // Direct SVG click inside a .mermaid container
      if (el.tagName === "svg" || el.tagName === "SVG") {
        var parent = el.parentElement;
        if (parent && parent.classList && parent.classList.contains("mermaid")) {
          return el;
        }
      }
      // Clicked on the .mermaid container or a child element
      if (el.classList && el.classList.contains("mermaid")) {
        return el.querySelector("svg");
      }
      el = el.parentElement;
    }
    return null;
  }

  // --- Event delegation: single listener on document, works across navigations ---

  document.addEventListener("click", function (e) {
    // Ignore clicks when the overlay is already open (handled by overlay listener)
    if (overlay && overlay.classList.contains("active")) return;

    var svg = findMermaidSvg(e.target);
    if (svg) {
      e.preventDefault();
      e.stopPropagation();
      openOverlay(svg);
    }
  }, true);  // Use capture phase to beat any stopPropagation in the theme

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && overlay && overlay.classList.contains("active")) {
      closeOverlay();
    }
  });
})();
